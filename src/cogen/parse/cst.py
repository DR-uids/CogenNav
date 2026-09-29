"""CST 导出：按 ``nodePath`` + ``depth`` 的惰性子树，绝不整树传输。

``nodePath`` 语义：根节点为 ``""``，节点路径 P 的第 i 个子节点为
``P == "" ? str(i) : f"{P}.{i}"``；索引的是**全部子节点**（含匿名节点），
因为 CST 浏览器要看到标点等未命名节点。

``truncated`` 严格表示「因 depth 限制未返回该节点的子节点」；
如果是子节点数量超过上限被截掉，用 ``childrenCapped`` 单独标记，
避免前端把「有子节点但被截断」误判成可以继续懒加载。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..security import sanitize_label
from . import tscompat as ts
from .tscompat import SourceIndex


class CstPathError(LookupError):
    """nodePath 指向的节点不存在。"""


@dataclass(frozen=True)
class CstOptions:
    depth: int = 4
    max_children: int = 500
    leaf_text_limit: int = 80

    def clamped(self) -> CstOptions:
        return CstOptions(
            depth=max(1, min(self.depth, 12)),
            max_children=max(1, min(self.max_children, 2000)),
            leaf_text_limit=max(8, min(self.leaf_text_limit, 500)),
        )


def parse_node_path(node_path: str) -> list[int]:
    text = (node_path or "").strip()
    if not text:
        return []
    if len(text) > 200:
        raise CstPathError("nodePath 过长")
    indices: list[int] = []
    for part in text.split("."):
        if not part.isdigit():
            raise CstPathError(f"nodePath 非法: {node_path!r}")
        indices.append(int(part))
    return indices


def resolve_node(root: Any, node_path: str) -> list[Any]:
    """按 nodePath 返回从根到目标的节点链（用于顺带取字段名）。"""
    chain = [root]
    current = root
    for index in parse_node_path(node_path):
        nxt = ts.child(current, index)
        if nxt is None:
            raise CstPathError(f"节点不存在: {node_path}")
        chain.append(nxt)
        current = nxt
    return chain


def field_of(chain: list[Any]) -> str | None:
    """目标节点在其父节点中的字段名。"""
    if len(chain) < 2:
        return None
    parent, node = chain[-2], chain[-1]
    for index in range(ts.child_count(parent)):
        candidate = ts.child(parent, index)
        if candidate is not None and candidate.id == node.id:
            return ts.field_name_for_child(parent, index)
    return None


def _as_index(value: SourceIndex | bytes) -> SourceIndex:
    """允许直接传 bytes（测试/脚本方便），内部统一成 SourceIndex。"""
    return value if isinstance(value, SourceIndex) else SourceIndex(bytes(value))


def serialize_subtree(
    source: SourceIndex | bytes,
    node: Any,
    *,
    field: str | None = None,
    depth: int,
    options: CstOptions,
) -> dict[str, Any]:
    """把一个节点序列化成前端契约里的 ``CstNode``。

    ``source`` 传 ``SourceIndex`` 可复用行索引（推荐）；传 ``bytes`` 会自动建索引。
    """
    index = _as_index(source)
    start_byte = ts.start_byte(node)
    end_byte = ts.end_byte(node)
    payload: dict[str, Any] = {
        "type": ts.node_type(node),
        "named": ts.is_named(node),
        "field": field,
        "start": list(index.point(start_byte)),
        "end": list(index.point(end_byte)),
        "startByte": start_byte,
        "endByte": end_byte,
        "childCount": ts.child_count(node),
        "error": ts.is_error(node),
        "missing": ts.is_missing(node),
        "truncated": False,
        "childrenCapped": False,
        "text": None,
        "children": [],
    }

    total_children = ts.child_count(node)
    if total_children == 0:
        payload["text"] = sanitize_label(
            index.text(start_byte, end_byte),
            limit=options.leaf_text_limit,
        )
        return payload

    if depth <= 0:
        payload["truncated"] = True
        return payload

    nodes = ts.children(node)[: options.max_children]
    payload["childrenCapped"] = len(nodes) < total_children
    payload["children"] = [
        serialize_subtree(
            index,
            child_node,
            field=ts.field_name_for_child(node, child_index),
            depth=depth - 1,
            options=options,
        )
        for child_index, child_node in enumerate(nodes)
    ]
    return payload


def summarize_sexp(tree: Any, *, limit: int = 4000) -> str:
    """超大文件的降级摘要：tree-sitter 自带的 S-表达式。"""
    try:
        text = str(tree.root_node)
    except Exception:  # pragma: no cover - 原生层异常兜底
        return ""
    return text[:limit]
