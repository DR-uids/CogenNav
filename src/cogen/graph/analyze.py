"""图分析：Louvain 社区、import 环、god nodes、孤儿模块与统计。

社区发现跑在**符号子图**上（class/function/method/...），把 ``contains``（类→方法）
与 ``calls/extends/implements/instantiates`` 都算作联系；孤立符号不参与，避免 Louvain
把它们塞进同一个"垃圾桶社区"。随机种子固定，保证同一份代码多次分析结果一致。
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from .schema import EdgeRec, NodeRec

#: 参与社区发现的边关系与权重
_COMMUNITY_RELATIONS: dict[str, float] = {
    "calls": 2.0,
    "extends": 2.0,
    "implements": 2.0,
    "instantiates": 1.5,
    "type_uses": 1.0,
    "contains": 1.0,
}

#: 不算"概念节点"的结构性 kind（社区与 god nodes 都排除）
_STRUCTURAL_KINDS = {"repo", "dir", "file", "external", "community"}

#: 小于该规模的社区会被并入邻居（否则小仓库会出现上百个碎片社区）
MIN_COMMUNITY_SIZE = 5

_GOD_NODE_LIMIT = 20
_CROSS_COMMUNITY_LIMIT = 20
_ORPHAN_FILE_LIMIT = 50


@dataclass
class CommunityInfo:
    id: int
    size: int
    cohesion: float
    name: str
    named_by: str
    top_symbols: list[dict[str, Any]] = field(default_factory=list)
    #: 社区内容指纹（M4 的 LLM 命名按它缓存）
    content_hash: str = ""

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "cohesion": self.cohesion,
            "namedBy": self.named_by,
            "topSymbols": self.top_symbols,
        }


@dataclass
class AnalysisResult:
    communities: list[CommunityInfo] = field(default_factory=list)
    node_community: dict[str, int] = field(default_factory=dict)
    god_nodes: list[dict[str, Any]] = field(default_factory=list)
    cycles: list[dict[str, Any]] = field(default_factory=list)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    cross_community: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def analyze_graph(
    nodes: list[NodeRec],
    edges: list[EdgeRec],
    *,
    resolved_call_rate: float | None = None,
) -> AnalysisResult:
    result = AnalysisResult()
    node_by_id = {node.id: node for node in nodes}

    # ── 度数（全部节点、按有向边计）─────────────────────────────────
    in_degree: Counter[str] = Counter()
    out_degree: Counter[str] = Counter()
    for edge in edges:
        out_degree[edge.src] += 1
        in_degree[edge.dst] += 1

    # ── 符号子图 → Louvain 社区 ─────────────────────────────────────
    graph = nx.Graph()
    for node in nodes:
        if node.kind in _STRUCTURAL_KINDS:
            continue
        graph.add_node(node.id)
    for edge in edges:
        weight = _COMMUNITY_RELATIONS.get(edge.relation)
        if weight is None:
            continue
        if edge.src in graph and edge.dst in graph and edge.src != edge.dst:
            graph.add_edge(edge.src, edge.dst, weight=weight)

    active = graph.subgraph([n for n, d in graph.degree() if d > 0]).copy()
    communities: list[list[str]] = []
    if active.number_of_nodes() > 0:
        # resolution < 1：倾向更大的社区，避免小仓库被切成一堆碎片
        communities = [
            sorted(c) for c in nx.community.louvain_communities(active, seed=42, resolution=0.8)
        ]
        communities = _merge_small_communities(active, communities)

    for index, members in enumerate(sorted(communities, key=len, reverse=True)):
        for member in members:
            result.node_community[member] = index
        cohesion = _cohesion(active, members)
        top = sorted(
            members,
            key=lambda nid: -(out_degree[nid] + in_degree[nid]),
        )[:5]
        digest = hashlib.sha1("|".join(members).encode()).hexdigest()[:16]
        highlight = node_by_id[top[0]].name if top and top[0] in node_by_id else None
        result.communities.append(
            CommunityInfo(
                id=index,
                size=len(members),
                cohesion=cohesion,
                name=_community_name(
                    [node_by_id[nid] for nid in members if nid in node_by_id], highlight=highlight
                ),
                named_by="heuristic",
                content_hash=digest,
                top_symbols=[
                    {
                        "id": nid,
                        "name": node_by_id[nid].name,
                        "kind": node_by_id[nid].kind,
                        "file": node_by_id[nid].file,
                    }
                    for nid in top
                    if nid in node_by_id
                ],
            )
        )

    # ── god nodes ───────────────────────────────────────────────────
    symbol_nodes = [n for n in nodes if n.kind not in _STRUCTURAL_KINDS and n.kind != "community"]
    ranked = sorted(
        symbol_nodes,
        key=lambda n: -(in_degree[n.id] + out_degree[n.id]),
    )
    result.god_nodes = [
        {
            "id": node.id,
            "name": node.name,
            "kind": node.kind,
            "file": node.file,
            "degree": in_degree[node.id] + out_degree[node.id],
            "inDegree": in_degree[node.id],
            "outDegree": out_degree[node.id],
        }
        for node in ranked[:_GOD_NODE_LIMIT]
        if in_degree[node.id] + out_degree[node.id] > 0
    ]

    # ── import 环（文件级强连通分量）────────────────────────────────
    import_graph = nx.DiGraph()
    file_paths = {node.id[len("file:") :] for node in nodes if node.kind == "file"}
    for path in file_paths:
        import_graph.add_node(path)
    for edge in edges:
        if edge.relation != "imports":
            continue
        src_file = _file_of(edge.src, node_by_id)
        dst_file = _file_of(edge.dst, node_by_id)
        if src_file and dst_file and src_file != dst_file:
            import_graph.add_edge(src_file, dst_file)
    cycles = [
        {"files": sorted(component), "size": len(component)}
        for component in nx.strongly_connected_components(import_graph)
        if len(component) > 1
    ]
    result.cycles = sorted(cycles, key=lambda item: -int(str(item["size"])))[:50]

    # ── 孤儿模块：既没有导入别人也没被别人导入的文件 ─────────────────
    connected_files = {path for path, degree in import_graph.degree() if degree > 0}
    orphans = [
        {"path": path, "reason": "没有 import 关系的文件"}
        for path in sorted(file_paths - connected_files)
    ]
    result.orphans = orphans[:_ORPHAN_FILE_LIMIT]

    # ── 跨社区连接（值得人工确认的"意外联系"）───────────────────────
    cross: list[dict[str, Any]] = []
    for edge in edges:
        left = result.node_community.get(edge.src)
        right = result.node_community.get(edge.dst)
        if left is None or right is None or left == right:
            continue
        if edge.relation in ("contains", "defines"):
            continue
        cross.append(
            {
                "source": edge.src,
                "target": edge.dst,
                "relation": edge.relation,
                "sourceCommunity": left,
                "targetCommunity": right,
            }
        )
    result.cross_community = cross[:_CROSS_COMMUNITY_LIMIT]

    # ── 统计 ────────────────────────────────────────────────────────
    result.stats = {
        "communities": len(result.communities),
        "largestCommunity": max((c.size for c in result.communities), default=0),
        "godNodes": len(result.god_nodes),
        "importCycles": len(result.cycles),
        "orphanFiles": len(orphans),
        "crossCommunityEdges": len(cross),
        "communityNodes": len(result.node_community),
        "resolvedCallRate": resolved_call_rate,
    }
    return result


def _merge_small_communities(
    graph: nx.Graph, communities: list[list[str]], *, minimum: int = MIN_COMMUNITY_SIZE
) -> list[list[str]]:
    """把成员数不足 ``minimum`` 的社区并入"联系最紧"的邻居社区（无邻居则保留）。"""
    membership = {node: index for index, group in enumerate(communities) for node in group}
    changed = True
    while changed:
        changed = False
        sizes = Counter(membership.values())
        for index, group in enumerate(communities):
            if not group or sizes[index] >= minimum:
                continue
            weights: Counter[int] = Counter()
            for node in group:
                for neighbour in graph.neighbors(node):
                    other = membership.get(neighbour)
                    if other is not None and other != index:
                        weights[other] += 1
            if not weights:
                continue
            target = weights.most_common(1)[0][0]
            communities[target] = communities[target] + group
            for node in group:
                membership[node] = target
            communities[index] = []
            changed = True
    return [sorted(group) for group in communities if group]


def _cohesion(graph: nx.Graph, members: list[str]) -> float:
    if len(members) < 2:
        return 0.0
    internal = graph.subgraph(members).number_of_edges()
    possible = len(members) * (len(members) - 1) / 2
    return round(internal / possible, 4) if possible else 0.0


def _community_name(nodes: list[NodeRec], *, highlight: str | None = None) -> str:
    """确定性命名：主目录 + 度数最高的符号名（M4 会用 LLM 覆盖它）。"""
    directories: Counter[str] = Counter()
    for node in nodes:
        if node.file and "/" in node.file:
            directories[node.file.split("/")[0]] += 1
        elif node.file:
            directories["(根目录)"] += 1
    top_dir = directories.most_common(1)[0][0] if directories else "(无文件)"
    symbol = highlight or (nodes[0].name if nodes else "?")
    return f"{top_dir} · {symbol}"


def _file_of(node_id: str, node_by_id: dict[str, NodeRec]) -> str | None:
    node = node_by_id.get(node_id)
    if node is None:
        return None
    if node.kind == "file":
        return node.qualified
    return node.file


def summarize_community_files(
    node_community: dict[str, int], nodes: list[NodeRec]
) -> dict[int, Counter[str]]:
    """社区 → 文件分布（给报告与 LLM 命名用）。"""
    by_community: dict[int, Counter[str]] = defaultdict(Counter)
    node_by_id = {node.id: node for node in nodes}
    for node_id, community in node_community.items():
        node = node_by_id.get(node_id)
        if node and node.file:
            by_community[community][node.file] += 1
    return by_community
