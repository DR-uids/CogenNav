"""Git 浅克隆：argv 数组 + 协议白名单 + 体积/时长看门狗。

安全要点（计划 §9）：
- 只用 argv 数组（``shell=False``），URL 已在 ``resolve`` 阶段白名单校验；
- 隔离环境：``GIT_CONFIG_NOSYSTEM`` + ``GIT_CONFIG_GLOBAL=/dev/null``（不读用户全局配置，
  避免把仓库外的 filter/hook 带进来）、``GIT_TERMINAL_PROMPT=0``、协议白名单；
- **token 走环境变量注入 http.extraHeader，绝不进入 argv**，日志/异常一律脱敏；
- 独立进程组 + 磁盘体积与墙钟双看门狗，超限即 kill 并清理半成品目录。
"""

from __future__ import annotations

import base64
import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from ..security import redact_secrets
from .resolve import Target

_POLL_INTERVAL_S = 0.5
_ERROR_TAIL_BYTES = 2000


class CloneError(RuntimeError):
    """克隆失败（网络、鉴权、超限或超时）。"""


def _build_env(cache_dir: Path, token: str | None) -> dict[str, str]:
    git_home = cache_dir / "git-home"
    git_tmp = cache_dir / "git-tmp"
    git_home.mkdir(parents=True, exist_ok=True)
    git_tmp.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(git_home),
        "TMPDIR": str(git_tmp),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": shutil.which("true") or "/usr/bin/true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ALLOW_PROTOCOL": "http:https:git:ssh",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
    }
    if token:
        # 通过 git 的 config-from-env 注入认证头：token 不出现在命令行参数里
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {basic}"
    return env


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
        for name in list(dirnames):
            try:
                if os.path.islink(os.path.join(dirpath, name)):
                    dirnames.remove(name)
            except OSError:
                continue
    return total


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def head_sha(repo_dir: Path, settings: Settings) -> str | None:
    """读取快照的 HEAD commit；失败返回 None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_build_env(settings.cache_dir, None),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def clone_repo(
    target: Target,
    dest: Path,
    settings: Settings,
    *,
    on_message: Callable[[str], None] | None = None,
) -> str | None:
    """把远端仓库浅克隆到 ``dest``；已存在快照则复用。返回 commit sha。"""
    if target.url is None:
        raise CloneError("目标不是远端仓库")
    dest = Path(dest)

    if (dest / ".git").is_dir():
        if on_message:
            on_message("复用已有快照")
        return head_sha(dest, settings)

    dest.parent.mkdir(parents=True, exist_ok=True)
    err_file = settings.cache_dir / "clone-stderr.log"
    err_file.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--no-recurse-submodules",
        "--quiet",
    ]
    if target.ref:
        argv += ["--branch", target.ref]
    argv += [target.url, str(dest)]

    env = _build_env(settings.cache_dir, settings.git_token)
    if on_message:
        on_message("正在浅克隆仓库…")

    with open(err_file, "wb") as err_handle:
        try:
            proc = subprocess.Popen(
                argv,
                env=env,
                cwd=str(dest.parent),
                stdout=subprocess.DEVNULL,
                stderr=err_handle,
                start_new_session=True,
            )
        except OSError as exc:
            raise CloneError(f"无法启动 git: {exc}") from exc

        deadline = time.monotonic() + settings.clone_timeout_s
        while True:
            try:
                proc.wait(timeout=_POLL_INTERVAL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > deadline:
                _kill_tree(proc)
                shutil.rmtree(dest, ignore_errors=True)
                raise CloneError(f"克隆超时（超过 {settings.clone_timeout_s:.0f} 秒）")
            if _dir_size(dest) > settings.max_total_bytes:
                _kill_tree(proc)
                shutil.rmtree(dest, ignore_errors=True)
                raise CloneError(f"仓库体积超过上限（{settings.max_total_bytes / 2**30:.1f} GiB）")

    if proc.returncode != 0:
        detail = _read_error_tail(err_file)
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneError(f"git clone 失败（exit {proc.returncode}）: {detail}")

    sha = head_sha(dest, settings)
    if on_message:
        on_message(f"克隆完成 {sha[:8] if sha else ''}".strip())
    return sha


def _read_error_tail(err_file: Path) -> str:
    try:
        data = err_file.read_bytes()[-_ERROR_TAIL_BYTES:]
    except OSError:
        return "（无 stderr 输出）"
    text = data.decode("utf-8", "replace").strip()
    return redact_secrets(text) or "（无 stderr 输出）"
