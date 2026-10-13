"""Enabled upstreams, name collisions, and failure isolation."""

import pytest
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from apps.common.http import build_http_app
from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.config import Settings, UpstreamConfig
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.main import create_app
from apps.gateway.app.registry import GatewayConfigError, Registry, _colliding_public_names
from apps.gateway.app.store import InMemoryStore
from apps.support_mcp.app.main import create_app as create_support
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp


def _marker_app(marker: str, calls: list[str] | None = None):
    mcp = MCPServer(marker)

    @mcp.tool(name="echo")
    def echo() -> str:
        """Return this server's marker."""
        if calls is not None:
            calls.append(marker)
        return marker

    return build_http_app(mcp, lambda: {"status": "ok"})


def _duplicate_names_app(calls: list[str]):
    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        tool = Tool(name="echo", description="duplicate", input_schema={"type": "object", "properties": {}})
        return ListToolsResult(tools=[tool, tool])

    async def on_call_tool(_context: object, _params: object) -> CallToolResult:
        calls.append("called")
        return CallToolResult(content=[TextContent(type="text", text="first")], is_error=False)

    mcp = Server("duplicates", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    return build_http_app(mcp, lambda: {"status": "ok"})


def _clear_upstream_env(monkeypatch) -> None:
    for name in (
        "WOOCOMMERCE_MCP_URL",
        "SUPPORT_MCP_URL",
        "UTILITY_MCP_URL",
        "WOOCOMMERCE_MCP_ENABLED",
        "SUPPORT_MCP_ENABLED",
        "UTILITY_MCP_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_enable_flags_default_and_reject_unrecognized_values(monkeypatch) -> None:
    _clear_upstream_env(monkeypatch)
    monkeypatch.setenv("UTILITY_MCP_URL", "http://utility.example/mcp")
    monkeypatch.setenv("SUPPORT_MCP_URL", "http://support.example/mcp")
    enabled = {item.name: item.enabled for item in Settings.from_env().upstreams}
    assert enabled == {"utility": True, "support": True}

    monkeypatch.setenv("UTILITY_MCP_ENABLED", "off")
    monkeypatch.setenv("SUPPORT_MCP_ENABLED", "maybe")
    flagged = {item.name: item.enabled for item in Settings.from_env().upstreams}
    assert flagged == {"utility": False, "support": False}

    monkeypatch.setenv("UTILITY_MCP_ENABLED", "yes")
    assert next(item.enabled for item in Settings.from_env().upstreams if item.name == "utility") is True


@pytest.mark.asyncio
async def test_disabled_tools_disappear_and_failures_stay_isolated(launch_app) -> None:
    utility = launch_app(create_utility())
    support = launch_app(create_support())
    store = InMemoryStore()
    issued = await store.issue()
    for tool_name in ("utility__convert_units", "support__search_notes", "down__missing"):
        assert await store.grant(issued.key_id, tool_name) is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("utility", f"{utility}/mcp", 5),
                UpstreamConfig("support", f"{support}/mcp", 5),
                UpstreamConfig("down", "http://127.0.0.1:1/mcp", 1),
            ]
        ),
        store=store,
    )
    gateway = launch_app(app)
    mcp_url = f"{gateway}/mcp"

    async with open_mcp(mcp_url, issued.raw_key) as client:
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        assert "utility__convert_units" in names
        assert "support__search_notes" in names
        assert not any(name.startswith("down__") for name in names)

        failed = await client.call_tool("down__missing", {"note": "argument-sentinel"})
        assert failed.is_error is True
        assert "ConnectError" in failed.content[0].text
        still = await client.call_tool(
            "utility__convert_units",
            {"value": 1, "from_unit": "kg", "to_unit": "g"},
        )
        assert still.is_error is False
        assert still.content[0].text == "1000"

    app.state.registry.set_enabled("support", False)

    async with open_mcp(mcp_url, issued.raw_key) as client:
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        assert "support__search_notes" not in names
        assert "utility__convert_units" in names
        hidden = await client.call_tool("support__search_notes", {"query": "argument-sentinel"})
        assert hidden.is_error is True
        assert hidden.content[0].text == "Upstream support is disabled."
        again = await client.call_tool(
            "utility__convert_units",
            {"value": 2, "from_unit": "km", "to_unit": "m"},
        )
        assert again.is_error is False
        assert again.content[0].text == "2000"

    blob = "\n".join(repr(row) for row in store.audit_log)
    assert "argument-sentinel" not in blob
    assert issued.raw_key not in blob
    assert [row.outcome for row in store.audit_log] == [
        "upstream_failure",
        "success",
        "error",
        "success",
    ]
    assert store.audit_log[2].actor == issued.key_id
    assert store.audit_log[2].tool_name == "support__search_notes"
    codes = {(item.code, item.upstream_id) for item in app.state.registry.diagnostics}
    assert ("upstream_list_failed", "down") in codes
    assert ("upstream_disabled", "support") in codes


