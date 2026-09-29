"""Go 抽取器：符号 / 引用 / 导入（单文件、纯语法）。

结构照 ``python.py``：一次递归下行同时维护 ``scope`` 与其种类 ``scope_kind``。
Go 相比 Python 的几处语法差异（实测 tree-sitter-go 0.25 的 CST）：

- 方法用 ``method_declaration``，名字与接收者类型是**两个不同字段**
  （``name`` = ``field_identifier``，``receiver`` = ``parameter_list``），
  因此 ``s.Serve()`` 要先从 ``(s *Server)`` 里剥出 ``Server`` 才能得到 ``Server.Serve``；
- 类型声明外层是 ``type_declaration``，内层才是 ``type_spec``
  （``type Alias = int`` 的内层节点叫 ``type_alias``，名字字段同样是 ``name``）；
- 接口里的方法签名是 ``method_elem``，不能当成 ``method`` 符号；
- ``import_spec`` **没有** ``path`` / ``name`` 这些具名字段，
  别名与路径是两个无名子节点（``f "os"`` → ``package_identifier`` + ``interpreted_string_literal``）；
- ``const A, B = 1, 2`` 的 ``name`` 字段只给出**最后一个**标识符，
  多名字要自己扫 ``name`` 之前的 ``identifier`` 兄弟节点。
"""

from __future__ import annotations

from .base import Extractor, FileContext, FileExtraction, ImportRef, Reference, Symbol

#: 参数 / 返回值 / 字段位置上的类型引用最多记多少条（防止大文件噪声淹掉图谱）
MAX_TYPE_USES_PER_FILE = 300

#: ``type_spec`` 右侧节点类型 → 符号 kind
TYPE_SPEC_KINDS: dict[str, str] = {
    "struct_type": "struct",
    "interface_type": "interface",
}


def _last_segment(text: str) -> str:
    """``pkg.Sub`` → ``Sub``；``*Server`` → ``Server``；``[]int`` → ``int``。"""
    return text.strip().lstrip("*").replace("[]", "").strip().split(".")[-1].strip()


def _strip_pointer(text: str) -> str:
    return text.strip().lstrip("*").strip()


