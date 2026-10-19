"""Chat provider interface. API keys stay in the server environment.

The browser never receives the provider key, the request body, or a provider
error body. Tests supply their own implementation and do not call this client.
"""

import logging
import os
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
                logger.warning("Chat provider rejected the completion: status=%s", status)
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
