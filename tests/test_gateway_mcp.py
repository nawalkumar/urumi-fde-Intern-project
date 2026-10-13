"""Protocol tests against the gateway's Streamable HTTP endpoint."""

import json
import urllib.request

import pytest

from apps.gateway.app.config import UpstreamConfig
from apps.gateway.app.dispatch import ToolDispatcher
from apps.support_mcp.app.main import create_app as create_support
from apps.utility_mcp.app.main import create_app as create_utility
from apps.woocommerce_mcp.app.main import create_app as create_woocommerce
from tests.mcp_session import open_mcp, start_gateway
from tests.slow_mcp import create_slow_call_app, create_slow_list_app


def _health(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
        return json.loads(response.read().decode())


async def _tool_names(mcp_url: str, api_key: str) -> list[str]:
    async with open_mcp(mcp_url, api_key) as client:
        listed = await client.list_tools()
    return [tool.name for tool in listed.tools]


async def _call(mcp_url: str, name: str, arguments: dict, api_key: str):
    async with open_mcp(mcp_url, api_key) as client:
        return await client.call_tool(name, arguments)


@pytest.mark.asyncio
async def test_namespaced_tools_and_health(launch_app) -> None:
    support = launch_app(create_support())
    utility = launch_app(create_utility())
    woocommerce = launch_app(create_woocommerce())
    gateway, _store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("woocommerce", f"{woocommerce}/mcp", 5),
            UpstreamConfig("support", f"{support}/mcp", 5),
            UpstreamConfig("utility", f"{utility}/mcp", 5),
        ],
        permissions=("support__search_notes", "utility__convert_units"),
    )

    assert _health(gateway) == {"status": "ok"}
    assert _health(woocommerce)["woocommerce_api"] == "not_configured"
    names = await _tool_names(f"{gateway}/mcp", issued.raw_key)
    assert names == ["support__search_notes", "utility__convert_units"]


@pytest.mark.asyncio
async def test_dispatch_through_gateway(launch_app, monkeypatch) -> None:
    support = launch_app(create_support())
    utility = launch_app(create_utility())
    gateway, _store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("support", f"{support}/mcp", 5),
            UpstreamConfig("utility", f"{utility}/mcp", 5),
        ],
        permissions=("support__search_notes", "utility__convert_units"),
    )
    mcp_url = f"{gateway}/mcp"
    seen: list[tuple[str, str]] = []
    original = ToolDispatcher.execute_tool

    async def spy(self, principal, tool_name, arguments):
        seen.append((principal.key_id, tool_name))
        return await original(self, principal, tool_name, arguments)

    monkeypatch.setattr(ToolDispatcher, "execute_tool", spy)

    notes = await _call(mcp_url, "support__search_notes", {"query": "shipping"}, issued.raw_key)
    units = await _call(mcp_url, "utility__convert_units", {"value": 2, "from_unit": "km", "to_unit": "m"}, issued.raw_key)
    rejected = await _call(mcp_url, "utility__convert_units", {"value": 1, "from_unit": "km", "to_unit": "kg"}, issued.raw_key)

    assert seen == [
        (issued.key_id, "support__search_notes"),
        (issued.key_id, "utility__convert_units"),
        (issued.key_id, "utility__convert_units"),
    ]
    assert notes.is_error is False
    assert "3 business days" in notes.content[0].text
    assert units.is_error is False
    assert units.content[0].text == "2000"
    assert rejected.is_error is True
    assert "Unsupported conversion from km to kg." in rejected.content[0].text


@pytest.mark.asyncio
async def test_timeout_skips_slow_list_and_fails_slow_call(launch_app) -> None:
    utility = launch_app(create_utility())
    slow_list = launch_app(create_slow_list_app())
    slow_call = launch_app(create_slow_call_app())
    gateway, _store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("utility", f"{utility}/mcp", 5),
            UpstreamConfig("slowlist", f"{slow_list}/mcp", 0.4),
            UpstreamConfig("slowcall", f"{slow_call}/mcp", 0.4),
        ],
        permissions=("utility__convert_units", "slowcall__nap"),
    )
    mcp_url = f"{gateway}/mcp"
    names = await _tool_names(mcp_url, issued.raw_key)
    assert "utility__convert_units" in names
    assert not any(name.startswith("slowlist__") for name in names)

    note = "timeout-argument-sentinel"
    timed_out = await _call(mcp_url, "slowcall__nap", {"note": note}, issued.raw_key)
    assert timed_out.is_error is True
    assert "timed out" in timed_out.content[0].text.casefold()
    timeout_rows = [row for row in _store.audit_log if row.outcome == "timeout"]
    assert len(timeout_rows) == 1
    assert timeout_rows[0].tool_name == "slowcall__nap"
    assert timeout_rows[0].actor == issued.key_id
    if note in repr(timeout_rows[0]) or note in timed_out.content[0].text:
        raise AssertionError("An argument was recorded in the timeout result.")

    still_works = await _call(mcp_url, "utility__convert_units", {"value": 1, "from_unit": "kg", "to_unit": "g"}, issued.raw_key)
    assert still_works.is_error is False
    assert still_works.content[0].text == "1000"


@pytest.mark.asyncio
async def test_unavailable_upstream_is_isolated(launch_app) -> None:
    utility = launch_app(create_utility())
    gateway, _store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("utility", f"{utility}/mcp", 5),
            UpstreamConfig("down", "http://127.0.0.1:1/mcp", 1),
        ],
        permissions=("utility__convert_units", "down__missing"),
    )
    mcp_url = f"{gateway}/mcp"
    names = await _tool_names(mcp_url, issued.raw_key)
    assert names == ["utility__convert_units"]

    failed = await _call(mcp_url, "down__missing", {}, issued.raw_key)
    assert failed.is_error is True
    message = failed.content[0].text
    assert message == "Upstream call failed for down__missing: ConnectError."
    assert "127.0.0.1" not in message
    assert "WOOCOMMERCE" not in message

    succeeded = await _call(mcp_url, "utility__convert_units", {"value": 5, "from_unit": "m", "to_unit": "km"}, issued.raw_key)
    assert succeeded.is_error is False
    assert succeeded.content[0].text == "0.005"
