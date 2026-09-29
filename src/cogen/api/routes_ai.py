"""AI 接口：社区命名、架构摘要、图谱问答（SSE）。

未配置 LLM 时：命名回落确定性启发式，问答返回明确的 ``error`` 事件；接口本身不失败。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..ai import ask as ask_module
from ..ai import llm, naming
from ..config import get_settings
from ..graph.store import Store, db_path_for
from ..i18n import t
from .deps import check_repo_id
from .routes_files import repo_context

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["ai"])


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


def _require_repo(repo_id: str) -> None:
    """流式响应开始前先校验仓库存在，否则 404 无法变成合法的 SSE。"""
    rid = check_repo_id(repo_id)
    settings = get_settings()
    db_file = db_path_for(settings, rid)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail=t("api.repoNotFound"))
    with Store(db_file) as store:
        if store.load_repo_meta() is None:
            raise HTTPException(status_code=404, detail=t("api.repoNotFound"))


def _sse(name: str, payload: dict[str, object]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(payload, ensure_ascii=False)}


@router.get("/ai/status")
def ai_status(repo_id: str) -> dict[str, object]:
    _require_repo(repo_id)
    settings = get_settings()
    return {
        **llm.describe(settings),
        "tools": ask_module.tool_names(),
        "rag": "graph-tools",
    }


@router.post("/ask")
async def ask_question(repo_id: str, payload: AskRequest, request: Request) -> EventSourceResponse:
    """图谱问答：SSE 推送工具调用轨迹 + token 流 + 引用节点。"""
    _require_repo(repo_id)
    history = [turn.model_dump() for turn in payload.history]

    async def gen() -> AsyncIterator[dict[str, str]]:
        try:
            with repo_context(repo_id) as (store, root, settings):
                async for event in ask_module.answer(
                    settings,
                    store,
                    payload.question,
                    root_path=str(root),
                    history=history,
                ):
                    if await request.is_disconnected():
                        return
                    yield _sse(str(event.get("type", "message")), event)
        except HTTPException as exc:
            yield _sse("error", {"type": "error", "message": str(exc.detail)})
        except Exception as exc:  # 兜底：SSE 已经开始，只能作为错误事件送出
            yield _sse("error", {"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return EventSourceResponse(gen(), ping=15)


@router.post("/communities/name")
def name_communities(
    repo_id: str,
    force: bool = Query(False),
    limit: int | None = Query(None, ge=1, le=500),
) -> dict[str, object]:
    """为社区生成名称与摘要（LLM 可用时用 LLM，否则用确定性启发式）。"""
    with repo_context(repo_id) as (store, _root, settings):
        return naming.name_communities(settings, store, force=force, limit=limit)


@router.get("/summary")
def architecture_summary(repo_id: str) -> dict[str, object]:
    """整仓架构摘要（LLM；未配置时返回启发式要点）。"""
    with repo_context(repo_id) as (store, _root, settings):
        return naming.architecture_summary(settings, store)
