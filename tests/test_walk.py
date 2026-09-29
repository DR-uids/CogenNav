"""文件遍历测试：gitignore、跳过规则、上限、语言识别。"""

from __future__ import annotations

from pathlib import Path

from cogen.config import Settings
from cogen.ingest.walk import count_loc, walk_repo


def _walk(repo: Path, **overrides: object):
    settings = Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
    return walk_repo(repo, settings)


def test_collects_code_and_skips_noise(demo_repo: Path) -> None:
    result = _walk(demo_repo)
    paths = {f.path for f in result.files}

    assert {"src/a.py", "src/b.ts", "src/deep/c.go", "README.md", ".env.example"} <= paths
    assert paths.isdisjoint(
        {
            "app.log",  # .gitignore
            "secret.txt",  # .gitignore
            "build/out.js",  # .gitignore（目录）
            ".env",  # 密钥
            "package-lock.json",  # 生成物
            "logo.png",  # 二进制
            "big.py",  # 超过单文件上限
            "link.py",  # 符号链接（不跟随）
            "node_modules/pkg/index.js",  # 跳过目录
        }
    )


def test_skip_reasons_are_recorded(demo_repo: Path) -> None:
    skipped = {s.path: s.reason for s in _walk(demo_repo).skipped}
    assert skipped["app.log"] == "gitignore"
    assert skipped["secret.txt"] == "gitignore"
    assert skipped["build"] == "skip_dir"
    assert skipped[".env"] == "secrets"
    assert skipped["package-lock.json"] == "generated"
    assert skipped["logo.png"] == "binary"
    assert skipped["big.py"] == "too_large"
    assert skipped["link.py"] == "symlink"
    assert skipped["node_modules"] == "skip_dir"


def test_language_detection(demo_repo: Path) -> None:
    langs = {f.path: f.language for f in _walk(demo_repo).files}
    assert langs["src/a.py"] == "python"
    assert langs["src/b.ts"] == "typescript"
    assert langs["src/deep/c.go"] == "go"
    assert langs["script"] == "python"  # 无扩展名，按 shebang
    assert langs[".env.example"] == "unknown"


def test_file_metrics(demo_repo: Path) -> None:
    files = {f.path: f for f in _walk(demo_repo).files}
    a_py = files["src/a.py"]
    assert a_py.loc == 5
    assert a_py.size == len((demo_repo / "src" / "a.py").read_bytes())
    assert len(a_py.sha256) == 64
    assert a_py.parse_ok is True


def test_count_loc_edge_cases() -> None:
    assert count_loc(b"") == 0
    assert count_loc(b"a") == 1
    assert count_loc(b"a\n") == 1
    assert count_loc(b"a\nb\n") == 2
    assert count_loc(b"a\nb") == 2


def test_max_files_truncates(demo_repo: Path) -> None:
    result = _walk(demo_repo, max_files=2)
    assert result.truncated is True
    assert result.truncated_reason == "max_files"
    assert len(result.files) <= 3  # 触发点在 append 之后


def test_max_total_bytes_truncates(demo_repo: Path) -> None:
    result = _walk(demo_repo, max_total_bytes=10)
    assert result.truncated is True
    assert result.truncated_reason == "max_total_bytes"


def test_symlink_pointing_outside_is_not_followed(demo_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("token = 'x'\n")
    (demo_repo / "escape").symlink_to(outside)
    result = _walk(demo_repo)
    assert all(not f.path.startswith("escape") for f in result.files)
    assert any(s.path == "escape" and s.reason == "symlink" for s in result.skipped)


def test_nested_gitignore_is_respected(demo_repo: Path) -> None:
    (demo_repo / "src" / ".gitignore").write_text("b.ts\n")
    paths = {f.path for f in _walk(demo_repo).files}
    assert "src/b.ts" not in paths
    assert "src/a.py" in paths


def test_custom_limits_allow_larger_file(demo_repo: Path) -> None:
    result = _walk(demo_repo, max_file_bytes=10_000_000)
    assert "big.py" in {f.path for f in result.files}
