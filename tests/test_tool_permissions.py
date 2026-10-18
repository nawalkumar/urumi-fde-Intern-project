"""Per-tool authorization on the shared execute_tool path."""

import pytest

from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.config import UpstreamConfig
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp, start_gateway
from tests.test_upstream_controls import _duplicate_names_app, _marker_app

SENTINEL = "argument-sentinel-not-stored"


class Recording:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, arguments: dict) -> object:
        self.calls.append(name)
        raise AssertionError("this call should not reach an upstream")


def _dispatcher(store: InMemoryStore, registry: object) -> tuple[ToolDispatcher, Authenticator]:
    authenticator = Authenticator(store)
    return ToolDispatcher(registry, authenticator, Auditor(store)), authenticator


@pytest.mark.asyncio
async def test_permissions_are_exact_independent_and_audited(launch_app) -> None:
    utility = launch_app(create_utility())
    gateway, store, allowed = await start_gateway(
        launch_app,
        [UpstreamConfig("utility", f"{utility}/mcp", 5)],
        permissions=("utility__convert_units",),
    )
    other = await store.issue()
    assert await store.list_permissions(allowed.key_id) == ["utility__convert_units"]
    assert await store.list_permissions(other.key_id) == []
    assert await store.grant(other.key_id, "utility__*") is False
    assert await store.permits(allowed.key_id, "woocommerce__create_product") is False

    calls = {"n": 0}
    original = Registry.call

    async def spy(self, name, arguments):
        calls["n"] += 1
        return await original(self, name, arguments)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Registry, "call", spy)
    try:
        async with open_mcp(f"{gateway}/mcp", allowed.raw_key) as client:
            converted = await client.call_tool(
                "utility__convert_units",
                {"value": 1, "from_unit": "kg", "to_unit": "g", "note": SENTINEL},
            )
            denied = await client.call_tool(
                "woocommerce__create_product",
                {"name": SENTINEL},
            )
            unknown = await client.call_tool("nope__missing", {"note": SENTINEL})
        async with open_mcp(f"{gateway}/mcp", other.raw_key) as client:
            blocked = await client.call_tool(
                "utility__convert_units",
                {"value": 1, "from_unit": "kg", "to_unit": "g", "note": SENTINEL},
            )
    finally:
        monkeypatch.undo()

    assert converted.is_error is False
    assert converted.content[0].text == "1000"
    assert denied.is_error is True
    assert denied.content[0].text == "Unknown upstream in tool woocommerce__create_product."
    assert unknown.is_error is True
    assert unknown.content[0].text == "Unknown upstream in tool nope__missing."
    assert blocked.is_error is False
    assert blocked.content[0].text == "1000"
    assert calls["n"] == 4

    blob = "\n".join(repr(row) for row in store.audit_log)
    assert SENTINEL not in blob
    assert allowed.raw_key not in blob
    assert other.raw_key not in blob
    denied_rows = [row for row in store.audit_log if row.outcome == "unauthorized"]
    assert denied_rows == []
    assert store.audit_log[0].actor == allowed.key_id
    assert store.audit_log[0].tool_name == "utility__convert_units"
    assert store.audit_log[0].outcome == "success"
    assert store.audit_log[0].occurred_at.tzinfo is not None


@pytest.mark.asyncio
async def test_revoked_key_is_rejected_even_with_a_permission() -> None:
    store = InMemoryStore()
    issued = await store.issue()
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    dispatcher, authenticator = _dispatcher(store, Recording())
    principal = await authenticator.authenticate(issued.raw_key)
    assert await store.revoke(issued.key_id) is True
    result = await dispatcher.execute_tool(
        principal,
        "utility__convert_units",
        {"note": SENTINEL},
    )
    assert result.is_error is True
    assert result.content[0].text == "Authentication required."
    assert dispatcher.registry.calls == []
    assert store.audit_log[-1].actor == issued.key_id
    assert store.audit_log[-1].tool_name == "utility__convert_units"
    assert store.audit_log[-1].outcome == "unauthorized"
    assert SENTINEL not in repr(store.audit_log[-1])
    assert issued.raw_key not in repr(store.audit_log[-1])


@pytest.mark.asyncio
async def test_a_permitted_colliding_name_is_not_dispatched(launch_app) -> None:
    calls: list[str] = []
    duplicate = launch_app(_duplicate_names_app(calls))
    healthy = launch_app(_marker_app("kept"))
    registry = Registry(
        [
            UpstreamConfig("dup", f"{duplicate}/mcp", 5),
            UpstreamConfig("other", f"{healthy}/mcp", 5),
        ]
    )
    store = InMemoryStore()
    issued = await store.issue()
    assert await store.grant(issued.key_id, "dup__echo") is True
    assert await store.grant(issued.key_id, "other__echo") is True
    dispatcher, authenticator = _dispatcher(store, registry)
    principal = await authenticator.authenticate(issued.raw_key)
    conflict = await dispatcher.execute_tool(principal, "dup__echo", {"note": SENTINEL})
    assert conflict.is_error is True
    assert conflict.content[0].text == (
        "Duplicate public tool name dup__echo. Conflicting registration excluded."
    )
    assert calls == []
    kept = await dispatcher.execute_tool(principal, "other__echo", {})
    assert kept.is_error is False
    assert kept.content[0].text == "kept"
    assert SENTINEL not in "\n".join(repr(row) for row in store.audit_log)


def test_grant_command_without_a_database_prints_no_key(monkeypatch, capsys) -> None:
    from apps.gateway.provision import main

    monkeypatch.delenv("GATEWAY_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as caught:
        main(["grant", "ab" * 8, "utility__convert_units"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "urk_" not in captured.out
    assert "urk_" not in captured.err
