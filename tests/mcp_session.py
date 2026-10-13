"""Open the official MCP client, optionally with a gateway API key."""

from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from apps.gateway.app.config import Settings
from apps.gateway.app.main import create_app
from apps.gateway.app.store import InMemoryStore


@asynccontextmanager
async def open_mcp(url: str, api_key: str | None = None):
    headers = {}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(5.0)) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            yield client


async def start_gateway(
    launch_app,
    upstreams: list,
    store: InMemoryStore | None = None,
    permissions: tuple[str, ...] = (),
):
    """Launch a gateway that uses an in-memory key store and one live key.

    permissions are exact public tool names granted to that key. A key with
    an empty tuple cannot call tools.
    """
    resolved = store if store is not None else InMemoryStore()
    issued = await resolved.issue()
    for tool_name in permissions:
        if not await resolved.grant(issued.key_id, tool_name):
            raise RuntimeError("The test key did not accept a permission.")
    base_url = launch_app(create_app(Settings(upstreams=upstreams), store=resolved))
    return base_url, resolved, issued
