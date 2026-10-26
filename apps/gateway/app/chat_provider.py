"""Chat provider interface. API keys stay in the server environment.

The browser never receives the provider key, the request body, or a provider
error body. A non-200 response logs the HTTP status and a sanitized detail.
Tests supply their own implementation and do not call this client.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx2

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """The provider did not return a usable answer. The message is fixed."""


@dataclass
class ToolCall:
    name: str
    arguments: object
    call_id: str = ""


@dataclass
class ModelTurn:
    message: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatProvider(Protocol):
    async def complete(self, *, messages: list[dict[str, str]], tools: list[dict[str, object]]) -> ModelTurn:
        """Return the next assistant turn. Implementations must not log prompts."""


class HttpChatProvider:
    """OpenAI-compatible chat completions. The key is not written to logs."""

    def __init__(self, *, url: str, api_key: str, model: str, timeout: float) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def complete(self, *, messages: list[dict[str, str]], tools: list[dict[str, object]]) -> ModelTurn:
        payload: dict[str, object] = {"model": self._model, "messages": messages}
        if tools:
            payload["tools"] = [_openai_tool(tool) for tool in tools]
        try:
            async with httpx2.AsyncClient(timeout=httpx2.Timeout(self._timeout)) as client:
                response = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                        "User-Agent": "urumi-gateway/1.0",
                    },
                    json=payload,
                )
        except Exception as exc:
            logger.warning("Chat provider failed: transport")
            raise ProviderError("The chat provider could not answer.") from exc
        status = response.status_code
        if status != 200:
            if isinstance(status, int):
                logger.warning(
                    "Chat provider rejected the completion: status=%s detail=%s",
                    status,
                    _provider_error_detail(response, self._api_key),
                )
            else:
                logger.warning("Chat provider rejected the completion: status=unreadable")
            raise ProviderError("The chat provider could not answer.")
        try:
            body = response.json()
            choice = body["choices"][0]["message"]
        except Exception as exc:
            logger.warning("Chat provider failed: unreadable")
            raise ProviderError("The chat provider could not answer.") from exc
        message = choice.get("content") if isinstance(choice, dict) else ""
        if not isinstance(message, str):
            message = ""
        calls: list[ToolCall] = []
        raw_calls = choice.get("tool_calls") if isinstance(choice, dict) else None
        if isinstance(raw_calls, list):
            for item in raw_calls[:8]:
                parsed = _parse_call(item)
                if parsed is not None:
                    calls.append(parsed)
        return ModelTurn(message=message[:4000], tool_calls=calls)


def provider_from_env(*, timeout: float) -> HttpChatProvider | None:
    key = os.environ.get("CHAT_API_KEY", "").strip()
    if not key:
        return None
    url = os.environ.get("CHAT_API_URL", "").strip() or "https://api.openai.com/v1/chat/completions"
    model = os.environ.get("CHAT_MODEL", "").strip() or "gpt-4o-mini"
    return HttpChatProvider(url=url, api_key=key, model=model, timeout=timeout)


def _provider_error_detail(response: object, api_key: str) -> str:
    """Return a short provider reason. Callers must not log the raw body."""
    try:
        body = response.json()  # type: ignore[attr-defined]
    except Exception:
        return "unreadable"
    message = _error_message(body)
    if message is None:
        return "unreadable"
    collapsed = " ".join(message.split())
    if not collapsed:
        return "unreadable"
    if _sensitive_detail(message, collapsed, api_key):
        return "redacted"
    return collapsed[:160]


def _error_message(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return None


_AUTHORIZATION_MARKERS = ("bearer", "authorization")
# Recognizable credential forms. A plain diagnostic has none of these shapes.
_TOKEN_SHAPED = re.compile(
    r"(?i)(?:"
    r"\b(?:sk|pk|rk|ak)-[a-z0-9_-]{4,}"
    r"|\beyJ[a-z0-9_-]{8,}(?:\.[a-z0-9_-]{8,}){1,2}"
    r"|\b(?:ghp|gho|ghu|ghs|ghr|glpat)_[a-z0-9_]{8,}"
    r"|\bgithub_pat_[a-z0-9_]{8,}"
    r"|\bxox[baprs]-[a-z0-9-]{8,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\burk_[0-9a-f]{8,}(?:\.[a-z0-9_-]+)?"
    r")"
)


def _sensitive_detail(raw: str, collapsed: str, api_key: str) -> bool:
    """True when the detail must be replaced with a fixed redacted value.

    Authorization markers are matched without case. A token-shaped value is
    redacted whole, including when it is not the configured provider key.
    """
    for text in (raw, collapsed):
        if api_key and api_key in text:
            return True
        folded = text.casefold()
        if any(marker in folded for marker in _AUTHORIZATION_MARKERS):
            return True
        if _TOKEN_SHAPED.search(text):
            return True
    return False


def _openai_tool(tool: dict[str, object]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name"),
            "description": tool.get("description") or "",
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _parse_call(item: object) -> ToolCall | None:
    if not isinstance(item, dict):
        return None
    function = item.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        return None
    call_id = item.get("id") if isinstance(item.get("id"), str) else ""
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        import json

        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = None
    return ToolCall(name=function["name"], arguments=arguments, call_id=call_id)
