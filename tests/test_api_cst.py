"""CST / 文件接口测试：文件列表、源码、惰性子树、安全与降级。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _indexed(client: TestClient, demo_repo, wait_job) -> str:
    created = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    wait_job(client, created["jobId"])
    return str(created["repoId"])


def test_files_list(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    body = client.get(f"/api/repos/{repo_id}/files").json()
    assert body["total"] >= 5
    paths = {f["path"] for f in body["files"]}
    assert {"src/a.py", "src/b.ts", "README.md"} <= paths
    a_py = next(f for f in body["files"] if f["path"] == "src/a.py")
    assert a_py["language"] == "python"
    assert a_py["nodeCount"] > 0
    assert a_py["parseOk"] is True

    filtered = client.get(f"/api/repos/{repo_id}/files", params={"q": "src/"}).json()
    assert all(f["path"].startswith("src/") for f in filtered["files"])
    assert filtered["total"] < body["total"]


def test_files_pagination(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    first = client.get(f"/api/repos/{repo_id}/files", params={"limit": 2}).json()
    assert len(first["files"]) == 2
    second = client.get(f"/api/repos/{repo_id}/files", params={"limit": 2, "offset": 2}).json()
    assert {f["path"] for f in first["files"]} & {f["path"] for f in second["files"]} == set()


def test_file_text(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    body = client.get(f"/api/repos/{repo_id}/file", params={"path": "src/a.py"}).json()
    assert body["language"] == "python"
    assert "def main" in body["text"]
    assert body["lines"] == 5
    assert body["truncated"] is False


def test_file_path_traversal_rejected(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    for bad in ["../../etc/passwd", "/etc/passwd", "src/../../etc/passwd"]:
        resp = client.get(f"/api/repos/{repo_id}/file", params={"path": bad})
        assert resp.status_code in (400, 404), f"{bad} → {resp.status_code}"


def test_skipped_files_are_not_readable(client: TestClient, demo_repo, wait_job) -> None:
    """被跳过的 .env 没有入库，因此不可读——这是安全属性。"""
    repo_id = _indexed(client, demo_repo, wait_job)
    assert client.get(f"/api/repos/{repo_id}/file", params={"path": ".env"}).status_code == 404
    assert (
        client.get(
            f"/api/repos/{repo_id}/file", params={"path": "node_modules/pkg/index.js"}
        ).status_code
        == 404
    )


def test_cst_root_and_lazy_children(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    root = client.get(f"/api/repos/{repo_id}/cst", params={"path": "src/a.py", "depth": 1}).json()
    assert root["node"]["type"] == "module"
    assert root["totalNodes"] > 1
    first_child = root["node"]["children"][0]
    assert first_child["truncated"] is True
    assert first_child["children"] == []

    # 懒加载该子节点：带 nodePath 再请求一次
    deeper = client.get(
        f"/api/repos/{repo_id}/cst",
        params={"path": "src/a.py", "nodePath": "0", "depth": 3},
    ).json()
    assert deeper["node"]["type"] == first_child["type"]
    assert deeper["node"]["children"], "懒加载应返回子节点"
    assert deeper["node"]["start"] == first_child["start"]


def test_cst_total_nodes_matches_files_metric(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    files = client.get(f"/api/repos/{repo_id}/files", params={"q": "src/a.py"}).json()["files"]
    cst_body = client.get(f"/api/repos/{repo_id}/cst", params={"path": "src/a.py"}).json()
    assert cst_body["totalNodes"] == files[0]["nodeCount"]


def test_cst_bad_node_path(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    for bad in ["abc", "99", "0.99.0"]:
        resp = client.get(f"/api/repos/{repo_id}/cst", params={"path": "src/a.py", "nodePath": bad})
        assert resp.status_code == 404, f"{bad} → {resp.status_code}"


def test_cst_415_for_language_without_grammar(client: TestClient, demo_repo, wait_job) -> None:
    """没有语法的文件（markdown）应明确 415，但源码仍可浏览。"""
    repo_id = _indexed(client, demo_repo, wait_job)
    resp = client.get(f"/api/repos/{repo_id}/cst", params={"path": "README.md"})
    assert resp.status_code == 415
    assert "没有可用语法" in resp.json()["detail"]
    assert client.get(f"/api/repos/{repo_id}/file", params={"path": "README.md"}).status_code == 200


def test_cst_sexp_format(client: TestClient, demo_repo, wait_job) -> None:
    repo_id = _indexed(client, demo_repo, wait_job)
    body = client.get(
        f"/api/repos/{repo_id}/cst", params={"path": "src/a.py", "format": "sexp"}
    ).json()
    assert body["format"] == "sexp"
    assert body["sexp"].startswith("(module")


def test_cst_error_nodes_are_exposed(client: TestClient, tmp_path, wait_job, cogen_home) -> None:
    """语法错误文件必须能返回 CST（把 ERROR/MISSING 暴露给前端），而不是 500。"""
    repo = tmp_path / "broken-repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def broken(:\n    pass\n")
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    wait_job(client, created["jobId"])
    body = client.get(
        f"/api/repos/{created['repoId']}/cst", params={"path": "bad.py", "depth": 6}
    ).json()
    assert body["node"]["type"] == "module"

    def walk(node: dict) -> list[dict]:
        out = [node]
        for child in node.get("children") or []:
            out.extend(walk(child))
        return out

    nodes = walk(body["node"])
    assert any(n["error"] or n["missing"] for n in nodes)


def test_file_endpoints_404_for_unknown_repo(client: TestClient) -> None:
    assert client.get("/api/repos/nope-123/files").status_code == 404
    assert client.get("/api/repos/nope-123/file", params={"path": "a.py"}).status_code == 404
    assert client.get("/api/repos/nope-123/cst", params={"path": "a.py"}).status_code == 404


def test_stale_flag_when_file_changed_after_index(client: TestClient, demo_repo, wait_job) -> None:
    """索引后文件被改动时要给出 stale 提示，而不是静默返回过期统计。"""
    repo_id = _indexed(client, demo_repo, wait_job)
    target = demo_repo / "src" / "a.py"
    target.write_text(target.read_text() + "\n# changed after index\n")
    file_body = client.get(f"/api/repos/{repo_id}/file", params={"path": "src/a.py"}).json()
    assert file_body["stale"] is True
    cst_body = client.get(f"/api/repos/{repo_id}/cst", params={"path": "src/a.py"}).json()
    assert cst_body["stale"] is True
    assert "changed after index" in file_body["text"]
