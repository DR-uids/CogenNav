"""Java 抽取器：符号 / 引用 / 导入（单文件、纯语法）。

结构照 ``python.py``：一次递归下行同时维护 ``scope``（父符号 qualified）与
``scope_kind``（module/class/function）。实测 tree-sitter-java 0.23 的几处要点：

- 嵌套类没有"限定名"节点，必须靠下行时的 ``scope`` 拼出 ``Outer.Inner.method``；
- 字段声明的名字藏在 ``variable_declarator`` 里（``field_declaration`` 本身只有
  ``modifiers`` / 类型 / ``variable_declarator``），``modifiers`` 的文本形如 ``static final``；
- ``extends`` / ``implements`` 不是独立语句，而是 ``class_declaration`` 上的
  ``superclass`` / ``super_interfaces`` 两个字段（``implements`` 里还有一层 ``type_list``）；
- ``import java.util.*;`` 的 ``*`` 是 ``asterisk`` 节点，与 dotted 路径平级；
- 构造函数在 CST 里叫 ``constructor_declaration``，用类名当方法名。
"""

from __future__ import annotations

from .base import Extractor, FileContext, FileExtraction, ImportRef, Reference, Symbol

#: 一个文件最多记多少条字段/参数类型引用（防止大文件噪声）
MAX_TYPE_USES_PER_FILE = 300

#: 直接建符号的声明节点类型 → (kind, 名字取法)
TYPE_DECL_KINDS: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "struct",
}

#: 能进入"类体"的节点类型（决定 scope_kind 是 class 还是 function）
TYPE_BODY_TYPES = ("class_body", "interface_body", "enum_body")


