"""仓库文件遍历：``.gitignore`` 语义 + 跳过规则 + 上限保护 + 二进制与语言识别。

安全与规模约定（计划 §8/§9）：
- **不跟随符号链接**、不递归 submodule；
- 单文件超限 / 总体积超限 / 文件数超限只记录跳过原因并截断，不抛异常中断整轮；
- 跳过 ``.env*``（密钥），保留 ``.env.example`` 之类的模板文件；
- 遍历顺序排序，保证可复现。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import PathSpec

from ..config import Settings
from ..graph.schema import FileRecord, SkippedFile
from ..i18n import t
from ..parse.languages import detect_language

#: 目录名精确匹配即跳过
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".cogen",
        ".cache",
        ".pnpm-store",
        ".pnpm-home",
        "node_modules",
        "bower_components",
        "jspm_packages",
        "vendor",
        "dist",
        "build",
        "out",
        "output",
        "target",
        "coverage",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".parcel-cache",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".gradle",
        ".idea",
        ".vscode",
        "Pods",
        "DerivedData",
    }
)

#: 命中的文件名（小写）视为生成物，跳过
SKIP_FILENAMES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "composer.lock",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "gemfile.lock",
        "go.sum",
        "mix.lock",
    }
)

SKIP_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map")

#: 精确匹配就跳过的文件名（VCS 标记、系统垃圾文件）。注意 ``.git`` 在子模块里是**文件**
SKIP_FILENAMES_EXACT = frozenset({".git", ".hg", ".svn", ".DS_Store", "Thumbs.db"})

#: ``.env*`` 默认跳过（可能含密钥），这些模板除外
ENV_TEMPLATES = frozenset(
    {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.test"}
)

_BINARY_SNIFF_BYTES = 8192
_PROGRESS_EVERY = 200


class _StopWalk(Exception):
    """内部信号：达到规模上限，提前结束遍历。"""


@dataclass
class WalkResult:
    root: Path
    files: list[FileRecord] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False
    truncated_reason: str | None = None


def count_loc(data: bytes) -> int:
    """行数：换行数 + 末行没有换行符时补 1。"""
    if not data:
        return 0
    newlines = data.count(b"\n")
    return newlines + (0 if data.endswith(b"\n") else 1)


def _relative_to_base(rel_path: str, base: str) -> str | None:
    if not base:
        return rel_path
    prefix = f"{base}/"
    if rel_path.startswith(prefix):
        return rel_path[len(prefix) :]
    return None


def _is_ignored(rel_path: str, specs: list[tuple[str, PathSpec]]) -> bool:
    for base, spec in specs:
        candidate = _relative_to_base(rel_path, base)
        if candidate is not None and spec.match_file(candidate):
            return True
    return False


def walk_repo(
    root: Path,
    settings: Settings,
    *,
    on_progress: Callable[[int, str], None] | None = None,
) -> WalkResult:
    """遍历 ``root``，返回可索引文件与跳过清单。"""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(t("walk.dirMissing", root=root))

    result = WalkResult(root=root)

    def handle_file(entry: Path, rel: str, st: os.stat_result) -> None:
        name_lower = entry.name.lower()
        if entry.name in SKIP_FILENAMES_EXACT:
            result.skipped.append(SkippedFile(rel, "vcs_marker"))
            return
        if name_lower in SKIP_FILENAMES or name_lower.endswith(SKIP_SUFFIXES):
            result.skipped.append(SkippedFile(rel, "generated"))
            return
        if name_lower.startswith(".env") and name_lower not in ENV_TEMPLATES:
            result.skipped.append(SkippedFile(rel, "secrets"))
            return

        if st.st_size > settings.max_file_bytes:
            result.skipped.append(SkippedFile(rel, "too_large"))
            return
        try:
            data = entry.read_bytes()
        except OSError as exc:
            result.skipped.append(SkippedFile(rel, f"unreadable: {exc.strerror or exc}"))
            return
        if b"\0" in data[:_BINARY_SNIFF_BYTES]:
            result.skipped.append(SkippedFile(rel, "binary"))
            return

        first_line = data.split(b"\n", 1)[0].decode("utf-8", "replace") if data else ""
        result.files.append(
            FileRecord(
                path=rel,
                language=detect_language(rel, first_line),
                size=len(data),
                loc=count_loc(data),
                sha256=hashlib.sha256(data).hexdigest(),
                mtime=float(st.st_mtime),
            )
        )
        result.total_bytes += len(data)

    def check_limits() -> None:
        if len(result.files) > settings.max_files:
            result.truncated = True
            result.truncated_reason = "max_files"
            raise _StopWalk
        if result.total_bytes > settings.max_total_bytes:
            result.truncated = True
            result.truncated_reason = "max_total_bytes"
            raise _StopWalk

    def walk_dir(dir_path: Path, specs: list[tuple[str, PathSpec]], rel: str) -> None:
        local_specs = specs
        gitignore = dir_path / ".gitignore"
        if gitignore.is_file():
            try:
                lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
                local_specs = [*specs, (rel, PathSpec.from_lines("gitignore", lines))]
            except OSError:
                local_specs = specs

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            result.skipped.append(SkippedFile(rel or ".", f"unreadable_dir: {exc.strerror or exc}"))
            return

        for entry in entries:
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            try:
                st = entry.lstat()
            except OSError:
                continue

            if stat.S_ISLNK(st.st_mode):
                result.skipped.append(SkippedFile(child_rel, "symlink"))
                continue

            if stat.S_ISDIR(st.st_mode):
                if entry.name in SKIP_DIRS:
                    result.skipped.append(SkippedFile(child_rel, "skip_dir"))
                    continue
                if _is_ignored(child_rel, local_specs):
                    result.skipped.append(SkippedFile(child_rel, "gitignore_dir"))
                    continue
                walk_dir(entry, local_specs, child_rel)
                continue

            if not stat.S_ISREG(st.st_mode):
                continue
            if _is_ignored(child_rel, local_specs):
                result.skipped.append(SkippedFile(child_rel, "gitignore"))
                continue

            handle_file(entry, child_rel, st)
            check_limits()
            if on_progress is not None and len(result.files) % _PROGRESS_EVERY == 0:
                on_progress(len(result.files), child_rel)

    with contextlib.suppress(_StopWalk):
        walk_dir(root, [], "")

    if on_progress is not None:
        on_progress(len(result.files), "")
    return result
