"""Test-only upstream whose list or call exceeds a short gateway timeout."""

import asyncio

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from apps.common.http import build_http_app


def create_slow_list_app():
    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        await asyncio.sleep(2)
        return ListToolsResult(tools=[Tool(name="hidden", description="slow list", input_schema={"type": "object"})])

    async def on_call_tool(_context: object, _params: object) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text="unused")], is_error=False)

    mcp = Server("slow-list", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    return build_http_app(mcp, lambda: {"status": "ok"})


def create_slow_call_app():
    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(
            tools=[Tool(name="nap", description="Sleep before returning.", input_schema={"type": "object", "properties": {}})]
        )

    async def on_call_tool(_context: object, _params: object) -> CallToolResult:
        await asyncio.sleep(2)
        return CallToolResult(content=[TextContent(type="text", text="awake")], is_error=False)

    mcp = Server("slow-call", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    return build_http_app(mcp, lambda: {"status": "ok"})
