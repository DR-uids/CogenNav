"""FastAPI 应用：REST + SSE 接口，并在构建过前端后托管 web/dist。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from . import deps
from .routes_ai import router as ai_router
from .routes_files import router as files_router
from .routes_graph import router as graph_router
from .routes_jobs import router as jobs_router
from .routes_repos import router as repos_router

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = REPO_ROOT / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    yield
    # 退出时关闭任务线程池与 jobs.sqlite 连接
    deps.shutdown_state()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CogenNav",
        version=__version__,
        description="代码仓库 CST 解析与知识图谱导航服务",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    # 开发期：Vite dev server 直连后端时使用（默认走代理，不需要 CORS）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5199", "http://localhost:5199"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "home": str(settings.root),
            "llm_configured": settings.llm_configured,
            "web_built": (WEB_DIST / "index.html").exists(),
        }

    app.include_router(repos_router)
    app.include_router(jobs_router)
    app.include_router(files_router)
    app.include_router(graph_router)
    app.include_router(ai_router)

    _mount_web(app)
    return app


def _mount_web(app: FastAPI) -> None:
    if (WEB_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
        return

    @app.get("/")
    def index_hint() -> dict[str, str]:
        return {
            "service": "CogenNav",
            "hint": "前端尚未构建。开发时运行 `make dev`（Vite: http://127.0.0.1:5199），"
            "或运行 `make build-web` 后由本服务托管。",
            "health": "/api/health",
        }


app = create_app()
