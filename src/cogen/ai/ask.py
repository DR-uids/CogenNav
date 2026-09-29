"""图谱问答：把 ``cogen.graph.tools`` 的只读查询暴露成 LLM 工具，跑 tool-calling 循环。

- **只读**：工具集里没有任何执行/写文件的入口（计划 §9 的硬约束）；
- 工具调用阶段用非流式（便于拿到完整参数），**最终回答用流式**，因此 SSE 既有工具轨迹也有 token 流；
- 未配置 Key 时立刻返回明确事件，前端提示"未配置 LLM"，其余功能不受影响。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from ..config import Settings
from ..graph import tools
from ..graph.store import Store
from ..i18n import t
from . import llm

MAX_TOOL_ROUNDS = 4
MAX_TOOL_RESULT_CHARS = 8000
MAX_CITATIONS = 20
MAX_HISTORY_TURNS = 6


def system_prompt() -> str:
    """系统提示词：回答语言跟随当前界面语言（英文界面就该用英文回答）。"""
    return t("ask.systemPrompt")


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "repo_overview",
            "description": "仓库总览：文件数、行数、语言分布、节点边数、调用解析率、社区数量。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbols",
            "description": "按名字/限定名/路径子串搜索符号（类、函数、方法、变量等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "子串，例如 Engine 或 sessions.py"},
                    "kind": {"type": "string", "description": "可选：限定节点类型"},
                    "limit": {"type": "integer", "description": "默认 20"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_symbol",
            "description": "取某个符号的详情：定义位置、代码片段、所属社区、出入边（谁调用它 / 它调用了谁）。",
            "parameters": {
                "type": "object",
                "properties": {"nodeId": {"type": "string"}},
                "required": ["nodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "neighbors",
            "description": "取某个节点 N 度邻域（可指定方向与关系），用于看调用上下游或依赖关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodeId": {"type": "string"},
                    "depth": {"type": "integer", "description": "默认 2"},
                    "direction": {"type": "string", "enum": ["both", "out", "in"]},
                    "relations": {
                        "type": "string",
                        "description": "逗号分隔，如 calls,imports,extends",
                    },
                },
                "required": ["nodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "impact",
            "description": "影响面：谁依赖这个符号（入边闭包）+ 需要回归的文件清单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodeId": {"type": "string"},
                    "depth": {"type": "integer", "description": "默认 3"},
                },
                "required": ["nodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "path_between",
            "description": "两个符号之间的最短关系路径（用于解释两个模块是怎么连上的）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "maxDepth": {"type": "integer", "description": "默认 6"},
                },
                "required": ["from", "to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_communities",
            "description": "列出社区（模块聚类）、god nodes（关键节点）、import 环与孤儿文件。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取仓库内某个已索引文件的内容（可选行范围），用于解释具体实现。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "startLine": {"type": "integer", "description": "1 起，默认 1"},
                    "endLine": {"type": "integer", "description": "1 起，默认 startLine+80"},
                },
                "required": ["path"],
            },
        },
    },
]


def tool_names() -> list[str]:
    return [spec["function"]["name"] for spec in TOOL_SPECS]


def _compact(
    payload: dict[str, Any], *, node_limit: int = 40, edge_limit: int = 60
) -> dict[str, Any]:
    """裁剪工具返回值，避免把整张图塞进上下文。"""
    trimmed = dict(payload)
    if "nodes" in trimmed and isinstance(trimmed["nodes"], list):
        trimmed["nodes"] = trimmed["nodes"][:node_limit]
    if "edges" in trimmed and isinstance(trimmed["edges"], list):
        trimmed["edges"] = trimmed["edges"][:edge_limit]
    if "communities" in trimmed and isinstance(trimmed["communities"], list):
        trimmed["communities"] = [
            {k: v for k, v in community.items() if k != "topSymbols"}
            for community in trimmed["communities"][:15]
        ]
    return trimmed


def execute_tool(
    store: Store, name: str, arguments: dict[str, Any], *, root_path: str | None = None
) -> dict[str, Any]:
    """执行一个只读工具；任何异常都转成 ``{"error": ...}``，让模型自己纠偏。"""
    try:
        if name == "repo_overview":
            meta = store.load_repo_meta()
            counts = store.graph_counts()
            groups = store.group_counts()
            graph_meta = store.get_meta().get("graph") or {}
            return {
                "target": meta.target if meta else None,
                "files": meta.file_count if meta else 0,
                "loc": meta.loc if meta else 0,
                "languages": meta.languages if meta else {},
                "nodes": counts["nodes"],
                "edges": counts["edges"],
                "byKind": groups["byKind"],
                "resolvedCallRate": graph_meta.get("resolvedCallRate"),
            }
        if name == "search_symbols":
            return _compact(
                tools.search_payload(
                    store,
                    str(arguments.get("query", "")),
                    kind=arguments.get("kind"),
                    limit=int(arguments.get("limit", 20)),
                    root_path=root_path,
                )
            )
        if name == "get_symbol":
            return _compact(
                tools.symbol_payload(store, str(arguments["nodeId"]), root_path=root_path)
            )
        if name == "neighbors":
            return _compact(
                tools.neighbors_payload(
                    store,
                    str(arguments["nodeId"]),
                    depth=int(arguments.get("depth", 2)),
                    direction=str(arguments.get("direction", "both")),
                    relations=_split(arguments.get("relations")),
                )
            )
        if name == "impact":
            return _compact(
                tools.impact_payload(
                    store, str(arguments["nodeId"]), depth=int(arguments.get("depth", 3))
                ),
                node_limit=30,
            )
        if name == "path_between":
            return _compact(
                tools.path_payload(
                    store,
                    str(arguments["from"]),
                    str(arguments["to"]),
                    max_depth=int(arguments.get("maxDepth", 6)),
                )
            )
        if name == "list_communities":
            return _compact(tools.analysis_payload(store))
        if name == "read_file":
            return _read_file(store, arguments, root_path=root_path)
        return {"error": t("ask.unknownTool", name=name)}
    except KeyError as exc:
        return {"error": t("ask.objectNotFound", error=exc)}
    except Exception as exc:  # 工具内部错误不能让整轮对话崩掉
        return {"error": f"{type(exc).__name__}: {exc}"}


def _split(value: Any) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [item.strip() for item in str(value).split(",") if item.strip()] or None


def _read_file(store: Store, arguments: dict[str, Any], *, root_path: str | None) -> dict[str, Any]:
    from pathlib import Path

    rel = str(arguments.get("path", ""))
    record = store.get_file(rel)
    if record is None:
        return {"error": t("ask.fileNotIndexed", path=rel)}
    if not root_path:
        return {"error": t("ask.missingRoot")}
    start = max(1, int(arguments.get("startLine", 1)))
    end = int(arguments.get("endLine", start + 80))
    end = min(end, start + 200)
    path = Path(root_path) / rel
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"error": t("ask.readFailed", error=exc)}
    return {
        "path": rel,
        "language": record.language,
        "startLine": start,
        "endLine": min(end, len(lines)),
        "lines": len(lines),
        "content": "\n".join(lines[start - 1 : end]),
    }


def _collect_citations(payload: Any, found: dict[str, dict[str, Any]]) -> None:
    """从工具结果里挑出可点击的符号（保持出现顺序、去重、限量）。"""
    if len(found) >= MAX_CITATIONS:
        return
    if isinstance(payload, dict):
        node = payload.get("node")
        if (
            isinstance(node, dict)
            and node.get("id")
            and node.get("kind")
            not in (
                "repo",
                "dir",
                "community",
            )
        ):
            found.setdefault(
                node["id"],
                {
                    "id": node["id"],
                    "name": node.get("name"),
                    "kind": node.get("kind"),
                    "file": node.get("file"),
                },
            )
        for key in ("nodes", "results", "nodesByHop"):
            values = payload.get(key)
            if isinstance(values, list):
                for item in values:
                    if len(found) >= MAX_CITATIONS:
                        return
                    if (
                        isinstance(item, dict)
                        and item.get("id")
                        and item.get("kind")
                        not in (
                            "repo",
                            "dir",
                            "community",
                        )
                    ):
                        found.setdefault(
                            item["id"],
                            {
                                "id": item["id"],
                                "name": item.get("name"),
                                "kind": item.get("kind"),
                                "file": item.get("file"),
                            },
                        )
        for value in payload.values():
            _collect_citations(value, found)


async def answer(
    settings: Settings,
    store: Store,
    question: str,
    *,
    root_path: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """SSE 事件流：``tool`` / ``delta`` / ``done`` / ``error``。"""
    if not llm.is_configured(settings):
        yield {"type": "error", "message": t("ask.llmNotConfigured")}
        return

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt()}]
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    citations: dict[str, dict[str, Any]] = {}
    for round_index in range(MAX_TOOL_ROUNDS):
        try:
            response = await asyncio.to_thread(llm.complete, settings, messages, tools=TOOL_SPECS)
        except llm.LLMError as exc:
            yield {"type": "error", "message": t("ask.modelCallFailed", error=exc)}
            return
        except llm.LLMNotConfigured:
            yield {"type": "error", "message": t("ask.llmNotConfiguredShort")}
            return

        choices = getattr(response, "choices", None) or []
        if not choices:
            yield {"type": "error", "message": t("ask.emptyModelResult")}
            return
        message = choices[0].message
        calls = list(llm.iter_tool_calls(message))
        if not calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call["id"] or f"call_{round_index}",
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
                    }
                    for call in calls
                ],
            }
        )
        for call in calls:
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            yield {"type": "tool", "name": call["name"], "arguments": arguments}
            result = await asyncio.to_thread(
                execute_tool, store, call["name"], arguments, root_path=root_path
            )
            _collect_citations(result, citations)
            yield {
                "type": "tool_result",
                "name": call["name"],
                "summary": _summarize(name=call["name"], result=result),
            }
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{round_index}",
                    "content": json.dumps(result, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS],
                }
            )
    else:
        messages.append({"role": "user", "content": t("ask.forceConclusion")})

    text_parts: list[str] = []
    try:
        async for delta in llm.stream_complete(settings, messages):
            text_parts.append(delta)
            yield {"type": "delta", "text": delta}
    except llm.LLMError as exc:
        yield {"type": "error", "message": t("ask.generateFailed", error=exc)}
        return
    yield {
        "type": "done",
        "answer": "".join(text_parts),
        "citations": list(citations.values()),
    }


def _summarize(*, name: str, result: dict[str, Any]) -> str:
    """工具轨迹里的一行摘要（直接显示在 UI 上，因此跟着界面语言走）。"""
    if "error" in result:
        return f"{name}: {result['error']}"
    if "nodes" in result and isinstance(result["nodes"], list):
        return t(
            "ask.toolSummary.nodes",
            name=name,
            nodes=len(result["nodes"]),
            edges=len(result.get("edges") or []),
        )
    if "results" in result and isinstance(result["results"], list):
        return t("ask.toolSummary.matches", name=name, count=len(result["results"]))
    if "incoming" in result:
        return t(
            "ask.toolSummary.edges",
            name=name,
            incoming=len(result.get("incoming") or []),
            outgoing=len(result.get("outgoing") or []),
        )
    if "content" in result:
        return t(
            "ask.toolSummary.file",
            name=name,
            path=result.get("path"),
            start=result.get("startLine"),
            end=result.get("endLine"),
        )
    return t("ask.toolSummary.done", name=name)
