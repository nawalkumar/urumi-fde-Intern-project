"""Session-authenticated chat. A gateway API key cannot call this route."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.gateway.app.chat import ChatAnswer, ChatService
from apps.gateway.app.chat_provider import ChatProvider
from apps.gateway.app.config import Settings
from apps.gateway.app.directory import UserRecord
from apps.gateway.app.keys import StoreUnavailable, bearer_token

_COOKIE = "urumi_session"
_BAD = {"error": "invalid_request", "error_description": "The request was rejected."}
_UNAUTHORIZED = {"error": "invalid_token", "error_description": "Authentication required."}
_MAX_MESSAGES = 12
_MAX_CONTENT = 2000


def register_chat_routes(
    app: FastAPI,
    store: object,
    service: ChatService | None,
) -> None:
    @app.post("/admin/chat")
    async def chat(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(
                {"error": "unavailable", "error_description": "The chat provider is not configured."},
                status_code=503,
            )
        user = await _user(store, request)
        if isinstance(user, JSONResponse):
            return user
        raw = await request.body()
        if len(raw) > 32_000:
            return JSONResponse(_BAD, status_code=400)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_BAD, status_code=400)
        messages = _messages(body)
        if messages is None:
            return JSONResponse(_BAD, status_code=400)
        answer = await service.answer(user, messages)
        return JSONResponse(_public(answer), status_code=answer.status)


def build_chat_service(
    *,
    dispatcher: object,
    authenticator: object,
    auditor: object,
    store: object,
    registry: object,
    provider: ChatProvider | None,
    settings: Settings,
) -> ChatService | None:
    if provider is None:
        return None
    return ChatService(
        dispatcher=dispatcher,  # type: ignore[arg-type]
        authenticator=authenticator,  # type: ignore[arg-type]
        auditor=auditor,  # type: ignore[arg-type]
        store=store,
        registry=registry,
        provider=provider,
        settings=settings,
    )


def _public(answer: ChatAnswer) -> dict[str, object]:
    if answer.status != 200:
        return {"error": "unavailable", "error_description": answer.message, "activity": []}
    return {
        "message": answer.message,
        "activity": [{"tool": item.tool, "outcome": item.outcome} for item in answer.activity],
    }


def _messages(body: object) -> list[dict[str, str]] | None:
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return None
    rows = body["messages"]
    if not rows or len(rows) > _MAX_MESSAGES:
        return None
    cleaned: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        role = row.get("role")
        content = row.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            return None
        text = content.strip()
        if not text or len(text) > _MAX_CONTENT:
            return None
        cleaned.append({"role": role, "content": text})
    if cleaned[-1]["role"] != "user":
        return None
    return cleaned


def _session_token(request: Request) -> str | None:
    cookie = request.cookies.get(_COOKIE)
    if cookie:
        return cookie
    token = bearer_token(request.headers.get("authorization"))
    if token and token.startswith("urs_"):
        return token
    return None


async def _user(store: object, request: Request) -> UserRecord | JSONResponse:
    raw = _session_token(request)
    if not raw:
        return JSONResponse(_UNAUTHORIZED, status_code=401)
    try:
        user = await store.authenticate_session(raw)  # type: ignore[attr-defined]
    except StoreUnavailable:
        return JSONResponse(
            {"error": "unavailable", "error_description": "Authentication is unavailable."},
            status_code=503,
        )
    if user is None:
        return JSONResponse(_UNAUTHORIZED, status_code=401)
    return user
