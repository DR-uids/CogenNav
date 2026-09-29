"""需要网络的测试（默认跳过）：真实 git 浅克隆。

运行方式：``make test NET=1`` 或 ``pytest -m network``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cogen.config import Settings
from cogen.ingest.clone import CloneError, clone_repo
from cogen.ingest.resolve import clone_path, resolve_target
from cogen.ingest.walk import walk_repo

pytestmark = pytest.mark.network


def test_clone_small_public_repo(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        home=tmp_path / ".cogen",
        clone_timeout_s=120,
    )
    target = resolve_target("octocat/Hello-World")
    dest = clone_path(settings, target)
    sha = clone_repo(target, dest, settings)
    assert sha and len(sha) == 40
    assert (dest / "README").exists() or (dest / "README.md").exists()

    # 复用已有快照：第二次不应重新克隆
    assert clone_repo(target, dest, settings) == sha

    result = walk_repo(dest, settings)
    assert any(f.path.lower().startswith("readme") for f in result.files)


def test_clone_missing_repo_raises(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home=tmp_path / ".cogen", clone_timeout_s=120)  # type: ignore[call-arg]
    target = resolve_target("https://github.com/cogen-nav-does-not-exist/nope-xyz")
    with pytest.raises(CloneError):
        clone_repo(target, clone_path(settings, target), settings)


def test_rejects_ext_transport_never_reaches_git(tmp_path: Path) -> None:
    """`ext::` 在解析阶段就该被拒，绝不能把命令交给 git。"""
    from cogen.security import UnsafeTargetError

    with pytest.raises(UnsafeTargetError):
        resolve_target("ext::sh -c 'touch /tmp/cogen-pwned'")


def test_index_and_delete_git_repo_removes_snapshot(tmp_path, monkeypatch) -> None:
    """端到端：索引公开仓库 → DELETE 必须清掉克隆快照（但不影响用户目录）。"""
    import time

    from fastapi.testclient import TestClient

    from cogen.api import deps
    from cogen.api.app import create_app
    from cogen.config import reset_settings_cache

    home = tmp_path / ".cogen"
    monkeypatch.setenv("COGEN_HOME", str(home))
    deps.shutdown_state()
    reset_settings_cache()
    try:
        with TestClient(create_app()) as client:
            created = client.post("/api/repos", json={"target": "octocat/Hello-World"}).json()
            deadline = time.monotonic() + 180
            state = "queued"
            while time.monotonic() < deadline:
                job = client.get(f"/api/jobs/{created['jobId']}").json()
                state = job["state"]
                if state in ("done", "error"):
                    break
                time.sleep(0.2)
            assert state == "done", job

            detail = client.get(f"/api/repos/{created['repoId']}").json()
            from pathlib import Path

            snapshot = Path(detail["rootPath"])
            assert snapshot.is_dir()
            assert client.delete(f"/api/repos/{created['repoId']}").status_code == 204
            assert not snapshot.exists(), "删除仓库后克隆快照应被清理"
    finally:
        deps.shutdown_state()
        reset_settings_cache()
