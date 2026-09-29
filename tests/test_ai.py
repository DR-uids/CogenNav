"""M4 测试：LLM 客户端容错、社区命名降级与缓存、问答 tool-calling 循环。

不依赖真实 LLM：要么不配 Key（验证降级），要么 monkeypatch ``cogen.ai.llm`` 的两个出口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cogen.ai import ask as ask_module
from cogen.ai import llm, naming
from cogen.config import get_settings
from cogen.graph.store import open_store

FIXTURE = Path(__file__).parent / "fixtures" / "graph_repo"


@pytest.fixture
def indexed(client: TestClient, wait_job) -> dict[str, object]:
    created = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    return {"repoId": created["repoId"], "job": job}


# ── LLM 客户端 ──────────────────────────────────────────────────────
def test_parse_json_object_variants() -> None:
    assert llm.parse_json_object('{"name": "A", "summary": "s"}') == {
        "name": "A",
        "summary": "s",
    }
    assert llm.parse_json_object('```json\n{"name": "B"}\n```') == {"name": "B"}
    assert llm.parse_json_object('好的，结果如下：{"name": "C"} 完毕') == {"name": "C"}
    assert llm.parse_json_object("完全不是 JSON") == {}
    assert llm.parse_json_object("[1,2,3]") == {}


def test_describe_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-secret-value")
    described = llm.describe(settings)
    assert described["configured"] is True
    assert "sk-secret-value" not in json.dumps(described)


def test_scrub_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_redact", True)
    assert "sk-abcdef123456" not in llm.scrub(settings, "API_KEY=sk-abcdef123456")
    monkeypatch.setattr(settings, "llm_redact", False)
    assert "sk-abcdef123456" in llm.scrub(settings, "API_KEY=sk-abcdef123456")


# ── 未配置 LLM 时的降级 ─────────────────────────────────────────────
def test_ai_status_without_key(client: TestClient, indexed) -> None:
    body = client.get(f"/api/repos/{indexed['repoId']}/ai/status").json()
    assert body["configured"] is False
    assert "search_symbols" in body["tools"]
    assert body["rag"] == "graph-tools"


def test_ask_without_key_returns_error_event(client: TestClient, indexed) -> None:
    resp = client.post(
        f"/api/repos/{indexed['repoId']}/ask", json={"question": "谁调用了 render？"}
    )
    assert resp.status_code == 200
    text = resp.text
    assert "event: error" in text
    assert "未配置 LLM" in text


def test_summary_falls_back_to_heuristic(client: TestClient, indexed) -> None:
    body = client.get(f"/api/repos/{indexed['repoId']}/summary").json()
    assert body["generatedBy"] == "heuristic"
    assert "仓库：" in body["summary"]
    assert "主要社区" in body["summary"]


def test_name_communities_heuristic_and_cache(client: TestClient, indexed, cogen_home) -> None:
    repo_id = str(indexed["repoId"])
    body = client.post(f"/api/repos/{repo_id}/communities/name").json()
    assert body["llm"] is False
    assert body["updated"] >= 1
    assert all(item["namedBy"] == "heuristic" for item in body["communities"])
    assert all(item["name"] for item in body["communities"])

    settings = get_settings()
    with open_store(settings, repo_id) as store:
        cached = store.naming_cache()
        assert cached, "命名缓存没有写入"
        names = {row["name"] for row in store.community_rows()}
        assert names == {item["name"] for item in body["communities"]}

    # 重新索引后应当从缓存里恢复名字（社区表会被重写）
    again = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
    from tests.conftest import wait_for_job

    rerun = wait_for_job(client, again["jobId"])
    assert rerun["state"] == "done", rerun
    with open_store(settings, repo_id) as store:
        restored = {row["name"] for row in store.community_rows()}
    assert restored == names, "重索引后社区名没有复用缓存"


# ── 工具集 ──────────────────────────────────────────────────────────
def test_tools_are_read_only_and_working(client: TestClient, indexed, cogen_home) -> None:
    settings = get_settings()
    with open_store(settings, str(indexed["repoId"])) as store:
        assert ask_module.tool_names() == [
            "repo_overview",
            "search_symbols",
            "get_symbol",
            "neighbors",
            "impact",
            "path_between",
            "list_communities",
            "read_file",
        ]
        overview = ask_module.execute_tool(store, "repo_overview", {})
        assert overview["files"] >= 11
        search = ask_module.execute_tool(store, "search_symbols", {"query": "Engine"})
        assert search["total"] >= 1
        engine = next(r for r in search["results"] if r["kind"] == "class")
        symbol = ask_module.execute_tool(store, "get_symbol", {"nodeId": engine["id"]})
        assert symbol["node"]["name"] == "Engine"
        neighbors = ask_module.execute_tool(
            store, "neighbors", {"nodeId": engine["id"], "depth": 2}
        )
        assert neighbors["nodes"]
        impact = ask_module.execute_tool(store, "impact", {"nodeId": engine["id"]})
        assert "files" in impact
        read = ask_module.execute_tool(
            store,
            "read_file",
            {"path": "pyapp/core.py", "startLine": 1, "endLine": 5},
            root_path=str(FIXTURE),
        )
        assert "class Engine" in read["content"] or "render" in read["content"]
        assert ask_module.execute_tool(store, "read_file", {"path": ".env"})["error"]
        assert ask_module.execute_tool(store, "nope", {})["error"]


# ── 问答循环（mock LLM）─────────────────────────────────────────────
class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call_1"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


@pytest.mark.asyncio
async def test_answer_runs_tool_then_streams(
    client: TestClient, indexed, cogen_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    calls: list[list[dict[str, Any]]] = []

    def fake_complete(_settings, messages, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(messages))
        if len(calls) == 1:
            return _FakeResponse(
                _FakeMessage(
                    None,
                    [_FakeToolCall("search_symbols", json.dumps({"query": "Engine"}))],
                )
            )
        return _FakeResponse(_FakeMessage("已收集到信息", None))

    async def fake_stream(_settings, messages, **kwargs):  # type: ignore[no-untyped-def]
        for chunk in ("Engine ", "是核心类。"):
            yield chunk

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "stream_complete", fake_stream)

    events: list[dict[str, Any]] = []
    with open_store(settings, str(indexed["repoId"])) as store:
        meta = store.load_repo_meta()
        async for event in ask_module.answer(
            settings, store, "Engine 是什么？", root_path=meta.root_path if meta else None
        ):
            events.append(event)

    kinds = [event["type"] for event in events]
    assert kinds[0] == "tool" and events[0]["name"] == "search_symbols"
    assert "tool_result" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["answer"] == "Engine 是核心类。"
    assert events[-1]["citations"], "没有收集到引用节点"
    assert any(citation["name"] == "Engine" for citation in events[-1]["citations"])
    # 第二次调用（最终回答）必须已经带上工具结果
    assert any(message.get("role") == "tool" for message in calls[1])


@pytest.mark.asyncio
async def test_answer_reports_llm_failure(
    client: TestClient, indexed, cogen_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")

    def boom(*args: object, **kwargs: object) -> None:
        raise llm.LLMError("connection refused")

    monkeypatch.setattr(llm, "complete", boom)
    events = []
    with open_store(settings, str(indexed["repoId"])) as store:
        async for event in ask_module.answer(settings, store, "hi"):
            events.append(event)
    assert events[-1]["type"] == "error"
    assert "connection refused" in events[-1]["message"]


def test_naming_uses_llm_when_configured(
    client: TestClient, indexed, cogen_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    payloads = [
        {"name": "引擎核心", "summary": "负责编排"},
        {"name": "工具层", "summary": "格式化与渲染"},
    ]
    counter = {"n": 0}

    def fake_complete_json(_settings, messages, **kwargs):  # type: ignore[no-untyped-def]
        item = payloads[counter["n"] % len(payloads)]
        counter["n"] += 1
        return item

    monkeypatch.setattr(llm, "complete_json", fake_complete_json)
    with open_store(settings, str(indexed["repoId"])) as store:
        result = naming.name_communities(settings, store)
        assert result["llm"] is True
        assert all(item["namedBy"] == "llm" for item in result["communities"])
        assert {item["name"] for item in result["communities"]} <= {
            "引擎核心",
            "工具层",
        }
        # 已有 LLM 命名 + 指纹未变 → 再次调用不应再请求模型
        before = counter["n"]
        again = naming.name_communities(settings, store)
        assert again["llm"] is False
        assert counter["n"] == before
