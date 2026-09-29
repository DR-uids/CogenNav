"""任务相关接口：状态查询与 SSE 进度流。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..i18n import t
from ..jobs import JobManager, is_terminal
from .deps import check_job_id, get_manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

#: 订阅等待心跳；超时后回查一次任务状态，避免任务进程异常退出时连接悬挂
_POLL_TIMEOUT_S = 10.0


def _event_name(state: str) -> str:
    if state == "done":
        return "done"
    if state == "error":
        return "error"
    return "progress"


def _sse(state: str, payload: dict[str, object]) -> dict[str, str]:
    return {
        "event": _event_name(state),
        "data": json.dumps(payload, ensure_ascii=False),
    }


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = get_manager().status(check_job_id(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail=t("api.jobNotFound"))
    return job


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
    """SSE 进度流：先回放当前状态，再推送后续事件，终态后关闭。"""
    manager: JobManager = get_manager()
    jid = check_job_id(job_id)
    initial = manager.status(jid)
    if initial is None:
        raise HTTPException(status_code=404, detail=t("api.jobNotFound"))

    async def gen() -> AsyncIterator[dict[str, str]]:
        yield _sse(str(initial["state"]), initial)
        if is_terminal(str(initial["state"])):
            return

        queue = manager.subscribe(jid, asyncio.get_running_loop())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_POLL_TIMEOUT_S)
                except asyncio.TimeoutError:
                    # 兜底：任务线程若异常消失，这里把它判成终态，避免前端一直等
                    status = manager.status(jid)
                    if status is None or is_terminal(str(status["state"])):
                        if status is not None:
                            yield _sse(str(status["state"]), status)
                        return
                    continue
                yield _sse(event.state, event.to_api())
                if is_terminal(event.state):
                    return
        finally:
            manager.unsubscribe(jid, queue)

    return EventSourceResponse(gen(), ping=15)
