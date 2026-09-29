"""M6 边界与安全测试：编码、CRLF、超长行、极深路径、Unicode 文件名、空仓库、
子模块标记、越界符号链接，以及"删除仓库绝不能删掉用户的本地目录"。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from cogen.api import deps


def _build_edge_repo(root: Path) -> Path:
    repo = root / "edge-repo"
    repo.mkdir(parents=True)

    # Unicode 目录与文件名（含 CJK 标识符）
    (repo / "深/嵌套/目录").mkdir(parents=True)
    (repo / "深/嵌套/目录" / "模块.py").write_text("VALUE = 1\n")
    (repo / "中文文件.py").write_text("def 函数():\n    return 1\n")
    (repo / "emoji🚀.py").write_text("y = 2\n")

    # 极深目录
    deep = repo
    for index in range(30):
        deep = deep / f"lvl{index}"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("z = 3\n")

    # 编码与换行
    (repo / "latin1.py").write_bytes("x = 'caf\xe9'\n".encode("latin-1"))
    (repo / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    (repo / "bom.py").write_bytes(b"\xef\xbb\xbfx = 1\n")

    # 单行超长（约 400KB，仍在上限内）
    (repo / "long.py").write_text("x = " + "1 + " * 100_000 + "1\n")

    # 只有一个空行 / 只有注释
    (repo / "blank.py").write_text("\n")
    (repo / "comment.py").write_text("# 只有注释\n")

    # 子模块标记：.git 是文件而不是目录
    (repo / ".git").write_text("gitdir: ../.git/modules/edge\n")

    # 指向外部的符号链接（不应被跟随）
    outside = root / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("token = 'x'\n")
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    return repo


def test_indexes_edge_case_repo(client: TestClient, wait_job, tmp_path: Path, cogen_home) -> None:
    repo = _build_edge_repo(tmp_path)
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job

    files = client.get(f"/api/repos/{created['repoId']}/files", params={"limit": 500}).json()
    paths = {item["path"] for item in files["files"]}

    # Unicode / 极深路径 / 编码文件都被收录
    assert "中文文件.py" in paths
    assert "emoji🚀.py" in paths
    assert "深/嵌套/目录/模块.py" in paths
    assert "lvl0/" + "/".join(f"lvl{i}" for i in range(1, 30)) + "/deep.py" in paths
    assert {"latin1.py", "crlf.py", "bom.py", "long.py", "blank.py", "comment.py"} <= paths
    # 子模块标记与越界符号链接都不会被跟随
    assert not any(path.startswith("escape") for path in paths), sorted(paths)
    assert ".git" not in paths, "子模块的 .git 标记文件不该被索引"

    long_file = next(item for item in files["files"] if item["path"] == "long.py")
    assert long_file["nodeCount"] > 100
    assert long_file["parseOk"] is True

    deep_file = next(item for item in files["files"] if item["path"] == "中文文件.py")
    assert deep_file["language"] == "python"


def test_unicode_path_round_trips_through_api(
    client: TestClient, wait_job, tmp_path: Path, cogen_home
) -> None:
    repo = _build_edge_repo(tmp_path)
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    wait_job(client, created["jobId"])
    repo_id = created["repoId"]

    body = client.get(f"/api/repos/{repo_id}/file", params={"path": "中文文件.py"}).json()
    assert "函数" in body["text"]

    cst = client.get(f"/api/repos/{repo_id}/cst", params={"path": "中文文件.py", "depth": 4}).json()
    assert cst["node"]["type"] == "module"
    identifiers = _collect(cst["node"], "identifier")
    assert any(node["text"] == "函数" for node in identifiers)

    emoji = client.get(f"/api/repos/{repo_id}/file", params={"path": "emoji🚀.py"}).json()
    assert emoji["language"] == "python"

    # 越界符号链接即便在磁盘上存在也不可读（没进索引 + 路径校验）
    assert client.get(
        f"/api/repos/{repo_id}/file", params={"path": "escape/secret.py"}
    ).status_code in (
        400,
        404,
    )


def _collect(node: dict, node_type: str) -> list[dict]:
    found = [node] if node["type"] == node_type else []
    for child in node.get("children") or []:
        found.extend(_collect(child, node_type))
    return found


def test_crlf_and_latin1_files_are_readable(
    client: TestClient, wait_job, tmp_path: Path, cogen_home
) -> None:
    repo = _build_edge_repo(tmp_path)
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    wait_job(client, created["jobId"])
    repo_id = created["repoId"]

    crlf = client.get(f"/api/repos/{repo_id}/file", params={"path": "crlf.py"}).json()
    assert crlf["lines"] >= 2
    assert "a = 1" in crlf["text"]

    latin = client.get(f"/api/repos/{repo_id}/file", params={"path": "latin1.py"}).json()
    assert "caf" in latin["text"]  # 非 UTF-8 字节用替换字符兜底，不抛异常
    assert client.get(f"/api/repos/{repo_id}/cst", params={"path": "latin1.py"}).status_code == 200


def test_empty_repository(client: TestClient, wait_job, tmp_path: Path, cogen_home) -> None:
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    created = client.post("/api/repos", json={"target": str(empty)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    repo_id = created["repoId"]

    assert client.get(f"/api/repos/{repo_id}/files").json()["total"] == 0
    graph = client.get(f"/api/repos/{repo_id}/graph").json()
    # 空仓库仍有 1 个 repo 节点，但不应有 file/symbol 节点
    assert graph["edges"] == []
    assert [node["kind"] for node in graph["nodes"]] in ([], ["repo"])
    tree = client.get(f"/api/repos/{repo_id}/tree").json()
    assert tree["totalFiles"] == 0
    analysis = client.get(f"/api/repos/{repo_id}/analysis").json()
    assert analysis["communities"] == []
    assert client.get(f"/api/repos/{repo_id}/summary").json()["summary"]


def test_binary_only_repository(client: TestClient, wait_job, tmp_path: Path, cogen_home) -> None:
    repo = tmp_path / "binary-repo"
    repo.mkdir()
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\x03" * 1000)
    (repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00" + b"x" * 100)
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    files = client.get(f"/api/repos/{created['repoId']}/files").json()
    assert files["total"] == 0


def test_delete_local_repo_keeps_user_directory(
    client: TestClient, wait_job, demo_repo: Path, cogen_home
) -> None:
    """安全属性：删除索引**绝不能**删掉用户的本地仓库目录。"""
    created = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    wait_job(client, created["jobId"])
    assert client.delete(f"/api/repos/{created['repoId']}").status_code == 204
    assert demo_repo.is_dir()
    assert (demo_repo / "src" / "a.py").is_file()
    assert client.get(f"/api/repos/{created['repoId']}").status_code == 404


def test_delete_then_reindex(client: TestClient, wait_job, demo_repo: Path, cogen_home) -> None:
    first = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    wait_job(client, first["jobId"])
    client.delete(f"/api/repos/{first['repoId']}")
    again = client.post("/api/repos", json={"target": str(demo_repo)}).json()
    assert again["repoId"] == first["repoId"]
    assert wait_job(client, again["jobId"])["state"] == "done"


def test_broken_symlink_does_not_break_walk(
    client: TestClient, wait_job, tmp_path: Path, cogen_home
) -> None:
    repo = tmp_path / "broken-link-repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x = 1\n")
    (repo / "dangling.py").symlink_to(tmp_path / "does-not-exist.py")
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    paths = {
        item["path"] for item in client.get(f"/api/repos/{created['repoId']}/files").json()["files"]
    }
    assert paths == {"ok.py"}


def test_repo_root_removed_after_index(
    client: TestClient, wait_job, tmp_path: Path, cogen_home
) -> None:
    """索引后快照被删（git 快照被清理）→ 接口给出 410 而不是 500。"""
    repo = tmp_path / "gone-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    wait_job(client, created["jobId"])
    shutil.rmtree(repo)
    assert (
        client.get(f"/api/repos/{created['repoId']}/file", params={"path": "a.py"}).status_code
        == 410
    )


def test_repo_root_pending_while_indexing(
    client: TestClient, wait_job, tmp_path: Path, cogen_home
) -> None:
    """快照"还没建出来"（索引进行中）→ 409 稍后重试；任务结束后同一请求才是 410。

    对应真实时序：POST /api/repos 先把 meta（含 rootPath）写库并返回，git clone 是在
    后台线程里才把快照目录建出来的；这段窗口里前端若已经选中了这个新仓库，读树不该
    被告知"快照已不存在，请重新索引"。
    """
    repo = tmp_path / "pending-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    created = client.post("/api/repos", json={"target": str(repo)}).json()
    wait_job(client, created["jobId"])
    shutil.rmtree(repo)
    rid = created["repoId"]

    # 模拟"克隆/遍历进行中"：任务库里留一条 queued 任务，JobManager 就视其为 active。
    job_id = "job-pending-snapshot"
    manager = deps.get_manager()
    manager.store.create(job_id, rid, phase="clone")

    try:
        pending = client.get(f"/api/repos/{rid}/tree")
        assert pending.status_code == 409, pending.text
        assert "索引进行中" in pending.json()["detail"]
        # 需要磁盘的接口同样给 409，而不是骗用户去重新索引
        assert client.get(f"/api/repos/{rid}/file", params={"path": "a.py"}).status_code == 409
    finally:
        manager.store.update(job_id, state="done")

    gone = client.get(f"/api/repos/{rid}/tree")
    assert gone.status_code == 410, gone.text


def test_api_responses_are_not_cacheable(client: TestClient, cogen_home) -> None:
    """404/410 这类 4xx 默认可被浏览器缓存：一次瞬时失败会被"缓存住刷新也没用"。

    所以 API 响应必须显式 no-store，成功与失败都要。
    """
    assert client.get("/api/repos").headers.get("cache-control") == "no-store"
    missing = client.get("/api/repos/does-not-exist/tree")
    assert missing.status_code == 404, missing.text
    assert missing.headers.get("cache-control") == "no-store"
