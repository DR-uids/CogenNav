"""仓库目标解析：本地路径 / Git 地址 / ``owner/repo`` 简写 → 规范化 Target。

解析阶段**不联网**：只做形态识别、安全校验与 repoId 计算，克隆交给 ``clone.py``。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..config import Settings
from ..security import UnsafeTargetError, check_git_url, check_repo_relative_path, check_text

GITHUB_HOST = "github.com"

_SCHEME_URL = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<userinfo>[^/@]*@)?(?P<host>[^/:]+)"
    r"(?::(?P<port>\d+))?/(?P<path>.+)$"
)
_SCP_LIKE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9._-]+):(?P<path>.+)$")
_OWNER_REPO = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]*)/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?$"
)
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Target:
    """规范化后的索引目标。"""

    raw: str
    kind: Literal["local", "git"]
    ref: str | None = None
    subdir: str | None = None
    # 本地
    path: Path | None = None
    # 远端
    url: str | None = None
    host: str | None = None
    owner: str | None = None
    name: str | None = None


def slug(text: str, *, limit: int = 80) -> str:
    """把任意文本压成文件名安全的小写片段。"""
    out = _SLUG_UNSAFE.sub("-", text.strip()).strip("-").lower()
    return (out or "unnamed")[:limit]


def _looks_like_path(text: str) -> bool:
    return text.startswith(("/", "./", "../", "~"))


def resolve_target(raw: str, *, ref: str | None = None, subdir: str | None = None) -> Target:
    """解析目标。本地路径必须已存在；其余按 Git 地址处理。"""
    text = (raw or "").strip()
    if not text:
        raise UnsafeTargetError("仓库地址或本地路径不能为空")
    if "::" in text:
        # ext:: / transport:: 之类的辅助传输可以执行任意命令，必须在解析阶段就拒掉
        raise UnsafeTargetError("目标包含 '::'，拒绝非常规 Git 传输协议")

    clean_ref = check_text(ref, what="ref") if ref else None
    clean_subdir = check_repo_relative_path(subdir) if subdir else None

    if _looks_like_path(text):
        candidate = Path(text).expanduser()
        if not candidate.is_dir():
            raise UnsafeTargetError(f"本地路径不存在或不是目录: {candidate}")
        resolved = candidate.resolve()
        return Target(
            raw=text,
            kind="local",
            ref=clean_ref,
            subdir=clean_subdir,
            path=resolved,
            name=resolved.name,
        )

    # 无协议头的目标（含 owner/repo 简写）先看是否恰好是本地目录，否则按 Git 地址处理
    if "://" not in text and not _SCP_LIKE.match(text):
        candidate = Path(text).expanduser()
        if candidate.is_dir():
            resolved = candidate.resolve()
            return Target(
                raw=text,
                kind="local",
                ref=clean_ref,
                subdir=clean_subdir,
                path=resolved,
                name=resolved.name,
            )

    return _resolve_git(text, clean_ref, clean_subdir)


def _resolve_git(text: str, ref: str | None, subdir: str | None) -> Target:
    host = owner = name = None
    url = text

    match = _SCHEME_URL.match(text)
    if match:
        host = match.group("host")
        parts = [p for p in match.group("path").split("/") if p]
        if len(parts) >= 2:
            owner, name = parts[0], parts[1]
        elif parts:
            name = parts[0]
    else:
        scp = _SCP_LIKE.match(text)
        if scp:
            host = scp.group("host")
            parts = [p for p in scp.group("path").split("/") if p]
            if len(parts) >= 2:
                owner, name = parts[0], parts[1]
            elif parts:
                name = parts[0]
        else:
            local_repo = _OWNER_REPO.match(text)
            if not local_repo:
                raise UnsafeTargetError(
                    "无法识别的目标：请提供已存在的本地目录、"
                    "https://github.com/owner/repo 或 owner/repo"
                )
            host = GITHUB_HOST
            owner = local_repo.group("owner")
            name = local_repo.group("name")
            url = f"https://{GITHUB_HOST}/{owner}/{name}"

    if name and name.endswith(".git"):
        name = name[: -len(".git")]

    url = check_git_url(url)
    return Target(
        raw=text, kind="git", ref=ref, subdir=subdir, url=url, host=host, owner=owner, name=name
    )


def repo_id(target: Target) -> str:
    """稳定的仓库标识，同时是最安全的文件名：``<slug>@<ref>-<hash6>``。"""
    if target.kind == "local":
        assert target.path is not None
        digest = hashlib.sha1(str(target.path).encode("utf-8")).hexdigest()[:6]
        return f"local-{slug(target.path.name)}-{digest}"

    ref_slug = slug(target.ref or "HEAD", limit=40)
    digest = hashlib.sha1(f"{target.url}@{target.ref or ''}".encode()).hexdigest()[:6]
    parts = [slug(target.host or "git"), slug(target.owner or "_"), slug(target.name or "repo")]
    return f"{'__'.join(parts)}@{ref_slug}-{digest}"


def clone_path(settings: Settings, target: Target) -> Path:
    """远端仓库的快照目录：``.cogen/repos/<host>/<owner>/<name>/<ref>``。"""
    return (
        settings.repos_dir
        / slug(target.host or "git")
        / slug(target.owner or "_")
        / slug(target.name or "repo")
        / slug(target.ref or "HEAD", limit=40)
    )


def index_root(settings: Settings, target: Target) -> Path:
    """实际被遍历的根目录（本地目标支持 ``subdir`` 限定范围）。"""
    base = clone_path(settings, target) if target.kind == "git" else target.path
    assert base is not None
    if target.subdir:
        return base / target.subdir
    return base
