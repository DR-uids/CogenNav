"""图谱接口：目录树、全图/邻域、路径、影响面、搜索、符号卡片、分析结果。

所有实现都在 ``cogen.graph.tools``（与后续的问答 / MCP 共用），这里只做参数解析与错误映射。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..graph import tools
from ..i18n import t
from .routes_files import repo_context

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["graph"])


def _parse_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


@router.get("/tree")
def get_tree(
    repo_id: str,
    path: str = Query("", max_length=1024),
    depth: int = Query(3, ge=1, le=8),
) -> dict[str, object]:
    """目录树 + 聚合度量（loc / 文件数 / 符号数），供目录树与 Treemap 使用。"""
    with repo_context(repo_id) as (store, _root, _settings):
        try:
            return tools.tree_payload(store, path=path, depth=depth)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=t("api.dirNotFound", path=path)) from exc


@router.get("/analysis")
def get_analysis(repo_id: str) -> dict[str, object]:
    """社区、god nodes、import 环、孤儿文件与总体统计。"""
    with repo_context(repo_id) as (store, _root, _settings):
        return tools.analysis_payload(store)


@router.get("/graph")
def get_graph(
    repo_id: str,
    kinds: str | None = Query(None, max_length=512),
    relations: str | None = Query(None, max_length=512),
    confidence: str | None = Query(None, max_length=128),
    community: int | None = Query(None),
    limit: int = Query(1500, ge=1, le=20000),
    focus: str | None = Query(None, max_length=512),
    depth: int = Query(2, ge=1, le=6),
) -> dict[str, object]:
    with repo_context(repo_id) as (store, _root, _settings):
        try:
            return tools.graph_payload(
                store,
                kinds=_parse_list(kinds),
                relations=_parse_list(relations),
                confidences=_parse_list(confidence),
                community=community,
                limit=limit,
                focus=focus,
                depth=depth,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=t("api.nodeNotFound", node=focus)) from exc


@router.get("/graph/neighbors")
def get_neighbors(
    repo_id: str,
    node_id: str = Query(..., alias="nodeId", max_length=512),
    depth: int = Query(2, ge=1, le=6),
    direction: str = Query("both", pattern="^(both|out|in)$"),
    relations: str | None = Query(None, max_length=512),
    limit: int = Query(400, ge=1, le=5000),
) -> dict[str, object]:
    with repo_context(repo_id) as (store, _root, _settings):
        try:
            return tools.neighbors_payload(
                store,
                node_id,
                depth=depth,
                direction=direction,
                relations=_parse_list(relations),
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=t("api.nodeNotFound", node=node_id)
            ) from exc


@router.get("/graph/path")
def get_path(
    repo_id: str,
    source: str = Query(..., alias="from", max_length=512),
    target: str = Query(..., alias="to", max_length=512),
    max_depth: int = Query(6, alias="maxDepth", ge=1, le=12),
) -> dict[str, object]:
    with repo_context(repo_id) as (store, _root, _settings):
        try:
            return tools.path_payload(store, source, target, max_depth=max_depth)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=t("api.nodeNotFound", node=exc)) from exc


@router.get("/graph/impact")
def get_impact(
    repo_id: str,
    node_id: str = Query(..., alias="nodeId", max_length=512),
    depth: int = Query(3, ge=1, le=6),
    relations: str | None = Query(None, max_length=512),
) -> dict[str, object]:
    """影响面：谁依赖我（入边闭包）+ 需要回归的文件清单。"""
    with repo_context(repo_id) as (store, _root, _settings):
        try:
            return tools.impact_payload(
                store, node_id, depth=depth, relations=_parse_list(relations)
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=t("api.nodeNotFound", node=node_id)
            ) from exc


@router.get("/search")
def search_symbols(
    repo_id: str,
    q: str = Query(..., min_length=1, max_length=256),
    kind: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    with repo_context(repo_id) as (store, root, _settings):
        return tools.search_payload(store, q, kind=kind, limit=limit, root_path=str(root))


@router.get("/symbol")
def get_symbol(
    repo_id: str,
    node_id: str = Query(..., alias="nodeId", max_length=512),
) -> dict[str, object]:
    with repo_context(repo_id) as (store, root, _settings):
        try:
            return tools.symbol_payload(store, node_id, root_path=str(root))
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=t("api.nodeNotFound", node=node_id)
            ) from exc
