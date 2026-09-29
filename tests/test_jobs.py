"""任务管理器与流水线测试：进度事件、终态、失败落库。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cogen.config import get_settings
from cogen.graph.schema import RepoMeta
from cogen.graph.store import open_store
from cogen.ingest import pipeline
from cogen.ingest.resolve import resolve_target
from cogen.jobs import JobManager, new_job_id


class _FakeProgress:
    """不依赖线程的进度记录器（用于直接调用 pipeline）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.state = "running"

    def update(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if kwargs.get("state"):
            self.state = str(kwargs["state"])

    def done(self, message: str | None = None) -> None:
        self.calls.append({"state": "done", "message": message})
        self.state = "done"


def _meta(repo_id: str) -> RepoMeta:
    return RepoMeta(repo_id=repo_id, target="/tmp/x", source="local", ref=None, root_path="/tmp/x")


def test_index_repo_reports_and_persists(cogen_home: Path, demo_repo: Path) -> None:
    settings = get_settings()
    target = resolve_target(str(demo_repo))
    progress = _FakeProgress()
    with open_store(settings, "local-demo-1") as store:
        pipeline.index_repo(target, settings, store, progress)  # type: ignore[arg-type]
        meta = store.load_repo_meta()
        assert meta is not None
        assert meta.state == "done"
        assert meta.file_count >= 5
        assert meta.languages.get("python", 0) >= 1
        assert store.file_count() == meta.file_count
        assert store.total_loc() == meta.loc

    phases = [str(c.get("phase")) for c in progress.calls if c.get("phase")]
    assert "walk" in phases
    assert progress.state == "done"


def test_index_repo_marks_error_on_failure(
    cogen_home: Path, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    target = resolve_target(str(demo_repo))

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("walk 炸了")

    monkeypatch.setattr(pipeline, "walk_repo", boom)
    progress = _FakeProgress()
    with open_store(settings, "local-demo-2") as store:
        with pytest.raises(RuntimeError):
            pipeline.index_repo(target, settings, store, progress)  # type: ignore[arg-type]
        meta = store.load_repo_meta()
        assert meta is not None
        assert meta.state == "error"
        assert "walk 炸了" in (meta.message or "")


def test_index_repo_marks_truncation(cogen_home: Path, demo_repo: Path) -> None:
    """规模超限必须是「部分索引」而不是静默成功。"""
    from cogen.config import Settings

    settings = Settings(_env_file=None, home=cogen_home, max_files=2)  # type: ignore[call-arg]
    settings.ensure_dirs()
    target = resolve_target(str(demo_repo))
    progress = _FakeProgress()
    with open_store(settings, "local-demo-trunc") as store:
        pipeline.index_repo(target, settings, store, progress)  # type: ignore[arg-type]
        meta = store.load_repo_meta()
        assert meta is not None
        assert meta.state == "done"
        assert "截断" in (meta.message or "")
        assert store.get_meta()["truncated"] is True
        assert store.get_meta()["truncated_reason"] == "max_files"


def test_orphan_jobs_are_marked_interrupted(cogen_home: Path) -> None:
    """上次异常退出留下的 running 任务不能永久阻塞同一仓库的重新索引。"""
    from cogen.config import get_settings as _gs
    from cogen.jobs import JobStore

    settings = _gs()
    settings.ensure_dirs()
    store = JobStore(settings.root / "jobs.sqlite")
    store.create("orphan123456", "local-demo-orphan")
    assert store.active_for_repo("local-demo-orphan") is not None
    store.close()

    manager = JobManager(settings)  # 新建管理器应清理残留
    try:
        assert manager.active_for_repo("local-demo-orphan") is None
        status = manager.status("orphan123456")
        assert status is not None
        assert status["state"] == "error"
        assert "中断" in (status["error"] or "")
    finally:
        manager.shutdown()


def test_job_manager_records_states(cogen_home: Path) -> None:
    settings = get_settings()
    manager = JobManager(settings)
    job_id = new_job_id()

    def runner(store, progress) -> None:  # type: ignore[no-untyped-def]
        progress.update(phase="walk", current=3, total=10, message="走查中")
        progress.done("完成")

    try:
        manager.submit(job_id, _meta("local-demo-3"), runner)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = manager.status(job_id)
            assert status is not None
            if status["state"] in ("done", "error"):
                break
            time.sleep(0.05)
        status = manager.status(job_id)
        assert status is not None
        assert status["state"] == "done"
        assert status["phase"] == "done"
        assert status["progress"] == 1.0
        assert status["message"] == "完成"
        assert status["finishedAt"]
    finally:
        manager.shutdown()


def test_job_manager_records_failure(cogen_home: Path) -> None:
    settings = get_settings()
    manager = JobManager(settings)
    job_id = new_job_id()

    def runner(store, progress) -> None:  # type: ignore[no-untyped-def]
        raise ValueError("目标不可达")

    try:
        manager.submit(job_id, _meta("local-demo-4"), runner)
        deadline = time.monotonic() + 10
        status = None
        while time.monotonic() < deadline:
            status = manager.status(job_id)
            if status and status["state"] == "error":
                break
            time.sleep(0.05)
        assert status is not None
        assert status["state"] == "error"
        assert "目标不可达" in (status["error"] or "")
    finally:
        manager.shutdown()


def test_active_for_repo_blocks_duplicate(cogen_home: Path) -> None:
    settings = get_settings()
    manager = JobManager(settings)
    job_id = new_job_id()
    started = time.monotonic()

    def runner(store, progress) -> None:  # type: ignore[no-untyped-def]
        time.sleep(1.0)
        progress.done("ok")

    try:
        manager.submit(job_id, _meta("local-demo-5"), runner)
        while time.monotonic() - started < 5:
            active = manager.active_for_repo("local-demo-5")
            if active is not None:
                assert active["id"] == job_id
                break
            time.sleep(0.05)
        else:
            raise AssertionError("未观察到运行中的任务")
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_manager_streams_live_progress(cogen_home: Path) -> None:
    """SSE 依赖的实时推流：running 态必须能收到，终态必须收尾。"""
    import asyncio

    settings = get_settings()
    manager = JobManager(settings)
    job_id = new_job_id()

    def runner(store, progress) -> None:  # type: ignore[no-untyped-def]
        for i in range(1, 4):
            progress.update(phase="walk", current=i, total=4, force=True)
            time.sleep(0.05)
        progress.done("完成")

    try:
        queue = manager.subscribe(job_id, asyncio.get_running_loop())
        manager.submit(job_id, _meta("local-demo-6"), runner)
        events = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=5)
            events.append(event)
            if event.state in ("done", "error"):
                break
        assert any(e.state == "running" and e.current > 0 for e in events)
        assert events[-1].state == "done"
    finally:
        manager.unsubscribe(job_id, queue)  # type: ignore[possibly-undefined]
        manager.shutdown()
