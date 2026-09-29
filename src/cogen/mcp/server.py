"""MCP Server：把图谱查询暴露给 AI 编程助手（Claude Code / Cursor / Inspector…）。

- **只读**：工具与资源和 Web 问答共用 ``cogen.graph.tools``，没有任何执行/写入口（计划 §9）；
- 传输：``stdio``（默认）与 ``streamable-http``；
- 目标仓库：``--repo <repoId>`` 指定，省略时用最近索引的那个仓库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..graph import tools
from ..graph.store import Store, db_path_for, list_repo_metas
from ..parse.cst import CstOptions, CstPathError, field_of, resolve_node, serialize_subtree
from ..parse.parser import UnknownLanguageError, parse_file_cached, resolve_language
from ..security import PathEscapeError, check_repo_relative_path, ensure_within

MAX_PAYLOAD_NODES = 60
MAX_PAYLOAD_EDGES = 120


def resolve_repo_id(settings: Settings, requested: str | None) -> str:
    """选一个仓库：显式指定优先，否则用最近索引的那个。"""
    if requested:
        if not db_path_for(settings, requested).exists():
            raise SystemExit(f"仓库不存在: {requested}")
        return requested
    metas = list_repo_metas(settings)
    if not metas:
        raise SystemExit("还没有索引任何仓库，先运行：cogen index <target>")
    return metas[0].repo_id


class RepoSession:
    """按需打开仓库库；每次查询开一条短连接，避免跨线程共享 sqlite 连接。"""

    def __init__(self, settings: Settings, repo_id: str) -> None:
        self.settings = settings
        self.repo_id = repo_id

    def store(self) -> Store:
        return Store(db_path_for(self.settings, self.repo_id))

    def root_path(self) -> str:
        with self.store() as store:
            meta = store.load_repo_meta()
        return meta.root_path if meta else ""

    def overview(self) -> dict[str, Any]:
        with self.store() as store:
            meta = store.load_repo_meta()
            counts = store.graph_counts()
            groups = store.group_counts()
            stored = store.get_meta()
        return {
            "repoId": self.repo_id,
            "target": meta.target if meta else None,
            "files": meta.file_count if meta else 0,
            "loc": meta.loc if meta else 0,
            "languages": meta.languages if meta else {},
            "nodes": counts["nodes"],
            "edges": counts["edges"],
            "byKind": groups["byKind"],
            "byRelation": groups["byRelation"],
            "resolvedCallRate": (stored.get("graph") or {}).get("resolvedCallRate"),
            "message": meta.message if meta else None,
        }


def _trim(payload: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(payload)
    if isinstance(trimmed.get("nodes"), list):
        trimmed["nodes"] = trimmed["nodes"][:MAX_PAYLOAD_NODES]
    if isinstance(trimmed.get("edges"), list):
        trimmed["edges"] = trimmed["edges"][:MAX_PAYLOAD_EDGES]
    return trimmed


def create_server(settings: Settings, repo_id: str) -> Any:
    """构造 MCPServer 实例（注册全部只读工具与资源）。"""
    from mcp.server import MCPServer

    session = RepoSession(settings, repo_id)
    server = MCPServer(
        "cogen",
        version="0.1.0",
        instructions=(
            "CogenNav 代码图谱。用这些只读工具查询已索引仓库的符号、调用关系、"
            "影响面与社区划分；不要臆造不存在的符号。先 repo_overview 建立整体认识，"
            "再用 search_symbols / get_symbol / callers 深入。"
        ),
    )

    @server.tool(description="仓库总览：规模、语言、节点边数、调用解析率")
    def repo_overview() -> dict[str, Any]:
        return session.overview()

    @server.tool(description="按名字/限定名/路径搜索符号")
    def search_symbols(query: str, kind: str | None = None, limit: int = 20) -> dict[str, Any]:
        with session.store() as store:
            return tools.search_payload(
                store, query, kind=kind, limit=min(limit, 100), root_path=session.root_path()
            )

    @server.tool(description="取符号详情：定义位置、代码片段、出入边、所属社区")
    def get_symbol(node_id: str) -> dict[str, Any]:
        with session.store() as store:
            try:
                return tools.symbol_payload(store, node_id, root_path=session.root_path())
            except KeyError:
                return {"error": f"节点不存在: {node_id}"}

    @server.tool(description="节点 N 度邻域（可指定方向与关系）")
    def neighbors(
        node_id: str,
        depth: int = 2,
        direction: str = "both",
        relations: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        with session.store() as store:
            try:
                return _trim(
                    tools.neighbors_payload(
                        store,
                        node_id,
                        depth=max(1, min(depth, 4)),
                        direction=direction if direction in ("both", "out", "in") else "both",
                        relations=[item.strip() for item in relations.split(",")]
                        if relations
                        else None,
                        limit=min(limit, 1000),
                    )
                )
            except KeyError:
                return {"error": f"节点不存在: {node_id}"}

    @server.tool(description="谁调用了这个符号（入边，calls 关系）")
    def callers(node_id: str, depth: int = 1, limit: int = 100) -> dict[str, Any]:
        return neighbors(node_id, depth=depth, direction="in", relations="calls", limit=limit)

    @server.tool(description="这个符号调用了谁（出边，calls 关系）")
    def callees(node_id: str, depth: int = 1, limit: int = 100) -> dict[str, Any]:
        return neighbors(node_id, depth=depth, direction="out", relations="calls", limit=limit)

    @server.tool(description="影响面：谁依赖它（入边闭包）+ 需要回归的文件清单")
    def impact(node_id: str, depth: int = 3) -> dict[str, Any]:
        with session.store() as store:
            try:
                return _trim(tools.impact_payload(store, node_id, depth=max(1, min(depth, 5))))
            except KeyError:
                return {"error": f"节点不存在: {node_id}"}

    @server.tool(description="两个符号之间的最短关系路径")
    def path_between(source: str, target: str, max_depth: int = 6) -> dict[str, Any]:
        with session.store() as store:
            try:
                return _trim(
                    tools.path_payload(store, source, target, max_depth=max(1, min(max_depth, 10)))
                )
            except KeyError as exc:
                return {"error": f"节点不存在: {exc}"}

    @server.tool(description="目录树 + 聚合度量（loc / 文件数 / 符号数）")
    def file_tree(path: str = "", depth: int = 3) -> dict[str, Any]:
        with session.store() as store:
            try:
                return tools.tree_payload(store, path=path, depth=max(1, min(depth, 6)))
            except KeyError:
                return {"error": f"目录不存在: {path}"}

    @server.tool(description="读取已索引文件内容（可选行范围）")
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict[str, Any]:
        from ..ai.ask import execute_tool

        with session.store() as store:
            arguments: dict[str, Any] = {"path": path, "startLine": start_line}
            if end_line:
                arguments["endLine"] = end_line
            return execute_tool(store, "read_file", arguments, root_path=session.root_path())

    @server.tool(description="取某个文件的 CST 子树（惰性，按 nodePath + depth）")
    def get_cst(path: str, node_path: str = "", depth: int = 4) -> dict[str, Any]:
        root = Path(session.root_path())
        try:
            rel = check_repo_relative_path(path)
        except Exception as exc:  # 非法路径
            return {"error": str(exc)}
        try:
            absolute = ensure_within(root, root / rel)
        except PathEscapeError:
            return {"error": "路径越界"}
        with session.store() as store:
            record = store.get_file(rel)
            if record is None:
                return {"error": f"文件未索引: {rel}"}
        if resolve_language(record.language) is None:
            return {"error": f"该语言没有可用语法（{record.language}），只能浏览源码"}
        try:
            parsed = parse_file_cached(absolute, record.language)
            options = CstOptions(depth=depth).clamped()
            chain = resolve_node(parsed.root, node_path)
        except (CstPathError, UnknownLanguageError, OSError) as exc:
            return {"error": str(exc)}
        return {
            "path": rel,
            "language": record.language,
            "nodePath": node_path,
            "depth": options.depth,
            "totalNodes": record.node_count or parsed.node_count,
            "node": serialize_subtree(
                parsed.index, chain[-1], field=field_of(chain), depth=options.depth, options=options
            ),
        }

    @server.tool(description="社区（模块聚类）、god nodes、import 环、孤儿文件")
    def list_communities() -> dict[str, Any]:
        with session.store() as store:
            return tools.analysis_payload(store)

    @server.tool(description="文件间的模块依赖（imports 关系）")
    def module_dependencies(path: str) -> dict[str, Any]:
        from ..extract.base import file_node_id

        node_id = file_node_id(path)
        with session.store() as store:
            try:
                return _trim(
                    tools.neighbors_payload(
                        store, node_id, depth=1, direction="both", relations=["imports"], limit=200
                    )
                )
            except KeyError:
                return {"error": f"文件不存在于图谱: {path}"}

    @server.resource(
        "cogen://repo/overview",
        name="仓库总览",
        description="文件数、语言分布、图谱规模与调用解析率",
        mime_type="application/json",
    )
    def repo_overview_resource() -> dict[str, Any]:
        return session.overview()

    @server.resource(
        "cogen://repo/analysis",
        name="社区与关键节点",
        description="社区划分、god nodes、import 环、孤儿文件",
        mime_type="application/json",
    )
    def repo_analysis_resource() -> dict[str, Any]:
        with session.store() as store:
            return tools.analysis_payload(store)

    @server.resource(
        "cogen://repo/tree",
        name="目录树",
        description="目录树 + 聚合度量",
        mime_type="application/json",
    )
    def repo_tree_resource() -> dict[str, Any]:
        with session.store() as store:
            return tools.tree_payload(store, depth=4)

    return server


def run_mcp(
    *,
    repo_id: str | None = None,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8770,
) -> None:
    settings = get_settings()
    resolved = resolve_repo_id(settings, repo_id)
    server = create_server(settings, resolved)
    if transport == "stdio":
        server.run("stdio")
        return
    server.run("streamable-http", host=host, port=port, streamable_http_path="/mcp")
