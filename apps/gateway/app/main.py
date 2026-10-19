"""FastAPI gateway: health plus an aggregated Streamable HTTP MCP endpoint.

Clients authenticate with `Authorization: Bearer <gateway API key>` on `/mcp`.
The handler passes the sealed principal into ToolDispatcher.execute_tool.
tools/list uses that same principal and returns only tools the key may call.

GET /admin/status uses the same live-key check. Tool grants do not authorize
it. When Settings.status_permission is "required", the key also needs the
exact gateway__read_status permission. That name is not an upstream tool.
The default mode is "open", so an unset flag keeps accepting any live key.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.common.http import build_http_app
from apps.gateway.app.admin_api import register_admin_routes
from apps.gateway.app.chat_api import build_chat_service, register_chat_routes
from apps.gateway.app.chat_provider import provider_from_env
from apps.gateway.app.catalog import refresh_registry
from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.config import Settings
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.http_auth import ApiKeyGuard, current_principal, require_live_principal
from apps.gateway.app.permissions import STATUS_READ_PERMISSION
from apps.gateway.app.registry import Registry
from apps.gateway.app.keys import Principal, StoreUnavailable
from apps.gateway.app.store import InMemoryStore, PostgresStore, UnconfiguredStore
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult

KeyStore = InMemoryStore | PostgresStore | UnconfiguredStore
logger = logging.getLogger(__name__)

_STATUS_FORBIDDEN = {
    "error": "forbidden",
    "error_description": "Status access was denied.",
}


def create_app(settings: Settings | None = None, store: KeyStore | None = None, chat_provider: object | None = None):
    resolved = settings if settings is not None else Settings.from_env()
    resolved_store = store if store is not None else _store_from_settings(resolved)
    authenticator = Authenticator(resolved_store)
    auditor = Auditor(resolved_store)
    registry = Registry(resolved.upstreams)
    dispatcher = ToolDispatcher(registry, authenticator, auditor)

    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        try:
            granted = await authenticator.visible_names(current_principal.get())
        except StoreUnavailable:
            logger.warning("Permission check failed: StoreUnavailable")
            return ListToolsResult(tools=[])
        if not granted:
            return ListToolsResult(tools=[])
        listed = await registry.list_tools_result()
        return ListToolsResult(tools=[tool for tool in listed.tools if tool.name in granted])

    async def on_call_tool(_context: object, params: object) -> CallToolResult:
        name = getattr(params, "name")
        arguments = getattr(params, "arguments", None)
        return await dispatcher.execute_tool(current_principal.get(), name, arguments)

    mcp = Server("urumi-gateway", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def health():
        if await resolved_store.ping():
            return {"status": "ok"}
        return JSONResponse({"status": "unavailable"}, status_code=503)

    def register_status(app: FastAPI) -> None:
        @app.get("/admin/status")
        async def admin_status(request: Request) -> Any:
            gate = await require_live_principal(
                authenticator,
                auditor,
                request.headers.get("authorization"),
            )
            if not isinstance(gate, Principal):
                return gate
            if resolved.status_permission == "required":
                try:
                    allowed = await authenticator.permits(gate.key_id, STATUS_READ_PERMISSION)
                except StoreUnavailable:
                    logger.warning("Status permission check failed: StoreUnavailable")
                    return JSONResponse(
                        {
                            "error": "unavailable",
                            "error_description": "Authentication is unavailable.",
                        },
                        status_code=503,
                    )
                if not allowed:
                    await auditor.record(gate.key_id, STATUS_READ_PERMISSION, "unauthorized")
                    return JSONResponse(_STATUS_FORBIDDEN, status_code=403)
            await registry.list_tools_result()
            return registry.status_report()

    @asynccontextmanager
    async def opened():
        async with resolved_store.lifespan():
            try:
                await refresh_registry(registry, resolved_store, resolved)
            except StoreUnavailable:
                logger.warning("Upstream catalog was not loaded: StoreUnavailable")
            yield

    def register_routes(app: FastAPI) -> None:
        register_status(app)
        register_admin_routes(app, resolved_store, registry, resolved)
        provider = provider_from_env(timeout=resolved.chat_provider_timeout_seconds) if chat_provider is None else chat_provider
        register_chat_routes(
            app,
            resolved_store,
            build_chat_service(
                dispatcher=dispatcher,
                authenticator=authenticator,
                auditor=auditor,
                store=resolved_store,
                registry=registry,
                provider=provider,  # type: ignore[arg-type]
                settings=resolved,
            ),
        )

    app = build_http_app(
        mcp,
        health,
        asgi_wrapper=lambda asgi: ApiKeyGuard(asgi, authenticator, auditor),
        store_lifespan=opened,
        register_routes=register_routes,
    )
    app.state.registry = registry
    return app


def _store_from_settings(settings: Settings) -> KeyStore:
    if settings.database_url:
        return PostgresStore(settings.database_url)
    return UnconfiguredStore()


app = create_app()
