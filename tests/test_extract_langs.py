"""M3 语言抽取器测试：Go / Java / 通用启发式（rust、c 为代表）。

三个语言共用同一套流程：``parse_source(src, language)`` → ``FileContext`` → ``extract()``，
断言只看符号 / 引用 / 导入三类产物，不触碰 ``start_point`` / ``end_point``
（py-tree-sitter 0.26 的 ``Point`` 会破坏堆，见 ``tscompat`` 的说明）。
"""

from __future__ import annotations

import pytest

from cogen.extract.base import FileContext, FileExtraction
from cogen.extract.generic import GenericExtractor
from cogen.extract.go import GoExtractor
from cogen.extract.java import JavaExtractor
from cogen.parse.parser import parse_source

GO_SOURCE = b"""package main

import (
    "fmt"
)

const MaxN = 3

type Server struct {
    Addr string
}

func (s *Server) Serve() error {
    s.log()
    fmt.Println("serving")
    return nil
}

func main() {
    s := &Server{Addr: "127.0.0.1"}
    s.Serve()
}
"""

JAVA_SOURCE = b"""package demo;

import java.util.List;

public class A extends B implements C {
    private int x;

    A() {
        this.x = 1;
    }

    int f() {
        helper();
        return new A();
    }
}
"""

RUST_SOURCE = b"""use std::collections::HashMap;

fn main() {
    let c = Config::new();
    let x = load();
    println!("{}", x);
}

struct Config {
    name: String,
}

impl Config {
    fn new() -> Self {
        Self { name: String::new() }
    }
}
"""

C_SOURCE = b"""#include <stdio.h>

int add(int a, int b);

int add(int a, int b) {
    return a + b;
}
"""

#: 健壮性用例：故意语法错误 / 截断，抽取器只要求不抛异常
BROKEN_SNIPPETS: list[tuple[str, str, bytes]] = [
    ("go", "a.go", b"func (s *Server"),
    ("java", "A.java", b"class A { void f("),
    ("rust", "a.rs", b"struct {"),
]


def extract(language: str, path: str, source: bytes, extractor: object) -> FileExtraction:
    ctx = FileContext(path, language, parse_source(source, language))
    return extractor.extract(ctx)  # type: ignore[attr-defined]


def go_extract() -> FileExtraction:
    return extract("go", "a.go", GO_SOURCE, GoExtractor())


def java_extract() -> FileExtraction:
    return extract("java", "A.java", JAVA_SOURCE, JavaExtractor())


# ── 语言标识与注册 ──────────────────────────────────────────────────
def test_extractor_language_and_aliases() -> None:
    assert GoExtractor.language == "go"
    assert JavaExtractor.language == "java"
    assert GenericExtractor.language == "generic"
    assert GenericExtractor.aliases == ()


# ── Go ─────────────────────────────────────────────────────────────
def test_go_symbols_are_qualified_per_scope() -> None:
    symbols = go_extract().symbols
    got = {(s.kind, s.qualified) for s in symbols}
    assert ("struct", "Server") in got
    assert ("method", "Server.Serve") in got  # 方法用「接收者类型.方法名」
    assert ("function", "main") in got
    assert ("constant", "MaxN") in got


def test_go_method_parent_is_receiver_type() -> None:
    method = next(s for s in go_extract().symbols if s.kind == "method")
    assert method.name == "Serve"
    assert method.parent == "Server"


def test_go_references_call_and_instantiation() -> None:
    refs = go_extract().references
    calls = [(r.name, r.receiver) for r in refs if r.kind == "call"]
    assert ("Serve", "s") in calls  # 方法调用
    assert ("Println", "fmt") in calls  # 选择器调用
    assert any(r.kind == "instantiates" and r.name == "Server" for r in refs)


def test_go_imports_parse_module_path() -> None:
    imports = go_extract().imports
    assert [i.module for i in imports] == ["fmt"]
    assert imports[0].alias is None


def test_go_import_alias_is_kept() -> None:
    source = b'package main\n\nimport (\n    f "os"\n    _ "net/http/pprof"\n)\n'
    imports = extract("go", "b.go", source, GoExtractor()).imports
    assert [(i.module, i.alias) for i in imports] == [("os", "f"), ("net/http/pprof", "_")]


def test_go_reference_scope_points_at_enclosing_symbol() -> None:
    refs = go_extract().references
    scopes = {r.scope for r in refs if r.kind == "call"}
    assert "Server.Serve" in scopes
    assert "main" in scopes


# ── Java ───────────────────────────────────────────────────────────
def test_java_symbols_cover_class_method_field_and_constructor() -> None:
    out = java_extract()
    got = {(s.kind, s.qualified) for s in out.symbols}
    assert ("class", "A") in got
    assert ("method", "A.f") in got
    assert ("method", "A.A") in got  # 构造函数用类名
    assert ("variable", "A.x") in got


