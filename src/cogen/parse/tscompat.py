"""py-tree-sitter API 适配层：所有节点访问都经过这里，升级时只改这一处。

已核实的 py-tree-sitter 0.26 行为（写代码前实测确认）：
- 节点属性：``type`` / ``is_named`` / ``is_error`` / ``is_missing`` / ``has_error`` /
  ``child_count`` / ``children`` / ``child(i)`` / ``field_name_for_child(i)``；
- ``child(i)`` 越界会抛 ``IndexError`` 而不是返回 ``None`` → 这里包一层；
- ``descendant_count`` **包含自身**，因此总节点数 = ``root.descendant_count``（O(1)，无需 Python 遍历）；
- ``node.text`` 返回 ``bytes``；
- 语法错误可能表现为 ``ERROR`` 节点，也可能只有 ``MISSING`` 节点（此时 ``is_error`` 为假），
  所以错误统计必须同时看 ``is_error`` 与 ``is_missing``；
- ⚠️ **``start_point`` / ``end_point`` 返回的 ``Point`` 原生对象不可大量使用**：3000 个节点上
  遍历读取一次就会破坏堆，随后在 GC / 解释器退出时 Bus error 或 Segmentation fault
  （本项目实测稳定复现）。因此行列一律用 ``SourceIndex`` 从字节偏移换算，**不要**访问
  ``start_point`` / ``end_point`` / ``range``。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from typing import Any

DEFAULT_ERROR_SCAN_LIMIT = 200_000


def node_type(node: Any) -> str:
    return str(node.type)


def is_named(node: Any) -> bool:
    return bool(node.is_named)


def is_error(node: Any) -> bool:
    return bool(node.is_error)


def is_missing(node: Any) -> bool:
    return bool(node.is_missing)


def has_error(node: Any) -> bool:
    return bool(node.has_error)


def child_count(node: Any) -> int:
    return int(node.child_count)


def children(node: Any) -> list[Any]:
    return list(node.children)


def child(node: Any, index: int) -> Any | None:
    """取第 i 个子节点；越界返回 ``None``（原生 API 越界会抛 IndexError）。"""
    if index < 0 or index >= child_count(node):
        return None
    try:
        return node.child(index)
    except IndexError:  # pragma: no cover - 原生层偶发越界
        return None


def same_node(left: Any, right: Any) -> bool:
    """两个 ``Node`` 包装器是否指向同一个原生节点。

    ⚠️ 不能用 ``is`` 或 ``==``：每次访问属性都会新建包装对象（实测 ``is`` 恒为 False），
    但原生 ``id`` 相同。
    """
    if left is None or right is None:
        return False
    try:
        return int(left.id) == int(right.id)
    except (AttributeError, TypeError):  # pragma: no cover - 兜底
        return (node_type(left), start_byte(left), end_byte(left)) == (
            node_type(right),
            start_byte(right),
            end_byte(right),
        )


def child_by_field_name(node: Any, name: str) -> Any | None:
    return node.child_by_field_name(name)


def field_name_for_child(node: Any, index: int) -> str | None:
    name = node.field_name_for_child(index)
    return str(name) if name else None


def field_name_for_named_child(node: Any, index: int) -> str | None:
    name = node.field_name_for_named_child(index)
    return str(name) if name else None


class SourceIndex:
    """源码字节 + 行起始偏移表：字节偏移 → (行, 列)，并且不触碰 ``Point`` 原生对象。

    ``Point`` 的坑见模块 docstring；用 ``bytes.find`` 建表是 C 级循环，1 MiB 文件约 1ms。
    """

    __slots__ = ("_line_starts", "source")

    def __init__(self, source: bytes) -> None:
        self.source = source
        starts = [0]
        pos = source.find(b"\n")
        while pos != -1:
            starts.append(pos + 1)
            pos = source.find(b"\n", pos + 1)
        self._line_starts = starts

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def point(self, byte_offset: int) -> tuple[int, int]:
        """按 UTF-8 字节列（与 tree-sitter 的列语义一致）返回 0-based (行, 列)。"""
        offset = max(0, min(byte_offset, len(self.source)))
        row = bisect_right(self._line_starts, offset) - 1
        return (row, offset - self._line_starts[row])

    def text(self, start: int, end: int) -> str:
        return self.source[start:end].decode("utf-8", "replace")


def start_byte(node: Any) -> int:
    return int(node.start_byte)


def end_byte(node: Any) -> int:
    return int(node.end_byte)


def byte_range(node: Any) -> tuple[int, int]:
    return (start_byte(node), end_byte(node))


def descendant_count(node: Any) -> int:
    """总节点数（含自身）。"""
    return int(node.descendant_count)


def node_bytes(source: bytes, node: Any) -> bytes:
    start, end = byte_range(node)
    return source[start:end]


def node_text(source: bytes, node: Any) -> str:
    return node_bytes(source, node).decode("utf-8", "replace")


def iter_nodes(root: Any, *, limit: int = DEFAULT_ERROR_SCAN_LIMIT) -> Iterator[Any]:
    """前序遍历（显式栈，避免深树递归爆栈）。"""
    stack = [root]
    seen = 0
    while stack:
        node = stack.pop()
        yield node
        seen += 1
        if seen >= limit:
            return
        stack.extend(reversed(children(node)))


def count_problems(root: Any, *, limit: int = DEFAULT_ERROR_SCAN_LIMIT) -> tuple[int, int]:
    """返回 ``(error_count, missing_count)``；无错误时零成本短路。"""
    if not has_error(root):
        return (0, 0)
    errors = 0
    missing = 0
    for node in iter_nodes(root, limit=limit):
        if is_error(node):
            errors += 1
        elif is_missing(node):
            missing += 1
    return (errors, missing)
