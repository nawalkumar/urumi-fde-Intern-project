"""Deterministic unit conversion. The gateway exposes this as utility__convert_units."""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from apps.common.http import build_http_app

FACTORS = {
    ("km", "m"): 1000.0,
    ("m", "km"): 0.001,
    ("kg", "g"): 1000.0,
    ("g", "kg"): 0.001,
}

def create_app():
    mcp = MCPServer("utility")

    @mcp.tool(name="convert_units")
    def convert_units(value: float, from_unit: str, to_unit: str) -> str:
        """Convert value between km/m or kg/g."""
        factor = FACTORS.get((from_unit.casefold(), to_unit.casefold()))
        if factor is None:
            raise ToolError(f"Unsupported conversion from {from_unit} to {to_unit}.")
        converted = value * factor
        return str(int(converted)) if converted.is_integer() else str(converted)

    return build_http_app(mcp, lambda: {"status": "ok"})


app = create_app()
