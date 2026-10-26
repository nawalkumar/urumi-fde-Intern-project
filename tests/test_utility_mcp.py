"""Direct checks for utility unit conversion and its MCP tool."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from apps.utility_mcp.app.main import convert_units
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp


def test_kilometers_and_miles_in_both_directions():
    assert convert_units(10, "km", "mi") == "6.2137119223733395"
    assert convert_units(10, "mi", "km") == "16.09344"
    assert convert_units(10, "KM", "Mi") == "6.2137119223733395"
    assert convert_units(1, "Mi", "KM") == "1.609344"


def test_full_unit_names_stay_unsupported():
    with pytest.raises(ToolError, match="Unsupported conversion from kilometer to mile"):
        convert_units(10, "kilometer", "mile")
    with pytest.raises(ToolError, match="Unsupported conversion from mile to kilometer"):
        convert_units(1, "mile", "kilometer")


def test_existing_pairs_stay_exact():
    assert convert_units(2, "km", "m") == "2000"
    assert convert_units(1.0, "km", "m") == "1000"
    assert convert_units(0, "km", "m") == "0"
    assert convert_units(-2, "km", "m") == "-2000"
    assert convert_units(5, "m", "km") == "0.005"
    assert convert_units(1, "kg", "g") == "1000"
    assert convert_units(1, "g", "kg") == "0.001"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param(None, id="none"),
        pytest.param("10", id="numeric-string"),
        pytest.param("abc", id="text"),
    ],
)
def test_invalid_numbers_raise_tool_error(value: object) -> None:
    with pytest.raises(ToolError, match="^Value must be a finite number\\.$"):
        convert_units(value, "km", "m")


@pytest.mark.asyncio
async def test_mcp_exposes_and_executes_mile_conversion(launch_app) -> None:
    base = launch_app(create_utility())
    async with open_mcp(f"{base}/mcp") as client:
        listed = await client.list_tools()
        result = await client.call_tool("convert_units", {"value": 10, "from_unit": "km", "to_unit": "mi"})
        reverse = await client.call_tool("convert_units", {"value": 10, "from_unit": "mi", "to_unit": "km"})
    assert [tool.name for tool in listed.tools] == ["convert_units"]
    properties = listed.tools[0].input_schema["properties"]
    assert set(properties) == {"value", "from_unit", "to_unit"}
    assert properties["value"]["type"] == "number"
    assert result.is_error is False
    assert result.content[0].text == "6.2137119223733395"
    assert reverse.is_error is False
    assert reverse.content[0].text == "16.09344"


@pytest.mark.asyncio
async def test_mcp_rejects_values_that_a_float_schema_would_coerce(launch_app) -> None:
    base = launch_app(create_utility())
    samples = (True, False, None, "10", "abc")
    async with open_mcp(f"{base}/mcp") as client:
        results = [
            await client.call_tool("convert_units", {"value": sample, "from_unit": "km", "to_unit": "m"})
            for sample in samples
        ]
    for sample, result in zip(samples, results):
        assert result.is_error is True
        text = result.content[0].text
        assert text == "Error executing tool convert_units: Value must be a finite number."
        assert str(sample) not in text
        assert text not in {"1000", "0", "nan", "inf", "-inf"}
