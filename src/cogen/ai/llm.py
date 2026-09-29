"""LLM 客户端封装：OpenAI 兼容、可选、永不阻塞主流程。

设计约定（计划 §3/§9）：
- 只做 **OpenAI 兼容** 的 HTTP 调用，不绑定厂商：``COGEN_LLM_BASE_URL/_API_KEY/_MODEL``；
- 未配置 Key 时抛 ``LLMNotConfigured``，调用方必须降级为确定性实现，**不能**让功能变不可用；
- 发送前对内容做密钥脱敏（``security.redact_secrets``），可用 ``COGEN_LLM_REDACT=0`` 关闭；
- 一次性请求失败重试一次；超时/限流都转成 ``LLMError``，由调用方决定降级。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from functools import lru_cache
from typing import Any

from ..config import Settings
from ..security import redact_secrets

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


class LLMNotConfigured(RuntimeError):
    """没有配置 API Key，AI 功能不可用（应降级）。"""


class LLMError(RuntimeError):
    """调用失败（网络、限流、返回格式异常）。"""


def is_configured(settings: Settings) -> bool:
    return bool(settings.llm_api_key)


def _client(settings: Settings) -> Any:
    from openai import OpenAI

    if not settings.llm_api_key:
        raise LLMNotConfigured("未配置 COGEN_LLM_API_KEY")
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or DEFAULT_BASE_URL,
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )


def _async_client(settings: Settings) -> Any:
    from openai import AsyncOpenAI

    if not settings.llm_api_key:
        raise LLMNotConfigured("未配置 COGEN_LLM_API_KEY")
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or DEFAULT_BASE_URL,
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )


def scrub(settings: Settings, text: str) -> str:
    """按配置决定是否对送出去的内容做密钥脱敏。"""
    return redact_secrets(text) if settings.llm_redact else text


def _scrub_messages(settings: Settings, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not settings.llm_redact:
        return messages
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            cleaned.append({**message, "content": redact_secrets(content)})
        else:
            cleaned.append(message)
    return cleaned


def complete(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> Any:
    """同步对话补全，返回原始 response（调用方按需取 message / tool_calls）。"""
    client = _client(settings)
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": _scrub_messages(settings, messages),
        "temperature": temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        return client.chat.completions.create(**payload)
    except LLMNotConfigured:
        raise
    except Exception as exc:  # 网络/鉴权/限流统一成 LLMError
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc


def complete_text(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> str:
    response = complete(settings, messages, temperature=temperature)
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMError("模型没有返回任何候选结果")
    return (choices[0].message.content or "").strip()


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抠出第一个 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def complete_json(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """要求模型输出 JSON：优先用 json_object 模式，失败则退化为解析文本。"""
    client = _client(settings)
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": _scrub_messages(settings, messages),
        "temperature": temperature,
        "max_tokens": settings.llm_max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        response = client.chat.completions.create(**payload)
    except Exception as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMError("模型没有返回任何候选结果")
    return parse_json_object(choices[0].message.content or "")


async def stream_complete(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> AsyncIterator[str]:
    """流式补全：逐段 yield 文本增量（供 SSE 用）。"""
    client = _async_client(settings)
    try:
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            messages=_scrub_messages(settings, messages),
            temperature=temperature,
            max_tokens=settings.llm_max_tokens,
            stream=True,
        )
        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0].delta, "content", None)
            if delta:
                yield delta
    except LLMNotConfigured:
        raise
    except Exception as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc


def iter_tool_calls(message: Any) -> Iterator[dict[str, Any]]:
    """把一条 assistant 消息里的 tool_calls 统一成 ``{id, name, arguments}``。"""
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        yield {
            "id": getattr(call, "id", "") or "",
            "name": getattr(function, "name", "") if function else "",
            "arguments": getattr(function, "arguments", "") if function else "",
        }


@lru_cache(maxsize=1)
def _cached_model_list() -> tuple[str, ...]:  # pragma: no cover - 仅调试用
    return ()


def describe(settings: Settings) -> dict[str, Any]:
    """给 /api/health 与前端展示用的 LLM 状态（不泄漏 Key）。"""
    return {
        "configured": is_configured(settings),
        "model": settings.llm_model,
        "baseUrl": settings.llm_base_url or DEFAULT_BASE_URL,
        "redact": settings.llm_redact,
    }
