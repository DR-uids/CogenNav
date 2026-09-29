"""CLI 测试：index / status / warm 与终端进度。"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cogen.cli import main
from cogen.jobs import ConsoleProgress


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("index", "serve", "status", "export", "ask", "mcp", "warm"):
        assert command in result.output


def test_status_json(cogen_home: Path) -> None:
    result = CliRunner().invoke(main, ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["version"]
    assert payload["home"].endswith(".cogen")
    assert payload["limits"]["parse_workers"] >= 1
    assert payload["llm"]["configured"] is False


def test_index_local_repo_json(cogen_home: Path, demo_repo: Path) -> None:
    result = CliRunner().invoke(main, ["index", str(demo_repo), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repoId"].startswith("local-demo-repo-")
    assert payload["state"] == "done"
    assert payload["fileCount"] >= 5
    assert payload["languages"]["python"] >= 1
    assert payload["parse"]["parsed"] >= 1
    assert payload["parse"]["mode"] in ("pool", "serial")


def test_index_rejects_dangerous_target(cogen_home: Path) -> None:
    result = CliRunner().invoke(main, ["index", "ext::sh -c true"])
    assert result.exit_code == 2
    assert "::" in result.output or "不合法" in result.output


def test_index_reports_missing_local_path(cogen_home: Path) -> None:
    result = CliRunner().invoke(main, ["index", "./definitely-missing-dir"])
    assert result.exit_code == 2
    assert "不存在" in result.output


def test_warm_explains_when_xlang_missing(cogen_home: Path) -> None:
    """当前环境未安装扩展层：必须给出安装指引而不是报栈。"""
    from cogen.parse.languages import xlang_available

    if xlang_available():  # pragma: no cover - 装了扩展层就不走这条分支
        return
    result = CliRunner().invoke(main, ["warm", "rust"])
    assert result.exit_code == 2
    assert "[xlang]" in result.output
    assert "核心层已内置" in result.output


def test_console_progress_prints_phases() -> None:
    lines: list[str] = []
    progress = ConsoleProgress(lines.append)
    progress.update(phase="clone", message="正在浅克隆仓库…", force=True)
    progress.update(phase="walk", current=3, total=10, message="已发现 3 个文件", force=True)
    progress.done("共 3 个文件")
    joined = "\n".join(lines)
    assert "[克隆仓库]" in joined
    assert "[遍历文件]" in joined
    assert "(3/10)" in joined
    assert "[完成] 共 3 个文件" in joined


def test_ask_requires_llm_key(cogen_home: Path, tmp_path: Path, monkeypatch) -> None:
    """没索引仓库 / 没配 Key 两种情况都必须给明确指引而不是报栈。"""
    # 先建一个空仓库
    repo = tmp_path / "ask-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    assert CliRunner().invoke(main, ["index", str(repo)]).exit_code == 0

    monkeypatch.delenv("COGEN_LLM_API_KEY", raising=False)
    from cogen.config import reset_settings_cache

    reset_settings_cache()
    result = CliRunner().invoke(main, ["ask", "谁调用了 x？"])
    assert result.exit_code == 2
    assert "COGEN_LLM_API_KEY" in result.output


def test_ask_without_indexed_repo(cogen_home: Path) -> None:
    result = CliRunner().invoke(main, ["ask", "hi"])
    assert result.exit_code == 2
    assert "cogen index" in result.output
