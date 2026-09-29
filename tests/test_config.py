"""配置解析测试：沙箱要求所有数据目录落在工作区内。"""

from __future__ import annotations

import os
from pathlib import Path

from cogen.config import Settings, get_settings, reset_settings_cache


def test_home_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COGEN_HOME", str(tmp_path / "data"))
    reset_settings_cache()
    try:
        s = get_settings()
        assert s.root == tmp_path / "data"
        s.ensure_dirs()
        for d in (s.repos_dir, s.db_dir, s.exports_dir, s.cache_dir):
            assert d.is_dir()
            assert str(d).startswith(str(tmp_path))
    finally:
        reset_settings_cache()


def test_relative_home_resolves_under_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COGEN_HOME", ".cogen")
    monkeypatch.chdir(tmp_path)
    reset_settings_cache()
    try:
        assert get_settings().root == tmp_path / ".cogen"
    finally:
        reset_settings_cache()


def test_defaults_match_plan_limits() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.max_files == 20_000
    assert s.max_file_bytes == 1_048_576
    assert s.max_total_bytes == 2_147_483_648
    assert s.clone_timeout_s == 300.0
    assert s.llm_redact is True
    assert s.llm_configured is False
    assert s.effective_parse_workers() == max(1, (os.cpu_count() or 2) - 1)


def test_workers_respect_explicit_setting() -> None:
    s = Settings(_env_file=None, parse_workers=3)  # type: ignore[call-arg]
    assert s.effective_parse_workers() == 3
