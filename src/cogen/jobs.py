"""后台索引任务：线程执行 + 进度事件广播 + 独立 jobs.sqlite 持久化。

任务表放在**全局** ``.cogen/jobs.sqlite``（而不是每个仓库的库里），这样
``GET /api/jobs/{jobId}`` 无需先知道 repoId；仓库库只存 meta/files/nodes/edges。
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .graph.schema import RepoMeta
from .graph.store import Store, open_store
from .security import redact_secrets

_EMIT_INTERVAL_S = 0.15

_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    repo_id     TEXT NOT NULL,
    phase       TEXT NOT NULL,
    state       TEXT NOT NULL,
    progress    REAL NOT NULL DEFAULT 0,
    current     INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    file        TEXT,
    error       TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_repo ON jobs(repo_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


class JobStore:
    """任务表读写（单文件 SQLite）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_JOBS_DDL)
            self._conn.commit()

    def create(self, job_id: str, repo_id: str, phase: str = "resolve") -> dict[str, Any]:
        row = {
            "id": job_id,
            "repoId": repo_id,
            "phase": phase,
            "state": "queued",
            "progress": 0.0,
            "current": 0,
            "total": 0,
            "message": None,
            "file": None,
            "error": None,
            "startedAt": now_iso(),
            "finishedAt": None,
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(id, repo_id, phase, state, progress, current, total, message, "
                "file, error, started_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET repo_id=excluded.repo_id, phase=excluded.phase, "
                "state=excluded.state, progress=0, current=0, total=0, message=NULL, file=NULL, "
                "error=NULL, started_at=excluded.started_at, finished_at=NULL",
                (
                    job_id,
                    repo_id,
                    phase,
                    "queued",
                    0.0,
                    0,
                    0,
                    None,
                    None,
                    None,
                    row["startedAt"],
                    None,
                ),
            )
            self._conn.commit()
        return row

    def update(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "repoId",
            "phase",
            "state",
            "progress",
            "current",
            "total",
            "message",
            "file",
            "error",
            "startedAt",
            "finishedAt",
        }
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        mapping = {
            "repoId": "repo_id",
            "phase": "phase",
            "state": "state",
            "progress": "progress",
            "current": "current",
            "total": "total",
            "message": "message",
            "file": "file",
            "error": "error",
            "startedAt": "started_at",
            "finishedAt": "finished_at",
        }
        assignments = ", ".join(f"{mapping[k]} = ?" for k in cols)
        values = [*cols.values(), job_id]
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
            self._conn.commit()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def active_for_repo(self, repo_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE repo_id = ? AND state IN ('queued','running') "
                "ORDER BY started_at DESC LIMIT 1",
                (repo_id,),
            ).fetchone()
        return _row_to_job(row) if row else None

    def mark_orphans(self, note: str = "进程重启，任务已中断") -> int:
        """把残留的 queued/running 任务标记为中断，避免永久阻塞同一仓库。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET state='error', message=?, error=?, finished_at=? "
                "WHERE state IN ('queued','running')",
                (note, note, now_iso()),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "repoId": row["repo_id"],
        "phase": row["phase"],
        "state": row["state"],
        "progress": float(row["progress"]),
        "current": int(row["current"]),
        "total": int(row["total"]),
        "message": row["message"],
        "file": row["file"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


@dataclass(frozen=True)
class JobEvent:
    """一次进度事件（SSE 载荷）。"""

    phase: str
    state: str
    progress: float
    current: int
    total: int
    message: str | None = None
    file: str | None = None
    error: str | None = None

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": self.phase,
            "state": self.state,
            "progress": self.progress,
            "current": self.current,
            "total": self.total,
            "message": self.message,
        }
        if self.file:
            payload["file"] = self.file
        if self.error:
            payload["error"] = self.error
        return payload


class Progress:
    """传给 runner 的进度上报句柄：写库 + 广播 + 节流。"""

    def __init__(self, manager: JobManager, job_id: str) -> None:
        self._manager = manager
        self._job_id = job_id
        self._last_emit = 0.0
        self._last_phase: str | None = None
        self.phase = "resolve"
        self.state = "running"
        self.current = 0
        self.total = 0
        self.message: str | None = None
        self.file: str | None = None
        self.error: str | None = None

    def update(
        self,
        *,
        phase: str | None = None,
        state: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        file: str | None = None,
        error: str | None = None,
        force: bool = False,
    ) -> None:
        if phase is not None:
            if phase != self._last_phase:
                force = True
                self._last_phase = phase
            self.phase = phase
        if state is not None:
            if state != self.state:
                force = True
            self.state = state
        if current is not None:
            self.current = current
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message
        if file is not None:
            self.file = file
        if error is not None:
            self.error = error

        now = time.monotonic()
        terminal = self.state in ("done", "error")
        if not force and not terminal and now - self._last_emit < _EMIT_INTERVAL_S:
            return
        self._last_emit = now

        progress_ratio = 0.0
        if self.state == "done":
            progress_ratio = 1.0
        elif self.total > 0:
            progress_ratio = min(1.0, self.current / self.total)

        event = JobEvent(
            phase=self.phase,
            state=self.state,
            progress=progress_ratio,
            current=self.current,
            total=self.total,
            message=self.message,
            file=self.file,
            error=self.error if terminal else None,
        )
        fields: dict[str, Any] = {
            "phase": self.phase,
            "state": self.state,
            "progress": progress_ratio,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "file": self.file,
        }
        if self.error:
            fields["error"] = self.error
        if terminal:
            fields["finishedAt"] = now_iso()
        self._manager._persist(self._job_id, event, fields)

    def done(self, message: str | None = None) -> None:
        self.update(phase="done", state="done", message=message or "索引完成", force=True)

    def fail(self, error: str) -> None:
        clean = redact_secrets(error)
        self.update(state="error", message=clean, error=clean, force=True)


Runner = Callable[[Store, Progress], None]

#: 阶段中文标签（CLI 与前端共用同一套语义）
PHASE_LABELS: dict[str, str] = {
    "resolve": "解析目标",
    "clone": "克隆仓库",
    "walk": "遍历文件",
    "parse": "解析语法",
    "extract": "抽取符号",
    "build": "构建图谱",
    "analyze": "分析社区",
    "name": "生成命名",
    "done": "完成",
}


class ConsoleProgress:
    """终端进度打印（CLI / 基准脚本用；不需要 JobManager 与数据库）。"""

    def __init__(self, writer: Callable[[str], None] | None = None) -> None:
        self._write = writer or (lambda line: print(line, file=sys.stderr))
        self.phase = "resolve"
        self.state = "running"
        self.message: str | None = None
        self.file: str | None = None
        self.error: str | None = None
        self._last_line = ""

    def _emit(self, line: str) -> None:
        if line != self._last_line:
            self._last_line = line
            self._write(line)

    def update(
        self,
        *,
        phase: str | None = None,
        state: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        file: str | None = None,
        error: str | None = None,
        force: bool = False,
    ) -> None:
        if phase is not None:
            self.phase = phase
        if state is not None:
            self.state = state
        if message is not None:
            self.message = message
        if file is not None:
            self.file = file
        if error is not None:
            self.error = error
        label = PHASE_LABELS.get(self.phase, self.phase)
        detail = self.message or ""
        if total:
            detail = f"{detail} ({current or 0}/{total})"
        self._emit(f"[{label}] {detail}".rstrip())

    def done(self, message: str | None = None) -> None:
        self.state = "done"
        self._emit(f"[完成] {message or '索引完成'}")

    def fail(self, error: str) -> None:
        self.state = "error"
        self.error = error
        self._emit(f"[失败] {error}")


class JobManager:
    """索引任务的创建、执行与订阅。"""

    def __init__(self, settings: Settings, *, max_workers: int = 2) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self.store = JobStore(settings.root / "jobs.sqlite")
        # 任务只活在进程内：启动时任何 queued/running 都是上次异常退出的残留
        self.store.mark_orphans()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cogen-job")
        self._lock = threading.Lock()
        self._subscribers: dict[
            str, list[tuple[asyncio.Queue[JobEvent], asyncio.AbstractEventLoop]]
        ] = {}

    # ── 任务生命周期 ────────────────────────────────────────────────
    def submit(self, job_id: str, meta: RepoMeta, runner: Runner) -> None:
        self.store.create(job_id, meta.repo_id)
        self._executor.submit(self._run, job_id, meta, runner)

    def _run(self, job_id: str, meta: RepoMeta, runner: Runner) -> None:
        progress = Progress(self, job_id)
        progress.update(phase="resolve", state="running", message="开始索引", force=True)
        try:
            store = open_store(self.settings, meta.repo_id)
        except sqlite3.Error as exc:
            progress.fail(f"无法打开仓库数据库: {exc}")
            return
        try:
            runner(store, progress)
        except Exception as exc:  # 任何失败都必须落到任务状态里，不能只打日志
            progress.fail(f"{type(exc).__name__}: {exc}")
        else:
            if progress.state != "error":
                progress.done(progress.message or "索引完成")
        finally:
            store.close()

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.store.get(job_id)

    def active_for_repo(self, repo_id: str) -> dict[str, Any] | None:
        return self.store.active_for_repo(repo_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.store.close()

    # ── 进度持久化与广播 ────────────────────────────────────────────
    def _persist(self, job_id: str, event: JobEvent, fields: dict[str, Any]) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.store.update(job_id, **fields)
        self._broadcast(job_id, event)

    def _broadcast(self, job_id: str, event: JobEvent) -> None:
        with self._lock:
            targets = list(self._subscribers.get(job_id, ()))
        for q, loop in targets:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                # 事件循环已关闭（客户端断开），忽略
                continue

    # ── 订阅（SSE）──────────────────────────────────────────────────
    def subscribe(self, job_id: str, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[JobEvent]:
        q: asyncio.Queue[JobEvent] = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(job_id, []).append((q, loop))
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[JobEvent]) -> None:
        with self._lock:
            entries = self._subscribers.get(job_id)
            if not entries:
                return
            self._subscribers[job_id] = [(qq, loop) for qq, loop in entries if qq is not q]
            if not self._subscribers[job_id]:
                self._subscribers.pop(job_id, None)


def is_terminal(state: str) -> bool:
    return state in ("done", "error")
