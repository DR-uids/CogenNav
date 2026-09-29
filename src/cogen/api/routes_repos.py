"""仓库相关接口：创建索引任务、列表、详情、删除。"""

from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from ..config import get_settings
from ..graph.schema import RepoMeta
from ..graph.store import Store, db_path_for, delete_repo_data, list_repo_metas, open_store
from ..ingest.pipeline import index_repo
from ..ingest.resolve import index_root, repo_id, resolve_target
from ..jobs import new_job_id
from ..security import PathEscapeError, UnsafeTargetError, ensure_within
from .deps import check_repo_id, get_manager

router = APIRouter(prefix="/api/repos", tags=["repos"])


class RepoCreateRequest(BaseModel):
    target: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, max_length=255)
    subdir: str | None = Field(default=None, max_length=1024)


class RepoCreateResponse(BaseModel):
    repoId: str
    jobId: str


@router.post("", status_code=201, response_model=RepoCreateResponse)
def create_repo(payload: RepoCreateRequest) -> RepoCreateResponse:
    """提交一个仓库目标，立即返回任务 id；真正的工作在后台线程执行。"""
    settings = get_settings()
    settings.ensure_dirs()

    try:
        target = resolve_target(payload.target, ref=payload.ref, subdir=payload.subdir)
    except UnsafeTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    root = index_root(settings, target)
    # 本地目标必须当场存在；远端目标的快照目录要等 clone 阶段才创建
    if target.kind == "local" and not root.is_dir():
        raise HTTPException(status_code=400, detail=f"索引根目录不存在: {root}")

    rid = repo_id(target)
    manager = get_manager()
    active = manager.active_for_repo(rid)
    if active is not None:
        raise HTTPException(status_code=409, detail=f"该仓库已有索引任务在执行（{active['id']}）")

    job_id = new_job_id()
    meta = RepoMeta(
        repo_id=rid,
        target=target.raw,
        source=target.kind,
        ref=target.ref,
        root_path=str(root),
        state="queued",
        message="排队中",
        job_id=job_id,
    )
    with open_store(settings, rid) as store:
        store.save_repo_meta(meta)

    manager.submit(job_id, meta, partial(index_repo, target, settings))
    return RepoCreateResponse(repoId=rid, jobId=job_id)


@router.get("")
def list_repos() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    manager = get_manager()
    repos = []
    for meta in list_repo_metas(settings):
        item = meta.to_api()
        # 若该仓库有正在跑的任务，用任务状态覆盖仓库状态（meta 只在阶段末更新）
        active = manager.active_for_repo(meta.repo_id)
        if active is not None:
            item["state"] = active["state"]
            item["message"] = active["message"] or item["message"]
            item["jobId"] = active["id"]
        repos.append(item)
    return {"repos": repos}


@router.get("/{repo_id}")
def get_repo(repo_id: str) -> dict[str, object]:
    rid = check_repo_id(repo_id)
    settings = get_settings()
    db_file = db_path_for(settings, rid)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail="仓库不存在")
    with Store(db_file) as store:
        meta = store.load_repo_meta()
    if meta is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    return meta.to_api()


@router.delete("/{repo_id}", status_code=204)
def delete_repo(repo_id: str) -> Response:
    rid = check_repo_id(repo_id)
    settings = get_settings()
    db_file = db_path_for(settings, rid)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail="仓库不存在")

    with Store(db_file) as store:
        meta = store.load_repo_meta()

    delete_repo_data(settings, rid)

    # 只清理 .cogen/repos 内的克隆快照：先做越界校验，再逐个删除目录
    if meta is not None and meta.source == "git":
        try:
            snapshot = ensure_within(settings.repos_dir, Path(meta.root_path))
        except PathEscapeError:
            snapshot = None
        if snapshot is not None and snapshot.exists():
            shutil.rmtree(snapshot, ignore_errors=True)
            _prune_empty_parents(snapshot.parent)

    return Response(status_code=204)


def _prune_empty_parents(path: Path) -> None:
    """删除克隆快照后，把空掉的 host/owner/name 目录一并清理。"""
    stop = get_settings().repos_dir.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
