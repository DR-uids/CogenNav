"""图谱构建：把文件清单 + 抽取结果 + 引用解析结果拼成节点与边。

节点层次：repo → dir → file → symbol（class/function/...）；外部依赖单独成 ``external`` 节点。
边：``contains``（结构包含）、``defines``（文件定义符号）、``imports``（文件→文件/外部模块）、
``calls``/``extends``/``implements``/``instantiates``（引用解析结果）、``tests``（测试→被测文件）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from ..extract.base import (
    EXTERNAL_NODE_PREFIX,
    FILE_NODE_PREFIX,
    FileExtraction,
    dir_node_id,
    file_node_id,
    repo_node_id,
    symbol_node_id,
)
from ..extract.resolve_calls import ResolutionResult
from .schema import EdgeRec, FileRecord, NodeRec

#: 测试文件的路径特征（用于生成 tests 边）
_TEST_MARKERS = (
    "/tests/",
    "/test/",
    "/__tests__/",
    "/spec/",
    "test_",
    "_test.",
    ".test.",
    ".spec.",
)


@dataclass
class GraphData:
    """建图结果（未落库）。"""

    nodes: list[NodeRec] = field(default_factory=list)
    edges: list[EdgeRec] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def is_test_path(path: str) -> bool:
    lowered = f"/{path.lower()}"
    return any(marker in lowered for marker in _TEST_MARKERS)


def build_graph(
    *,
    repo_id: str,
    repo_name: str,
    files: list[FileRecord],
    extractions: dict[str, FileExtraction],
    resolution: ResolutionResult,
    commit: str | None = None,
) -> GraphData:
    nodes: list[NodeRec] = []
    edges: list[EdgeRec] = []
    seen_edges: set[tuple[str, str, str, str, int, int]] = set()

    def add_edge(
        src: str,
        dst: str,
        relation: str,
        confidence: str,
        file: str | None = None,
        start: tuple[int, int] | None = None,
        end: tuple[int, int] | None = None,
    ) -> None:
        sl, sc = start or (0, 0)
        el, ec = end or (0, 0)
        key = (src, dst, relation, file or "", sl, sc)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(
            EdgeRec(
                src=src,
                dst=dst,
                relation=relation,  # type: ignore[arg-type]
                confidence=confidence,  # type: ignore[arg-type]
                file=file,
                sl=sl,
                sc=sc,
                el=el,
                ec=ec,
            )
        )

    # ── 仓库 / 目录 / 文件节点 ──────────────────────────────────────
    root_id = repo_node_id(repo_id)
    nodes.append(
        NodeRec(
            id=root_id,
            kind="repo",
            name=repo_name,
            qualified=repo_id,
            extra={"commit": commit} if commit else {},
        )
    )

    directories: set[str] = set()
    for record in files:
        parent = str(PurePosixPath(record.path).parent)
        while parent and parent != ".":
            directories.add(parent)
            parent = str(PurePosixPath(parent).parent)
    for directory in sorted(directories):
        nodes.append(
            NodeRec(
                id=dir_node_id(directory),
                kind="dir",
                name=PurePosixPath(directory).name,
                qualified=directory,
            )
        )

    top_level_dirs: set[str] = set()
    for record in files:
        nodes.append(
            NodeRec(
                id=file_node_id(record.path),
                kind="file",
                name=PurePosixPath(record.path).name,
                qualified=record.path,
                file=record.path,
                language=record.language,
                extra={
                    "loc": record.loc,
                    "size": record.size,
                    "nodeCount": record.node_count,
                    "errorCount": record.error_count,
                },
            )
        )
        parent = str(PurePosixPath(record.path).parent)
        if parent == ".":
            add_edge(root_id, file_node_id(record.path), "contains", "EXTRACTED")
        else:
            add_edge(dir_node_id(parent), file_node_id(record.path), "contains", "EXTRACTED")
            top_level_dirs.add(parent.split("/")[0])

    for directory in sorted(directories):
        parts = directory.split("/")
        if len(parts) == 1:
            add_edge(root_id, dir_node_id(directory), "contains", "EXTRACTED")
        else:
            parent = "/".join(parts[:-1])
            add_edge(dir_node_id(parent), dir_node_id(directory), "contains", "EXTRACTED")

    # ── 符号节点 + defines/contains 边 ─────────────────────────────
    symbol_ids_by_file: dict[str, dict[str, str]] = defaultdict(dict)
    for path, extraction in extractions.items():
        root_symbols: list[tuple[str, str, str | None]] = []  # (node_id, qualified, parent)
        for symbol in extraction.symbols:
            ordinal = int(symbol.extra.get("ordinal", 0))
            node_id = symbol_node_id(
                extraction.language, path, symbol.qualified, symbol.kind, ordinal
            )
            symbol_ids_by_file[path][symbol.qualified] = node_id
            nodes.append(
                NodeRec(
                    id=node_id,
                    kind=symbol.kind,  # type: ignore[arg-type]
                    name=symbol.name,
                    qualified=symbol.qualified,
                    file=path,
                    sl=symbol.start[0],
                    sc=symbol.start[1],
                    el=symbol.end[0],
                    ec=symbol.end[1],
                    language=extraction.language,
                    extra={"doc": symbol.doc} if symbol.doc else {},
                )
            )
            root_symbols.append((node_id, symbol.qualified, symbol.parent))

        for sym_id, _qualified, sym_parent in root_symbols:
            if sym_parent is None:
                add_edge(file_node_id(path), sym_id, "defines", "EXTRACTED", file=path)
            else:
                owner_id = symbol_ids_by_file[path].get(sym_parent)
                if owner_id:
                    add_edge(owner_id, sym_id, "contains", "EXTRACTED", file=path)
                else:
                    add_edge(file_node_id(path), sym_id, "defines", "EXTRACTED", file=path)

    # ── 外部节点（调用与导入都已在解析阶段按次数截断）────────────────
    external_names: set[str] = set(resolution.external_targets)
    for ref in resolution.references:
        if ref.target_id.startswith(EXTERNAL_NODE_PREFIX):
            external_names.add(ref.target_id)

    for ext_id in sorted(external_names):
        name = ext_id[len(EXTERNAL_NODE_PREFIX) :]
        nodes.append(
            NodeRec(
                id=ext_id,
                kind="external",
                name=name,
                qualified=name,
                extra={"external": True},
            )
        )

    # ── imports 边 ─────────────────────────────────────────────────
    for ref in resolution.references:
        if ref.relation == "imports" and ref.target_id.startswith("ext:"):
            if ref.target_id in external_names:
                add_edge(
                    ref.source_id,
                    ref.target_id,
                    "imports",
                    "EXTRACTED",
                    file=ref.file,
                    start=ref.start,
                    end=ref.end,
                )
        elif ref.relation == "imports":
            add_edge(
                ref.source_id,
                ref.target_id,
                "imports",
                "EXTRACTED",
                file=ref.file,
                start=ref.start,
                end=ref.end,
            )

    # ── 引用边（calls/extends/...）与 tests 边 ──────────────────────
    test_targets: dict[str, set[str]] = defaultdict(set)
    for ref in resolution.references:
        if ref.relation == "imports":
            continue
        add_edge(
            ref.source_id,
            ref.target_id,
            ref.relation,
            ref.confidence,
            file=ref.file,
            start=ref.start,
            end=ref.end,
        )
        if is_test_path(ref.file):
            if ref.target_id.startswith(FILE_NODE_PREFIX):
                test_targets[ref.file].add(ref.target_id[len(FILE_NODE_PREFIX) :])
            else:
                target_path = _file_of_node(ref.target_id)
                if target_path:
                    test_targets[ref.file].add(target_path)

    known_paths = {f.path for f in files}
    for test_file, targets in test_targets.items():
        for target_path in sorted(targets):
            if target_path != test_file and target_path in known_paths:
                add_edge(
                    file_node_id(test_file),
                    file_node_id(target_path),
                    "tests",
                    "INFERRED",
                    file=test_file,
                )

    # ── 度数：写进 node.extra，供 /graph 按热度排序与前端定大小 ──────
    in_degree: Counter[str] = Counter()
    out_degree: Counter[str] = Counter()
    for edge in edges:
        out_degree[edge.src] += 1
        in_degree[edge.dst] += 1
    for node in nodes:
        extra = dict(node.extra)
        extra["inDegree"] = in_degree[node.id]
        extra["outDegree"] = out_degree[node.id]
        extra["degree"] = in_degree[node.id] + out_degree[node.id]
        node.extra.clear()
        node.extra.update(extra)

    # ── 统计 ────────────────────────────────────────────────────────
    by_kind = Counter(node.kind for node in nodes)
    by_relation = Counter(edge.relation for edge in edges)
    by_confidence = Counter(edge.confidence for edge in edges)
    stats: dict[str, Any] = {
        "nodes": len(nodes),
        "edges": len(edges),
        "byKind": dict(by_kind.most_common()),
        "byRelation": dict(by_relation.most_common()),
        "byConfidence": dict(by_confidence.most_common()),
        "directories": len(directories),
        "topLevelDirs": sorted(top_level_dirs),
        **resolution.stats.to_api(),
    }
    return GraphData(nodes=nodes, edges=edges, stats=stats)


def _file_of_node(node_id: str) -> str | None:
    """从符号 id 里取出文件路径：``<lang>:<path>#<qualified>``。"""
    if "#" not in node_id or ":" not in node_id:
        return None
    head = node_id.split("#", 1)[0]
    return head.split(":", 1)[1]
