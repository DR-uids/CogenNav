"""M5 测试：graph.json / GRAPH_REPORT.md 导出与 MCP Server（只读工具集）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from cogen.cli import main
from cogen.config import get_settings
from cogen.export.json_export import export_json
from cogen.export.report import generate_report
from cogen.graph.store import db_path_for, open_store
from cogen.mcp.server import RepoSession, create_server, resolve_repo_id

FIXTURE = Path(__file__).parent / "fixtures" / "graph_repo"


@pytest.fixture
def indexed(client: TestClient, wait_job) -> dict[str, object]:
    created = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    return {"repoId": created["repoId"], "job": job}


# ── 导出 ────────────────────────────────────────────────────────────
def test_export_json_document(client: TestClient, indexed, cogen_home) -> None:
    settings = get_settings()
    with open_store(settings, str(indexed["repoId"])) as store:
        path = export_json(store, settings)
    assert path.exists()
    document = json.loads(path.read_text())
    assert document["version"] == 1
    assert document["generator"].startswith("cogen/")
    assert document["repo"]["repoId"] == indexed["repoId"]
    assert document["nodes"] and document["edges"]
    assert document["communities"]
    assert document["stats"]["nodes"] == len(document["nodes"])
    assert document["stats"]["parse"]["parsed"] >= 1
    assert document["stats"]["graph"]["callsTotal"] >= 1
    assert any(node["kind"] == "class" for node in document["nodes"])
    assert any(edge["relation"] == "calls" for edge in document["edges"])


def test_export_report_sections(client: TestClient, indexed, cogen_home) -> None:
    settings = get_settings()
    with open_store(settings, str(indexed["repoId"])) as store:
        path = generate_report(store, settings)
    text = path.read_text(encoding="utf-8")
    for heading in (
        "## 概览",
        "## 主要社区",
        "## 关键节点",
        "## 依赖环",
        "## 孤儿模块",
        "## 跨社区连接",
        "## 待办热点",
        "## 疑似未被调用",
        "## 复现方式",
    ):
        assert heading in text, f"缺少小节: {heading}"
    assert "pyapp" in text


def test_export_to_custom_path(client: TestClient, indexed, cogen_home, tmp_path: Path) -> None:
    settings = get_settings()
    target = tmp_path / "out" / "graph.json"
    with open_store(settings, str(indexed["repoId"])) as store:
        path = export_json(store, settings, output=target)
    assert path == target and target.exists()


def test_cli_export(client: TestClient, indexed, cogen_home) -> None:
    result = CliRunner().invoke(main, ["export", "--repo", str(indexed["repoId"]), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert Path(payload["graph"]).exists()
    assert Path(payload["report"]).exists()


def test_cli_export_unknown_repo(client: TestClient, indexed, cogen_home) -> None:
    result = CliRunner().invoke(main, ["export", "--repo", "nope"])
    assert result.exit_code == 2
    assert "不存在" in result.output


# ── MCP ─────────────────────────────────────────────────────────────
def test_resolve_repo_id(client: TestClient, indexed, cogen_home) -> None:
    settings = get_settings()
    assert resolve_repo_id(settings, str(indexed["repoId"])) == indexed["repoId"]
    # 不指定时取最近索引的仓库
    assert resolve_repo_id(settings, None) == indexed["repoId"]
    assert db_path_for(settings, str(indexed["repoId"])).exists()


def test_resolve_repo_id_without_repos(cogen_home) -> None:
    with pytest.raises(SystemExit) as exc:
        resolve_repo_id(get_settings(), None)
    assert "cogen index" in str(exc.value)


def test_repo_session_overview(client: TestClient, indexed, cogen_home) -> None:
    session = RepoSession(get_settings(), str(indexed["repoId"]))
    overview = session.overview()
    assert overview["files"] >= 11
    assert overview["nodes"] > 10
    assert overview["byKind"]["class"] >= 1
    assert session.root_path().endswith("graph_repo")


@pytest.mark.asyncio
async def test_mcp_client_tools_and_resources(client: TestClient, indexed, cogen_home) -> None:
    """真正走 MCP 协议（内存传输）调用工具与资源。"""
    from mcp import Client

    server = create_server(get_settings(), str(indexed["repoId"]))
    async with Client(server) as mcp_client:
        listed = await mcp_client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {
            "repo_overview",
            "search_symbols",
            "get_symbol",
            "neighbors",
            "callers",
            "callees",
            "impact",
            "path_between",
            "file_tree",
            "read_file",
            "get_cst",
            "list_communities",
            "module_dependencies",
        } <= names

        overview = await mcp_client.call_tool("repo_overview", {})
        assert overview.structured_content["files"] >= 11

        found = await mcp_client.call_tool("search_symbols", {"query": "Engine"})
        engine = next(
            item for item in found.structured_content["results"] if item["kind"] == "class"
        )

        symbol = await mcp_client.call_tool("get_symbol", {"node_id": engine["id"]})
        assert symbol.structured_content["node"]["name"] == "Engine"

        callers = await mcp_client.call_tool("callers", {"node_id": engine["id"]})
        assert "nodes" in callers.structured_content

        impact = await mcp_client.call_tool("impact", {"node_id": engine["id"], "depth": 3})
        assert "files" in impact.structured_content

        cst = await mcp_client.call_tool(
            "get_cst", {"path": "pyapp/core.py", "node_path": "", "depth": 2}
        )
        assert cst.structured_content["node"]["type"] == "module"

        missing = await mcp_client.call_tool("get_symbol", {"node_id": "nope"})
        assert "error" in missing.structured_content

        resources = await mcp_client.list_resources()
        uris = {str(resource.uri) for resource in resources.resources}
        assert "cogen://repo/overview" in uris
        assert "cogen://repo/analysis" in uris

        read = await mcp_client.read_resource("cogen://repo/overview")
        assert read


@pytest.mark.asyncio
async def test_mcp_tools_are_read_only(client: TestClient, indexed, cogen_home) -> None:
    """工具集里不允许出现执行/写文件的入口（安全基线）。"""
    server = create_server(get_settings(), str(indexed["repoId"]))
    listed = await server.list_tools()
    names = {tool.name for tool in listed}
    assert names.isdisjoint(
        {"run_command", "shell", "exec", "write_file", "delete_file", "run_tests", "install"}
    )


def test_cli_mcp_without_repos(cogen_home) -> None:
    result = CliRunner().invoke(main, ["mcp", "--stdio"])
    assert result.exit_code == 2
    assert "cogen index" in result.output