def test_duplicate_upstream_ids_are_rejected(launch_app) -> None:
    first = launch_app(_marker_app("alpha"))
    second = launch_app(_marker_app("beta"))
    with pytest.raises(GatewayConfigError, match="Duplicate upstream identifier 'utility'"):
        Registry(
            [
                UpstreamConfig("utility", f"{first}/mcp", 5),
                UpstreamConfig("utility", f"{second}/mcp", 5),
            ]
        )
    with pytest.raises(GatewayConfigError, match="Duplicate upstream identifier 'utility'"):
        Registry(
            [
                UpstreamConfig("utility", "http://127.0.0.1:1/mcp", 1, enabled=False),
                UpstreamConfig("utility", f"{second}/mcp", 5, enabled=True),
            ]
        )
    with pytest.raises(GatewayConfigError, match="does not contain '__'"):
        Registry([UpstreamConfig("woo__store", "http://127.0.0.1:1/mcp", 1)])


@pytest.mark.asyncio
async def test_duplicate_public_tool_names_are_excluded(launch_app, caplog) -> None:
    calls: list[str] = []
    base = launch_app(_duplicate_names_app(calls))
    healthy = launch_app(_marker_app("kept"))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{base}/mcp", 5),
            UpstreamConfig("other", f"{healthy}/mcp", 5),
        ]
    )
    conflict = "Duplicate public tool name dup__echo. Conflicting registration excluded."

    with caplog.at_level("ERROR"):
        early = await registry.call("dup__echo", {})
    assert early.is_error is True
    assert early.content[0].text == conflict
    assert calls == []
    assert conflict in caplog.text
    assert any(
        item.code == "tool_excluded" and item.tool_name == "dup__echo" and item.upstream_id == "dup"
        for item in registry.diagnostics
    )

    kept = await registry.call("other__echo", {})
    assert kept.is_error is False
    assert kept.content[0].text == "kept"

    caplog.clear()
    with caplog.at_level("ERROR"):
        listed = await registry.list_tools_result()
    assert [tool.name for tool in listed.tools] == ["other__echo"]
    assert conflict in caplog.text

    again = await registry.call("dup__echo", {})
    assert again.is_error is True
    assert again.content[0].text == conflict
    assert calls == []


@pytest.mark.asyncio
async def test_shared_original_names_stay_distinct(launch_app) -> None:
    first = launch_app(_marker_app("alpha"))
    second = launch_app(_marker_app("beta"))
    registry = Registry(
        [
            UpstreamConfig("utility", f"{first}/mcp", 5),
            UpstreamConfig("support", f"{second}/mcp", 5),
        ]
    )
    listed = await registry.list_tools_result()
    assert [tool.name for tool in listed.tools] == ["utility__echo", "support__echo"]
    alpha = await registry.call("utility__echo", {})
    beta = await registry.call("support__echo", {})
    assert alpha.is_error is False
    assert alpha.content[0].text == "alpha"
    assert beta.is_error is False
    assert beta.content[0].text == "beta"


_SECRET = "gateway-sentinel-secret"
_SECRET_URL = "http://gateway-sentinel-secret@127.0.0.1:9/internal"
_LEAKY = {"note": _SECRET, "callback": _SECRET_URL}


def _assert_clean(blob: str) -> None:
    if _SECRET in blob or _SECRET_URL in blob:
        raise AssertionError("Sensitive text appeared in gateway output.")


def _diag(registry: Registry) -> list[tuple[str, str, str]]:
    return [(item.code, item.upstream_id, item.tool_name) for item in registry.diagnostics]


def _messages(registry: Registry) -> str:
    return "\n".join(item.message for item in registry.diagnostics)


