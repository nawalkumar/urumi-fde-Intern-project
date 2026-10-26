"""FastAPI gateway: health plus an aggregated Streamable HTTP MCP endpoint.

Clients authenticate with `Authorization: Bearer <gateway API key>` on `/mcp`.
The handler passes the sealed principal into ToolDispatcher.execute_tool.
tools/list uses that same principal and returns only tools the key may call.

GET /admin/status uses the same live-key check. Tool grants do not authorize
it. When Settings.status_permission is "required", the key also needs the
exact gateway__read_status permission. That name is not an upstream tool.
The default mode is "open", so an unset flag keeps accepting any live key.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.common.http import build_http_app
from apps.gateway.app.admin_api import _admin, register_admin_routes
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
from apps.gateway.app.directory import UserRecord
from apps.gateway.app.store import InMemoryStore, PostgresStore, UnconfiguredStore
from apps.gateway.app.stores.api import register_store_routes
from apps.gateway.app.stores.cluster import ClusterClient, incluster_client
from apps.gateway.app.discovery import (
    DiscoveryResult,
    DiscoveryState,
    client_search_body,
    correlation_id,
    discover,
    list_result,
    operator_summary,
    rank_tools,
    validate_query,
)
from apps.gateway.app.permissions import is_tool_permission
from apps.gateway.app.stores.service import StoreService
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult

KeyStore = InMemoryStore | PostgresStore | UnconfiguredStore
logger = logging.getLogger(__name__)

_STATUS_FORBIDDEN = {
    "error": "forbidden",
    "error_description": "Status access was denied.",
}


def create_app(
    settings: Settings | None = None,
    store: KeyStore | None = None,
    chat_provider: object | None = None,
    cluster_client: ClusterClient | None = None,
    start_store_worker: bool | None = None,
    store_autostart: bool = True,
):
    resolved = settings if settings is not None else Settings.from_env()
    resolved_store = store if store is not None else _store_from_settings(resolved)
    authenticator = Authenticator(resolved_store)
    auditor = Auditor(resolved_store)
    registry = Registry(resolved.upstreams)
    dispatcher = ToolDispatcher(registry, authenticator, auditor, resolved_store)
    client = cluster_client
    if client is None and resolved.stores.cluster_reconciler:
        client = incluster_client()
    reconciles_cluster = bool(resolved.stores.cluster_reconciler and client is not None)
    store_service = StoreService(
        resolved_store,
        registry,
        resolved,
        client if reconciles_cluster else None,
        autostart=store_autostart,
        cluster_reconciler=reconciles_cluster,
    )
    run_worker = resolved.stores.enabled if start_store_worker is None else start_store_worker

    discovery_state = DiscoveryState()

    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        result = await discover(
            registry,
            authenticator,
            resolved_store,
            current_principal.get(),
            query=None,
            correlation=correlation_id(None),
            state=discovery_state,
        )
        if result.error:
            logger.warning("Permission check failed: %s", result.error)
            return ListToolsResult(tools=[])
        return list_result(result.tools)

    async def on_call_tool(_context: object, params: object) -> CallToolResult:
        name = getattr(params, "name")
        arguments = getattr(params, "arguments", None)
        return await dispatcher.execute_tool(current_principal.get(), name, arguments)

    mcp = Server(
        "urumi-gateway",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        get_tool_input_schema=_no_input_schema,
    )

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
            worker = None
            if run_worker:
                worker = asyncio.create_task(store_service.worker())
            try:
                yield
            finally:
                store_service.stop()
                if worker is not None:
                    worker.cancel()
                    try:
                        await worker
                    except asyncio.CancelledError:
                        pass

    def register_routes(app: FastAPI) -> None:
        register_status(app)
        register_admin_routes(app, resolved_store, registry, resolved)
        register_store_routes(app, store_service)
        provider = provider_from_env(timeout=resolved.chat_provider_timeout_seconds) if chat_provider is None else chat_provider
        @app.get("/admin/discovery")
        async def admin_discovery(request: Request) -> Any:
            user = await _admin(resolved_store, request)
            if not isinstance(user, UserRecord):
                return user
            try:
                await registry.list_tools_result()
            except Exception as exc:
                logger.warning("Discovery refresh failed: %s", type(exc).__name__)
            return await operator_summary(registry, resolved_store, discovery_state)

        @app.post("/admin/discovery/search")
        async def admin_discovery_search(request: Request) -> JSONResponse:
            user = await _admin(resolved_store, request)
            if not isinstance(user, UserRecord):
                return user
            return await _search_response(request, principal=None, admin=True)

        @app.post("/discovery/search")
        async def client_discovery_search(request: Request) -> JSONResponse:
            gate = await require_live_principal(
                authenticator,
                auditor,
                request.headers.get("authorization"),
            )
            if not isinstance(gate, Principal):
                return gate
            return await _search_response(request, principal=gate, admin=False)

        async def _search_response(request: Request, *, principal: Principal | None, admin: bool) -> JSONResponse:
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "malformed", "tools": []}, status_code=400)
            if not isinstance(body, dict) or not isinstance(body.get("query"), str):
                return JSONResponse({"error": "malformed", "tools": []}, status_code=400)
            query = body["query"]
            presented = request.headers.get("x-request-id")
            if admin:
                problem = validate_query(query)
                if problem:
                    return JSONResponse({"error": problem, "tools": []}, status_code=400)
                try:
                    listed = await registry.list_tools_result()
                except Exception as exc:
                    logger.warning("Discovery search failed: %s", type(exc).__name__)
                    return JSONResponse({"error": "unavailable", "tools": []}, status_code=503)
                advertised = [tool for tool in listed.tools if is_tool_permission(tool.name)]
                try:
                    ranked = rank_tools(advertised, query)
                except Exception as exc:
                    logger.warning("Discovery ranking failed: %s", type(exc).__name__)
                    return JSONResponse({"error": "unavailable", "tools": []}, status_code=503)
                result_tools = client_search_body(
                    DiscoveryResult(ranked=ranked, correlation_id=correlation_id(presented))
                )
                result_tools["scope"] = "advertised"
                return JSONResponse(result_tools)
            result = await discover(
                registry,
                authenticator,
                resolved_store,
                principal,
                query=query,
                correlation=correlation_id(presented),
                state=discovery_state,
            )
            if result.error in {"malformed", "query_too_long"}:
                return JSONResponse({"error": result.error, "tools": []}, status_code=400)
            if result.error:
                return JSONResponse({"error": "unavailable", "tools": []}, status_code=503)
            return JSONResponse(client_search_body(result))

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
    app.state.store_service = store_service
    app.state.discovery = discovery_state
    return app


def _no_input_schema(_name: str) -> None:
    """Give the MCP SDK no schema for its Mcp-Param-* header check on tools/call.

    Without this hook, the SDK runs on_list_tools before dispatching a
    2026-07-28 tools/call that has arguments. That refreshes every enabled
    upstream before execute_tool checks the grant, including for calls it then
    denies. This hook makes no network or store call and answers the same for
    every name, so it reveals nothing about ungranted tools.

    Trade-off: the gateway does not reject a request whose Mcp-Param-* header
    disagrees with an x-mcp-header argument. The upstream still receives the
    body value, with headers the gateway's own client derives from it.
    """


def _store_from_settings(settings: Settings) -> KeyStore:
    if settings.database_url:
        return PostgresStore(settings.database_url)
    return UnconfiguredStore()


app = create_app()
