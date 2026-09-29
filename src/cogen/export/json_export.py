"""导出 graph.json：可移植、可 diff、可被别的工具消费。

结构见计划 §6.1；``version`` 用于后续兼容判断。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from .. import __version__
from ..config import Settings
from ..graph import tools
from ..graph.schema import GRAPH_SCHEMA_VERSION
from ..graph.store import Store


def build_document(store: Store, *, generator: str | None = None) -> dict[str, Any]:
    meta = store.load_repo_meta()
    stored = store.get_meta()
    analysis = tools.analysis_payload(store)
    nodes = store.iter_nodes(order_by_degree=False)
    edges = store.iter_edges(limit=None)
    return {
        "version": GRAPH_SCHEMA_VERSION,
        "generator": generator or f"cogen/{__version__}",
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": meta.to_api() if meta else None,
        "stats": {
            # 顶层 nodes/edges 是**图谱**规模；原始阶段统计放在 graph/parse 里，避免键名打架
            "nodes": len(nodes),
            "edges": len(edges),
            "communities": len(analysis["communities"]),
            "importCycles": len(analysis["cycles"]),
            "orphanFiles": len(analysis["orphans"]),
            "graph": stored.get("graph") or {},
            "parse": stored.get("parse") or {},
        },
        "communities": analysis["communities"],
        "godNodes": analysis["godNodes"],
        "cycles": analysis["cycles"],
        "orphans": analysis["orphans"],
        "nodes": nodes,
        "edges": edges,
    }


def export_json(
    store: Store,
    settings: Settings,
    *,
    output: Path | None = None,
    repo_id: str | None = None,
) -> Path:
    """把图谱写成 ``graph.json``；默认落在 ``.cogen/exports/<repoId>/graph.json``。"""
    meta = store.load_repo_meta()
    identifier = repo_id or (meta.repo_id if meta else "repo")
    target = output or (settings.exports_dir / identifier / "graph.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    document = build_document(store)
    target.write_bytes(orjson.dumps(document, option=orjson.OPT_INDENT_2))
    return target