def _switching_app(state: dict[str, str], calls: list[str]):
    async def on_list_tools(_context: object, _params: object) -> ListToolsResult:
        mode = state["mode"]
        if mode == "fail":
            raise RuntimeError(state["boom"])
        tool = Tool(
            name="echo",
            description=state["description"],
            input_schema={"type": "object", "properties": {}},
        )
        if mode == "duplicate":
            return ListToolsResult(tools=[tool, tool])
        if mode == "unique":
            return ListToolsResult(tools=[tool])
        raise RuntimeError("unexpected list mode")

    async def on_call_tool(_context: object, _params: object) -> CallToolResult:
        calls.append("called")
        return CallToolResult(content=[TextContent(type="text", text="called")], is_error=False)

    mcp = Server("switching", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    return build_http_app(mcp, lambda: {"status": "ok"})


async def _execute(registry: Registry, granted: tuple[str, ...], tool_name: str, arguments: dict[str, str]):
    store = InMemoryStore()
    issued = await store.issue()
    for name in granted:
        assert await store.grant(issued.key_id, name) is True
    authenticator = Authenticator(store)
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(store))
    principal = await authenticator.authenticate(issued.raw_key)
    result = await dispatcher.execute_tool(principal, tool_name, arguments)
    return result, store


def test_held_snapshot_keeps_a_shared_public_name_colliding() -> None:
    # A failed participant stays in this set. Dropping it would leave one copy.
    assert _colliding_public_names({"left": ["shared__echo"], "right": ["shared__echo"]}) == {"shared__echo"}
    assert _colliding_public_names({"right": ["shared__echo"]}) == set()
    assert _colliding_public_names({"dup": ["dup__echo", "dup__echo"], "other": ["other__echo"]}) == {"dup__echo"}


@pytest.mark.asyncio
async def test_collision_recovers_when_a_later_listing_is_unique(launch_app) -> None:
    dup_calls: list[str] = []
    other_calls: list[str] = []
    state = {"mode": "duplicate", "boom": _SECRET, "description": _SECRET_URL}
    duplicate = launch_app(_switching_app(state, dup_calls))
    other = launch_app(_marker_app("kept", other_calls))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("other", f"{other}/mcp", 5),
        ]
    )
    conflict = "Duplicate public tool name dup__echo. Conflicting registration excluded."

    hidden = await registry.list_tools_result()
    assert [tool.name for tool in hidden.tools] == ["other__echo"]
    rejected, store = await _execute(registry, ("dup__echo", "other__echo"), "dup__echo", _LEAKY)
    assert rejected.is_error is True
    _assert_clean(rejected.content[0].text)
    assert rejected.content[0].text == conflict
    assert dup_calls == []
    assert other_calls == []
    _assert_clean(repr(store.audit_log[-1]))
    assert store.audit_log[-1].tool_name == "dup__echo"
    assert store.audit_log[-1].outcome == "error"
    _assert_clean(_messages(registry))

    state["mode"] = "unique"
    restored = await registry.list_tools_result()
    assert [tool.name for tool in restored.tools] == ["dup__echo", "other__echo"]
    assert not any(item.code == "tool_excluded" for item in registry.diagnostics)
    called = await registry.call("dup__echo", _LEAKY)
    _assert_clean(called.content[0].text)
    assert called.is_error is False
    assert called.content[0].text == "called"
    assert dup_calls == ["called"]
    assert other_calls == []


@pytest.mark.asyncio
async def test_new_collision_is_rejected_without_a_fresh_client_list(launch_app) -> None:
    dup_calls: list[str] = []
    other_calls: list[str] = []
    state = {"mode": "unique", "boom": _SECRET, "description": _SECRET_URL}
    duplicate = launch_app(_switching_app(state, dup_calls))
    other = launch_app(_marker_app("kept", other_calls))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("other", f"{other}/mcp", 5),
        ]
    )
    conflict = "Duplicate public tool name dup__echo. Conflicting registration excluded."

    listed = await registry.list_tools_result()
    assert [tool.name for tool in listed.tools] == ["dup__echo", "other__echo"]
    state["mode"] = "duplicate"
    rejected, store = await _execute(registry, ("dup__echo", "other__echo"), "dup__echo", _LEAKY)
    assert rejected.is_error is True
    _assert_clean(rejected.content[0].text)
    assert rejected.content[0].text == conflict
    assert dup_calls == []
    assert other_calls == []
    assert any(item.code == "tool_excluded" and item.tool_name == "dup__echo" for item in registry.diagnostics)
    _assert_clean(repr(store.audit_log[-1]))
    _assert_clean(_messages(registry))

    agreed = await registry.list_tools_result()
    assert [tool.name for tool in agreed.tools] == ["other__echo"]
    kept = await registry.call("other__echo", {})
    assert kept.is_error is False
    assert kept.content[0].text == "kept"
    assert other_calls == ["kept"]
    assert dup_calls == []


