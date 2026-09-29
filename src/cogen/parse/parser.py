"""单文件解析：语言解析、节点计数、语法错误统计。

语言来源优先级：核心层 wheel → 扩展层（``cogen[xlang]``，默认未安装）→ 不可解析。
不可解析时返回 ``None``，由调用方决定降级行为（只浏览源码等）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser

from . import tscompat as ts
from .languages import (
    has_core_grammar,
    is_display_only,
    load_language,
    load_xlang_language,
    xlang_available,
)
from .tscompat import SourceIndex


class UnknownLanguageError(LookupError):
    """该语言在当前环境没有可用语法。"""


@dataclass
class ParsedSource:
    """一次成功解析的结果（tree/root 由调用方按需使用，用完即弃，不持久化）。"""

    language: str
    source: bytes
    index: SourceIndex
    tree: Any
    root: Any
    node_count: int
    error_count: int
    missing_count: int


@dataclass(frozen=True)
class ParseOutcome:
    """解析统计（可跨进程 pickle，用于回填 files 表）。"""

    path: str
    language: str
    ok: bool
    node_count: int = 0
    error_count: int = 0
    missing_count: int = 0
    duration_ms: float = 0.0
    error: str | None = None


def resolve_language(language: str) -> Language | None:
    """解析出可用的 ``Language``；没有语法时返回 ``None``。"""
    if has_core_grammar(language):
        return load_language(language)
    if is_display_only(language):
        return None
    if xlang_available():
        # 扩展层：首次调用会联网下载原生解析器（缓存目录已钉在工作区内）
        return load_xlang_language(language)
    return None


def is_parsable(language: str) -> bool:
    if has_core_grammar(language):
        return True
    if is_display_only(language):
        return False
    return xlang_available()


def parse_source(source: bytes, language: str) -> ParsedSource:
    """解析源码；语言不可用时抛 ``UnknownLanguageError``。"""
    lang = resolve_language(language)
    if lang is None:
        raise UnknownLanguageError(f"该语言没有可用语法: {language}")
    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    errors, missing = ts.count_problems(root)
    return ParsedSource(
        language=language,
        source=source,
        index=SourceIndex(source),
        tree=tree,
        root=root,
        node_count=ts.descendant_count(root),
        error_count=errors,
        missing_count=missing,
    )


@lru_cache(maxsize=8)
def _parse_cached(abs_path: str, language: str, mtime_ns: int, size: int) -> ParsedSource:
    """CST 视图会为同一文件连续发多次子树请求，这里的缓存避免重复解析。"""
    del mtime_ns, size  # 仅参与缓存键
    return parse_source(Path(abs_path).read_bytes(), language)


def parse_file_cached(path: Path, language: str) -> ParsedSource:
    """带缓存地解析磁盘文件（键含 mtime/size，文件改动即失效）。"""
    stat = Path(path).stat()
    return _parse_cached(str(path), language, stat.st_mtime_ns, stat.st_size)


def clear_parse_cache() -> None:
    _parse_cached.cache_clear()


def parse_file(
    path: Path,
    language: str,
    *,
    max_bytes: int,
    report_path: str | None = None,
) -> ParseOutcome:
    """读取并解析一个文件，把任何失败转成 ``ParseOutcome.error``。

    ``report_path`` 用于回填数据库：调用方传仓库内相对路径，避免把机器绝对路径写进图谱。
    """
    started = time.perf_counter()
    label = report_path or str(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return ParseOutcome(
            path=label, language=language, ok=False, error=f"read_failed: {exc.strerror or exc}"
        )
    if len(data) > max_bytes:
        return ParseOutcome(path=label, language=language, ok=False, error="too_large")
    try:
        parsed = parse_source(data, language)
    except UnknownLanguageError:
        return ParseOutcome(path=label, language=language, ok=False, error="no_grammar")
    except Exception as exc:  # 原生解析器可能因敌意输入抛错，不能让它中断整轮
        return ParseOutcome(
            path=label,
            language=language,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return ParseOutcome(
        path=label,
        language=language,
        ok=True,
        node_count=parsed.node_count,
        error_count=parsed.error_count,
        missing_count=parsed.missing_count,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )
