"""共享测试夹具：临时 COGEN_HOME、demo 仓库、FastAPI 测试客户端。"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cogen.api import deps
from cogen.api.app import create_app
from cogen.config import reset_settings_cache


@pytest.fixture
def cogen_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把 COGEN_HOME 指到临时目录，并清掉配置/任务管理器单例。"""
    deps.shutdown_state()
    reset_settings_cache()
    home = tmp_path / ".cogen"
    monkeypatch.setenv("COGEN_HOME", str(home))
    reset_settings_cache()
    try:
        yield home
    finally:
        deps.shutdown_state()
        reset_settings_cache()


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    """构造一个覆盖各种边界的小仓库（见 test_walk 的断言）。"""
    repo = tmp_path / "demo-repo"
    (repo / "src" / "deep").mkdir(parents=True)
    (repo / "node_modules" / "pkg").mkdir(parents=True)

    (repo / ".gitignore").write_text("*.log\nbuild/\nsecret.txt\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("import os\n\n\ndef main():\n    return os.getcwd()\n")
    (repo / "src" / "b.ts").write_text("export const x: number = 1;\n")
    (repo / "src" / "deep" / "c.go").write_text("package main\n\nfunc main() {}\n")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "script").write_text("#!/usr/bin/env python3\nprint('hi')\n")
    (repo / ".env.example").write_text("API_KEY=\n", encoding="utf-8")

    # 以下都应被跳过
    (repo / "app.log").write_text("ignored by gitignore\n")
    (repo / "secret.txt").write_text("ignored by gitignore\n")
    (repo / ".env").write_text("API_KEY=abcdef123456\n")
    (repo / "package-lock.json").write_text("{}")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (repo / "big.py").write_text("x = 1\n" * 300_000)
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("var a = 1;\n")
    os.symlink(repo / "src" / "a.py", repo / "link.py")
    return repo


def wait_for_job(client: TestClient, job_id: str, *, timeout: float = 20.0) -> dict[str, object]:
    """轮询任务直到进入终态（done / error）。"""
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["state"] in ("done", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {last}")


@pytest.fixture
def wait_job() -> Callable[..., dict[str, object]]:
    """把轮询助手暴露成夹具，避免测试模块跨目录 import conftest。"""
    return wait_for_job


@pytest.fixture
def client(cogen_home: Path) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def index_demo(client: TestClient, demo_repo: Path) -> dict[str, object]:
    """提交 demo 仓库并等索引完成，返回 {repoId, jobId, job}。"""
    resp = client.post("/api/repos", json={"target": str(demo_repo)})
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    job = wait_for_job(client, payload["jobId"])
    return {**payload, "job": job}
