"""接口测试：仓库摄取、任务状态、SSE、列表与删除。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _first_event(text: str) -> tuple[str, dict[str, object]]:
    """从 SSE 原始文本里解析第一个事件名与 JSON 载荷。"""
    name = ""
    data: dict[str, object] = {}
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            break
    return name, data


def test_index_local_repo_end_to_end(client: TestClient, demo_repo: Path, wait_job) -> None:
    resp = client.post("/api/repos", json={"target": str(demo_repo)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["repoId"].startswith("local-demo-repo-")

    job = wait_job(client, body["jobId"])
    assert job["state"] == "done", job
    assert job["phase"] == "done"
    assert job["progress"] == 1.0

    detail = client.get(f"/api/repos/{body['repoId']}").json()
    assert detail["state"] == "done"
    assert detail["fileCount"] >= 5
    assert detail["loc"] > 0
    assert detail["languages"]["python"] >= 1
    assert detail["commit"] is None  # 本地目标没有 commit


def test_repo_list_and_delete(client: TestClient, demo_repo: Path, wait_job) -> None:
    created = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    wait_job(client, created["jobId"])

    listed = client.get("/api/repos").json()["repos"]
    assert [r["repoId"] for r in listed] == [created["repoId"]]

    assert client.delete(f"/api/repos/{created['repoId']}").status_code == 204
    assert client.get(f"/api/repos/{created['repoId']}").status_code == 404
    assert client.get("/api/repos").json()["repos"] == []
    assert client.delete(f"/api/repos/{created['repoId']}").status_code == 404


def test_sse_replays_terminal_state(client: TestClient, demo_repo: Path, wait_job) -> None:
    """任务已结束时订阅也必须能拿到终态事件，否则前端进度条会一直等。"""
    created = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    wait_job(client, created["jobId"])

    with client.stream("GET", f"/api/jobs/{created['jobId']}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = b"".join(resp.iter_bytes()).decode("utf-8")

    name, data = _first_event(raw)
    assert name == "done"
    assert data["state"] == "done"
    assert data["progress"] == 1.0


def test_job_404_for_unknown_id(client: TestClient) -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.get("/api/jobs/../etc/passwd").status_code == 404


def test_rejects_dangerous_targets(client: TestClient) -> None:
    cases = [
        {"target": "ext::sh -c 'touch /tmp/pwned'"},
        {"target": "file:///etc/passwd"},
        {"target": "--upload-pack=/bin/sh"},
        {"target": "https://github.com/a/b extra"},
        {"target": "/definitely/not/here"},
        {"target": ""},
    ]
    for payload in cases:
        resp = client.post("/api/repos", json=payload)
        assert resp.status_code in (400, 422), f"{payload} → {resp.status_code} {resp.text}"
        assert resp.json()["detail"]


def test_rejects_ref_injection(client: TestClient) -> None:
    resp = client.post(
        "/api/repos", json={"target": "psf/requests", "ref": "--upload-pack=/bin/sh"}
    )
    assert resp.status_code == 400


def test_rejects_path_traversal_repo_id(client: TestClient) -> None:
    # .hidden / .. 都不符合 repoId 白名单，必须 404 而不是去拼数据库路径
    assert client.get("/api/repos/.hidden").status_code == 404
    assert client.delete("/api/repos/.hidden").status_code == 404
    assert client.get("/api/repos/..").status_code in (404, 405)


def test_reindex_after_completion_is_allowed(client: TestClient, demo_repo: Path, wait_job) -> None:
    first = client.post("/api/repos", json={"target": str(demo_repo)})
    assert first.status_code == 201
    wait_job(client, first.json()["jobId"])
    # 上一次任务已结束，应当允许重新索引
    again = client.post("/api/repos", json={"target": str(demo_repo)})
    assert again.status_code == 201
    assert again.json()["repoId"] == first.json()["repoId"]
