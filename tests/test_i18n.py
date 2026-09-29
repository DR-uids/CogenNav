"""后端语言切换：``Accept-Language`` → 错误 detail 与索引进度文案。

前端可以切中英文，后端文案必须跟着走，否则英文界面里会冒出中文报错。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from cogen.i18n import (
    DEFAULT_LOCALE,
    MESSAGES,
    SUPPORTED_LOCALES,
    current_locale,
    reset_locale,
    resolve_locale,
    set_locale,
    t,
    what_label,
)


def test_catalog_has_all_locales_in_sync() -> None:
    """少一条翻译就是漏译：所有语言的键必须完全一致。"""
    reference = set(MESSAGES[DEFAULT_LOCALE])
    for locale in SUPPORTED_LOCALES:
        assert set(MESSAGES[locale]) == reference, locale
    assert set(MESSAGES) == set(SUPPORTED_LOCALES)


def test_resolve_locale_parses_accept_language() -> None:
    assert resolve_locale("en-US,en;q=0.9,zh;q=0.8") == "en"
    assert resolve_locale("zh-CN") == "zh"
    assert resolve_locale("zh-Hans-CN,zh;q=0.9") == "zh"
    # 不认识的语言与缺失的请求头都回落到默认（中文），保证 CLI / MCP 行为不变
    assert resolve_locale("fr-FR,fr;q=0.9") == DEFAULT_LOCALE
    assert resolve_locale(None) == DEFAULT_LOCALE
    assert resolve_locale("") == DEFAULT_LOCALE


def test_translate_interpolates_and_keeps_unknown_placeholders() -> None:
    assert t("api.repoNotFound", locale="en") == "Repository not found"
    assert t("api.dirNotFound", locale="en", path="src/api") == "Directory not found: src/api"
    assert t("api.dirNotFound", locale="zh", path="src/api") == "目录不存在: src/api"
    # 缺参数时保留占位符，而不是渲染出 "None"
    assert "{path}" in t("api.dirNotFound", locale="en")
    # 未知键原样返回，便于定位漏配的文案
    assert t("nope.missing", locale="en") == "nope.missing"


def test_what_label_follows_current_locale() -> None:
    set_locale("en")
    try:
        assert what_label("target") == "target"
        assert t("security.empty", what=what_label("target")) == "target cannot be empty"
    finally:
        set_locale(DEFAULT_LOCALE)
    assert what_label("repoUrl") == "仓库地址"
    assert current_locale() == DEFAULT_LOCALE


def test_locale_context_is_scoped() -> None:
    token = set_locale("en")
    assert current_locale() == "en"
    reset_locale(token)
    assert current_locale() == DEFAULT_LOCALE


def test_api_detail_follows_accept_language(client: TestClient) -> None:
    """同步路由跑在工作线程里，ContextVar 必须跟着过去（否则中间件等于白设）。"""
    zh = client.get("/api/repos/no-such-repo")
    assert zh.status_code == 404
    assert zh.json()["detail"] == "仓库不存在"

    en = client.get("/api/repos/no-such-repo", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert en.status_code == 404
    assert en.json()["detail"] == "Repository not found"

    # 不认识的请求头回落中文，保证既有客户端行为不变
    other = client.get("/api/repos/no-such-repo", headers={"Accept-Language": "de"})
    assert other.json()["detail"] == "仓库不存在"


def test_security_error_follows_accept_language(client: TestClient) -> None:
    """领域异常（安全校验）也要跟着请求语言走。"""
    body = {"target": "https://github.com/owner/repo", "ref": "-x"}
    en = client.post("/api/repos", json=body, headers={"Accept-Language": "en"})
    assert en.status_code == 400
    assert "cannot start with" in en.json()["detail"]

    zh = client.post("/api/repos", json=body)
    assert zh.status_code == 400
    assert "不能以" in zh.json()["detail"]


def test_index_progress_uses_submitting_language(
    client: TestClient, demo_repo: Path, wait_job: Callable[..., dict[str, object]]
) -> None:
    """索引跑在独立线程：语言在提交时捕获，进度与终态文案都是提交者的语言。"""
    created = client.post(
        "/api/repos",
        json={"target": str(demo_repo)},
        headers={"Accept-Language": "en"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    job = wait_job(client, payload["jobId"])
    assert job["state"] == "done", job
    assert "files /" in str(job["message"])

    meta = client.get(f"/api/repos/{payload['repoId']}").json()
    assert "files /" in str(meta["message"])


def test_index_progress_defaults_to_chinese(
    client: TestClient, demo_repo: Path, wait_job: Callable[..., dict[str, object]]
) -> None:
    created = client.post("/api/repos", json={"target": str(demo_repo)})
    assert created.status_code == 201, created.text
    payload = created.json()

    job = wait_job(client, payload["jobId"])
    assert job["state"] == "done", job
    assert "个文件" in str(job["message"])


def test_ask_error_event_follows_accept_language(
    client: TestClient, index_demo: dict[str, object]
) -> None:
    """SSE 生成器在子任务里迭代：语言同样要读到（否则对话框里会冒出中文报错）。"""
    repo_id = str(index_demo["repoId"])
    url = f"/api/repos/{repo_id}/ask"

    en = client.post(url, json={"question": "hi"}, headers={"Accept-Language": "en"})
    assert en.status_code == 200, en.text
    assert "LLM is not configured" in en.text

    zh = client.post(url, json={"question": "hi"})
    assert zh.status_code == 200, zh.text
    assert "未配置 LLM" in zh.text


def test_tree_root_name_follows_accept_language(
    client: TestClient, index_demo: dict[str, object]
) -> None:
    repo_id = str(index_demo["repoId"])

    en = client.get(f"/api/repos/{repo_id}/tree", headers={"Accept-Language": "en"})
    assert en.status_code == 200, en.text
    assert en.json()["node"]["name"] == "(root)"

    zh = client.get(f"/api/repos/{repo_id}/tree")
    assert zh.json()["node"]["name"] == "(根目录)"
