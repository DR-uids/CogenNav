"""Python 抽取器（参考实现：其余语言的 extractor 按同样结构写）。

单文件、纯语法：符号 / 引用 / 导入。跨文件绑定在 ``resolve_calls.py``。
作用域用一次递归下行同时维护（scope = 最近的类或函数 qualified），
因此嵌套函数、方法、类级变量都能拿到正确的 ``parent`` 与行列。
"""

from __future__ import annotations

from ..parse.tscompat import same_node
from .base import Extractor, FileContext, FileExtraction, ImportRef, Reference, Symbol

#: 模块级/类级变量最多记录多少个（防止大文件里变量噪声淹没图谱）
MAX_VARIABLES_PER_FILE = 200


class PythonExtractor(Extractor):
    language = "python"
    aliases: tuple[str, ...] = ()

    def extract(self, ctx: FileContext) -> FileExtraction:
        out = FileExtraction(file=ctx.path, language=self.language)
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

        def add_reference(node: object, kind: str, scope: str | None) -> None:
            fn = ctx.field(node, "function")
            if fn is None:
                return
            type_name = ctx.type(fn)
            if type_name == "identifier":
                name = ctx.text_of(fn)
                if not name:
                    return
                out.references.append(
                    Reference(
                        name=name,
                        kind=kind,
                        start=ctx.start(fn),
                        end=ctx.end(fn),
                        scope=scope,
                        full=name,
                    )
                )
            elif type_name == "attribute":
                obj = ctx.field(fn, "object")
                attr = ctx.field(fn, "attribute")
                if attr is None:
                    return
                name = ctx.text_of(attr)
                receiver = ctx.text_of(obj) if obj is not None else None
                out.references.append(
                    Reference(
                        name=name,
                        kind=kind,
                        start=ctx.start(fn),
                        end=ctx.end(fn),
                        scope=scope,
                        receiver=receiver,
                        full=ctx.text_of(fn),
                        self_receiver=receiver in ("self", "cls"),
                    )
                )

        def handle_import(node: object) -> None:
            for child in ctx.named_children(node):
                if ctx.type(child) == "aliased_import":
                    name_node = ctx.field(child, "name")
                    alias_node = ctx.field(child, "alias")
                    if name_node is None:
                        continue
                    out.imports.append(
                        ImportRef(
                            module=ctx.text_of(name_node),
                            start=ctx.start(node),
                            alias=ctx.text_of(alias_node) if alias_node is not None else None,
                        )
                    )
                else:
                    out.imports.append(ImportRef(module=ctx.text_of(child), start=ctx.start(node)))

        def handle_import_from(node: object) -> None:
            module_node = ctx.field(node, "module_name")
            level = 0
            module = ""
            if module_node is not None:
                if ctx.type(module_node) == "relative_import":
                    raw = ctx.text_of(module_node)
                    dots = len(raw) - len(raw.lstrip("."))
                    level = dots
                    module = raw[dots:]
                else:
                    module = ctx.text_of(module_node)

            names: list[str] = []
            alias: str | None = None
            for child in ctx.named_children(node):
                child_type = ctx.type(child)
                if same_node(child, module_node):
                    continue
                if child_type == "dotted_name":
                    names.append(ctx.text_of(child).split(".")[-1])
                elif child_type == "aliased_import":
                    name_node = ctx.field(child, "name")
                    alias_node = ctx.field(child, "alias")
                    if name_node is not None:
                        names.append(ctx.text_of(name_node).split(".")[-1])
                    if alias_node is not None and len(names) == 1:
                        alias = ctx.text_of(alias_node)
                elif child_type == "wildcard_import":
                    names.append("*")
            out.imports.append(
                ImportRef(
                    module=module,
                    start=ctx.start(node),
                    names=tuple(names),
                    alias=alias,
                    level=level,
                )
            )

        def visit(node: object, scope: str, class_scope: str, scope_kind: str) -> None:
            nonlocal variable_count
            node_type = ctx.type(node)

            if node_type == "decorated_definition":
                inner = ctx.field(node, "definition")
                if inner is not None:
                    visit(inner, scope, class_scope, scope_kind)
                return

            if node_type == "class_definition":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                qualified = add_symbol(node, "class", name, scope or None, ctx.docstring_of(node))
                supers = ctx.field(node, "superclasses")
                if supers is not None:
                    for arg in ctx.named_children(supers):
                        if ctx.type(arg) == "keyword_argument":
                            continue
                        full = ctx.text_of(arg)
                        base = full.split("[")[0].strip()
                        if not base:
                            continue
                        parts = base.split(".")
                        out.references.append(
                            Reference(
                                name=parts[-1],
                                kind="extends",
                                start=ctx.start(arg),
                                end=ctx.end(arg),
                                scope=qualified,
                                receiver=".".join(parts[:-1]) or None,
                                full=base,
                            )
                        )
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, qualified, "class")
                return

            if node_type == "function_definition":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                kind = "method" if class_scope else "function"
                qualified = add_symbol(node, kind, name, scope or None, ctx.docstring_of(node))
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, class_scope, "function")
                return

            if node_type == "import_statement":
                handle_import(node)
                return
            if node_type == "import_from_statement":
                handle_import_from(node)
                return

            if node_type == "expression_statement":
                inner = ctx.named_children(node)
                if (
                    inner
                    and ctx.type(inner[0]) == "assignment"
                    and scope_kind in ("module", "class")
                ):
                    visit(inner[0], scope, class_scope, scope_kind)
                return

            if node_type == "assignment" and scope_kind in ("module", "class"):
                left = ctx.field(node, "left")
                if (
                    left is not None
                    and ctx.type(left) == "identifier"
                    and (variable_count < MAX_VARIABLES_PER_FILE)
                ):
                    variable_count += 1
                    name = ctx.text_of(left)
                    add_symbol(
                        left,
                        "constant" if name.isupper() else "variable",
                        name,
                        scope or None,
                    )
                return

            if node_type == "call":
                add_reference(node, "call", scope or None)

            for child in ctx.children(node):
                visit(child, scope, class_scope, scope_kind)

        visit(ctx.root, "", "", "module")
        return out


EXTRACTOR = PythonExtractor()

__all__ = ["EXTRACTOR", "PythonExtractor"]
