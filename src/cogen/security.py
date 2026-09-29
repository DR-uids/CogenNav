"""输入校验与脱敏：所有外部输入（仓库目标、仓库内路径）都必须先过这里。

对应计划 §9 的 L1 安全基线：
- 目标白名单，拒绝 ``ext::``、``file://``、以 ``-`` 开头等参数注入形态；
- 路径越界校验（防 ``..`` 与符号链接穿越）；
- 文本脱敏，避免密钥进入日志、图谱或 LLM 提示词。
"""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED_SCHEMES = frozenset({"http", "https", "git", "ssh"})

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")
_SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")

# 键值型密钥：KEY=value / key: "value"（不要求词边界，避免漏掉 FOO_API_KEY=...）
_KV_SECRET = re.compile(
    r"((?:api[_-]?key|apikey|secret|token|password|passwd|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret)\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})\2",
    re.IGNORECASE,
)
# Authorization: Bearer xxx
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-+/=]{8,})")
# URL 内嵌凭据：https://user:token@host/...
_URL_CREDS = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)")


class UnsafeTargetError(ValueError):
    """目标（URL 或路径）不满足安全约束。"""


class UnsafePathError(ValueError):
    """仓库内相对路径不合法（绝对路径、``..``、控制字符等）。"""


class PathEscapeError(ValueError):
    """路径解析后落在允许的根目录之外。"""


def check_text(raw: str, *, what: str = "目标") -> str:
    """通用文本校验：非空、无控制字符、无空白、不以 ``-`` 开头。"""
    text = (raw or "").strip()
    if not text:
        raise UnsafeTargetError(f"{what}不能为空")
    if _CONTROL_CHARS.search(text):
        raise UnsafeTargetError(f"{what}包含控制字符")
    if any(ch.isspace() for ch in text):
        raise UnsafeTargetError(f"{what}不能包含空白字符")
    if text.startswith("-"):
        # 防参数注入：`git clone <target>` 中形如 --upload-pack=... 的目标
        raise UnsafeTargetError(f"{what}不能以 '-' 开头")
    if text.startswith("~"):
        raise UnsafeTargetError(f"{what}不支持 ~ 展开，请使用绝对路径")
    return text


def check_git_url(url: str) -> str:
    """校验一个 Git 远端地址：仅允许 http/https/git/ssh 与 scp 形式。"""
    text = check_text(url, what="仓库地址")
    if "::" in text:
        # ext:: / transport:: 之类的辅助传输可以执行任意命令
        raise UnsafeTargetError("仓库地址包含 '::'，拒绝非常规传输协议")
    match = _SCHEME.match(text)
    if match:
        scheme = match.group("scheme").lower()
        if scheme not in ALLOWED_SCHEMES:
            raise UnsafeTargetError(f"不允许的协议: {scheme}://（仅支持 http/https/git/ssh）")
        return text
    if _SCP_LIKE.match(text):
        return text
    raise UnsafeTargetError("仓库地址必须是 http(s)://、git://、ssh:// 或 user@host:path 形式")


def check_repo_relative_path(rel: str) -> str:
    """校验来自 API 的仓库内相对路径（用于取文件、取 CST）。"""
    text = (rel or "").strip().replace("\\", "/")
    if not text:
        raise UnsafePathError("路径不能为空")
    if _CONTROL_CHARS.search(text):
        raise UnsafePathError("路径包含控制字符")
    if text.startswith("/") or _WINDOWS_DRIVE.match(text):
        raise UnsafePathError("必须是仓库内的相对路径")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePathError("路径不能包含 '..'")
    return "/".join(parts)


def ensure_within(base: Path, candidate: Path) -> Path:
    """把 candidate 解析成绝对路径，并确保它仍位于 base 之内（防符号链接穿越）。"""
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved == base_resolved:
        return candidate_resolved
    if base_resolved not in candidate_resolved.parents:
        raise PathEscapeError(f"路径越界: {candidate} 不在 {base} 之内")
    return candidate_resolved


def redact_url(url: str) -> str:
    """抹掉 URL 中内嵌的用户名/口令（例如 https://user:token@host/...）。"""
    return _URL_CREDS.sub(r"\1***\3", url)


def redact_secrets(text: str) -> str:
    """脱敏常见密钥形态；用于日志、错误信息与送入 LLM 的片段。"""
    if not text:
        return text
    out = _URL_CREDS.sub(r"\1***\3", text)
    out = _KV_SECRET.sub(r"\1\2***\2", out)
    return _BEARER.sub(r"\1***", out)


def sanitize_label(text: str, *, limit: int = 256) -> str:
    """清理展示用文本：控制字符转空格、压缩空白、截断。"""
    cleaned = _CONTROL_CHARS.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]