def test_java_extends_and_implements() -> None:
    refs = java_extract().references
    assert any(r.kind == "extends" and r.name == "B" for r in refs)
    assert any(r.kind == "implements" and r.name == "C" for r in refs)


def test_java_calls_and_instantiation() -> None:
    refs = java_extract().references
    calls = [(r.name, r.receiver) for r in refs if r.kind == "call"]
    assert ("helper", None) in calls
    assert any(r.kind == "instantiates" and r.name == "A" for r in refs)
    assert all(r.scope == "A.f" for r in refs if r.name == "helper")


def test_java_imports_full_dotted_paths() -> None:
    out = java_extract()
    assert [i.module for i in out.imports] == ["java.util.List"]

    wildcard = extract("java", "B.java", b"import java.util.*;\n", JavaExtractor()).imports
    assert wildcard[0].module == "java.util"
    assert wildcard[0].names == ("*",)


def test_java_nested_class_qualified_includes_outer() -> None:
    source = b"""class Outer {
    class Inner {
        void m() { }
    }
}
"""
    out = extract("java", "Outer.java", source, JavaExtractor())
    got = {(s.kind, s.qualified) for s in out.symbols}
    assert ("class", "Outer.Inner") in got
    assert ("method", "Outer.Inner.m") in got


def test_java_static_final_uppercase_is_constant() -> None:
    source = b"""class A {
    static final int MAX_N = 3;
    private int x;
}
"""
    out = extract("java", "A.java", source, JavaExtractor())
    got = {(s.kind, s.name) for s in out.symbols}
    assert ("constant", "MAX_N") in got
    assert ("variable", "x") in got


# ── 通用启发式（rust / c） ──────────────────────────────────────────
def test_generic_rust_symbols() -> None:
    out = extract("rust", "main.rs", RUST_SOURCE, GenericExtractor())
    got = {(s.kind, s.qualified) for s in out.symbols}
    assert ("function", "main") in got
    assert ("struct", "Config") in got
    assert ("function", "Config.new") in got  # impl 成员挂到类型下


def test_generic_rust_import_and_calls() -> None:
    out = extract("rust", "main.rs", RUST_SOURCE, GenericExtractor())
    assert [i.module for i in out.imports] == ["std::collections::HashMap"]
    names = {r.name for r in out.references if r.kind == "call"}
    assert {"Config::new", "load"} <= names


def test_generic_c_symbols_import_and_call() -> None:
    out = extract("c", "a.c", C_SOURCE, GenericExtractor())
    got = {(s.kind, s.qualified) for s in out.symbols}
    assert ("function", "add") in got
    assert [i.module for i in out.imports] == ["stdio.h"]

    with_call = extract(
        "c", "b.c", b'#include <stdio.h>\nvoid helper(void) { printf("x"); }\n', GenericExtractor()
    )
    assert any(r.kind == "call" and r.name == "printf" for r in with_call.references)


def test_generic_does_not_treat_import_as_call() -> None:
    out = extract("rust", "main.rs", RUST_SOURCE, GenericExtractor())
    assert all(r.name != "use" for r in out.references)


def test_generic_handles_unsupported_shapes() -> None:
    """json / html 这类没有函数概念的语法不能崩，也不能硬凑出符号。"""
    out = extract("json", "a.json", b'{"a": 1}', GenericExtractor())
    assert isinstance(out, FileExtraction)
    assert out.language == "generic"


# ── 健壮性：语法错误 / 截断的源码 ────────────────────────────────────
@pytest.mark.parametrize(("language", "path", "source"), BROKEN_SNIPPETS)
@pytest.mark.parametrize(
    "extractor", [GoExtractor(), JavaExtractor(), GenericExtractor()], ids=lambda e: e.language
)
def test_broken_source_never_raises(
    language: str, path: str, source: bytes, extractor: object
) -> None:
    """哪个抽取器接到哪门语言的坏源码都必须返回 FileExtraction（最坏是空的）。"""
    ctx = FileContext(path, language, parse_source(source, language))
    out = extractor.extract(ctx)  # type: ignore[attr-defined]
    assert isinstance(out, FileExtraction)
    assert out.errors == []


def test_go_truncated_source_keeps_partial_symbol() -> None:
    out = extract("go", "a.go", b"func main() {\n\tx := 1\n", GoExtractor())
    assert isinstance(out, FileExtraction)
    assert [s.qualified for s in out.symbols] == ["main"]


def test_java_truncated_source_returns_extraction() -> None:
    """``class A { void f(`` 会被 tree-sitter 整个包进 ERROR 节点，此时抽不到符号也不能崩。"""
    out = extract("java", "A.java", b"class A {\n    void f(\n", JavaExtractor())
    assert isinstance(out, FileExtraction)
    assert out.file == "A.java"
    assert out.errors == []


def test_generic_broken_source_returns_extraction() -> None:
    out = extract("rust", "a.rs", b"impl { fn", GenericExtractor())
    assert isinstance(out, FileExtraction)
    assert out.file == "a.rs"
    assert out.language == "generic"
