"""TypeScript / TSX / JavaScript 抽取器。

三种语言共用同一套节点类型（tree-sitter-typescript 是 JS 语法的超集，
``language_tsx`` 只是额外多了 JSX），所以一个 extractor 服务 ``typescript``/``tsx``/
``javascript`` 三个语言 id（靠 ``aliases`` 登记，见 ``registry.register``）。
"""

from __future__ import annotations

from ..parse import tscompat as ts
from .base import Extractor, FileContext, FileExtraction, ImportRef, Reference, Symbol

MAX_VARIABLES_PER_FILE = 200

_FUNCTION_LIKE = {"arrow_function", "function_expression", "function"}


class TypeScriptExtractor(Extractor):
    language = "typescript"
    aliases: tuple[str, ...] = ("tsx", "javascript")

    def extract(self, ctx: FileContext) -> FileExtraction:
        out = FileExtraction(file=ctx.path, language=ctx.language)
        counters: dict[tuple[str, str], int] = {}
        variable_count = 0

        def add_symbol(
            node: object,
            kind: str,
            name: str,
            parent: str | None,
            doc: str | None = None,
        ) -> str:
            qualified = f"{parent}.{name}" if parent else name
            key = (qualified, kind)
            ordinal = counters.get(key, 0)
            counters[key] = ordinal + 1
            out.symbols.append(
                Symbol(
                    kind=kind,
                    name=name,
                    qualified=qualified,
                    start=ctx.start(node),
                    end=ctx.end(node),
                    parent=parent,
                    doc=doc,
                    extra={"ordinal": ordinal},
                )
            )
            return qualified

        def add_call(node: object, scope: str | None) -> None:
            fn = ctx.field(node, "function")
            if fn is None:
                return
            fn_type = ctx.type(fn)
            if fn_type == "identifier":
                name = ctx.text_of(fn)
                if name:
                    out.references.append(
                        Reference(
                            name=name,
                            kind="call",
                            start=ctx.start(fn),
                            end=ctx.end(fn),
                            scope=scope,
                            full=name,
                        )
                    )
            elif fn_type == "member_expression":
                obj = ctx.field(fn, "object")
                prop = ctx.field(fn, "property")
                if prop is None:
                    return
                name = ctx.text_of(prop)
                receiver = ctx.text_of(obj) if obj is not None else None
                out.references.append(
                    Reference(
                        name=name,
                        kind="call",
                        start=ctx.start(fn),
                        end=ctx.end(fn),
                        scope=scope,
                        receiver=receiver,
                        full=ctx.text_of(fn),
                        self_receiver=receiver == "this",
                    )
                )

        def handle_heritage(node: object, scope: str) -> None:
            # tree-sitter-typescript 把 extends/implements 包在 class_heritage 里，
            # 直接扫 class 的子节点是拿不到的（实测）
            clauses: list[object] = []
            for child in ctx.children(node):
                child_type = ctx.type(child)
                if child_type == "class_heritage":
                    clauses.extend(ctx.children(child))
                elif child_type in ("extends_clause", "implements_clause"):
                    clauses.append(child)
            for child in clauses:
                child_type = ctx.type(child)
                if child_type == "extends_clause":
                    for value in ctx.named_children(child):
                        if ctx.type(value) == "identifier":
                            out.references.append(
                                Reference(
                                    name=ctx.text_of(value),
                                    kind="extends",
                                    start=ctx.start(value),
                                    end=ctx.end(value),
                                    scope=scope,
                                    full=ctx.text_of(value),
                                )
                            )
                elif child_type == "implements_clause":
                    for value in ctx.named_children(child):
                        if ctx.type(value) in ("type_identifier", "identifier", "generic_type"):
                            text = ctx.text_of(value).split("<")[0]
                            out.references.append(
                                Reference(
                                    name=text.split(".")[-1],
                                    kind="implements",
                                    start=ctx.start(value),
                                    end=ctx.end(value),
                                    scope=scope,
                                    full=text,
                                )
                            )

        def handle_import(node: object) -> None:
            source = ctx.field(node, "source")
            if source is None:
                return
            module = ctx.text_of(source).strip("\"'`")
            names: list[str] = []
            alias: str | None = None
            for clause in ctx.named_children(node):
                if ctx.type(clause) != "import_clause":
                    continue
                for child in ctx.named_children(clause):
                    child_type = ctx.type(child)
                    if child_type == "identifier":
                        alias = ctx.text_of(child)
                        names.append(alias)
                    elif child_type == "namespace_import":
                        for inner in ctx.named_children(child):
                            if ctx.type(inner) == "identifier":
                                alias = ctx.text_of(inner)
                                names.append(alias)
                    elif child_type == "named_imports":
                        for spec in ctx.named_children(child):
                            if ctx.type(spec) == "import_specifier":
                                name_node = ctx.field(spec, "name")
                                if name_node is not None:
                                    names.append(ctx.text_of(name_node))
            out.imports.append(
                ImportRef(module=module, start=ctx.start(node), names=tuple(names), alias=alias)
            )

        def handle_variable(
            node: object, scope: str, class_scope: str, scope_kind: str, declaration: str
        ) -> None:
            """``const f = () => {}`` 记成 function，其余记 variable/constant。"""
            nonlocal variable_count
            for declarator in ctx.named_children(node):
                if ctx.type(declarator) != "variable_declarator":
                    continue
                name_node = ctx.field(declarator, "name")
                if name_node is None or ctx.type(name_node) != "identifier":
                    continue
                name = ctx.text_of(name_node)
                value = ctx.field(declarator, "value")
                value_type = ctx.type(value) if value is not None else ""
                if value_type in _FUNCTION_LIKE:
                    add_symbol(declarator, "function", name, scope or None)
                elif variable_count < MAX_VARIABLES_PER_FILE:
                    variable_count += 1
                    kind = "constant" if declaration == "const" and name.isupper() else "variable"
                    add_symbol(declarator, kind, name, scope or None)
                if value is not None:
                    # 必须继续下行：`const x = helper(...)` 里的调用不能被漏掉
                    visit(value, scope, class_scope, scope_kind)

        def visit(node: object, scope: str, class_scope: str, scope_kind: str) -> None:
            node_type = ctx.type(node)

            if node_type == "export_statement":
                for child in ctx.named_children(node):
                    visit(child, scope, class_scope, scope_kind)
                return

            if node_type == "class_declaration" or node_type == "class":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "(anonymous)"
                qualified = add_symbol(node, "class", name, scope or None)
                handle_heritage(node, qualified)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, qualified, "class")
                return

            if node_type == "interface_declaration":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                qualified = add_symbol(node, "interface", name, scope or None)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, qualified, "class")
                return

            if node_type == "enum_declaration":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                qualified = add_symbol(node, "enum", name, scope or None)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, qualified, "class")
                return

            if node_type == "type_alias_declaration":
                name_node = ctx.field(node, "name")
                if name_node is not None and ctx.type(name_node) == "type_identifier":
                    add_symbol(node, "type", ctx.text_of(name_node), scope or None)
                return

            if node_type in ("function_declaration", "generator_function_declaration"):
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "(anonymous)"
                kind = "method" if scope_kind == "class" else "function"
                qualified = add_symbol(node, kind, name, scope or None)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, class_scope, "function")
                return

            if node_type == "method_definition":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "(computed)"
                qualified = add_symbol(node, "method", name, scope or None)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, class_scope, "function")
                return

            if node_type == "public_field_definition":
                name_node = ctx.field(node, "name")
                if name_node is not None and ctx.type(name_node) in (
                    "property_identifier",
                    "identifier",
                ):
                    add_symbol(node, "variable", ctx.text_of(name_node), scope or None)
                return

            if node_type in ("lexical_declaration", "variable_declaration"):
                declaration = "let"
                for child in ctx.children(node):
                    if not ts.is_named(child) and ctx.text_of(child) in ("const", "let", "var"):
                        declaration = ctx.text_of(child)
                        break
                handle_variable(node, scope, class_scope, scope_kind, declaration)
                return

            if node_type == "call_expression":
                add_call(node, scope or None)

            if node_type == "new_expression":
                ctor = ctx.field(node, "constructor")
                if ctor is not None:
                    text = ctx.text_of(ctor).split("<")[0]
                    out.references.append(
                        Reference(
                            name=text.split(".")[-1],
                            kind="instantiates",
                            start=ctx.start(ctor),
                            end=ctx.end(ctor),
                            scope=scope or None,
                            receiver=".".join(text.split(".")[:-1]) or None,
                            full=text,
                        )
                    )

            for child in ctx.children(node):
                visit(child, scope, class_scope, scope_kind)

        # export default / 顶层语句由 visit 递归处理
        for child in ctx.named_children(ctx.root):
            if ctx.type(child) in ("import_statement", "import_alias"):
                handle_import(child)
            else:
                visit(child, "", "", "module")

        return out


EXTRACTOR = TypeScriptExtractor()

__all__ = ["EXTRACTOR", "TypeScriptExtractor"]
