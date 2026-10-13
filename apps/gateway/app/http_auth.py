"""Require a gateway API key before the Streamable HTTP MCP app runs."""

import contextvars
import json

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.keys import Principal, StoreUnavailable, UNAUTHENTICATED, bearer_token

current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "gateway_principal",
    default=None,
)

_AUTH_BODY = {
    "error": "invalid_token",
    "error_description": "Authentication required.",
}
_UNAVAILABLE_BODY = {
    "error": "unavailable",
    "error_description": "Authentication is unavailable.",
}


class ApiKeyGuard:
    """ASGI wrapper for the mounted MCP app. /health is registered outside this wrapper."""

    def __init__(self, app: ASGIApp, authenticator: Authenticator, auditor: Auditor) -> None:
        self.app = app
        self._authenticator = authenticator
        self._auditor = auditor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            principal = await self._authenticator.authenticate(_presented_token(scope))
        except StoreUnavailable:
            await _send_json(send, 503, _UNAVAILABLE_BODY, www_authenticate=False)
            return
        if principal is None:
            await self._auditor.record(UNAUTHENTICATED, "", "unauthorized")
            await _send_json(send, 401, _AUTH_BODY, www_authenticate=True)
            return
        token = current_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            current_principal.reset(token)


async def require_live_principal(
    authenticator: Authenticator,
    auditor: Auditor,
    authorization: str | None,
) -> Principal | JSONResponse:
    """Accept the same live gateway key as /mcp. Tool grants are not consulted.

    Missing, malformed, unknown, and revoked keys produce the same 401 body.
    A live key is enough. This is not an admin role and not a tool permission.
    """
    try:
        principal = await authenticator.authenticate(bearer_token(authorization))
    except StoreUnavailable:
        return JSONResponse(_UNAVAILABLE_BODY, status_code=503)
    if principal is None:
        await auditor.record(UNAUTHENTICATED, "", "unauthorized")
        return JSONResponse(
            _AUTH_BODY,
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return principal


def _presented_token(scope: Scope) -> str | None:
    headers = scope.get("headers") or []
    raw_header = None
    for name, value in headers:
        if name.lower() == b"authorization":
            try:
                raw_header = value.decode("latin-1")
            except Exception:
                return None
            break
    return bearer_token(raw_header)


async def _send_json(send: Send, status_code: int, body: dict[str, str], *, www_authenticate: bool) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if www_authenticate:
        headers.append((b"www-authenticate", b'Bearer error="invalid_token"'))
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
