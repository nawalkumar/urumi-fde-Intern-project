"""tools/list returns every enabled upstream tool for a live key."""

import pytest

from apps.gateway.app.config import Settings, UpstreamConfig
from apps.gateway.app.main import create_app
from apps.gateway.app.store import InMemoryStore
from apps.support_mcp.app.main import create_app as create_support
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp
from tests.test_upstream_controls import _duplicate_names_app, _marker_app


async def _names(url: str, api_key: str) -> list[str]:
    async with open_mcp(url, api_key) as client:
        listed = await client.list_tools()
    return [tool.name for tool in listed.tools]


@pytest.mark.asyncio
async def test_discovery_follows_each_keys_current_grants(launch_app, caplog) -> None:
    utility = launch_app(create_utility())
    support = launch_app(create_support())
    extra = launch_app(_marker_app("extra"))
    store = InMemoryStore()
    utility_key = await store.issue()
    support_key = await store.issue()
    empty_key = await store.issue()
    assert await store.grant(utility_key.key_id, "utility__convert_units") is True
    assert await store.grant(support_key.key_id, "support__search_notes") is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("utility", f"{utility}/mcp", 5),
                UpstreamConfig("support", f"{support}/mcp", 5),
                UpstreamConfig("extra", f"{extra}/mcp", 5),
            ]
        ),
        store=store,
    )
    gateway = launch_app(app)
    url = f"{gateway}/mcp"

    advertised = ["extra__echo", "support__search_notes", "utility__convert_units"]
    assert sorted(await _names(url, empty_key.raw_key)) == advertised
    assert sorted(await _names(url, utility_key.raw_key)) == advertised
    assert sorted(await _names(url, support_key.raw_key)) == advertised

    assert await store.revoke_permission(utility_key.key_id, "utility__convert_units") is True
    assert sorted(await _names(url, utility_key.raw_key)) == advertised

    assert await store.revoke(support_key.key_id) is True
    failed = False
    try:
        await _names(url, support_key.raw_key)
    except Exception:
        failed = True
    assert failed is True

    blob = "\n".join(repr(row) for row in store.audit_log)
    text = caplog.text + blob
    assert utility_key.raw_key not in text
    assert support_key.raw_key not in text
    assert empty_key.raw_key not in text
    assert "extra__echo" not in blob


@pytest.mark.asyncio
async def test_discovery_omits_collisions_disabled_upstreams_and_failures(launch_app) -> None:
    calls: list[str] = []
    duplicate = launch_app(_duplicate_names_app(calls))
    healthy = launch_app(_marker_app("kept"))
    utility = launch_app(create_utility())
    store = InMemoryStore()
    issued = await store.issue()
    for tool_name in ("dup__echo", "other__echo", "utility__convert_units", "support__search_notes"):
        assert await store.grant(issued.key_id, tool_name) is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("dup", f"{duplicate}/mcp", 5),
                UpstreamConfig("other", f"{healthy}/mcp", 5),
                UpstreamConfig("utility", f"{utility}/mcp", 5),
                UpstreamConfig("support", "http://127.0.0.1:1/mcp", 1, enabled=False),
                UpstreamConfig("down", "http://127.0.0.1:1/mcp", 1),
            ]
        ),
        store=store,
    )
    gateway = launch_app(app)
    url = f"{gateway}/mcp"

    assert await _names(url, issued.raw_key) == ["other__echo", "utility__convert_units"]

    async with open_mcp(url, issued.raw_key) as client:
        conflict = await client.call_tool("dup__echo", {})
    assert conflict.is_error is True
    assert "Conflicting registration excluded" in conflict.content[0].text
    assert calls == []

    app.state.registry.set_enabled("utility", False)
    assert await _names(url, issued.raw_key) == ["other__echo"]