@pytest.mark.asyncio
async def test_reenable_clears_the_disabled_diagnostic(launch_app) -> None:
    utility = launch_app(create_utility())
    registry = Registry([UpstreamConfig("utility", f"{utility}/mcp", 5)])

    await registry.list_tools_result()
    registry.set_enabled("utility", False)
    hidden = await registry.list_tools_result()
    assert hidden.tools == []
    assert _diag(registry) == [("upstream_disabled", "utility", "")]
    denied = await registry.call(
        "utility__convert_units",
        {"value": 1, "from_unit": "kg", "to_unit": "g", "note": _SECRET},
    )
    assert denied.is_error is True
    _assert_clean(denied.content[0].text)
    assert denied.content[0].text == "Upstream utility is disabled."
    assert f"{utility}/mcp" not in _messages(registry)

    registry.set_enabled("utility", True)
    assert registry.diagnostics == []
    restored = await registry.list_tools_result()
    assert [tool.name for tool in restored.tools] == ["utility__convert_units"]
    assert registry.diagnostics == []
    called = await registry.call(
        "utility__convert_units",
        {"value": 1, "from_unit": "kg", "to_unit": "g"},
    )
    assert called.is_error is False
    assert called.content[0].text == "1000"


@pytest.mark.asyncio
async def test_successful_list_clears_a_failure_diagnostic(launch_app) -> None:
    flaky_calls: list[str] = []
    other_calls: list[str] = []
    state = {"mode": "fail", "boom": _SECRET, "description": _SECRET_URL}
    flaky = launch_app(_switching_app(state, flaky_calls))
    other = launch_app(_marker_app("kept", other_calls))
    registry = Registry(
        [
            UpstreamConfig("flaky", f"{flaky}/mcp", 5),
            UpstreamConfig("other", f"{other}/mcp", 5),
        ]
    )

    failed = await registry.list_tools_result()
    assert [tool.name for tool in failed.tools] == ["other__echo"]
    assert _diag(registry) == [("upstream_list_failed", "flaky", "")]
    _assert_clean(_messages(registry))
    assert flaky_calls == []

    state["mode"] = "unique"
    restored = await registry.list_tools_result()
    assert [tool.name for tool in restored.tools] == ["flaky__echo", "other__echo"]
    assert registry.diagnostics == []
    called = await registry.call("flaky__echo", _LEAKY)
    _assert_clean(called.content[0].text)
    assert called.is_error is False
    assert called.content[0].text == "called"
    assert flaky_calls == ["called"]
    kept = await registry.call("other__echo", {})
    assert kept.is_error is False
    assert other_calls == ["kept"]


@pytest.mark.asyncio
async def test_repeated_refresh_does_not_duplicate_diagnostics(launch_app) -> None:
    dup_calls: list[str] = []
    state = {"mode": "duplicate", "boom": _SECRET, "description": _SECRET_URL}
    duplicate = launch_app(_switching_app(state, dup_calls))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("down", "http://127.0.0.1:1/mcp", 0.4),
            UpstreamConfig("paused", "http://127.0.0.1:1/mcp", 5, enabled=False),
        ]
    )

    listed = await registry.list_tools_result()
    assert [tool.name for tool in listed.tools] == []
    first = _diag(registry)
    assert first == [
        ("upstream_list_failed", "down", ""),
        ("upstream_disabled", "paused", ""),
        ("tool_excluded", "dup", "dup__echo"),
    ]
    _assert_clean(_messages(registry))
    assert "127.0.0.1" not in _messages(registry)
    await registry.list_tools_result()
    await registry.list_tools_result()
    assert _diag(registry) == first
    assert len(first) == len(set(first))
    assert dup_calls == []

    other = launch_app(_marker_app("kept"))
    paired = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("other", f"{other}/mcp", 5),
            UpstreamConfig("paused", "http://127.0.0.1:1/mcp", 5, enabled=False),
        ]
    )
    showed = await paired.list_tools_result()
    assert [tool.name for tool in showed.tools] == ["other__echo"]
    keys = _diag(paired)
    again = await paired.list_tools_result()
    assert [tool.name for tool in again.tools] == ["other__echo"]
    assert _diag(paired) == keys
    assert keys.count(("tool_excluded", "dup", "dup__echo")) == 1
    assert keys.count(("upstream_disabled", "paused", "")) == 1


