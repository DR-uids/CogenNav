"""安全基线测试：目标白名单、路径越界、脱敏（计划 §9）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cogen.api.deps import check_job_id, check_repo_id
from cogen.security import (
    PathEscapeError,
    UnsafePathError,
    UnsafeTargetError,
    check_git_url,
    check_repo_relative_path,
    ensure_within,
    redact_secrets,
    redact_url,
    sanitize_label,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/psf/requests",
        "http://git.example.com/team/repo.git",
        "ssh://git@github.com/psf/requests.git",
        "git://example.com/repo.git",
        "git@github.com:psf/requests.git",
    ],
)
def test_accepts_safe_git_urls(url: str) -> None:
    assert check_git_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c 'touch /tmp/pwned'",
        "file:///etc/passwd",
        "--upload-pack=/bin/sh",
        "https://github.com/a/b extra",
        "ftp://example.com/repo.git",
        "https://github.com/a/b\nrm -rf /",
        "transport::custom",
    ],
)
def test_rejects_dangerous_git_urls(url: str) -> None:
    with pytest.raises(UnsafeTargetError):
        check_git_url(url)


@pytest.mark.parametrize(
    "rel",
    ["../../etc/passwd", "/etc/passwd", "C:/windows", "a/../../b", "", "\x00bad"],
)
def test_rejects_bad_relative_paths(rel: str) -> None:
    with pytest.raises(UnsafePathError):
        check_repo_relative_path(rel)


def test_accepts_normal_relative_path() -> None:
    assert check_repo_relative_path("./src//a.py") == "src/a.py"
    assert check_repo_relative_path("src\\a.py") == "src/a.py"


def test_ensure_within_blocks_escape(tmp_path: Path) -> None:
    base = tmp_path / "root"
    (base / "sub").mkdir(parents=True)
    assert ensure_within(base, base / "sub") == (base / "sub").resolve()
    with pytest.raises(PathEscapeError):
        ensure_within(base, base / ".." / "outside")
    with pytest.raises(PathEscapeError):
        ensure_within(base, Path("/etc/passwd"))


def test_ensure_within_blocks_symlink_escape(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = base / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(PathEscapeError):
        ensure_within(base, link)


def test_redact_secrets() -> None:
    text = "COGEN_LLM_API_KEY=sk-abcdef123456\nAuthorization: Bearer abcdefghijklmn"
    out = redact_secrets(text)
    assert "sk-abcdef123456" not in out
    assert "abcdefghijklmn" not in out
    assert "***" in out


def test_redact_url_credentials() -> None:
    assert redact_url("https://x-access-token:ghp_secret@github.com/a/b") == (
        "https://x-access-token:***@github.com/a/b"
    )


def test_sanitize_label() -> None:
    assert sanitize_label("  a\x00b\n c  ") == "a b c"
    assert len(sanitize_label("x" * 500, limit=10)) == 10


@pytest.mark.parametrize("bad", ["..", "a/b", "", "x" * 300, ".hidden"])
def test_repo_id_whitelist(bad: str) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        check_repo_id(bad)


def test_repo_id_accepts_generated_shape() -> None:
    assert check_repo_id("github.com__psf__requests@main-1a2b3c") == (
        "github.com__psf__requests@main-1a2b3c"
    )
    assert check_job_id("abcdef123456") == "abcdef123456"
