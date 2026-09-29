"""抽取框架：Extractor 协议、中间数据结构、节点 id 规则与内建名表。

设计要点（计划 §6.1 / §8）：
- extractor **只做单文件、纯语法**的事：产出符号、引用、导入；跨文件绑定交给
  ``resolve_calls.py``，因为"谁调用了谁"必须看到整个仓库才能定；
- 所有产物都是可 pickle 的 dataclass（在 worker 进程里生成，回到主进程再建图）；
- id 必须**确定性且与位置无关**：``<lang>:<relpath>#<qualified>.<kind>[#n]``，
  位置（行列）只作为字段存储，改动行号不会让 id 变化（增量索引的前提）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..parse import tscompat as ts
from ..parse.parser import ParsedSource

#: 目录 / 文件 / 仓库 / 外部符号的 id 前缀（与语言符号区分开）
REPO_NODE_PREFIX = "repo:"
DIR_NODE_PREFIX = "dir:"
FILE_NODE_PREFIX = "file:"
EXTERNAL_NODE_PREFIX = "ext:"


def repo_node_id(repo_id: str) -> str:
    return f"{REPO_NODE_PREFIX}{repo_id}"


def dir_node_id(path: str) -> str:
    return f"{DIR_NODE_PREFIX}{path}"


def file_node_id(path: str) -> str:
    return f"{FILE_NODE_PREFIX}{path}"


def external_node_id(name: str) -> str:
    return f"{EXTERNAL_NODE_PREFIX}{name}"


def symbol_node_id(language: str, path: str, qualified: str, kind: str, ordinal: int = 0) -> str:
    """符号 id：``<lang>:<path>#<qualified>.<kind>``，同名同 kind 用 ``#n`` 区分。"""
    base = f"{language}:{path}#{qualified}.{kind}"
    return base if ordinal <= 0 else f"{base}#{ordinal}"


@dataclass(frozen=True)
class Symbol:
    """一个定义：类 / 函数 / 方法 / 接口 / 结构体 / 枚举 / 变量 / 常量 / 类型。"""

    kind: str
    name: str
    qualified: str
    start: tuple[int, int]
    end: tuple[int, int]
    parent: str | None = None  # 父符号的 qualified（用于 class→method 的 contains）
    doc: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reference:
    """一次名字引用（调用 / 继承 / 实现 / 实例化 / 类型使用）。"""

    name: str  # 被引用的简单名，如 "send"
    kind: str  # call | extends | implements | instantiates | type_uses
    start: tuple[int, int]
    end: tuple[int, int]
    scope: str | None = None  # 发生引用的符号 qualified（caller）
    receiver: str | None = None  # 形如 os.getcwd 的 "os"；null 表示裸名
    full: str | None = None  # 原始文本，如 "os.getcwd"
    self_receiver: bool = False  # self.x / this.x / $this->x


@dataclass(frozen=True)
class ImportRef:
    """一条导入语句。"""

    module: str  # "os" / "./utils" / "github.com/x/y"
    start: tuple[int, int]
    names: tuple[str, ...] = ()  # from x import a, b → ("a","b")
    alias: str | None = None  # import x as y → "y"
    level: int = 0  # 相对导入层级（Python 的前导点）


@dataclass
class FileExtraction:
    """一个文件的抽取结果（worker → 主进程）。"""

    file: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Extractor(Protocol):
    """语言抽取器协议。"""

    language: str
    aliases: tuple[str, ...]

    def extract(self, ctx: FileContext) -> FileExtraction: ...


class FileContext:
    """传给 extractor 的只读上下文（封装 tree-sitter 访问，避免各语言各写一套）。"""

    __slots__ = ("index", "language", "parsed", "path", "root", "source", "text")

    def __init__(self, path: str, language: str, parsed: ParsedSource) -> None:
        self.path = path
        self.language = language
        self.source = parsed.source
        self.text = parsed.source.decode("utf-8", "replace")
        self.parsed = parsed
        self.root = parsed.root
        self.index = parsed.index

    # ── 节点访问（都走 tscompat）────────────────────────────────────
    def type(self, node: Any) -> str:
        return ts.node_type(node)

    def children(self, node: Any) -> list[Any]:
        return ts.children(node)

    def named_children(self, node: Any) -> list[Any]:
        return [child for child in ts.children(node) if ts.is_named(child)]

    def field(self, node: Any, name: str) -> Any | None:
        return ts.child_by_field_name(node, name)

    def text_of(self, node: Any) -> str:
        return ts.node_text(self.source, node)

    def start(self, node: Any) -> tuple[int, int]:
        return self.index.point(ts.start_byte(node))

    def end(self, node: Any) -> tuple[int, int]:
        return self.index.point(ts.end_byte(node))

    def walk(self, node: Any | None = None) -> list[Any]:
        """前序 DFS（返回列表，便于多次遍历）。"""
        return list(ts.iter_nodes(node or self.root))

    def docstring_of(self, node: Any, *, field_name: str = "body") -> str | None:
        """取函数/类的第一段字符串字面量作为 doc（语言无关的粗略实现）。"""
        body = self.field(node, field_name)
        if body is None:
            return None
        for child in self.children(body):
            if not ts.is_named(child):
                continue
            if self.type(child) in ("expression_statement", "comment"):
                inner = self.named_children(child)
                if inner and self.type(inner[0]) == "string":
                    return _clean_doc(self.text_of(inner[0]))
            return None
        return None