class JavaExtractor(Extractor):
    language = "java"
    aliases: tuple[str, ...] = ()

    def extract(self, ctx: FileContext) -> FileExtraction:
        out = FileExtraction(file=ctx.path, language=self.language)
        counters: dict[tuple[str, str], int] = {}
        type_uses = 0

        def add_symbol(
            node: object,
            kind: str,
            name: str,
            parent: str | None,
            *,
            qualified: str | None = None,
            doc: str | None = None,
        ) -> str:
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
                    doc=doc,
                    extra={"ordinal": ordinal},
                )
            )
            return full

        def add_type_uses(node: object | None, scope: str | None) -> None:
            """收集一段子树里出现的 ``type_identifier``（字段类型 / 参数 / 返回值）。"""
            nonlocal type_uses
            if node is None:
                return
            for child in ctx.walk(node):
                if ctx.type(child) != "type_identifier":
                    continue
                if type_uses >= MAX_TYPE_USES_PER_FILE:
                    return
                name = ctx.text_of(child).split(".")[-1]
                if not name:
                    continue
                type_uses += 1
                out.references.append(
                    Reference(
                        name=name,
                        kind="type_uses",
                        start=ctx.start(child),
                        end=ctx.end(child),
                        scope=scope,
                        full=ctx.text_of(child),
                    )
                )

        def handle_import(node: object) -> None:
            """``import a.b.C;`` → module=a.b.C；``import a.b.*;`` → module=a.b, names=("*",)。"""
            raw = ctx.text_of(node).strip().rstrip(";").strip()
            for prefix in ("import static ", "import "):
                if raw.startswith(prefix):
                    raw = raw[len(prefix) :].strip()
                    break
            if not raw:
                return
            names: tuple[str, ...] = ()
            module = raw
            if raw.endswith(".*"):
                module = raw[:-2].strip()
                names = ("*",)
            if module:
                out.imports.append(ImportRef(module=module, start=ctx.start(node), names=names))

        def handle_type_decl(node: object, scope: str | None, kind: str) -> None:
            name_node = ctx.field(node, "name")
            name = ctx.text_of(name_node) if name_node is not None else "?"
            qualified = add_symbol(node, kind, name, scope, doc=ctx.docstring_of(node))
            # extends
            parent = ctx.field(node, "superclass")
            if parent is not None:
                for child in ctx.named_children(parent):
                    if (
                        ctx.type(child).endswith("identifier")
                        or ctx.type(child) == "type_identifier"
                    ):
                        out.references.append(
                            Reference(
                                name=ctx.text_of(child).split(".")[-1],
                                kind="extends",
                                start=ctx.start(child),
                                end=ctx.end(child),
                                scope=qualified,
                                full=ctx.text_of(child),
                            )
                        )
            # implements（⚠️ 字段名是 ``interfaces``，不是节点类型 ``super_interfaces``）
            interfaces = ctx.field(node, "interfaces")
            if interfaces is None:
                interfaces = ctx.field(node, "super_interfaces")
            if interfaces is not None:
                for item in ctx.walk(interfaces):
                    if ctx.type(item) != "type_identifier":
                        continue
                    out.references.append(
                        Reference(
                            name=ctx.text_of(item).split(".")[-1],
                            kind="implements",
                            start=ctx.start(item),
                            end=ctx.end(item),
                            scope=qualified,
                            full=ctx.text_of(item),
                        )
                    )
            body = ctx.field(node, "body")
            for child in ctx.children(body) if body is not None else []:
                visit(child, qualified, "class")

        def handle_method(node: object, scope: str | None, fallback_name: str) -> None:
            name_node = ctx.field(node, "name")
            name = ctx.text_of(name_node) if name_node is not None else fallback_name
            qualified = add_symbol(node, "method", name, scope, doc=ctx.docstring_of(node))
            add_type_uses(ctx.field(node, "type"), qualified)
            add_type_uses(ctx.field(node, "parameters"), qualified)
            body = ctx.field(node, "body")
            for child in ctx.children(body) if body is not None else []:
                visit(child, qualified, "function")

        def handle_field(node: object, scope: str | None) -> None:
            # ⚠️ ``modifiers`` 这个具名字段在本版本语法里取不到（``child_by_field_name`` 返回
            # None），必须按节点类型扫所有子节点（含未具名节点）的文本。
            modifier_text = ""
            for child in ctx.children(node):
                if ctx.type(child) == "modifiers":
                    modifier_text = ctx.text_of(child)
                    break
            is_static_final = "static" in modifier_text and "final" in modifier_text
            add_type_uses(ctx.field(node, "type"), scope)
            for declarator in ctx.children(node):
                if ctx.type(declarator) != "variable_declarator":
                    continue
                name_node = ctx.field(declarator, "name")
                if name_node is None:
                    continue
                name = ctx.text_of(name_node)
                if not name:
                    continue
                kind = "constant" if (is_static_final and name.isupper()) else "variable"
                add_symbol(declarator, kind, name, scope)

        def add_call(node: object, scope: str | None) -> None:
            name_node = ctx.field(node, "name")
            if name_node is None:
                return
            name = ctx.text_of(name_node)
            if not name:
                return
            obj = ctx.field(node, "object")
            receiver = ctx.text_of(obj) if obj is not None else None
            out.references.append(
                Reference(
                    name=name,
                    kind="call",
                    start=ctx.start(node),
                    end=ctx.end(node),
                    scope=scope,
                    receiver=receiver,
                    full=ctx.text_of(node) if receiver else name,
                    self_receiver=receiver in ("this", "super"),
                )
            )

        def add_instantiation(node: object, scope: str | None) -> None:
            type_node = ctx.field(node, "type")
            if type_node is None:
                return
            full = ctx.text_of(type_node).strip()
            name = full.split("<")[0].split(".")[-1].strip()
            if not name:
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

        def visit(node: object, scope: str | None, scope_kind: str) -> None:
            node_type = ctx.type(node)

            if node_type == "import_declaration":
                handle_import(node)
                return

            if node_type in TYPE_DECL_KINDS:
                handle_type_decl(node, scope, TYPE_DECL_KINDS[node_type])
                return

            if node_type == "method_declaration":
                handle_method(node, scope, "?")
                return

            if node_type == "constructor_declaration":
                name_node = ctx.field(node, "name")
                fallback = ctx.text_of(name_node) if name_node is not None else "?"
                handle_method(node, scope, fallback)
                return

            if node_type == "field_declaration":
                if scope_kind == "class":
                    handle_field(node, scope)
                return

            if node_type == "method_invocation":
                add_call(node, scope)
            elif node_type == "object_creation_expression":
                add_instantiation(node, scope)

            for child in ctx.children(node):
                visit(child, scope, scope_kind)

        visit(ctx.root, None, "module")
        return out


EXTRACTOR = JavaExtractor()

__all__ = ["EXTRACTOR", "JavaExtractor"]
