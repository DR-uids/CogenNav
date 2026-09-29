"""M3 图谱测试：抽取 → 建图 → 分析 → 查询接口。

fixture 见 ``tests/fixtures/graph_repo``：跨文件调用、类方法自调用、循环导入、
测试文件、无法静态解析的调用（内建 + 局部变量方法）各一份。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parent / "fixtures" / "graph_repo"


@pytest.fixture
def indexed(client: TestClient, wait_job) -> dict[str, object]:
    """索引 fixture 仓库并返回 {repoId, job, analysis}。"""
    created = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    analysis = client.get(f"/api/repos/{created['repoId']}/analysis").json()
    return {"repoId": created["repoId"], "job": job, "analysis": analysis}


def _edges(client: TestClient, repo_id: str, **params) -> list[dict]:
    query = {"limit": 5000, **params}
    return client.get(f"/api/repos/{repo_id}/graph", params=query).json()["edges"]


def _nodes(client: TestClient, repo_id: str, **params) -> list[dict]:
    query = {"limit": 5000, **params}
    return client.get(f"/api/repos/{repo_id}/graph", params=query).json()["nodes"]


def _find(nodes: list[dict], needle: str) -> dict | None:
    return next((n for n in nodes if n["qualified"].endswith(needle)), None)


# ── 抽取与解析质量 ──────────────────────────────────────────────────
def test_cross_file_call_is_inferred(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    step = _find(nodes, "Engine.step")
    render_fn = _find(nodes, "render")
    assert step and render_fn, [n["qualified"] for n in nodes][:20]
    match = [
        e
        for e in edges
        if e["source"] == step["id"] and e["target"] == render_fn["id"] and e["relation"] == "calls"
    ]
    assert match, "Engine.step → render 的跨文件调用未解析"
    assert match[0]["confidence"] == "INFERRED"


def test_self_method_call_is_extracted(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    run = _find(nodes, "Engine.run")
    step = _find(nodes, "Engine.step")
    match = [
        e
        for e in edges
        if e["source"] == run["id"] and e["target"] == step["id"] and e["relation"] == "calls"
    ]
    assert match and match[0]["confidence"] == "EXTRACTED"


def test_second_hop_call_resolved(client: TestClient, indexed) -> None:
    """helpers.render → utils.fmt（跨文件第二跳）。"""
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    render_fn = _find(nodes, "render")
    fmt_fn = _find(nodes, "fmt")
    assert any(e["source"] == render_fn["id"] and e["target"] == fmt_fn["id"] for e in edges), (
        "render → fmt 未解析"
    )


def test_typescript_cross_file_call(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    run = _find(nodes, "Runner.run")
    helper = _find(nodes, "helper")
    assert run and helper
    assert any(
        e["source"] == run["id"] and e["target"] == helper["id"] and e["relation"] == "calls"
        for e in edges
    ), "Runner.run → helper 未解析"


def test_resolution_rate_meets_target(client: TestClient, indexed) -> None:
    """验收：可解析调用的解析率 ≥ 80%。"""
    stats = indexed["analysis"]["stats"]  # type: ignore[index]
    rate = stats["resolvedCallRate"]
    assert rate is not None, stats
    assert rate >= 0.8, stats


# ── 图结构完整性 ────────────────────────────────────────────────────
def test_graph_has_structural_hierarchy(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    kinds = {n["kind"] for n in nodes}
    assert {"repo", "dir", "file"} <= kinds
    assert "defines" in {e["relation"] for e in edges}
    assert any(n["kind"] == "class" and n["name"] == "Engine" for n in nodes)
    assert any(n["kind"] == "method" and n["name"] == "step" for n in nodes)


def test_no_dangling_edges_and_unique_ids(client: TestClient, indexed) -> None:
    """契约测试：不得有悬空边、重复 id。"""
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    edges = _edges(client, repo_id)
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids)), "存在重复节点 id"
    known = set(ids)
    dangling = [
        edge for edge in edges if edge["source"] not in known or edge["target"] not in known
    ]
    assert dangling == [], f"悬空边: {dangling[:3]}"


def test_import_cycle_detected(client: TestClient, indexed) -> None:
    cycles = indexed["analysis"]["cycles"]  # type: ignore[index]
    flat = [set(cycle["files"]) for cycle in cycles]
    assert any({"pyapp/cycle_a.py", "pyapp/cycle_b.py"} <= files for files in flat), (
        f"未发现 cycle_a/cycle_b 的 import 环: {cycles}"
    )


def test_communities_assigned(client: TestClient, indexed) -> None:
    communities = indexed["analysis"]["communities"]  # type: ignore[index]
    assert communities, "没有社区"
    assert all(c["namedBy"] == "heuristic" for c in communities)
    assert all(c["name"] for c in communities)
    assert all(c["size"] >= 1 for c in communities)


def test_tests_edge_exists(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    edges = _edges(client, repo_id)
    assert any(e["relation"] == "tests" and e["source"].endswith("test_core.py") for e in edges), (
        "测试文件没有 tests 边"
    )


# ── 查询接口 ────────────────────────────────────────────────────────
def test_tree_payload(client: TestClient, indexed) -> None:
    body = client.get(f"/api/repos/{indexed['repoId']}/tree", params={"depth": 3}).json()
    assert body["totalFiles"] >= 11
    assert body["totalLoc"] > 0
    names = {child["name"] for child in body["node"]["children"]}
    assert {"pyapp", "web", "README.md"} <= names
    pyapp = next(c for c in body["node"]["children"] if c["name"] == "pyapp")
    assert pyapp["type"] == "dir"
    assert pyapp["files"] >= 6
    assert pyapp["symbols"] >= 5
    assert pyapp["loc"] > 0


def test_search_endpoint(client: TestClient, indexed) -> None:
    body = client.get(f"/api/repos/{indexed['repoId']}/search", params={"q": "Engine"}).json()
    assert body["total"] >= 1
    assert all("Engine" in r["qualified"] or "Engine" in r["name"] for r in body["results"])
    assert any(r["kind"] == "class" for r in body["results"])


def test_symbol_payload(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    engine = _find(_nodes(client, repo_id), "Engine")
    body = client.get(f"/api/repos/{repo_id}/symbol", params={"nodeId": engine["id"]}).json()
    assert body["node"]["name"] == "Engine"
    assert any(edge["relation"] == "defines" for edge in body["incoming"])
    assert any(edge["relation"] == "contains" for edge in body["outgoing"])
    assert body["community"] is not None
    assert body["definition"]["snippet"]


def test_neighbors_payload(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    step = _find(_nodes(client, repo_id), "Engine.step")
    body = client.get(
        f"/api/repos/{repo_id}/graph/neighbors",
        params={"nodeId": step["id"], "depth": 2, "direction": "out"},
    ).json()
    names = {node["name"] for node in body["nodes"]}
    assert "step" in names
    assert "render" in names, names
    assert all(node["hop"] >= 0 for node in body["nodes"])


def test_path_payload(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    nodes = _nodes(client, repo_id)
    run = _find(nodes, "Engine.run")
    fmt_fn = _find(nodes, "fmt")
    body = client.get(
        f"/api/repos/{repo_id}/graph/path",
        params={"from": run["id"], "to": fmt_fn["id"], "maxDepth": 6},
    ).json()
    assert body["found"] is True
    assert body["nodes"][0]["id"] == run["id"]
    assert body["nodes"][-1]["id"] == fmt_fn["id"]


def test_impact_payload(client: TestClient, indexed) -> None:
    """影响面：改 fmt 会波及 render / step / run，并列出相关文件。"""
    repo_id = str(indexed["repoId"])
    fmt_fn = _find(_nodes(client, repo_id), "fmt")
    body = client.get(
        f"/api/repos/{repo_id}/graph/impact", params={"nodeId": fmt_fn["id"], "depth": 3}
    ).json()
    names = {node["name"] for node in body["nodes"]}
    assert {"render", "step"} <= names, names
    files = {item["path"] for item in body["files"]}
    assert "pyapp/helpers.py" in files
    assert body["root"]["name"] == "fmt"


def test_graph_filters_and_truncation(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    only_calls = client.get(
        f"/api/repos/{repo_id}/graph", params={"relations": "calls", "limit": 5000}
    ).json()
    assert only_calls["edges"]
    assert {edge["relation"] for edge in only_calls["edges"]} == {"calls"}

    tiny = client.get(f"/api/repos/{repo_id}/graph", params={"limit": 5}).json()
    assert len(tiny["nodes"]) == 5
    assert tiny["truncated"] is True
    assert tiny["total"]["nodes"] > 5


def test_confidence_filter_hides_ambiguous(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    body = client.get(
        f"/api/repos/{repo_id}/graph",
        params={"confidence": "EXTRACTED,INFERRED", "limit": 5000},
    ).json()
    assert body["edges"]
    assert {edge["confidence"] for edge in body["edges"]} <= {"EXTRACTED", "INFERRED"}


def test_graph_endpoints_404(client: TestClient, indexed) -> None:
    repo_id = str(indexed["repoId"])
    assert client.get("/api/repos/nope/tree").status_code == 404
    assert client.get("/api/repos/nope/analysis").status_code == 404
    assert (
        client.get(
            f"/api/repos/{repo_id}/graph/neighbors", params={"nodeId": "missing"}
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/repos/{repo_id}/symbol", params={"nodeId": "missing"}).status_code == 404
    )
    assert (
        client.get(f"/api/repos/{repo_id}/tree", params={"path": "no/such/dir"}).status_code == 404
    )


def test_incremental_reindex_only_reparses_changed_files(
    client: TestClient, indexed, cogen_home, tmp_path
) -> None:
    """M5 验收：改一个文件后重索引，只重新解析那一个文件，其余复用抽取结果。"""
    import json

    from cogen.config import get_settings
    from cogen.graph.store import open_store

    repo_id = str(indexed["repoId"])
    settings = get_settings()
    with open_store(settings, repo_id) as store:
        cached_before = len(store.load_extractions())
    assert cached_before >= 10, "首次索引应当把抽取结果落库"

    target = FIXTURE / "pyapp" / "core.py"
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(original + "\n\n# 增量测试改动\n", encoding="utf-8")
        again = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
        from tests.conftest import wait_for_job

        job = wait_for_job(client, again["jobId"])
        assert job["state"] == "done", job
        assert "增量" in (job.get("message") or ""), job
        with open_store(settings, repo_id) as store:
            after = store.get_meta()["parse"]
            # 只解析改动过的那一个文件
            assert after["parsed"] == 1, json.dumps(after, ensure_ascii=False)
            assert after["nodes"] > 0
            # 图仍然是完整的（复用文件的边还在）
            edges = store.iter_edges(relations=["calls"])
            assert len(edges) >= 7
            assert store.graph_counts()["nodes"] > 30
            # 缓存数量不变（旧的没被清掉）
            assert len(store.load_extractions()) == cached_before
    finally:
        target.write_text(original, encoding="utf-8")
        # 恢复原始内容的索引状态，避免影响其它用例
        client.post("/api/repos", json={"target": str(FIXTURE)})


def test_incremental_reindex_after_delete_cleans_cache(
    client: TestClient, indexed, cogen_home
) -> None:
    """删掉文件后重索引：陈旧抽取缓存要被清理，图里也不能再出现它的边。"""
    from cogen.config import get_settings
    from cogen.graph.store import open_store

    repo_id = str(indexed["repoId"])
    settings = get_settings()
    extra = FIXTURE / "pyapp" / "cycle_b.py"
    original = extra.read_text(encoding="utf-8")
    try:
        extra.unlink()
        again = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
        from tests.conftest import wait_for_job

        wait_for_job(client, again["jobId"])
        with open_store(settings, repo_id) as store:
            cached = store.load_extractions()
            assert "pyapp/cycle_b.py" not in cached
            paths = {node["file"] for node in store.iter_nodes(kinds=["file"])}
            assert "pyapp/cycle_b.py" not in paths
            # 依赖它的文件仍然在（只是边变少了）
            assert "pyapp/cycle_a.py" in {
                node["qualified"] for node in store.iter_nodes(kinds=["file"])
            }
    finally:
        extra.write_text(original, encoding="utf-8")
        client.post("/api/repos", json={"target": str(FIXTURE)})
