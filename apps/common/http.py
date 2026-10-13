"""Mount an MCP Streamable HTTP app beside a health route."""

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer

McpService = MCPServer | Server


def build_http_app(
    mcp: McpService,
    health_payload: Callable[[], Any],
    *,
    asgi_wrapper: Callable[[Any], Any] | None = None,
    store_lifespan: Callable[[], AsyncIterator[None]] | None = None,
    register_routes: Callable[[FastAPI], None] | None = None,
) -> FastAPI:
    """Serve /health and the SDK Streamable HTTP endpoint at /mcp.

    host="0.0.0.0" keeps the SDK from auto-enabling a localhost-only Host
    allowlist, so Docker service names are accepted. uvicorn still binds the port.
    asgi_wrapper, when set, wraps only the mounted MCP app. /health stays outside it.
    register_routes, when set, adds routes before the mount so they are not
    swallowed by the MCP app.
    """
    mcp_app = mcp.streamable_http_app(json_response=True, stateless_http=True, host="0.0.0.0")
    if asgi_wrapper is not None:
        mcp_app = asgi_wrapper(mcp_app)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            if store_lifespan is None:
                yield
            else:
                async with store_lifespan():
                    yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> Any:
        result = health_payload()
        if inspect.isawaitable(result):
            result = await result
        return result

    if register_routes is not None:
        register_routes(app)

    app.mount("/", mcp_app)
    return app
