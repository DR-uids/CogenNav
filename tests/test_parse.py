"""解析层测试：19 门核心语法、错误统计、CST 惰性切片、进程池看门狗与降级。"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from cogen.config import get_settings
from cogen.parse import cst
from cogen.parse import tscompat as ts
from cogen.parse.languages import core_languages, is_display_only
from cogen.parse.parser import (
    ParseOutcome,
    UnknownLanguageError,
    is_parsable,
    parse_file,
    parse_source,
)
from cogen.parse.pool import ParsePool, PoolUnavailable

SNIPPETS: dict[str, bytes] = {
    "python": b"def f():\n    return 1\n",
    "javascript": b"export function f() { return 1; }\n",
    "typescript": b"export function f(): number { return 1; }\n",
    "tsx": b"export const A = () => <div>hi</div>;\n",
    "go": b"package main\n\nfunc main() {}\n",
    "java": b"class A { int f() { return 1; } }\n",
    "rust": b"fn main() { let x = 1; }\n",
    "c": b"int main(void) { return 0; }\n",
    "cpp": b"int main() { return 0; }\n",
    "c_sharp": b"class A { int F() => 1; }\n",
    "ruby": b"def f\n  1\nend\n",
    "php": b"<?php function f() { return 1; }\n",
    "kotlin": b"fun main() { println(1) }\n",
    "swift": b"func f() -> Int { return 1 }\n",
    "bash": b"#!/bin/bash\necho hi\n",
    "json": b'{"a": 1}\n',
    "yaml": b"a: 1\n",
    "html": b"<html><body>hi</body></html>\n",
    "css": b"a { color: red; }\n",
}


def test_every_core_grammar_is_supported() -> None:
    """语法注册表与实测片段必须一一对应，防止登记了却没有语法。"""
    assert set(SNIPPETS) == set(core_languages())


@pytest.mark.parametrize("language", sorted(SNIPPETS))
def test_parse_snippet_per_language(language: str) -> None:
    parsed = parse_source(SNIPPETS[language], language)
    assert parsed.node_count > 1
    assert parsed.root is not None
    assert ts.node_type(parsed.root)


def test_error_file_reports_problems() -> None:
    parsed = parse_source(b"def broken(:\n    pass\n", "python")
    assert parsed.error_count + parsed.missing_count > 0  # MISSING/ERROR 都要算进来


def test_clean_file_has_no_problems() -> None:
    parsed = parse_source(b"x = 1\n", "python")
    assert (parsed.error_count, parsed.missing_count) == (0, 0)
    assert parsed.node_count == ts.descendant_count(parsed.root)


def test_display_only_language_is_not_parsable() -> None:
    assert is_display_only("markdown") is True
    assert is_parsable("markdown") is False
    with pytest.raises(UnknownLanguageError):
        parse_source(b"# hi\n", "markdown")


def test_parse_file_reports_too_large(tmp_path: Path) -> None:
    target = tmp_path / "big.py"
    target.write_text("x = 1\n" * 100)
    outcome = parse_file(target, "python", max_bytes=10)
    assert outcome.ok is False
    assert outcome.error == "too_large"


def test_parse_file_reports_no_grammar(tmp_path: Path) -> None:
    target = tmp_path / "a.md"
    target.write_text("# hi\n")
    assert parse_file(target, "markdown", max_bytes=1000).error == "no_grammar"


def test_pathological_input_does_not_crash() -> None:
    """1MiB 上限内的病态输入实测都是毫秒级；这里只要求不崩且能标出错误。"""
    parsed = parse_source(b"(" * 100_000, "python")
    assert parsed.node_count > 0
    assert parsed.error_count + parsed.missing_count > 0


def test_tscompat_child_out_of_range_returns_none() -> None:
    parsed = parse_source(b"x = 1\n", "python")
    root = parsed.root
    assert ts.child(root, 0) is not None
    assert ts.child(root, 999) is None
    assert ts.child(root, -1) is None


# ── CST 切片 ────────────────────────────────────────────────────────
def _python_tree() -> tuple[bytes, object]:
    source = b"import os\n\n\ndef f(a, b):\n    return a + b\n"
    return source, parse_source(source, "python").root


def test_cst_resolve_node_path() -> None:
    _source, root = _python_tree()
    assert cst.resolve_node(root, "") == [root]
    first = cst.resolve_node(root, "0")
    assert len(first) == 2
    deeper = cst.resolve_node(root, "1.3")
    assert len(deeper) == 3
    with pytest.raises(cst.CstPathError):
        cst.resolve_node(root, "abc")
    with pytest.raises(cst.CstPathError):
        cst.resolve_node(root, "99")


def test_cst_leaf_text_and_fields() -> None:
    source, root = _python_tree()
    payload = cst.serialize_subtree(source, root, depth=2, options=cst.CstOptions())
    assert payload["type"] == "module"
    assert payload["named"] is True
    assert payload["truncated"] is False
    func = payload["children"][1]
    assert func["type"] == "function_definition"
    names = [c for c in func["children"] if c["field"] == "name"]
    assert names and names[0]["text"] == "f"


def test_cst_depth_marks_truncated() -> None:
    source, root = _python_tree()
    payload = cst.serialize_subtree(source, root, depth=1, options=cst.CstOptions())
    func = payload["children"][1]
    assert func["truncated"] is True
    assert func["children"] == []
    assert func["childCount"] > 0  # 前端据此决定能否懒加载


def test_cst_children_capped_is_reported_separately() -> None:
    source = b"a = 1\nb = 2\n"
    root = parse_source(source, "python").root
    payload = cst.serialize_subtree(source, root, depth=4, options=cst.CstOptions(max_children=1))
    assert payload["childrenCapped"] is True
    assert len(payload["children"]) == 1
    assert payload["truncated"] is False  # 与「深度截断」语义区分开


def test_cst_node_path_validation() -> None:
    with pytest.raises(cst.CstPathError):
        cst.parse_node_path("1.x")
    with pytest.raises(cst.CstPathError):
        cst.parse_node_path("1." * 300)
    assert cst.parse_node_path("") == []
    assert cst.parse_node_path("2.0.1") == [2, 0, 1]


def test_cst_sexp_summary() -> None:
    source, _root = _python_tree()
    parsed = parse_source(source, "python")
    text = cst.summarize_sexp(parsed.tree, limit=50)
    assert text.startswith("(module")
    assert len(text) <= 50


# ── 进程池 ──────────────────────────────────────────────────────────
def test_pool_parses_files_in_process_pool(cogen_home: Path, demo_repo: Path) -> None:
    settings = get_settings()
    items = [("src/a.py", "python"), ("src/b.ts", "typescript"), ("README.md", "markdown")]
    items = [(p, lang) for p, lang in items if is_parsable(lang)]
    with ParsePool(settings, workers=2, chunk_size=2) as pool:
        outcomes = pool.parse_files(demo_repo, items)
    assert [o.path for o in outcomes] == [p for p, _ in items]
    assert all(o.ok for o in outcomes)
    assert all(o.node_count > 0 for o in outcomes)


def test_pool_serial_degradation_still_parses(cogen_home: Path, demo_repo: Path) -> None:
    settings = get_settings()
    with ParsePool(settings, workers=2) as pool:
        pool.mode = "serial"
        outcomes = pool.parse_files(demo_repo, [("src/a.py", "python")])
    assert outcomes[0].ok is True
    assert outcomes[0].node_count > 0


def test_pool_health_check_failure_degrades_to_serial(
    cogen_home: Path, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    pool = ParsePool(settings, workers=2)

    def boom(*, health_check: bool = False) -> object:
        raise PoolUnavailable("spawn 不可用")

    monkeypatch.setattr(pool, "_ensure_pool", boom)
    outcomes = pool.parse_files(demo_repo, [("src/a.py", "python")])
    assert pool.mode == "serial"
    assert "spawn 不可用" in (pool.degraded_reason or "")
    assert outcomes[0].ok is True


class _FakeAsync:
    def __init__(self, *, result: list[ParseOutcome] | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    def get(self, timeout: float | None = None) -> list[ParseOutcome]:
        if self._exc is not None:
            raise self._exc
        return self._result or []


class _FakePool:
    """模拟「整块超时 → 逐文件重试」：坏文件始终超时，好文件正常返回。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def map_async(self, fn: object, payload: list[tuple[str, str, str, int]]) -> _FakeAsync:
        self.calls.append([item[1] for item in payload])
        if len(payload) > 1:
            return _FakeAsync(exc=mp.TimeoutError())
        rel_path, language = payload[0][1], payload[0][2]
        if rel_path == "bad.py":
            return _FakeAsync(exc=mp.TimeoutError())
        return _FakeAsync(
            result=[ParseOutcome(path=rel_path, language=language, ok=True, node_count=7)]
        )

    def terminate(self) -> None:
        return None

    def join(self) -> None:
        return None


