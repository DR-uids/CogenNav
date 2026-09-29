"""分进程解析池：墙钟看门狗 + worker 环境变量白名单。

已核实 py-tree-sitter **没有** timeout / cancellation API，所以超时只能靠外部进程：
- 每块（chunk）用 ``map_async().get(timeout)`` 卡墙钟；
- 超时（或 worker 被原生解析器搞崩）→ ``terminate()`` 整池并重建，然后对该块**逐文件**重试，
  只把真正卡住的文件标记 ``parse_timeout``，同块其余文件照常入库；
- worker 启动时清掉环境里的密钥，避免原生解析器被打穿后带走 LLM Key / git token；
- 只传 ``(root, 相对路径, 语言)``，文件内容由 worker 自己读，避免把大文件 pickle 过去。
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from .parser import ParseOutcome, UnknownLanguageError, parse_file, parse_source

#: 明确要清掉的环境变量
_SENSITIVE_ENV_KEYS = (
    "COGEN_LLM_API_KEY",
    "COGEN_GIT_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
)
#: 兜底规则：任何以 _KEY / _TOKEN / _SECRET / _PASSWORD 结尾的变量
_SENSITIVE_ENV_RE = re.compile(r"(?i)(_KEY|_TOKEN|_SECRET|_PASSWORD|_PASSWD|_CREDENTIALS)$")

INDIVIDUAL_TIMEOUT_CAP_S = 30.0


@dataclass
class FileAnalysis:
    """一个文件走完「解析 + 抽取」后的产物（跨进程传回主进程）。"""

    outcome: ParseOutcome
    extraction: Any | None = None  # FileExtraction，避免与 extract 包循环导入


def analyze_task(payload: tuple[str, str, str, int]) -> FileAnalysis:
    """worker 任务：解析 + 单文件抽取（必须定义在模块顶层才能被 pickle）。"""
    from ..extract.base import FileContext, FileExtraction
    from ..extract.registry import get_extractor

    root_str, rel_path, language, max_bytes = payload
    path = Path(root_str) / rel_path
    started = time.perf_counter()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return FileAnalysis(
            ParseOutcome(
                path=rel_path,
                language=language,
                ok=False,
                error=f"read_failed: {exc.strerror or exc}",
            )
        )
    if len(data) > max_bytes:
        return FileAnalysis(
            ParseOutcome(path=rel_path, language=language, ok=False, error="too_large")
        )
    try:
        parsed = parse_source(data, language)
    except UnknownLanguageError:
        return FileAnalysis(
            ParseOutcome(path=rel_path, language=language, ok=False, error="no_grammar")
        )
    except Exception as exc:  # 原生解析器异常不能中断整轮
        return FileAnalysis(
            ParseOutcome(
                path=rel_path,
                language=language,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        )

    outcome = ParseOutcome(
        path=rel_path,
        language=language,
        ok=True,
        node_count=parsed.node_count,
        error_count=parsed.error_count,
        missing_count=parsed.missing_count,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )
    extraction: FileExtraction | None
    try:
        extraction = get_extractor(language).extract(FileContext(rel_path, language, parsed))
    except Exception as exc:  # 抽取器有 bug 也只影响这一个文件
        extraction = FileExtraction(
            file=rel_path,
            language=language,
            errors=[f"{type(exc).__name__}: {exc}"],
        )
    return FileAnalysis(outcome=outcome, extraction=extraction)


#: 进程池启动健康检查超时（spawn 需要父进程的 __main__ 可导入，坏了就降级）
HEALTH_CHECK_TIMEOUT_S = 5.0


class PoolUnavailable(RuntimeError):
    """进程池无法启动（例如父进程 __main__ 不可导入），调用方应降级为单进程解析。"""


def scrub_environment() -> list[str]:
    """在 worker 进程里清掉密钥类环境变量，返回被清掉的键。"""
    removed: list[str] = []
    for key in list(os.environ):
        if key in _SENSITIVE_ENV_KEYS or _SENSITIVE_ENV_RE.search(key):
            os.environ.pop(key, None)
            removed.append(key)
    return removed


def _worker_init() -> None:
    scrub_environment()


def _ping(_: int) -> int:
    """健康检查用：验证 worker 真的能跑起来并回传结果。"""
    return 0


def _parse_one(payload: tuple[str, str, str, int]) -> ParseOutcome:
    """worker 入口：必须定义在模块顶层才能被 pickle。"""
    root_str, rel_path, language, max_bytes = payload
    return parse_file(
        Path(root_str) / rel_path, language, max_bytes=max_bytes, report_path=rel_path
    )


class ParsePool:
    """按块调度 + 超时熔断 + 自动重建的解析池。"""

    def __init__(
        self,
        settings: Settings,
        *,
        workers: int | None = None,
        chunk_size: int = 32,
        task: Callable[[tuple[str, str, str, int]], Any] = _parse_one,
        timeout_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.task = task
        self._timeout_factory = timeout_factory or (
            lambda path, language: ParseOutcome(
                path=path, language=language, ok=False, error="parse_timeout"
            )
        )
        self.workers = workers or settings.effective_parse_workers()
        self.chunk_size = max(1, chunk_size)
        self._ctx = mp.get_context("spawn")
        self._pool: mp.pool.Pool | None = None
        #: "pool" | "serial"（进程池不可用时降级，保证索引不会因为环境问题整体失败）
        self.mode = "pool"
        self.degraded_reason: str | None = None

    # ── 池生命周期 ──────────────────────────────────────────────────
    def _ensure_pool(self, *, health_check: bool = False) -> mp.pool.Pool:
        if self._pool is None:
            self._pool = self._ctx.Pool(processes=self.workers, initializer=_worker_init)
            if health_check:
                self._check_health()
        return self._pool

    def _check_health(self) -> None:
        """spawn 会重新导入父进程的 ``__main__``；交互式/无 guard 的父进程会失败。

        那种情况下 worker 一起来就死，``map_async().get()`` 会一直等下去，所以先 ping 一次。
        """
        pool = self._pool
        if pool is None:
            return
        try:
            pool.map_async(_ping, [0]).get(timeout=HEALTH_CHECK_TIMEOUT_S)
        except Exception as exc:
            self._recycle()
            raise PoolUnavailable(f"{type(exc).__name__}: {exc}") from exc

    def _recycle(self) -> None:
        """超时/崩溃后彻底重建：terminate 比 close 更可靠（绕过卡死的原生调用）。"""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        with contextlib.suppress(Exception):  # 清理失败不影响主流程
            pool.terminate()
        with contextlib.suppress(Exception):
            pool.join()

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        pool.terminate()
        pool.join()

    def __enter__(self) -> ParsePool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── 解析调度 ────────────────────────────────────────────────────
    def parse_files(
        self,
        root: Path,
        items: Sequence[tuple[str, str]],
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[Any]:
        """``items`` 为 ``(相对路径, 语言)`` 序列；按 ``task`` 跑 worker 并原样返回结果。"""
        root = Path(root)
        if self.mode == "serial":
            return self._parse_serial(root, items, progress=progress)

        try:
            self._ensure_pool(health_check=True)
        except PoolUnavailable as exc:
            self.mode = "serial"
            self.degraded_reason = str(exc)
            return self._parse_serial(root, items, progress=progress)

        results: list[Any] = []
        total = len(items)
        for start in range(0, total, self.chunk_size):
            chunk = list(items[start : start + self.chunk_size])
            payload = [
                (str(root), rel_path, language, self.settings.max_file_bytes)
                for rel_path, language in chunk
            ]
            outcomes = self._run_chunk(payload)
            results.extend(outcomes)
            if progress is not None:
                progress(min(start + len(chunk), total), total, chunk[-1][0])
        return results

    def _parse_serial(
        self,
        root: Path,
        items: Sequence[tuple[str, str]],
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[Any]:
        """单进程降级路径：没有墙钟保护，但保证功能可用（仅在进程池不可用时使用）。"""
        results: list[Any] = []
        total = len(items)
        for index, (rel_path, language) in enumerate(items, start=1):
            try:
                results.append(
                    self.task((str(root), rel_path, language, self.settings.max_file_bytes))
                )
            except Exception as exc:  # 原生解析器/抽取器异常不能中断整轮
                results.append(
                    FileAnalysis(
                        ParseOutcome(
                            path=rel_path,
                            language=language,
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    if self.task is analyze_task
                    else ParseOutcome(
                        path=rel_path,
                        language=language,
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            if progress is not None:
                progress(index, total, rel_path)
        return results

    def _run_chunk(self, payload: list[tuple[str, str, str, int]]) -> list[ParseOutcome]:
        try:
            async_result = self._ensure_pool().map_async(self.task, payload)
            return list(async_result.get(timeout=self.settings.parse_chunk_timeout_s))
        except (mp.TimeoutError, TimeoutError):
            # 整块超时或 worker 崩溃：重建池后逐文件重试，只牺牲真正卡住的文件
            self._recycle()
            return self._retry_individually(payload)

    def _retry_individually(self, payload: list[tuple[str, str, str, int]]) -> list[Any]:
        timeout = max(
            1.0,
            min(
                self.settings.parse_chunk_timeout_s,
                self.settings.parse_individual_timeout_s,
                INDIVIDUAL_TIMEOUT_CAP_S,
            ),
        )
        outcomes: list[Any] = []
        for item in payload:
            try:
                async_result = self._ensure_pool().map_async(self.task, [item])
                outcomes.append(async_result.get(timeout=timeout)[0])
            except (mp.TimeoutError, TimeoutError):
                self._recycle()
                outcomes.append(self._timeout_factory(item[1], item[2]))
            except Exception:  # worker 崩溃/序列化失败
                self._recycle()
                outcomes.append(self._timeout_factory(item[1], item[2]))
        return outcomes
