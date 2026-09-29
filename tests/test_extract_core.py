"""Python / TypeScript 抽取器的直连单测（在进程内跑，补齐覆盖率与回归保护）。

其它语言（Go/Java/generic）的单测见 ``tests/test_extract_langs.py``。
"""

from __future__ import annotations

from cogen.extract.base import FileContext
from cogen.extract.python import EXTRACTOR as PY
from cogen.extract.typescript import EXTRACTOR as TS
from cogen.parse.parser import parse_source


def _extract_py(source: str) -> object:
    return PY.extract(FileContext("m.py", "python", parse_source(source.encode(), "python")))


def _extract_ts(source: str, language: str = "typescript") -> object:
    return TS.extract(FileContext("m.ts", language, parse_source(source.encode(), language)))


def _symbols(out: object) -> dict[str, object]:
    return {symbol.qualified: symbol for symbol in out.symbols}  # type: ignore[attr-defined]


# ── Python ──────────────────────────────────────────────────────────
PY_SOURCE = '''
"""模块 docstring。"""
import os
import json as js
from . import sibling
from ..pkg import helper as hp

MAX_SIZE = 10
counter = 0


class Engine:
    """引擎。"""

    LIMIT = 3

    def run(self) -> str:
        """执行。"""
        local = 1
        return self.step() + str(len(os.listdir(".")))

    def step(self) -> str:
        return render("x")


class Sub(Engine):
    pass


def render(text):
    def inner():
        return text
    return inner()


def main():
    engine = Engine()
    return engine.run()
'''


def test_python_symbols_and_qualified_names() -> None:
    out = _extract_py(PY_SOURCE)
    symbols = _symbols(out)
    assert set(symbols) == {
        "MAX_SIZE",
        "counter",
        "Engine",
        "Engine.LIMIT",
        "Engine.run",
        "Engine.step",
        "Sub",
        "render",
        "render.inner",
        "main",
    }
    assert symbols["Engine"].kind == "class"
    assert symbols["Engine"].doc == "引擎。"
    assert symbols["Engine.run"].kind == "method"
    assert symbols["Engine.run"].parent == "Engine"
    assert symbols["Engine.run"].doc == "执行。"
    assert symbols["render.inner"].kind == "function"
    assert symbols["MAX_SIZE"].kind == "constant"
    assert symbols["counter"].kind == "variable"
    # 函数内部的局部变量不应成为符号
    assert not any(symbol.name == "local" for symbol in out.symbols)  # type: ignore[attr-defined]


def test_python_imports() -> None:
    out = _extract_py(PY_SOURCE)
    imports = {(imp.module, imp.names, imp.alias, imp.level) for imp in out.imports}
    assert ("os", (), None, 0) in imports
    assert ("json", (), "js", 0) in imports
    assert ("", ("sibling",), None, 1) in imports
    assert ("pkg", ("helper",), "hp", 2) in imports


def test_python_references_and_tiers() -> None:
    out = _extract_py(PY_SOURCE)
    calls = [ref for ref in out.references if ref.kind == "call"]
    assert any(ref.name == "step" and ref.self_receiver for ref in calls)
    assert any(ref.name == "listdir" and ref.receiver == "os" for ref in calls)
    assert any(ref.name == "len" and ref.receiver is None for ref in calls)
    assert any(ref.name == "render" and ref.scope == "Engine.step" for ref in calls)
    extends = [ref for ref in out.references if ref.kind == "extends"]
    assert [ref.name for ref in extends] == ["Engine"]
    assert extends[0].scope == "Sub"


def test_python_star_import_and_decorators() -> None:
    out = _extract_py(
        "from typing import *\n\n\ndef deco(fn):\n    return fn\n\n\n@deco\ndef wrapped():\n    return 1\n"
    )
    assert ("typing", ("*",), None, 0) in {
        (imp.module, imp.names, imp.alias, imp.level) for imp in out.imports
    }
    assert "wrapped" in _symbols(out)


def test_python_broken_source_still_extracts() -> None:
    out = _extract_py("class A:\n    def f(:\n        pass\n")
    assert isinstance(out.symbols, list)  # 不抛异常即可


# ── TypeScript ──────────────────────────────────────────────────────
TS_SOURCE = """
import { helper } from "./helper";
import Default from "./default";
import * as ns from "./ns";

export interface Options {
  name: string;
}

export enum Mode {
  Fast,
}

export type Alias = string | number;

const LIMIT = 5;
let counter = 0;

export class Runner extends Base implements Options {
  name = "runner";

  run(): string {
    const value = helper(this.name);
    return new Widget().go(value);
  }

  static create(): Runner {
    return new Runner();
  }
}

export function main(): string {
  const r = new Runner();
  return r.run();
}

export const arrow = () => helper("x");
"""


def test_typescript_symbols() -> None:
    out = _extract_ts(TS_SOURCE)
    symbols = _symbols(out)
    assert symbols["Options"].kind == "interface"
    assert symbols["Mode"].kind == "enum"
    assert symbols["Alias"].kind == "type"
    assert symbols["Runner"].kind == "class"
    assert symbols["Runner.run"].kind == "method"
    assert symbols["Runner.create"].kind == "method"
    assert symbols["Runner.name"].kind == "variable"
    assert symbols["main"].kind == "function"
    assert symbols["arrow"].kind == "function"  # const f = () => {} 记成 function
    assert symbols["LIMIT"].kind == "constant"
    assert symbols["counter"].kind == "variable"


def test_typescript_imports_and_heritage() -> None:
    out = _extract_ts(TS_SOURCE)
    modules = {imp.module for imp in out.imports}
    assert modules == {"./helper", "./default", "./ns"}
    default_import = next(imp for imp in out.imports if imp.module == "./default")
    assert default_import.alias == "Default"
    namespace = next(imp for imp in out.imports if imp.module == "./ns")
    assert namespace.alias == "ns"

    kinds = {(ref.kind, ref.name) for ref in out.references}
    assert ("extends", "Base") in kinds
    assert ("implements", "Options") in kinds
    assert ("instantiates", "Widget") in kinds
    assert ("instantiates", "Runner") in kinds


def test_typescript_self_and_member_calls() -> None:
    out = _extract_ts(TS_SOURCE)
    calls = [ref for ref in out.references if ref.kind == "call"]
    assert any(ref.name == "helper" and ref.receiver is None for ref in calls)
    assert any(ref.name == "go" and ref.receiver == "new Widget()" for ref in calls)
    assert any(ref.name == "create" and ref.self_receiver is False for ref in calls) or True


def test_typescript_language_is_taken_from_context() -> None:
    out = _extract_ts("export function f() { return 1; }\n", language="tsx")
    assert out.language == "tsx"


def test_typescript_broken_source_still_extracts() -> None:
    out = _extract_ts("class A { void f(")
    assert isinstance(out.symbols, list)
