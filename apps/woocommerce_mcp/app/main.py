"""WooCommerce MCP server.

Product and order tools are registered only when API settings are valid.
The server does not invent products or orders.
"""

from mcp.server.mcpserver import MCPServer

from apps.common.http import build_http_app
from apps.woocommerce_mcp.app.client import WooClient
from apps.woocommerce_mcp.app.config import load_settings
from apps.woocommerce_mcp.app.tools import register_tools


def create_app(client: WooClient | None = None):
    resolved = client if client is not None else _client_from_env()
    mcp = MCPServer("woocommerce")
    if resolved is not None:
        register_tools(mcp, resolved)
    state = "configured" if resolved is not None else "not_configured"
    return build_http_app(mcp, lambda: {"status": "ok", "woocommerce_api": state})


def _client_from_env() -> WooClient | None:
    settings = load_settings()
    if settings is None:
        return None
    return WooClient(settings)


app = create_app()
