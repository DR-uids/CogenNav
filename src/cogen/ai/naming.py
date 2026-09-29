"""社区命名与架构摘要。

两条路径：
- **确定性启发式**（默认、永不失败）：主目录 + 度数最高的符号，配合社区规模/内聚度；
- **LLM 命名**（配置了 Key 时）：把社区内 top 文件与符号喂给模型，产出中文名与一句话摘要，
  按 ``content_hash`` 缓存——代码没变就不重复花钱。

任何 LLM 失败都回落到启发式，绝不把功能变成不可用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..graph.store import Store
from . import llm

#: 一次喂给模型的符号/文件上限（控制成本）
MAX_SYMBOLS_PER_PROMPT = 12
MAX_FILES_PER_PROMPT = 8

SYSTEM_PROMPT = (
    "你是代码架构分析助手。用户会给出一个代码社区的统计（目录、主要符号、文件），"
    "你要用简体中文给出简短命名与一句话职责描述。"
    '只输出 JSON，形如 {"name": "...", "summary": "..."}；'
    "name 不超过 12 个字，summary 不超过 60 个字，不要臆造不存在的模块。"
)


@dataclass
class CommunityNaming:
    community_id: int
    name: str
    summary: str | None
    named_by: str
    content_hash: str


def heuristic_name(directory: str | None, highlight: str | None, size: int) -> str:
    """确定性命名：``目录 · 代表符号``；没有文件时用规模兜底。"""
    if directory and highlight:
        return f"{directory} · {highlight}"
    if highlight:
        return highlight
    return f"{size} 个符号"


def _community_snapshot(store: Store, community_id: int) -> dict[str, Any]:
    members = store.iter_nodes(communities=[community_id], limit=MAX_SYMBOLS_PER_PROMPT * 3)
    files: Counter[str] = Counter()
    for member in members:
        if member.get("file"):
            files[member["file"]] += 1
    top_symbols = [
        {"name": member["name"], "kind": member["kind"], "file": member.get("file")}
        for member in members[:MAX_SYMBOLS_PER_PROMPT]
    ]
    directories: Counter[str] = Counter()
    for path in files:
        directories[path.split("/")[0] if "/" in path else "(根目录)"] += 1
    return {
        "topSymbols": top_symbols,
        "topFiles": [path for path, _ in files.most_common(MAX_FILES_PER_PROMPT)],
        "topDirectory": directories.most_common(1)[0][0] if directories else None,
        "size": len(members),
    }


def build_prompt(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    lines = [
        f"社区规模：{snapshot['size']} 个符号",
        f"主要目录：{snapshot['topDirectory'] or '(未知)'}",
        "主要文件：",
        *[f"- {path}" for path in snapshot["topFiles"]],
        "主要符号：",
        *[
            f"- {symbol['kind']} {symbol['name']}（{symbol['file'] or '未知文件'}）"
            for symbol in snapshot["topSymbols"]
        ],
    ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def name_communities(
    settings: Settings,
    store: Store,
    *,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """为社区生成名称与摘要；返回统计（改名数量、是否用了 LLM、错误列表）。"""
    rows = store.community_rows()
    results: list[CommunityNaming] = []
    used_llm = False
    errors: list[str] = []

    for row in rows[: limit or len(rows)]:
        community_id = int(row["id"])
        snapshot = _community_snapshot(store, community_id)
        heuristic = heuristic_name(
            snapshot["topDirectory"],
            snapshot["topSymbols"][0]["name"] if snapshot["topSymbols"] else None,
            snapshot["size"],
        )
        cached_name = row.get("name")
        # 内容指纹没变、且已经是 LLM 命名过（内容在 naming_cache 里），直接复用不再花钱
        if not force and row.get("namedBy") == "llm" and cached_name:
            results.append(
                CommunityNaming(
                    community_id,
                    str(cached_name),
                    row.get("summary"),
                    "llm",
                    str(row.get("contentHash") or ""),
                )
            )
            continue

        name, summary, named_by = heuristic, None, "heuristic"
        if llm.is_configured(settings):
            try:
                data = llm.complete_json(settings, build_prompt(snapshot))
                candidate = str(data.get("name") or "").strip()
                if candidate:
                    name = candidate[:40]
                    summary = str(data.get("summary") or "").strip() or None
                    named_by = "llm"
                    used_llm = True
            except llm.LLMError as exc:
                errors.append(f"社区 {community_id}: {exc}")
            except llm.LLMNotConfigured:
                pass

        results.append(
            CommunityNaming(
                community_id,
                name,
                summary,
                named_by,
                str(row.get("contentHash") or ""),
            )
        )

    if results:
        store.update_community_names(
            [
                {
                    "id": item.community_id,
                    "name": item.name,
                    "summary": item.summary,
                    "named_by": item.named_by,
                }
                for item in results
            ]
        )
    return {
        "updated": len(results),
        "llm": used_llm,
        "errors": errors[:5],
        "communities": [
            {
                "id": item.community_id,
                "name": item.name,
                "summary": item.summary,
                "namedBy": item.named_by,
            }
            for item in results
        ],
    }


def architecture_summary(settings: Settings, store: Store) -> dict[str, Any]:
    """整仓架构摘要（LLM）；未配置或失败时返回启发式要点。"""
    meta = store.load_repo_meta()
    analysis = store.get_meta().get("analysis") or {}
    communities = store.community_rows()[:12]
    lines = [
        f"仓库：{meta.target if meta else '未知'}",
        f"文件 {meta.file_count if meta else 0} 个 / {meta.loc if meta else 0} 行",
        "主要社区：",
        *[f"- {row['name']}（{row['size']} 个符号）" for row in communities],
        "关键节点：",
        *[
            f"- {node['kind']} {node['name']}（度数 {node['degree']}）"
            for node in (analysis.get("godNodes") or [])[:8]
        ],
    ]
    if analysis.get("cycles"):
        lines.append("存在 import 环：" + "、".join(str(c["size"]) for c in analysis["cycles"][:3]))

    fallback = "\n".join(lines)
    if not llm.is_configured(settings):
        return {"summary": fallback, "generatedBy": "heuristic"}
    try:
        text = llm.complete_text(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "你是代码架构分析助手。根据给定的社区与关键节点统计，"
                        "用简体中文输出 3-5 条要点（每条一行，以 - 开头），"
                        "说明这个仓库的模块划分、核心抽象与值得注意的耦合。不要臆造。"
                    ),
                },
                {"role": "user", "content": fallback},
            ],
            temperature=0.2,
        )
    except (llm.LLMError, llm.LLMNotConfigured) as exc:
        return {"summary": fallback, "generatedBy": "heuristic", "error": str(exc)}
    return {"summary": text or fallback, "generatedBy": "llm"}