def test_pool_timeout_retries_individually(
    cogen_home: Path, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    pool = ParsePool(settings, workers=1, chunk_size=4)
    fake = _FakePool()
    monkeypatch.setattr(pool, "_ensure_pool", lambda **kwargs: fake)

    outcomes = pool.parse_files(demo_repo, [("good.py", "python"), ("bad.py", "python")])
    assert fake.calls == [["good.py", "bad.py"], ["good.py"], ["bad.py"]]
    assert outcomes[0].ok is True and outcomes[0].node_count == 7
    assert outcomes[1].ok is False and outcomes[1].error == "parse_timeout"


def _probe_env(_: int) -> list[str]:
    import os

    return sorted(
        key
        for key in os.environ
        if key.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) or key == "GITHUB_TOKEN"
    )


def test_pool_worker_environment_is_scrubbed(
    cogen_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spawn 出来的 worker 里必须读不到密钥环境变量。"""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-not-leak")
    monkeypatch.setenv("COGEN_LLM_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "t-should-not-leak")
    monkeypatch.setenv("COGEN_HOME", str(cogen_home))

    from cogen.parse.pool import _worker_init

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=1, initializer=_worker_init) as pool:
        leaked = pool.map(_probe_env, [0])[0]

    assert leaked == []
    # 父进程环境不受影响
    import os

    assert os.environ["GITHUB_TOKEN"] == "ghp-should-not-leak"


def test_scrub_environment_removes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from cogen.parse.pool import scrub_environment

    monkeypatch.setenv("COGEN_LLM_API_KEY", "sk-x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-x")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "t")
    monkeypatch.setenv("COGEN_HOME", "/tmp/keep-me")
    removed = scrub_environment()
    import os

    assert "COGEN_LLM_API_KEY" in removed
    assert "GITHUB_TOKEN" in removed
    assert "MY_SERVICE_TOKEN" in removed
    assert "COGEN_LLM_API_KEY" not in os.environ
    assert os.environ["COGEN_HOME"] == "/tmp/keep-me"


def _walk_payload(node: dict) -> list[dict]:
    out = [node]
    for child in node.get("children") or []:
        out.extend(_walk_payload(child))
    return out


def test_cst_ranges_match_source_lines() -> None:
    """行列必须与源码一致（曾因推导式变量遮蔽 SourceIndex 而静默算错）。"""
    source = b"def f(a):\n    return a\n"
    root = parse_source(source, "python").root
    payload = cst.serialize_subtree(source, root, depth=6, options=cst.CstOptions(depth=6))
    identifiers = [n for n in _walk_payload(payload) if n["type"] == "identifier"]
    assert [(n["text"], n["start"]) for n in identifiers] == [
        ("f", [0, 4]),
        ("a", [0, 6]),
        ("a", [1, 11]),
    ]


def test_cst_serialization_does_not_crash_interpreter(tmp_path: Path) -> None:
    """回归：py-tree-sitter 0.26 的 ``Point`` 对象大量使用后会破坏堆，并在 GC 时段错误。

    真出现回归时子进程会以段错误退出（不是断言失败），所以必须在子进程里跑。
    """
    import subprocess
    import sys
    import textwrap

    repo_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "big.py"
    source.write_text("def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(300)))
    code = textwrap.dedent(
        f"""
        import gc
        from pathlib import Path
        from cogen.parse.parser import parse_source
        from cogen.parse import cst

        src = Path({str(source)!r}).read_bytes()
        parsed = parse_source(src, "python")
        payload = cst.serialize_subtree(
            parsed.index, parsed.root, depth=8, options=cst.CstOptions(depth=8)
        )
        gc.collect()
        gc.collect()
        assert len(payload["children"]) == 1
        print("ok")
        """
    )
    env = {"PYTHONPATH": str(repo_root / "src")}
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert proc.returncode == 0, f"子进程退出码 {proc.returncode}: {proc.stderr[-2000:]}"
    assert "ok" in proc.stdout


def test_source_index_points_and_text() -> None:
    from cogen.parse.tscompat import SourceIndex

    index = SourceIndex(b"ab\ncd\n\n")
    # 行首偏移 [0, 3, 6, 7]：末尾换行会多出一个空行，与 text.splitlines 语义一致
    assert index.line_count == 4
    assert index.point(0) == (0, 0)
    assert index.point(2) == (0, 2)
    assert index.point(3) == (1, 0)
    assert index.point(6) == (2, 0)
    assert index.point(9999) == (3, 0)  # 越界收敛到末尾
    assert index.text(3, 5) == "cd"