@pytest.mark.asyncio
async def test_partial_list_failure_does_not_dispatch_a_held_collision(launch_app) -> None:
    dup_calls: list[str] = []
    other_calls: list[str] = []
    state = {"mode": "duplicate", "boom": _SECRET, "description": _SECRET_URL}
    duplicate = launch_app(_switching_app(state, dup_calls))
    other = launch_app(_marker_app("kept", other_calls))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("other", f"{other}/mcp", 5),
        ]
    )
    conflict = "Duplicate public tool name dup__echo. Conflicting registration excluded."

    first = await registry.list_tools_result()
    assert [tool.name for tool in first.tools] == ["other__echo"]
    state["mode"] = "fail"
    second = await registry.list_tools_result()
    assert [tool.name for tool in second.tools] == ["other__echo"]
    assert ("tool_excluded", "dup", "dup__echo") in _diag(registry)
    assert ("upstream_list_failed", "dup", "") in _diag(registry)
    assert _diag(registry).count(("tool_excluded", "dup", "dup__echo")) == 1
    _assert_clean(_messages(registry))

    rejected, store = await _execute(registry, ("dup__echo", "other__echo"), "dup__echo", _LEAKY)
    assert rejected.is_error is True
    _assert_clean(rejected.content[0].text)
    assert rejected.content[0].text == conflict
    assert dup_calls == []
    assert other_calls == []
    _assert_clean(repr(store.audit_log[-1]))
    kept = await registry.call("other__echo", {})
    assert kept.is_error is False
    assert kept.content[0].text == "kept"
    assert other_calls == ["kept"]


@pytest.mark.asyncio
async def test_partial_failure_does_not_dispatch_a_shared_name_to_the_survivor(launch_app) -> None:
    # Different upstream ids cannot mint one public name through namespacing.
    # The held snapshots stand in for the last successful listings of two
    # participants. The failed participant must keep the survivor from looking unique.
    right_calls: list[str] = []
    spare_calls: list[str] = []
    right = launch_app(_marker_app("kept", right_calls))
    spare = launch_app(_marker_app("spare", spare_calls))
    registry = Registry(
        [
            UpstreamConfig("left", "http://127.0.0.1:1/mcp", 0.4),
            UpstreamConfig("right", f"{right}/mcp", 5),
            UpstreamConfig("spare", f"{spare}/mcp", 5),
        ]
    )
    held = Tool(
        name="right__echo",
        description=_SECRET_URL,
        input_schema={"type": "object", "properties": {}},
    )
    registry._registrations["left"] = [held]
    registry._registrations["right"] = [held]

    listed = await registry.list_tools_result()
    assert [tool.name for tool in listed.tools] == ["spare__echo"]
    assert ("tool_excluded", "right", "right__echo") in _diag(registry)
    assert ("upstream_list_failed", "left", "") in _diag(registry)
    _assert_clean(_messages(registry))
    assert "127.0.0.1" not in _messages(registry)

    rejected, store = await _execute(registry, ("right__echo", "spare__echo"), "right__echo", _LEAKY)
    assert rejected.is_error is True
    _assert_clean(rejected.content[0].text)
    assert rejected.content[0].text == (
        "Duplicate public tool name right__echo. Conflicting registration excluded."
    )
    assert right_calls == []
    assert spare_calls == []
    _assert_clean(repr(store.audit_log[-1]))

    spared = await registry.call("spare__echo", {})
    assert spared.is_error is False
    assert spared.content[0].text == "spare"
    assert spare_calls == ["spare"]
    assert right_calls == []
    again = await registry.list_tools_result()
    assert [tool.name for tool in again.tools] == ["spare__echo"]
    assert _diag(registry).count(("tool_excluded", "right", "right__echo")) == 1
