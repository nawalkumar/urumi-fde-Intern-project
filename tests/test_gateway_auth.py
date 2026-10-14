"""Gateway API key authentication, revocation, and audit logging."""

import logging

import httpx2
import pytest

from mcp.types import CallToolResult, TextContent

from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.config import UpstreamConfig
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.keys import Principal, hash_key
from apps.gateway.provision import main
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import (
    DELETE_PERMISSION,
    INSERT_AUDIT,
    INSERT_KEY,
    INSERT_PERMISSION,
    LIST_PERMISSIONS,
    REVOKE_KEY,
    SCHEMA,
    SELECT_KEY,
    SELECT_PERMISSION,
    InMemoryStore,
)
from apps.support_mcp.app.main import create_app as create_support
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp, start_gateway

SENTINEL = "argument-sentinel-not-stored"
UNKNOWN_ID = "ab" * 8


def _post(base: str, header: str | None):
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    with httpx2.Client(timeout=5) as client:
        return client.post(f"{base}/mcp", headers=headers, content=b'{"method":"tools/call"}')


def _audit_text(store: InMemoryStore) -> str:
    return "\n".join(repr(row) for row in store.audit_log)


@pytest.mark.asyncio
async def test_valid_key_lists_and_calls_without_auditing_arguments(launch_app, caplog) -> None:
    support = launch_app(create_support())
    gateway, store, issued = await start_gateway(
        launch_app,
        [UpstreamConfig("support", f"{support}/mcp", 5)],
        permissions=("support__search_notes",),
    )
    caplog.set_level(logging.DEBUG, logger="apps.gateway")

    async with open_mcp(f"{gateway}/mcp", issued.raw_key) as client:
        listed = await client.list_tools()
        assert [tool.name for tool in listed.tools] == ["support__search_notes"]
        assert store.audit_log == []
        result = await client.call_tool("support__search_notes", {"query": SENTINEL})

    assert result.is_error is False
    assert result.content[0].text == "No notes matched."
    assert len(store.audit_log) == 1
    row = store.audit_log[0]
    assert row.actor == issued.key_id
    assert row.tool_name == "support__search_notes"
    assert row.outcome == "success"
    assert row.occurred_at.tzinfo is not None
    blob = _audit_text(store)
    assert SENTINEL not in blob
    assert issued.raw_key not in blob
    assert issued.raw_key not in caplog.text


@pytest.mark.asyncio
async def test_rejected_keys_do_not_reach_upstream_and_a_live_key_still_works(launch_app, monkeypatch) -> None:
    utility = launch_app(create_utility())
    gateway, store, keeper = await start_gateway(
        launch_app,
        [UpstreamConfig("utility", f"{utility}/mcp", 5)],
        permissions=("utility__convert_units",),
    )
    revoked = await store.issue()
    assert await store.revoke(revoked.key_id) is True
    calls = {"n": 0}
    original_call = Registry.call
    original_list = Registry.list_tools_result

    async def spy_call(self, name, arguments):
        calls["n"] += 1
        return await original_call(self, name, arguments)

    async def spy_list(self):
        calls["n"] += 1
        return await original_list(self)

    monkeypatch.setattr(Registry, "call", spy_call)
    monkeypatch.setattr(Registry, "list_tools_result", spy_list)

    async with open_mcp(f"{gateway}/mcp", keeper.raw_key) as client:
        await client.list_tools()
    assert calls["n"] >= 1
    calls["n"] = 0

    unknown = f"urk_{UNKNOWN_ID}.{'c' * 43}"
    wrong_secret = f"urk_{keeper.key_id}.{'d' * 43}"
    bodies = [
        _post(gateway, None).content,
        _post(gateway, "Bearer").content,
        _post(gateway, "Basic urk_not_a_key").content,
        _post(gateway, f"Bearer {unknown}").content,
        _post(gateway, f"Bearer {wrong_secret}").content,
        _post(gateway, f"Bearer {revoked.raw_key}").content,
    ]
    assert calls["n"] == 0
    assert len(set(bodies)) == 1
    assert b"invalid_token" in bodies[0]
    assert b"Authentication required." in bodies[0]
    assert UNKNOWN_ID.encode() not in bodies[0]
    assert keeper.key_id.encode() not in bodies[0]
    assert revoked.key_id.encode() not in bodies[0]

    failed = False
    try:
        async with open_mcp(f"{gateway}/mcp") as client:
            await client.list_tools()
    except Exception:
        failed = True
    assert failed is True
    assert calls["n"] == 0

    blob = _audit_text(store)
    assert keeper.raw_key not in blob
    assert revoked.raw_key not in blob
    assert UNKNOWN_ID not in blob
    assert keeper.key_id not in blob
    assert revoked.key_id not in blob
    assert store.audit_log
    assert {row.actor for row in store.audit_log} == {"unauthenticated"}
    assert {row.outcome for row in store.audit_log} == {"unauthorized"}
    assert {row.tool_name for row in store.audit_log} == {""}

    async with open_mcp(f"{gateway}/mcp", keeper.raw_key) as client:
        result = await client.call_tool(
            "utility__convert_units",
            {"value": 1, "from_unit": "kg", "to_unit": "g"},
        )
    assert result.is_error is False
    assert result.content[0].text == "1000"
    assert calls["n"] >= 1
    assert store.audit_log[-1].actor == keeper.key_id
    assert store.audit_log[-1].tool_name == "utility__convert_units"
    assert store.audit_log[-1].outcome == "success"


