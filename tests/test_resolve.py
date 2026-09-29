"""目标解析测试：本地路径 / Git 地址 / repoId 稳定性。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cogen.config import Settings
from cogen.ingest.resolve import clone_path, index_root, repo_id, resolve_target
from cogen.security import UnsafePathError, UnsafeTargetError


def test_resolve_local_directory(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    target = resolve_target(str(repo))
    assert target.kind == "local"
    assert target.path == repo.resolve()
    rid = repo_id(target)
    assert rid.startswith("local-my-repo-")
    # 同一路径重复解析必须得到同一个 id（增量索引的前提）
    assert repo_id(resolve_target(str(repo))) == rid


def test_resolve_relative_path_rejected_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UnsafeTargetError):
        resolve_target("./not-there")


def test_resolve_owner_repo_shorthand() -> None:
    target = resolve_target("psf/requests")
    assert target.kind == "git"
    assert target.url == "https://github.com/psf/requests"
    assert (target.host, target.owner, target.name) == ("github.com", "psf", "requests")
    assert repo_id(target).startswith("github.com__psf__requests@")


def test_resolve_https_and_scp() -> None:
    https = resolve_target("https://github.com/psf/requests.git")
    assert https.name == "requests"
    scp = resolve_target("git@github.com:psf/requests.git")
    assert scp.host == "github.com"
    assert scp.url == "git@github.com:psf/requests.git"


def test_resolve_with_ref_and_subdir() -> None:
    target = resolve_target("psf/requests", ref="v2.31.0", subdir="src/requests")
    assert target.ref == "v2.31.0"
    assert target.subdir == "src/requests"
    assert "@v2.31.0-" in repo_id(target)


def test_resolve_rejects_ref_injection() -> None:
    with pytest.raises(UnsafeTargetError):
        resolve_target("psf/requests", ref="--upload-pack=/bin/sh")
    with pytest.raises(UnsafePathError):
        resolve_target("psf/requests", subdir="../../etc")


def test_resolve_rejects_dangerous_targets() -> None:
    for bad in ["ext::sh -c true", "file:///etc/passwd", "--upload-pack=/bin/sh", ""]:
        with pytest.raises(UnsafeTargetError):
            resolve_target(bad)


def test_rejects_transport_scheme_with_explicit_message() -> None:
    """ext:: 之类的辅助传输直接拒，绝不能落到 git。"""
    with pytest.raises(UnsafeTargetError, match="::"):
        resolve_target("ext::sh -c 'touch /tmp/pwned'")


def test_clone_and_index_paths(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home=tmp_path / ".cogen")  # type: ignore[call-arg]
    target = resolve_target("psf/requests", ref="main")
    path = clone_path(settings, target)
    assert path == settings.repos_dir / "github.com" / "psf" / "requests" / "main"
    assert index_root(settings, target) == path

    local = tmp_path / "local-repo"
    local.mkdir()
    scoped = resolve_target(str(local), subdir="pkg")
    assert index_root(settings, scoped) == local.resolve() / "pkg"


def test_repo_id_differs_across_refs() -> None:
    main = repo_id(resolve_target("psf/requests", ref="main"))
    dev = repo_id(resolve_target("psf/requests", ref="dev"))
    assert main != dev
