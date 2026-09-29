"""FastAPI 应用：REST + SSE 接口，并在构建过前端后托管 web/dist。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..i18n import reset_locale, resolve_locale, set_locale, t
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

    @app.middleware("http")
    async def locale_from_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """把 ``Accept-Language`` 固定成请求上下文里的语言。

        中间件里 set、finally 里 reset：语言只在本次请求内有效，不会串到别的请求。
        同步路由跑在 anyio 的工作线程里，ContextVar 会随之复制；后台索引任务线程
        不继承上下文，因此提交任务时单独把语言传给 ``JobManager.submit``。
        """
        token = set_locale(resolve_locale(request.headers.get("accept-language")))
        try:
            return await call_next(request)
        finally:
            reset_locale(token)

    @app.middleware("http")
    async def no_store_api(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """API 一律禁缓存。

        404/405/410/414 这类 4xx 按 RFC 9110 是**默认可缓存**的：一次瞬时失败
        （比如刚提交索引、快照还没就绪）会被浏览器缓存住，之后"刷新也还是它"，
        看起来像后端一直没修好。SSE 也不该被缓存，所以统一打 no-store。
        """
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

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
            "hint": t("api.webNotBuilt"),
            "health": "/api/health",
        }


app = create_app()