@pytest.mark.asyncio
async def test_upstream_failure_is_audited_without_sensitive_text(launch_app) -> None:
    utility = launch_app(create_utility())
    gateway, store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("utility", f"{utility}/mcp", 5),
            UpstreamConfig("down", "http://127.0.0.1:1/mcp", 1),
        ],
        permissions=("utility__convert_units", "down__missing"),
    )
    async with open_mcp(f"{gateway}/mcp", issued.raw_key) as client:
        failed = await client.call_tool("down__missing", {"note": SENTINEL})
    assert failed.is_error is True
    row = store.audit_log[-1]
    assert row.actor == issued.key_id
    assert row.tool_name == "down__missing"
    assert row.outcome == "upstream_failure"
    blob = repr(row)
    assert SENTINEL not in blob
    assert "127.0.0.1" not in blob
    assert "ConnectError" not in blob
    assert issued.raw_key not in blob

    async with open_mcp(f"{gateway}/mcp", issued.raw_key) as client:
        succeeded = await client.call_tool(
            "utility__convert_units",
            {"value": 5, "from_unit": "m", "to_unit": "km"},
        )
    assert succeeded.is_error is False
    assert store.audit_log[-1].outcome == "success"


@pytest.mark.asyncio
async def test_execute_tool_rejects_forged_and_revoked_principals() -> None:
    store = InMemoryStore()
    issued = await store.issue()
    authenticator = Authenticator(store)
    principal = await authenticator.authenticate(issued.raw_key)
    assert principal is not None
    await store.revoke(issued.key_id)

    class Recording:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call(self, name: str, arguments: dict) -> object:
            self.calls.append(name)
            raise AssertionError("revoked or forged principal reached an upstream")

    registry = Recording()
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(store))
    revoked = await dispatcher.execute_tool(
        principal,
        "utility__convert_units",
        {"value": 1, "from_unit": "km", "to_unit": "m", "note": SENTINEL},
    )
    assert revoked.is_error is True
    assert registry.calls == []
    assert store.audit_log[-1].actor == issued.key_id
    assert store.audit_log[-1].outcome == "unauthorized"
    assert SENTINEL not in repr(store.audit_log[-1])

    forged_name = await dispatcher.execute_tool(
        "attacker-chosen-actor",
        "utility__convert_units",
        {"note": SENTINEL},
    )
    assert forged_name.is_error is True
    assert registry.calls == []
    assert store.audit_log[-1].actor == "unauthenticated"
    assert "attacker-chosen-actor" not in repr(store.audit_log[-1])
    assert SENTINEL not in repr(store.audit_log[-1])

    forged_seal = await dispatcher.execute_tool(
        Principal(key_id=issued.key_id, seal="0" * 64),
        "utility__convert_units",
        {"note": SENTINEL},
    )
    assert forged_seal.is_error is True
    assert registry.calls == []
    assert store.audit_log[-1].actor == "unauthenticated"
    assert issued.key_id not in repr(store.audit_log[-1])


@pytest.mark.asyncio
async def test_audit_write_failure_keeps_the_completed_tool_result(caplog) -> None:
    """A completed upstream call stays successful when the audit insert fails.

    The gateway does not own the upstream database, so the tool result and the
    audit row are not one transaction. The failure is logged by exception type
    and the caller still receives the upstream result.
    """

    class BrokenAudit(InMemoryStore):
        async def add_audit(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("audit insert failed")

    store = BrokenAudit()
    issued = await store.issue()
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    authenticator = Authenticator(store)
    principal = await authenticator.authenticate(issued.raw_key)
    assert principal is not None

    class Ready:
        async def call(self, name: str, arguments: dict) -> CallToolResult:
            assert name == "utility__convert_units"
            assert arguments == {}
            return CallToolResult(content=[TextContent(type="text", text="converted")], is_error=False)

    dispatcher = ToolDispatcher(Ready(), authenticator, Auditor(store))
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.audit"):
        result = await dispatcher.execute_tool(principal, "utility__convert_units", {})
    assert result.is_error is False
    assert result.content[0].text == "converted"
    assert store.audit_log == []
    assert "Audit write failed: RuntimeError" in caplog.text
    assert "audit insert failed" not in caplog.text


@pytest.mark.asyncio
async def test_issue_stores_only_the_hash() -> None:
    store = InMemoryStore()
    issued = await store.issue()
    row = await store.get(issued.key_id)
    assert row is not None
    assert row.secret_hash == hash_key(issued.raw_key)
    assert issued.raw_key not in row.secret_hash
    assert issued.raw_key not in repr(store._keys)
    assert row.revoked_at is None
    assert row.created_at.tzinfo is not None


def test_provision_without_database_url_prints_no_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GATEWAY_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as caught:
        main(["issue"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "urk_" not in captured.out
    assert "urk_" not in captured.err
    assert "GATEWAY_DATABASE_URL" in captured.err


def test_sql_statements_are_static_or_parameterized() -> None:
    for statement in (
        INSERT_KEY,
        SELECT_KEY,
        REVOKE_KEY,
        INSERT_AUDIT,
        INSERT_PERMISSION,
        SELECT_PERMISSION,
        DELETE_PERMISSION,
        LIST_PERMISSIONS,
    ):
        assert "%s" in statement
    for statement in SCHEMA:
        assert "%s" not in statement
        assert "password" not in statement.casefold()
        assert "urk_" not in statement