class GoExtractor(Extractor):
    language = "go"
    aliases: tuple[str, ...] = ()

    def extract(self, ctx: FileContext) -> FileExtraction:
        out = FileExtraction(file=ctx.path, language=self.language)
        counters: dict[tuple[str, str], int] = {}
        type_uses = 0

        def add_symbol(
            node: object, kind: str, name: str, parent: str | None, qualified: str | None = None
        ) -> str:
            """登记符号；``qualified`` 显式给出时用于方法的 ``接收者.方法名``。"""
            name = name or "?"
            full = qualified or (f"{parent}.{name}" if parent else name)
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

        def add_type_use(node: object, scope: str | None) -> None:
            """参数 / 返回值 / 字段位置上的 ``type_identifier`` 记一条 ``type_uses``。"""
            nonlocal type_uses
            if type_uses >= MAX_TYPE_USES_PER_FILE:
                return
            full = _strip_pointer(ctx.text_of(node))
            name = _last_segment(full)
            if not name or not name[0].isalpha():
                return
            type_uses += 1
            out.references.append(
                Reference(
                    name=name,
                    kind="type_uses",
                    start=ctx.start(node),
                    end=ctx.end(node),
                    scope=scope,
                    full=full,
                )
            )

        def receiver_type(node: object) -> str | None:
            """从 ``method_declaration`` 的 ``receiver`` 里取接收者类型名。"""
            recv = ctx.field(node, "receiver")
            if recv is None:
                return None
            for child in ctx.walk(recv):
                if ctx.type(child) == "type_identifier":
                    name = ctx.text_of(child)
                    if name:
                        return name
            return None

        def signature_types(node: object, scope: str | None) -> None:
            """扫描参数表与返回值里出现的类型名。"""
            for field_name in ("parameters", "result"):
                sub = ctx.field(node, field_name)
                if sub is None:
                    continue
                for child in ctx.walk(sub):
                    if ctx.type(child) == "type_identifier":
                        add_type_use(child, scope)

        def handle_import(node: object) -> None:
            for spec in ctx.walk(node):
                if ctx.type(spec) != "import_spec":
                    continue
                alias: str | None = None
                module = ""
                for child in ctx.named_children(spec):
                    child_type = ctx.type(child)
                    if child_type == "interpreted_string_literal":
                        raw = ctx.text_of(child).strip()
                        module = raw[1:-1] if len(raw) >= 2 else raw
                    elif child_type in ("package_identifier", "dot", "blank_identifier"):
                        alias = ctx.text_of(child)
                if module:
                    out.imports.append(ImportRef(module=module, start=ctx.start(node), alias=alias))

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
            elif fn_type == "selector_expression":
                operand = ctx.field(fn, "operand")
                field = ctx.field(fn, "field")
                if field is None:
                    return
                receiver = ctx.text_of(operand) if operand is not None else None
                out.references.append(
                    Reference(
                        name=ctx.text_of(field),
                        kind="call",
                        start=ctx.start(fn),
                        end=ctx.end(fn),
                        scope=scope,
                        receiver=receiver,
                        full=ctx.text_of(fn),
                        self_receiver=receiver in ("s", "self"),
                    )
                )

        def add_composite(node: object, scope: str | None) -> None:
            """``T{...}`` / ``&pkg.T{...}`` → instantiates。"""
            type_node = ctx.field(node, "type")
            if type_node is None:
                return
            full = _strip_pointer(ctx.text_of(type_node))
            name = _last_segment(full)
            if not name or not name[0].isalpha():
                return
            receiver = ".".join(full.split(".")[:-1]) or None
            out.references.append(
                Reference(
                    name=name,
                    kind="instantiates",
                    start=ctx.start(type_node),
                    end=ctx.end(type_node),
                    scope=scope,
                    receiver=receiver,
                    full=full,
                )
            )

        def handle_value_declaration(node: object, scope: str | None) -> None:
            """``const`` / ``var``：只记顶层（模块级）声明，多名字也要全部记上。"""
            is_const = ctx.type(node) == "const_declaration"
            spec_type = "const_spec" if is_const else "var_spec"
            for spec in ctx.walk(node):
                if ctx.type(spec) != spec_type:
                    continue
                for child in ctx.named_children(spec):
                    if ctx.type(child) != "identifier":
                        continue
                    name = ctx.text_of(child)
                    if not name:
                        continue
                    add_symbol(
                        child,
                        "constant" if is_const or name.isupper() else "variable",
                        name,
                        scope,
                    )

        def visit(node: object, scope: str | None, scope_kind: str) -> None:
            node_type = ctx.type(node)

            if node_type == "import_declaration":
                handle_import(node)
                return

            if node_type == "function_declaration":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                qualified = add_symbol(node, "function", name, scope)
                signature_types(node, qualified)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, "function")
                return

            if node_type == "method_declaration":
                name_node = ctx.field(node, "name")
                name = ctx.text_of(name_node) if name_node is not None else "?"
                recv = receiver_type(node)
                qualified = f"{recv}.{name}" if recv else (scope or name)
                add_symbol(node, "method", name, recv, qualified=qualified)
                signature_types(node, qualified)
                body = ctx.field(node, "body")
                for child in ctx.children(body) if body is not None else []:
                    visit(child, qualified, "function")
                return

            if node_type == "type_declaration":
                for spec in ctx.named_children(node):
                    if ctx.type(spec) not in ("type_spec", "type_alias"):
                        continue
                    name_node = ctx.field(spec, "name")
                    name = ctx.text_of(name_node) if name_node is not None else "?"
                    right = ctx.field(spec, "type")
                    kind = TYPE_SPEC_KINDS.get(ctx.type(right) if right is not None else "", "type")
                    qualified = add_symbol(spec, kind, name, scope)
                    if right is not None:
                        for child in ctx.walk(right):
                            if ctx.type(child) == "type_identifier":
                                add_type_use(child, qualified)
                return

            if node_type in ("const_declaration", "var_declaration") and scope_kind == "module":
                handle_value_declaration(node, scope)
                return

            if node_type == "call_expression":
                add_call(node, scope)
            elif node_type == "composite_literal":
                add_composite(node, scope)
            elif node_type == "type_identifier" and scope_kind == "module":
                add_type_use(node, scope)

            for child in ctx.children(node):
                visit(child, scope, scope_kind)

        visit(ctx.root, None, "module")
        return out


EXTRACTOR = GoExtractor()

__all__ = ["EXTRACTOR", "GoExtractor"]