def _clean_doc(raw: str, *, limit: int = 400) -> str:
    text = raw.strip()
    for quote in ('"""', "'''", '"', "'", "`"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            text = text[len(quote) : -len(quote)]
            break
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


#: 常见内建/全局名：不算"未解析调用"，避免图谱被 print/len/os 噪声淹没
LANGUAGE_BUILTIN_GLOBALS: dict[str, frozenset[str]] = {
    "python": frozenset(
        [
            "abs",
            "all",
            "any",
            "ascii",
            "bin",
            "bool",
            "breakpoint",
            "bytearray",
            "bytes",
            "callable",
            "chr",
            "classmethod",
            "compile",
            "complex",
            "delattr",
            "dict",
            "dir",
            "divmod",
            "enumerate",
            "eval",
            "exec",
            "filter",
            "float",
            "format",
            "frozenset",
            "getattr",
            "globals",
            "hasattr",
            "hash",
            "help",
            "hex",
            "id",
            "input",
            "int",
            "isinstance",
            "issubclass",
            "iter",
            "len",
            "list",
            "locals",
            "map",
            "max",
            "memoryview",
            "min",
            "next",
            "object",
            "oct",
            "open",
            "ord",
            "pow",
            "print",
            "property",
            "range",
            "repr",
            "reversed",
            "round",
            "set",
            "setattr",
            "slice",
            "sorted",
            "staticmethod",
            "str",
            "sum",
            "super",
            "tuple",
            "type",
            "vars",
            "zip",
            "__import__",
            "self",
            "cls",
            "Exception",
            "BaseException",
            "ValueError",
            "TypeError",
            "KeyError",
            "IndexError",
            "AttributeError",
            "RuntimeError",
            "StopIteration",
            "OSError",
            "NotImplementedError",
            "NotImplemented",
            "Ellipsis",
            "None",
            "True",
            "False",
        ]
    ),
    "javascript": frozenset(
        [
            "console",
            "JSON",
            "Object",
            "Array",
            "Math",
            "Promise",
            "String",
            "Number",
            "Boolean",
            "Symbol",
            "Date",
            "RegExp",
            "Error",
            "Map",
            "Set",
            "WeakMap",
            "WeakSet",
            "parseInt",
            "parseFloat",
            "isNaN",
            "isFinite",
            "encodeURIComponent",
            "decodeURIComponent",
            "require",
            "module",
            "exports",
            "globalThis",
            "window",
            "document",
            "setTimeout",
            "setInterval",
            "clearTimeout",
            "clearInterval",
            "fetch",
            "this",
            "undefined",
            "null",
            "NaN",
            "Infinity",
        ]
    ),
    "typescript": frozenset(
        [
            "console",
            "JSON",
            "Object",
            "Array",
            "Math",
            "Promise",
            "String",
            "Number",
            "Boolean",
            "Symbol",
            "Date",
            "RegExp",
            "Error",
            "Map",
            "Set",
            "WeakMap",
            "WeakSet",
            "parseInt",
            "parseFloat",
            "isNaN",
            "isFinite",
            "encodeURIComponent",
            "decodeURIComponent",
            "require",
            "module",
            "exports",
            "globalThis",
            "window",
            "document",
            "setTimeout",
            "setInterval",
            "clearTimeout",
            "clearInterval",
            "fetch",
            "this",
            "undefined",
            "null",
            "NaN",
            "Infinity",
            "Partial",
            "Record",
            "Pick",
            "Omit",
            "Array",
            "Promise",
        ]
    ),
    "tsx": frozenset(),
    "go": frozenset(
        [
            "make",
            "len",
            "cap",
            "append",
            "panic",
            "recover",
            "new",
            "delete",
            "copy",
            "close",
            "print",
            "println",
            "complex",
            "real",
            "imag",
            "int",
            "int8",
            "int16",
            "int32",
            "int64",
            "uint",
            "uint8",
            "uint16",
            "uint32",
            "uint64",
            "uintptr",
            "float32",
            "float64",
            "string",
            "bool",
            "byte",
            "rune",
            "error",
            "any",
            "nil",
            "true",
            "false",
            "iota",
            "min",
            "max",
            "clear",
        ]
    ),
    "java": frozenset(
        [
            "System",
            "String",
            "Integer",
            "Long",
            "Double",
            "Float",
            "Boolean",
            "Character",
            "Object",
            "Math",
            "Thread",
            "Exception",
            "RuntimeException",
            "IllegalArgumentException",
            "NullPointerException",
            "List",
            "Map",
            "Set",
            "ArrayList",
            "HashMap",
            "HashSet",
            "Optional",
            "Stream",
            "StringBuilder",
            "Objects",
            "Arrays",
            "Collections",
            "this",
            "super",
            "null",
            "true",
            "false",
        ]
    ),
    "c_sharp": frozenset(
        [
            "Console",
            "String",
            "Int32",
            "Int64",
            "Double",
            "Boolean",
            "Object",
            "Math",
            "Task",
            "List",
            "Dictionary",
            "IEnumerable",
            "Exception",
            "this",
            "base",
            "null",
            "true",
            "false",
            "var",
        ]
    ),
    "rust": frozenset(
        [
            "println",
            "print",
            "format",
            "vec",
            "panic",
            "assert",
            "assert_eq",
            "Some",
            "None",
            "Ok",
            "Err",
            "String",
            "Vec",
            "Box",
            "Option",
            "Result",
            "self",
            "Self",
            "std",
            "core",
            "alloc",
        ]
    ),
    "ruby": frozenset(
        [
            "puts",
            "print",
            "require",
            "attr_accessor",
            "attr_reader",
            "attr_writer",
            "new",
            "self",
            "nil",
            "true",
            "false",
            "raise",
        ]
    ),
    "php": frozenset(
        [
            "echo",
            "print",
            "isset",
            "unset",
            "empty",
            "array",
            "list",
            "new",
            "clone",
            "self",
            "parent",
            "static",
            "null",
            "true",
            "false",
        ]
    ),
}


def builtin_globals(language: str) -> frozenset[str]:
    if language == "tsx":
        return LANGUAGE_BUILTIN_GLOBALS["typescript"]
    return LANGUAGE_BUILTIN_GLOBALS.get(language, frozenset())


def extraction_to_dict(extraction: FileExtraction) -> dict[str, Any]:
    """把抽取结果序列化成可存库的 dict（增量索引复用未变更文件的抽取）。"""
    return {
        "file": extraction.file,
        "language": extraction.language,
        "errors": list(extraction.errors),
        "symbols": [
            {
                "kind": symbol.kind,
                "name": symbol.name,
                "qualified": symbol.qualified,
                "start": list(symbol.start),
                "end": list(symbol.end),
                "parent": symbol.parent,
                "doc": symbol.doc,
                "extra": symbol.extra,
            }
            for symbol in extraction.symbols
        ],
        "references": [
            {
                "name": ref.name,
                "kind": ref.kind,
                "start": list(ref.start),
                "end": list(ref.end),
                "scope": ref.scope,
                "receiver": ref.receiver,
                "full": ref.full,
                "self_receiver": ref.self_receiver,
            }
            for ref in extraction.references
        ],
        "imports": [
            {
                "module": imp.module,
                "start": list(imp.start),
                "names": list(imp.names),
                "alias": imp.alias,
                "level": imp.level,
            }
            for imp in extraction.imports
        ],
    }


def extraction_from_dict(data: dict[str, Any]) -> FileExtraction:
    """从库里读回抽取结果（与 ``extraction_to_dict`` 严格对称）。"""
    return FileExtraction(
        file=str(data.get("file", "")),
        language=str(data.get("language", "unknown")),
        errors=list(data.get("errors") or []),
        symbols=[
            Symbol(
                kind=str(item["kind"]),
                name=str(item["name"]),
                qualified=str(item["qualified"]),
                start=(int(item["start"][0]), int(item["start"][1])),
                end=(int(item["end"][0]), int(item["end"][1])),
                parent=item.get("parent"),
                doc=item.get("doc"),
                extra=dict(item.get("extra") or {}),
            )
            for item in data.get("symbols") or []
        ],
        references=[
            Reference(
                name=str(item["name"]),
                kind=str(item["kind"]),
                start=(int(item["start"][0]), int(item["start"][1])),
                end=(int(item["end"][0]), int(item["end"][1])),
                scope=item.get("scope"),
                receiver=item.get("receiver"),
                full=item.get("full"),
                self_receiver=bool(item.get("self_receiver", False)),
            )
            for item in data.get("references") or []
        ],
        imports=[
            ImportRef(
                module=str(item["module"]),
                start=(int(item["start"][0]), int(item["start"][1])),
                names=tuple(str(name) for name in item.get("names") or ()),
                alias=item.get("alias"),
                level=int(item.get("level", 0)),
            )
            for item in data.get("imports") or []
        ],
    )


#: 语言无关的"看起来像类型"的名字（用于 instantiates 归类）
TYPE_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
