"""接口冒烟测试（M0：健康检查与根路由）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cogen import __version__
from cogen.api.app import WEB_DIST, create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert "home" in body
    assert isinstance(body["llm_configured"], bool)


def test_root_route_responds() -> None:
    """未构建前端时返回 JSON 提示；构建后由静态站点接管（返回 HTML）。"""
    with TestClient(create_app()) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    if (WEB_DIST / "index.html").exists():
        assert resp.headers["content-type"].startswith("text/html")
    else:
        assert resp.json()["service"] == "CogenNav"
