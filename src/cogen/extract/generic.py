"""通用启发式抽取器：没有专用实现的语言的兜底（rust / c / cpp / c_sharp / ruby / php /
kotlin / swift / bash / json / yaml / html / css……）。

与 ``python.py`` / ``go.py`` / ``java.py`` 不同，这里**不认识任何具体节点的语义**，
只按节点类型名做模式匹配，因此三条铁律：

1. **绝不抛异常**：任何字段取不到、结构长得不像预期，最多少抽一个符号，
   最坏情况返回空的 ``FileExtraction``（语料里什么奇怪语法都可能出现）；
2. **宁可少抽也不乱抽**：变量类节点只在顶层收（否则 C# 的 ``int x = 1;`` 会把每个
   局部变量都变成符号）；类/结构体/impl 内部的"成员"节点只有看起来像函数或嵌套类型时才收，
   否则 Rust 的 ``enum Color { Red }`` 会把枚举成员 ``Red`` 也变成符号；
3. **跳过节点不等于跳过子树**：Rust 的 ``let x = load();`` 里真正的调用在 ``let`` 节点内部，
   若在"局部变量"分支直接 return，``load()`` 这条引用就丢了。

Rust 的 ``impl Config { fn new() }`` 是这套启发式最典型的用例：``impl_item`` 没有
``name`` 字段，只有 ``type`` 字段（``Config``），名字要专门回退到 ``type``。
"""

from __future__ import annotations

from .base import Extractor, FileContext, FileExtraction, ImportRef, Reference, Symbol

#: 可能成为符号的声明节点前缀（``function*`` 覆盖 function_item / function_definition /
#: function_declaration / function_statement……；``variable*`` 覆盖 variable_declaration）
SYMBOL_TYPE_PREFIXES: tuple[str, ...] = (
    "def",
    "func",
    "function",
    "method",
    "class",
    "struct",
    "enum",
    "interface",
    "trait",
    "impl",
    "module",
    "namespace",
    "protocol",
    "extension",
    "constructor",
    "object",
    "record",
    "type",
    "const",
    "variable",
    "var",
    "let",
)

#: 含这些子串的节点类型不算符号（标识符/类型注解等"名字本身"）
NON_SYMBOL_SUBSTRINGS: tuple[str, ...] = (
    "identifier",
    "annotation",
    "specifier",
    "signature",
    "declarator",
    "parameter",
    "argument",
    "reference",
    "expression",
    "literal",
    "clause",
    "pattern",
    "comment",
    "decorator",
)

#: kind 映射：命中前缀后决定符号种类（先命中先算）
KIND_PREFIXES: tuple[tuple[str, str], ...] = (
    ("interface", "interface"),
    ("struct", "struct"),
    ("class", "class"),
    ("trait", "class"),
    ("impl", "class"),
    ("enum", "enum"),
    ("const", "constant"),
    ("var", "variable"),
    ("let", "variable"),
)

#: 这些 kind 的节点内部才有"成员"（方法/嵌套类），其它容器里的节点不建符号
MEMBER_CONTAINER_KINDS = ("class", "struct", "interface")

#: 这些容器里的节点一律不建符号（枚举成员 ``Red``、trait 的 ``fn greet(&self);``）
OPAQUE_CONTAINER_KINDS = ("enum", "constant", "variable", "function", "method")

#: 能作为名字的节点类型（取不到名字就放弃建符号）
NAME_NODE_TYPES = (
    "identifier",
    "type_identifier",
    "field_identifier",
    "name",
    "simple_identifier",
    "constant",
    "word",
)


def _symbol_kind(node_type: str) -> str:
    """节点类型 → 符号 kind；映射不到时按题目要求落到 ``function``。"""
    for prefix, kind in KIND_PREFIXES:
        if node_type.startswith(prefix):
            return kind
    return "function"


def _is_symbol_type(node_type: str) -> bool:
    """节点类型是否"看起来像一个定义"（``class`` 命中 ``class_declaration``，不命中 ``class_body``）。"""
    if not node_type or any(part in node_type for part in NON_SYMBOL_SUBSTRINGS):
        return False
    return any(
        node_type == prefix or node_type.startswith(f"{prefix}_") for prefix in SYMBOL_TYPE_PREFIXES
    )


def _looks_like_name(node_type: str) -> bool:
    return node_type in NAME_NODE_TYPES or node_type.endswith("_identifier")


