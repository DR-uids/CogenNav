"""API 层的共享单例与校验助手。"""

from __future__ import annotations

import re

from fastapi import HTTPException

from ..config import Settings, get_settings
from ..jobs import JobManager
from ..security import PathEscapeError, ensure_within

_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,199}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9-]{4,64}$")


def settings_dep() -> Settings:
    return get_settings()


_manager: JobManager | None = None


def get_manager() -> JobManager:
    """进程内单例任务管理器（懒创建）。"""
    global _manager
    if _manager is None:
        _manager = JobManager(get_settings())
    return _manager


def shutdown_state() -> None:
    """关闭任务管理器（应用退出或测试清理）。"""
    global _manager
    if _manager is not None:
        _manager.shutdown()
        _manager = None


def check_repo_id(repo_id: str) -> str:
    """repoId 会参与文件路径拼接，必须严格白名单。"""
    if not _REPO_ID.match(repo_id or ""):
        raise HTTPException(status_code=404, detail="仓库不存在")
    return repo_id


def check_job_id(job_id: str) -> str:
    if not _JOB_ID.match(job_id or ""):
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_id


def safe_db_path(settings: Settings, repo_id: str) -> str:
    """在拼接数据库路径前再做一次越界校验（纵深防御）。"""
    from ..graph.store import db_path_for

    path = db_path_for(settings, repo_id)
    try:
        ensure_within(settings.db_dir, path)
    except PathEscapeError as exc:
        raise HTTPException(status_code=404, detail="仓库不存在") from exc
    return str(path)
