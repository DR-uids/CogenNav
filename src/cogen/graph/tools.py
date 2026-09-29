"""图谱查询工具：Web API、AI 问答与 MCP **共用**的唯一实现（全部只读）。

计划 §4 的分层原则：任何界面都不得各写一套检索逻辑，因此这里只依赖 ``Store``
（SQLite）并返回可 JSON 序列化的 dict。写操作一律不在这里提供。
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from .store import Store

#: 默认参与"影响面"的关系：谁依赖我（入边）
IMPACT_RELATIONS = ("calls", "instantiates", "extends", "implements", "imports", "type_uses")

#: 只作为"结构"存在、不参与遍历的 kind
STRUCTURAL_KINDS = ("repo", "dir")

_MAX_SNIPPET_CHARS = 240


def _node_brief(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "kind": node["kind"],
        "name": node["name"],
        "qualified": node["qualified"],
        "file": node.get("file"),
        "language": node.get("language"),
        "community": node.get("community"),
        "degree": node.get("degree", 0),
        "inDegree": node.get("inDegree", 0),
        "outDegree": node.get("outDegree", 0),
        "start": node.get("start"),
    }


def _edge_brief(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": edge["source"],
        "target": edge["target"],
        "relation": edge["relation"],
        "confidence": edge["confidence"],
        "file": edge.get("file"),
        "start": edge.get("start"),
    }


class GraphView:
    """一次请求内的邻接视图（避免每跳重复查库）。"""

    def __init__(self, store: Store, relations: list[str] | None = None) -> None:
        self.edges = store.iter_edges(relations=relations or None)
        self.out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.inc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            self.out[edge["source"]].append(edge)
            self.inc[edge["target"]].append(edge)

    def neighbours(self, node_id: str, direction: str) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        if direction in ("out", "both"):
            found.extend((edge["target"], edge) for edge in self.out.get(node_id, []))
        if direction in ("in", "both"):
            found.extend((edge["source"], edge) for edge in self.inc.get(node_id, []))
        return found


def bfs(
    view: GraphView,
    start: str,
    *,
    depth: int,
    direction: str = "both",
    limit: int = 2000,
) -> tuple[list[str], set[tuple[str, str]], dict[str, int], bool]:
    """广度优先遍历；返回 (节点顺序, 边集合, 跳数表, 是否被 limit 截断)。"""
    order = [start]
    hops = {start: 0}
    edges: set[tuple[str, str]] = set()
    queue: deque[str] = deque([start])
    truncated = False
    while queue:
        current = queue.popleft()
        current_hop = hops[current]
        if current_hop >= depth:
            continue
        for neighbour, edge in view.neighbours(current, direction):
            edges.add((edge["source"], edge["target"]))
            if neighbour not in hops:
                if len(hops) >= limit:
                    truncated = True
                    continue
                hops[neighbour] = current_hop + 1
                order.append(neighbour)
                queue.append(neighbour)
    return order, edges, hops, truncated


def _payload_from_ids(
    store: Store,
    node_ids: list[str],
    view: GraphView,
    *,
    hops: dict[str, int] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    selected = set(node_ids)
    nodes = []
    for node in store.nodes_by_ids(node_ids):
        brief = _node_brief(node)
        if hops is not None:
            brief["hop"] = hops.get(node["id"], 0)
        nodes.append(brief)
    by_id = {node["id"]: node for node in nodes}
    edges = [
        _edge_brief(edge)
        for edge in view.edges
        if edge["source"] in selected and edge["target"] in selected
    ]
    return {
        "nodes": [by_id[node_id] for node_id in node_ids if node_id in by_id],
        "edges": edges,
        "total": {"nodes": len(nodes), "edges": len(edges)},
        "truncated": truncated,
    }


def graph_payload(
    store: Store,
    *,
    kinds: list[str] | None = None,
    relations: list[str] | None = None,
    confidences: list[str] | None = None,
    community: int | None = None,
    limit: int = 1500,
    focus: str | None = None,
    depth: int = 2,
) -> dict[str, Any]:
    """全图（按度数取 top-N）或某个焦点的 N 度邻域。"""
    view = GraphView(store, relations)

    if focus:
        if store.get_node(focus) is None:
            raise KeyError(focus)
        node_ids, _edges, hops, truncated = bfs(
            view, focus, depth=max(1, depth), direction="both", limit=max(limit, 200)
        )
        if kinds or community is not None or confidences:
            allowed = {
                node["id"]
                for node in store.nodes_by_ids(node_ids)
                if _matches(node, kinds, community)
            }
            node_ids = [node_id for node_id in node_ids if node_id in allowed]
        payload = _payload_from_ids(store, node_ids[:limit], view, hops=hops, truncated=truncated)
        payload["total"] = {"nodes": len(node_ids), "edges": len(payload["edges"])}
        payload["truncated"] = truncated or len(node_ids) > limit
        return payload

    all_nodes = store.iter_nodes(kinds=kinds or None)  # 已按度数降序
    if community is not None:
        all_nodes = [node for node in all_nodes if node.get("community") == community]
    total_nodes = len(all_nodes)
    selected = [node["id"] for node in all_nodes[:limit]]
    payload = _payload_from_ids(store, selected, view, truncated=total_nodes > limit)
    payload["total"] = {"nodes": total_nodes, "edges": len(payload["edges"])}
    if confidences:
        allowed_conf = set(confidences)
        payload["edges"] = [edge for edge in payload["edges"] if edge["confidence"] in allowed_conf]
    return payload


def _matches(node: dict[str, Any], kinds: list[str] | None, community: int | None) -> bool:
    if kinds and node["kind"] not in kinds:
        return False
    return community is None or node.get("community") == community


def neighbors_payload(
    store: Store,
    node_id: str,
    *,
    depth: int = 2,
    direction: str = "both",
    relations: list[str] | None = None,
    limit: int = 400,
) -> dict[str, Any]:
    if store.get_node(node_id) is None:
        raise KeyError(node_id)
    view = GraphView(store, relations)
    order, _edges, hops, truncated = bfs(
        view, node_id, depth=max(1, depth), direction=direction, limit=limit
    )
    payload = _payload_from_ids(store, order, view, hops=hops, truncated=truncated)
    payload["total"] = {"nodes": len(order), "edges": len(payload["edges"])}
    return payload


def path_payload(store: Store, source: str, target: str, *, max_depth: int = 6) -> dict[str, Any]:
    if store.get_node(source) is None:
        raise KeyError(source)
    if store.get_node(target) is None:
        raise KeyError(target)
    view = GraphView(store)
    previous: dict[str, str] = {}
    seen = {source}
    queue: deque[tuple[str, int]] = deque([(source, 0)])
    found = source == target
    while queue and not found:
        current, distance = queue.popleft()
        if distance >= max_depth:
            continue
        for neighbour, _edge in view.neighbours(current, "both"):
            if neighbour in seen:
                continue
            seen.add(neighbour)
            previous[neighbour] = current
            if neighbour == target:
                found = True
                break
            queue.append((neighbour, distance + 1))

    if not found:
        return {"found": False, "nodes": [], "edges": [], "total": {"nodes": 0, "edges": 0}}

    chain = [target]
    while chain[-1] != source:
        chain.append(previous[chain[-1]])
    chain.reverse()
    payload = _payload_from_ids(store, chain, view)
    payload["found"] = True
    return payload


def impact_payload(
    store: Store,
    node_id: str,
    *,
    depth: int = 3,
    relations: list[str] | None = None,
) -> dict[str, Any]:
    """影响面 = **谁依赖我**（入边闭包）+ 需要回归的文件清单。"""
    root = store.get_node(node_id)
    if root is None:
        raise KeyError(node_id)
    view = GraphView(store, relations or list(IMPACT_RELATIONS))
    order, _edges, hops, truncated = bfs(
        view, node_id, depth=max(1, depth), direction="in", limit=3000
    )
    payload = _payload_from_ids(store, order, view, hops=hops, truncated=truncated)
    files = Counter(
        node["file"] for node in payload["nodes"] if node.get("file") and node["id"] != node_id
    )
    payload["root"] = _node_brief(root)
    payload["files"] = [{"path": path, "count": count} for path, count in files.most_common(200)]
    payload["total"] = {"nodes": len(order), "edges": len(payload["edges"])}
    return payload


def symbol_payload(store: Store, node_id: str, *, root_path: str | None = None) -> dict[str, Any]:
    node = store.get_node(node_id)
    if node is None:
        raise KeyError(node_id)
    edges = store.edges_for_node(node_id, limit=500)
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    neighbour_ids = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    names = {
        other["id"]: other["name"]
        for other in store.nodes_by_ids(sorted(neighbour_ids - {node_id}))
    }
    for edge in edges:
        if edge["target"] == node_id:
            incoming.append(
                {
                    "source": edge["source"],
                    "sourceName": names.get(edge["source"], edge["source"]),
                    "relation": edge["relation"],
                    "confidence": edge["confidence"],
                    "file": edge.get("file"),
                    "start": edge.get("start"),
                }
            )
        if edge["source"] == node_id:
            outgoing.append(
                {
                    "target": edge["target"],
                    "targetName": names.get(edge["target"], edge["target"]),
                    "relation": edge["relation"],
                    "confidence": edge["confidence"],
                    "file": edge.get("file"),
                    "start": edge.get("start"),
                }
            )

    community = None
    if node.get("community") is not None:
        for row in store.community_rows():
            if row["id"] == node["community"]:
                community = {"id": row["id"], "name": row["name"]}
                break

    definition = None
    if node.get("file") and root_path:
        start = node.get("start") or [0, 0]
        definition = {
            "file": node["file"],
            "start": start,
            "end": node.get("end") or start,
            "snippet": _file_snippet(root_path, node["file"], start[0], radius=3),
        }
    return {
        "node": _node_brief(node),
        "incoming": incoming,
        "outgoing": outgoing,
        "community": community,
        "definition": definition,
    }


class _TreeNode:
    """目录树聚合节点（构建期使用）。"""

    __slots__ = ("children", "errors", "file", "files", "languages", "loc", "symbols")

    def __init__(self) -> None:
        self.loc = 0
        self.files = 0
        self.symbols = 0
        self.errors = 0
        self.languages: Counter[str] = Counter()
        self.children: dict[str, _TreeNode] = {}
        self.file: dict[str, Any] | None = None


def tree_payload(store: Store, *, path: str = "", depth: int = 3) -> dict[str, Any]:
    """目录树 + 聚合度量（loc / 文件数 / 符号数 / 语言 / 语法错误数）。"""
    symbol_counts = store.symbol_counts_by_file()
    files = list(store.iter_files())

    root = _TreeNode()
    for record in files:
        parts = PurePosixPath(record.path).parts
        directory = root
        for part in parts[:-1]:
            directory = directory.children.setdefault(part, _TreeNode())
        leaf = _TreeNode()
        leaf.file = {
            "name": parts[-1],
            "path": record.path,
            "type": "file",
            "loc": record.loc,
            "files": 1,
            "symbols": symbol_counts.get(record.path, 0),
            "language": record.language,
            "errorCount": record.error_count + record.missing_count,
            "children": [],
        }
        directory.children[parts[-1]] = leaf

    def aggregate(node: _TreeNode) -> None:
        for child in node.children.values():
            aggregate(child)
            node.loc += child.loc
            node.files += child.files
            node.symbols += child.symbols
            node.errors += child.errors
            node.languages.update(child.languages)
        if node.file is not None:
            node.loc += int(node.file["loc"])
            node.files += 1
            node.symbols += int(node.file["symbols"])
            node.errors += int(node.file["errorCount"])
            if node.file["language"]:
                node.languages[str(node.file["language"])] += 1

    aggregate(root)

    def serialize(node: _TreeNode, name: str, current: str, level: int) -> dict[str, Any]:
        is_file = node.file is not None
        payload: dict[str, Any] = {
            "name": name or "(根目录)",
            "path": current,
            "type": "file" if is_file else "dir",
            "loc": node.loc,
            "files": node.files,
            "symbols": node.symbols,
            "language": (node.file or {}).get("language"),
            "errorCount": node.errors,
            "children": [],
        }
        if level >= depth or is_file:
            return payload
        payload["children"] = [
            serialize(child, child_name, f"{current}/{child_name}".lstrip("/"), level + 1)
            for child_name, child in sorted(node.children.items())
        ]
        return payload

    # 支持从任意子目录开始取
    current_dir: _TreeNode = root
    start_name = ""
    start_path = ""
    if path:
        for part in PurePosixPath(path).parts:
            if part not in current_dir.children:
                raise KeyError(path)
            current_dir = current_dir.children[part]
            start_name = part
            start_path = f"{start_path}/{part}".lstrip("/")
    return {
        "path": start_path,
        "node": serialize(current_dir, start_name, start_path, 0),
        "totalLoc": current_dir.loc,
        "totalFiles": current_dir.files,
    }


def search_payload(
    store: Store,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 50,
    root_path: str | None = None,
) -> dict[str, Any]:
    nodes = store.search_nodes(query, kind=kind, limit=limit)
    results = []
    for node in nodes:
        snippet = None
        if root_path and node.get("file"):
            start = node.get("start") or [0, 0]
            snippet = _file_snippet(root_path, node["file"], start[0], radius=0)
        results.append(
            {
                "id": node["id"],
                "kind": node["kind"],
                "name": node["name"],
                "qualified": node["qualified"],
                "file": node.get("file"),
                "start": node.get("start"),
                "snippet": snippet,
            }
        )
    return {"results": results, "total": len(results)}


def analysis_payload(store: Store) -> dict[str, Any]:
    meta = store.get_meta()
    graph_stats = meta.get("graph") or {}
    analysis = meta.get("analysis") or {}
    counts = store.graph_counts()
    groups = store.group_counts()
    communities = []
    for row in store.community_rows():
        members = store.iter_nodes(communities=[int(row["id"])], limit=5)
        communities.append(
            {
                **row,
                "topSymbols": [
                    {
                        "id": member["id"],
                        "name": member["name"],
                        "kind": member["kind"],
                        "file": member.get("file"),
                    }
                    for member in members
                ],
            }
        )
    return {
        "communities": communities,
        "godNodes": analysis.get("godNodes") or [],
        "cycles": analysis.get("cycles") or [],
        "orphans": analysis.get("orphans") or [],
        "crossCommunity": analysis.get("crossCommunity") or [],
        "stats": {
            "nodes": counts["nodes"],
            "edges": counts["edges"],
            "byKind": groups["byKind"],
            "byRelation": groups["byRelation"],
            "byConfidence": groups["byConfidence"],
            "resolvedCallRate": graph_stats.get("resolvedCallRate"),
            "callsTotal": graph_stats.get("callsTotal"),
            "callsExtracted": graph_stats.get("callsExtracted"),
            "callsInferred": graph_stats.get("callsInferred"),
            "callsAmbiguous": graph_stats.get("callsAmbiguous"),
            "callsUnresolved": graph_stats.get("callsUnresolved"),
            "communities": len(communities),
            "importCycles": len(analysis.get("cycles") or []),
            "orphanFiles": len(analysis.get("orphans") or []),
        },
    }


# ── 源码片段（带缓存，避免为每个搜索结果重读文件）────────────────────


@lru_cache(maxsize=512)
def _read_lines_cached(path: str, mtime_ns: int) -> tuple[str, ...]:
    del mtime_ns
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(text.splitlines())


def _file_snippet(root_path: str, rel_path: str, row: int, *, radius: int = 0) -> str | None:
    absolute = Path(root_path) / rel_path
    try:
        stat = absolute.stat()
    except OSError:
        return None
    lines = _read_lines_cached(str(absolute), stat.st_mtime_ns)
    if not lines:
        return None
    start = max(0, row - radius)
    end = min(len(lines), row + radius + 1)
    text = "\n".join(lines[start:end]).strip()
    return text[:_MAX_SNIPPET_CHARS] or None
