"""M6 交付测试：单文件 graph.html、make demo 脚本、文档与 Makefile 门禁。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cogen.config import get_settings
from cogen.export.html_export import MAX_HTML_NODES, export_html
from cogen.graph.store import open_store

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "graph_repo"


@pytest.fixture
def indexed(client: TestClient, wait_job) -> dict[str, object]:
    created = client.post("/api/repos", json={"target": str(FIXTURE)}).json()
    job = wait_job(client, created["jobId"])
    assert job["state"] == "done", job
    return {"repoId": created["repoId"], "job": job}


# ── 单文件 graph.html ───────────────────────────────────────────────
def test_export_html_is_self_contained(client: TestClient, indexed, cogen_home) -> None:
    settings = get_settings()
    with open_store(settings, str(indexed["repoId"])) as store:
        path = export_html(store, settings)
    html = path.read_text(encoding="utf-8")

    assert "<canvas" in html and "graph-data" in html
    assert "graph_repo" in html
    # 不依赖任何外部资源（离线可打开）
    assert not re.search(r'src="https?://|href="https?://', html)
    # 内嵌数据能被解析，且只包含已被选中的节点两端
    match = re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    data = json.loads(match.group(1))
    assert len(data["nodes"]) <= MAX_HTML_NODES
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["edges"])
    assert any(node["kind"] == "class" for node in data["nodes"])
    assert path.stat().st_size < 8 * 1024 * 1024


def test_export_html_custom_path(client: TestClient, indexed, cogen_home, tmp_path: Path) -> None:
    settings = get_settings()
    target = tmp_path / "share" / "graph.html"
    with open_store(settings, str(indexed["repoId"])) as store:
        assert export_html(store, settings, output=target) == target
    assert target.exists()


def test_cli_export_includes_html(client: TestClient, indexed, cogen_home) -> None:
    from click.testing import CliRunner

    from cogen.cli import main

    result = CliRunner().invoke(main, ["export", "--repo", str(indexed["repoId"]), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert Path(payload["html"]).exists()
    assert Path(payload["report"]).exists()
    assert Path(payload["graph"]).exists()

    only_json = CliRunner().invoke(
        main, ["export", "--repo", str(indexed["repoId"]), "--format", "json", "--json"]
    )
    payload = json.loads(only_json.output)
    assert payload["html"] is None and payload["report"] is None
    assert Path(payload["graph"]).exists()


# ── make demo ───────────────────────────────────────────────────────
def test_demo_script_runs_clean(tmp_path: Path) -> None:
    """干净 COGEN_HOME 下一次跑通：索引 fixture → 导出三种产物。"""
    home = tmp_path / "demo-home"
    env = {
        "PATH": f"{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "COGEN_HOME": str(home),
        "TMPDIR": str(tmp_path),
        "HOME": str(tmp_path),
    }
    proc = subprocess.run(
        ["bash", "scripts/demo.sh", str(FIXTURE)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "graph.html" in proc.stdout
    exports = list((home / "exports").glob("*/"))
    assert exports, "没有生成导出目录"
    artifacts = {path.name for path in exports[0].iterdir()}
    assert {"graph.json", "GRAPH_REPORT.md", "graph.html"} <= artifacts


# ── 文档与门禁 ──────────────────────────────────────────────────────
def test_makefile_targets_exist() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("setup", "dev", "test", "test-all", "lint", "fmt", "index", "demo", "mcp"):
        assert re.search(rf"^{target}:", text, re.M), f"Makefile 缺少 target: {target}"


def test_readme_documents_mcp_and_safety() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("cogen mcp", "MCP", "已知坑", "不执行被测代码"):
        assert needle in text, f"README 缺少说明: {needle}"


def test_env_example_covers_llm_and_limits() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "COGEN_HOME",
        "COGEN_MAX_FILES",
        "COGEN_LLM_BASE_URL",
        "COGEN_LLM_API_KEY",
        "COGEN_LLM_REDACT",
    ):
        assert key in text
