"""Deterministic unit conversion. The gateway exposes this as utility__convert_units."""

import math
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import WithJsonSchema

from apps.common.http import build_http_app

_MILE_IN_KM = 1.609344

FACTORS = {
    ("km", "m"): 1000.0,
    ("m", "km"): 0.001,
    ("kg", "g"): 1000.0,
    ("g", "kg"): 0.001,
    ("km", "mi"): 1 / _MILE_IN_KM,
    ("mi", "km"): _MILE_IN_KM,
}


# A float annotation would turn booleans and numeric strings into floats, and would
# accept NaN and infinity, before this function ran. The published schema stays a number.
_NUMBER = Annotated[object, WithJsonSchema({"type": "number"})]


def _finite_number(value: object) -> float:
    """Accept an ordinary finite int or float. Reject every other value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("Value must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ToolError("Value must be a finite number.")
    return number


def convert_units(value: object, from_unit: str, to_unit: str) -> str:
    """Convert a finite number between km/m, km/mi, or kg/g."""
    number = _finite_number(value)
    factor = FACTORS.get((from_unit.casefold(), to_unit.casefold()))
    if factor is None:
        raise ToolError(f"Unsupported conversion from {from_unit} to {to_unit}.")
    converted = number * factor
    return str(int(converted)) if converted.is_integer() else str(converted)


def create_app():
    mcp = MCPServer("utility")

    @mcp.tool(name="convert_units")
    def convert_units_tool(value: _NUMBER, from_unit: str, to_unit: str) -> str:
        """Convert a finite number between km/m, km/mi, or kg/g."""
        return convert_units(value, from_unit, to_unit)

    return build_http_app(mcp, lambda: {"status": "ok"})


app = create_app()