class GenericExtractor(Extractor):
    """语言无关的启发式抽取器；``get_extractor()`` 对未注册语言返回它。"""

    language = "generic"
    aliases: tuple[str, ...] = ()

    def extract(self, ctx: FileContext) -> FileExtraction:
        out = FileExtraction(file=ctx.path, language=self.language)
        counters: dict[tuple[str, str], int] = {}
        #: 已经作为"类型"登记过的 qualified（用于跳过 ``impl Config`` 这类同名前缀节点）
        known_types: set[str] = set()

        def lookup_name(node: object, depth: int = 0) -> str | None:
            """尽量取名字：``name`` 字段 → ``declarator``/``pattern`` → 再找字段 → 再扫子节点。"""
            if depth > 3:
                return None
            for field_name in ("name", "declarator", "pattern"):
                found = ctx.field(node, field_name)
                if found is None:
                    continue
                text = ctx.text_of(found).strip()
                if text and _looks_like_name(ctx.type(found)):
                    return text
                nested = lookup_name(found, depth + 1)
                if nested:
                    return nested
            for child in ctx.named_children(node):
                found = ctx.field(node, ctx.type(child))
                if found is not None and _looks_like_name(ctx.type(found)):
                    text = ctx.text_of(found).strip()
                    if text:
                        return text
            for child in ctx.named_children(node):
                if _looks_like_name(ctx.type(child)):
                    text = ctx.text_of(child).strip()
                    if text:
                        return text
            return None

        def add_symbol(node: object, kind: str, name: str, parent: str | None) -> str:
            full = f"{parent}.{name}" if parent else name
            key = (full, kind)
            ordinal = counters.get(key, 0)
            counters[key] = ordinal + 1
            out.symbols.append(
                Symbol(
                    kind=kind,
                    name=name,
                    qualified=full,
                    start=ctx.start(node),
                    end=ctx.end(node),
                    parent=parent,
                    extra={"ordinal": ordinal},
                )
            )
            return full

        def first_named(node: object, *type_names: str) -> object | None:
            for child in ctx.named_children(node):
                if ctx.type(child) in type_names:
                    return child
            return None

        def handle_import(node: object) -> None:
            """导入语句：字符串字面量 / ``path``·``argument`` 字段 / 第一个具名子节点当 module。"""
            candidate = first_named(
                node,
                "string_literal",
                "interpreted_string_literal",
                "raw_string_literal",
                "string",
                "system_lib_string",
                "string_content",
            )
            if candidate is None:
                for field_name in ("argument", "path", "source", "module"):
                    found = ctx.field(node, field_name)
                    if found is not None:
                        candidate = found
                        break
            if candidate is None:
                for child in ctx.walk(node):
                    if "string" in ctx.type(child):
                        candidate = child
                        break
            if candidate is not None:
                module = ctx.text_of(candidate).strip()
            else:
                module = ""
                for child in ctx.named_children(node):
                    text = ctx.text_of(child).strip()
                    if text:
                        module = text
                        break
                if not module:
                    # ``using System;`` 的模块名藏在限定名节点里
                    module = ctx.text_of(node).strip()
            module = module.strip().strip("\"'`")
            module = module.strip().lstrip("<").rstrip(">").strip()
            module = module.split(" as ")[0].strip()
            module = module.rstrip(";").strip()
            for keyword in ("using namespace ", "using ", "import ", "from "):
                if module.startswith(keyword):
                    module = module[len(keyword) :].strip()
                    break
            module = module.split(" as ")[0].strip()
            if not module:
                return
            out.imports.append(ImportRef(module=module, start=ctx.start(node)))

        def handle_reference(node: object, scope: str | None) -> None:
            """类型名含 call / invocation → 记一条 call。"""
            callee = ctx.field(node, "function")
            if callee is None:
                callee = ctx.field(node, "callee")
            if callee is None:
                callee = ctx.field(node, "constructor")
            if callee is None:
                callee = ctx.field(node, "name")
            if callee is None:
                named = ctx.named_children(node)
                callee = named[0] if named else None
            if callee is None:
                return
            callee_type = ctx.type(callee)
            name = ""
            receiver: str | None = None
            if _looks_like_name(callee_type):
                name = ctx.text_of(callee).strip()
            elif callee_type in ("field_expression", "member_expression", "attribute", "selector"):
                field = ctx.field(callee, "field")
                obj = ctx.field(callee, "object")
                if field is None:
                    field = ctx.field(callee, "attribute")
                if field is not None:
                    name = ctx.text_of(field)
                    receiver = ctx.text_of(obj) if obj is not None else None
            if not name:
                name = lookup_name(callee) or ctx.text_of(callee).strip()
            name = name.split("(")[0].strip()
            if not name or not (name[0].isalpha() or name[0] == "_"):
                return
            out.references.append(
                Reference(
                    name=name,
                    kind="call",
                    start=ctx.start(callee),
                    end=ctx.end(callee),
                    scope=scope,
                    receiver=receiver,
                    full=ctx.text_of(callee).strip() or name,
                    self_receiver=receiver in ("self", "this", "cls"),
                )
            )

        def visit(node: object, scope: str | None, scope_kind: str, is_member: bool) -> None:
            node_type = ctx.type(node)

            if scope_kind == "module":
                lowered = node_type.lower()
                if (
                    "import" in lowered
                    or "include" in lowered
                    or lowered == "use_declaration"
                    or lowered.startswith("require")
                    or lowered.endswith("require")
                ):
                    handle_import(node)
                    return

            if _is_symbol_type(node_type):
                kind = _symbol_kind(node_type)
                name = lookup_name(node)
                is_local_var = kind in ("constant", "variable") and scope_kind != "module"
                full = f"{scope}.{name}" if (scope and name) else (name or "")
                if is_member and scope_kind in OPAQUE_CONTAINER_KINDS:
                    name = None  # 枚举成员 / trait 方法签名之类：不建符号
                if name and not is_local_var:
                    if full in known_types:
                        # ``impl Config``：类型本身已经登记过，只把成员方法挂到同名 scope 下
                        for child in ctx.children(node):
                            visit(child, full, "class", is_member=True)
                        return
                    qualified = add_symbol(node, kind, name, scope)
                    if kind in ("class", "struct", "interface", "enum"):
                        known_types.add(qualified)
                    child_kind = kind if kind in MEMBER_CONTAINER_KINDS else "opaque"
                    for child in ctx.children(node):
                        visit(child, qualified, child_kind, is_member=True)
                    return

            if "call" in node_type.lower() or "invocation" in node_type.lower():
                handle_reference(node, scope)

            for child in ctx.children(node):
                visit(child, scope, scope_kind, is_member)

        try:
            visit(ctx.root, None, "module", is_member=False)
        except Exception:  # pragma: no cover - 兜底：启发式抽取绝不能让整轮索引失败
            return FileExtraction(file=ctx.path, language=self.language)
        return out


EXTRACTOR = GenericExtractor()

__all__ = ["EXTRACTOR", "GenericExtractor"]
