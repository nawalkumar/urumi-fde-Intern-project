"""Read-only GET /admin/status. In-memory keys and local fake upstreams."""

import json

import httpx2
import pytest

from apps.common.http import build_http_app
from apps.gateway.app.config import Settings, StatusPermissionConfigError, UpstreamConfig, status_permission_mode
from apps.gateway.app.main import create_app
from apps.gateway.app.permissions import STATUS_READ_PERMISSION
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore, UnconfiguredStore
from apps.gateway.provision import _permission_args
from apps.utility_mcp.app.main import create_app as create_utility
from mcp.server.mcpserver import MCPServer
from tests.mcp_session import open_mcp
from tests.test_upstream_controls import _SECRET, _SECRET_URL, _assert_clean, _marker_app, _switching_app

UNKNOWN_ID = "ab" * 8


def _get(base: str, header: str | None, timeout: float = 10):
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    with httpx2.Client(timeout=timeout) as client:
        return client.get(f"{base}/admin/status", headers=headers)


def _post_mcp(base: str, header: str | None):
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    with httpx2.Client(timeout=10) as client:
        return client.post(f"{base}/mcp", headers=headers, content=b'{"method":"tools/call"}')


def _hidden(blob: str) -> None:
    _assert_clean(blob)
    if "127.0.0.1" in blob or "convert_units" in blob or "search_notes" in blob:
        raise AssertionError("Status output contained a URL or an unadvertised tool name.")


def _bearer(raw_key: str) -> str:
    return f"Bearer {raw_key}"


def test_status_report_is_unknown_before_any_list() -> None:
    registry = Registry([UpstreamConfig("utility", "http://127.0.0.1:9/mcp", 1, enabled=True)])
    report = registry.status_report()
    assert report["status"] == "ok"
    assert report["advertised_tool_count"] == 0
    assert report["diagnostics"] == []
    assert report["upstreams"] == [
        {
            "id": "utility",
            "enabled": True,
            "listing_state": "unknown",
            "advertised_tool_count": None,
            "excluded_tool_count": None,
        }
    ]
    _hidden(json.dumps(report))


@pytest.mark.asyncio
async def test_status_distinguishes_listing_success_failure_and_disabled(launch_app) -> None:
    flaky_calls: list[str] = []
    state = {"mode": "fail", "boom": _SECRET, "description": _SECRET_URL}
    utility = launch_app(create_utility())
    flaky = launch_app(_switching_app(state, flaky_calls))
    store = InMemoryStore()
    granted = await store.issue()
    assert await store.grant(granted.key_id, "utility__convert_units") is True
    assert await store.grant(granted.key_id, "flaky__echo") is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("utility", f"{utility}/mcp", 5),
                UpstreamConfig("flaky", f"{flaky}/mcp", 5),
                UpstreamConfig("support", "http://127.0.0.1:1/mcp", 5, enabled=False),
            ]
        ),
        store=store,
    )
    base = launch_app(app)
    failed = _get(base, _bearer(granted.raw_key))
    assert failed.status_code == 200
    _hidden(failed.text)
    body = failed.json()
    by_id = {item["id"]: item for item in body["upstreams"]}
    assert by_id["utility"] == {
        "id": "utility",
        "enabled": True,
        "listing_state": "listing_ok",
        "advertised_tool_count": 1,
        "excluded_tool_count": 0,
    }
    assert by_id["flaky"]["enabled"] is True
    assert by_id["flaky"]["listing_state"] == "listing_failed"
    assert by_id["flaky"]["advertised_tool_count"] is None
    assert by_id["support"] == {
        "id": "support",
        "enabled": False,
        "listing_state": "disabled",
        "advertised_tool_count": 0,
        "excluded_tool_count": 0,
    }
    assert body["advertised_tool_count"] == 1
    assert {(item["code"], item["upstream_id"], item["tool_name"]) for item in body["diagnostics"]} == {
        ("upstream_list_failed", "flaky", None),
        ("upstream_disabled", "support", None),
    }
    assert flaky_calls == []

    state["mode"] = "unique"
    recovered = _get(base, _bearer(granted.raw_key))
    assert recovered.status_code == 200
    _hidden(recovered.text)
    restored = {item["id"]: item for item in recovered.json()["upstreams"]}
    assert restored["flaky"]["listing_state"] == "listing_ok"
    assert restored["flaky"]["advertised_tool_count"] == 1
    assert restored["support"]["listing_state"] == "disabled"
    assert restored["utility"]["listing_state"] == "listing_ok"
    assert recovered.json()["advertised_tool_count"] == 2
    assert not any(item["code"] == "upstream_list_failed" for item in recovered.json()["diagnostics"])
    assert any(item["code"] == "upstream_disabled" for item in recovered.json()["diagnostics"])
    assert flaky_calls == []

    async with open_mcp(f"{base}/mcp", granted.raw_key) as client:
        listed = await client.list_tools()
        assert [tool.name for tool in listed.tools] == ["utility__convert_units", "flaky__echo"]
        called = await client.call_tool(
            "utility__convert_units",
            {"value": 1, "from_unit": "kg", "to_unit": "g", "note": _SECRET},
        )
    assert called.is_error is False
    assert called.content[0].text == "1000"
    assert [row.outcome for row in store.audit_log] == ["success"]
    assert store.audit_log[0].tool_name == "utility__convert_units"
    _assert_clean(repr(store.audit_log[0]))


@pytest.mark.asyncio
async def test_status_reports_an_excluded_public_tool(launch_app) -> None:
    dup_calls: list[str] = []
    other_calls: list[str] = []
    state = {"mode": "duplicate", "boom": _SECRET, "description": _SECRET_URL}
    duplicate = launch_app(_switching_app(state, dup_calls))
    other = launch_app(_marker_app("kept", other_calls))
    store = InMemoryStore()
    issued = await store.issue()
    assert await store.grant(issued.key_id, "other__echo") is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("dup", f"{duplicate}/mcp", 5),
                UpstreamConfig("other", f"{other}/mcp", 5),
            ]
        ),
        store=store,
    )
    base = launch_app(app)
    response = _get(base, _bearer(issued.raw_key))
    assert response.status_code == 200
    _hidden(response.text)
    body = response.json()
    by_id = {item["id"]: item for item in body["upstreams"]}
    assert by_id["dup"]["listing_state"] == "listing_ok"
    assert by_id["dup"]["advertised_tool_count"] == 0
    assert by_id["dup"]["excluded_tool_count"] == 1
    assert by_id["other"]["advertised_tool_count"] == 1
    assert body["advertised_tool_count"] == 1
    assert body["diagnostics"] == [
        {
            "code": "tool_excluded",
            "upstream_id": "dup",
            "tool_name": "dup__echo",
            "message": "Duplicate public tool name dup__echo. Conflicting registration excluded.",
        }
    ]
    assert dup_calls == []
    assert other_calls == []


@pytest.mark.asyncio
async def test_status_requires_a_live_key_and_ignores_tool_grants(launch_app) -> None:
    utility = launch_app(create_utility())
    store = InMemoryStore()
    granted = await store.issue()
    empty = await store.issue()
    revoked = await store.issue()
    assert await store.grant(granted.key_id, "utility__convert_units") is True
    assert await store.revoke(revoked.key_id) is True
    app = create_app(
        Settings(upstreams=[UpstreamConfig("utility", f"{utility}/mcp", 5)]),
        store=store,
    )
    base = launch_app(app)
    unknown = f"urk_{UNKNOWN_ID}.{'c' * 43}"
    denied = [
        _get(base, None),
        _get(base, "Bearer"),
        _get(base, _bearer(unknown)),
        _get(base, _bearer(revoked.raw_key)),
    ]
    mcp_denied = _post_mcp(base, None)
    assert mcp_denied.status_code == 401
    for response in denied:
        assert response.status_code == 401
        assert response.json() == mcp_denied.json()
        assert response.json() == {
            "error": "invalid_token",
            "error_description": "Authentication required.",
        }
        assert "invalid_token" in response.headers["www-authenticate"]
        _hidden(response.text)
        assert granted.key_id not in response.text
        assert revoked.key_id not in response.text
        assert UNKNOWN_ID not in response.text
        if granted.raw_key in response.text or revoked.raw_key in response.text or unknown in response.text:
            raise AssertionError("Sensitive text appeared in the status response.")
    assert app.state.registry.status_report()["upstreams"][0]["listing_state"] == "unknown"
    assert {row.actor for row in store.audit_log} == {"unauthenticated"}
    assert {row.outcome for row in store.audit_log} == {"unauthorized"}
    assert {row.tool_name for row in store.audit_log} == {""}

    with_grant = _get(base, _bearer(granted.raw_key))
    without_grant = _get(base, _bearer(empty.raw_key))
    assert with_grant.status_code == 200
    assert without_grant.status_code == 200
    assert with_grant.json() == without_grant.json()
    _hidden(with_grant.text)
    assert with_grant.json()["upstreams"][0]["listing_state"] == "listing_ok"
    assert with_grant.json()["advertised_tool_count"] == 1
    if granted.raw_key in with_grant.text or empty.raw_key in without_grant.text:
        raise AssertionError("Sensitive text appeared in the status response.")

    async with open_mcp(f"{base}/mcp", empty.raw_key) as client:
        hidden = await client.list_tools()
    assert [tool.name for tool in hidden.tools] == []
    async with open_mcp(f"{base}/mcp", granted.raw_key) as client:
        visible = await client.list_tools()
        called = await client.call_tool(
            "utility__convert_units",
            {"value": 2, "from_unit": "km", "to_unit": "m"},
        )
    assert [tool.name for tool in visible.tools] == ["utility__convert_units"]
    assert called.is_error is False
    assert called.content[0].text == "2000"

    with httpx2.Client(timeout=5) as client:
        health = client.get(f"{base}/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_status_reports_auth_store_unavailable_without_listing(launch_app) -> None:
    calls: list[str] = []
    state = {"mode": "unique", "boom": _SECRET, "description": _SECRET_URL}
    upstream = launch_app(_switching_app(state, calls))
    app = create_app(
        Settings(upstreams=[UpstreamConfig("utility", f"{upstream}/mcp", 5)]),
        store=UnconfiguredStore(),
    )
    base = launch_app(app)
    response = _get(base, _bearer(f"urk_{UNKNOWN_ID}.{'d' * 43}"))
    assert response.status_code == 503
    assert response.json() == {
        "error": "unavailable",
        "error_description": "Authentication is unavailable.",
    }
    _hidden(response.text)
    assert calls == []
    assert app.state.registry.status_report()["upstreams"][0]["listing_state"] == "unknown"
    assert response.json().get("upstreams") is None


def _status_named_tool(calls: list[str]):
    mcp = MCPServer("status-name")

    @mcp.tool(name="read_status")
    def read_status(note: str = "") -> str:
        """Record that this tool was invoked."""
        calls.append(note)
        return "upstream-status-body"

    return build_http_app(mcp, lambda: {"status": "ok"})


def test_status_permission_flag_defaults_open_and_rejects_typos(monkeypatch) -> None:
    monkeypatch.delenv("GATEWAY_STATUS_PERMISSION", raising=False)
    monkeypatch.delenv("GATEWAY_DATABASE_URL", raising=False)
    assert status_permission_mode(None) == "open"
    assert status_permission_mode("  ") == "open"
    assert status_permission_mode("OPEN") == "open"
    assert status_permission_mode(" Required ") == "required"
    assert Settings.from_env().status_permission == "open"
    monkeypatch.setenv("GATEWAY_STATUS_PERMISSION", "required")
    assert Settings.from_env().status_permission == "required"
    message = "GATEWAY_STATUS_PERMISSION must be empty, open, or required."
    for bad in ("require", "false", "0", "yes", "not-a-mode-secret"):
        with pytest.raises(StatusPermissionConfigError) as caught:
            status_permission_mode(bad)
        assert str(caught.value) == message
        monkeypatch.setenv("GATEWAY_STATUS_PERMISSION", bad)
        with pytest.raises(StatusPermissionConfigError) as from_env:
            Settings.from_env()
        assert str(from_env.value) == message


def test_grant_accepts_only_the_exact_status_permission() -> None:
    key_id = "ab" * 8
    assert STATUS_READ_PERMISSION == "gateway__read_status"
    assert _permission_args(key_id, STATUS_READ_PERMISSION) is True
    assert _permission_args(key_id, "utility__convert_units") is True
    assert _permission_args(key_id, "*") is False
    assert _permission_args(key_id, "gateway__*") is False
    assert _permission_args(key_id, "status") is False
    assert _permission_args(key_id, "") is False
    assert _permission_args("not-a-key-id", STATUS_READ_PERMISSION) is False


@pytest.mark.asyncio
async def test_required_status_permission_is_not_a_tool(launch_app) -> None:
    sentinel = "status-argument-sentinel"
    calls: list[str] = []
    utility = launch_app(create_utility())
    named = launch_app(_status_named_tool(calls))
    store = InMemoryStore()
    tool_key = await store.issue()
    status_key = await store.issue()
    empty = await store.issue()
    revoked = await store.issue()
    assert await store.grant(tool_key.key_id, "utility__convert_units") is True
    assert await store.grant(status_key.key_id, STATUS_READ_PERMISSION) is True
    assert await store.revoke(revoked.key_id) is True
    app = create_app(
        Settings(
            upstreams=[
                UpstreamConfig("utility", f"{utility}/mcp", 5),
                UpstreamConfig("gateway", f"{named}/mcp", 5),
            ],
            status_permission="required",
        ),
        store=store,
    )
    base = launch_app(app)

    denied = _get(base, _bearer(tool_key.raw_key))
    assert denied.status_code == 403
    assert denied.json() == {"error": "forbidden", "error_description": "Status access was denied."}
    assert tool_key.raw_key not in denied.text
    assert sentinel not in denied.text
    assert app.state.registry.status_report()["upstreams"][0]["listing_state"] == "unknown"
    assert app.state.registry.status_report()["upstreams"][1]["listing_state"] == "unknown"
    assert calls == []
    assert store.audit_log[-1].actor == tool_key.key_id
    assert store.audit_log[-1].tool_name == STATUS_READ_PERMISSION
    assert store.audit_log[-1].outcome == "unauthorized"
    _assert_clean(repr(store.audit_log[-1]))
    assert sentinel not in repr(store.audit_log[-1])
    assert tool_key.raw_key not in repr(store.audit_log[-1])

    missing = _get(base, None)
    revoked_response = _get(base, _bearer(revoked.raw_key))
    assert missing.status_code == 401
    assert revoked_response.status_code == 401
    assert missing.json() == revoked_response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required.",
    }
    assert revoked.key_id not in revoked_response.text
    assert revoked.raw_key not in revoked_response.text
    assert app.state.registry.status_report()["upstreams"][0]["listing_state"] == "unknown"

    allowed = _get(base, _bearer(status_key.raw_key))
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"
    assert {item["id"]: item["listing_state"] for item in allowed.json()["upstreams"]} == {
        "utility": "listing_ok",
        "gateway": "listing_ok",
    }
    assert status_key.raw_key not in allowed.text
    assert calls == []

    async with open_mcp(f"{base}/mcp", status_key.raw_key) as client:
        listed = await client.list_tools()
        blocked_tool = await client.call_tool(
            "utility__convert_units",
            {"value": 1, "from_unit": "km", "to_unit": "m", "note": sentinel},
        )
        blocked_status = await client.call_tool("gateway__read_status", {"note": sentinel})
    assert [tool.name for tool in listed.tools] == []
    assert blocked_tool.is_error is True
    assert blocked_tool.content[0].text == "Permission denied."
    assert blocked_status.is_error is True
    assert blocked_status.content[0].text == "Permission denied."
    assert calls == []
    assert sentinel not in "\n".join(repr(row) for row in store.audit_log)
    assert status_key.raw_key not in "\n".join(repr(row) for row in store.audit_log)

    async with open_mcp(f"{base}/mcp", empty.raw_key) as client:
        empty_list = await client.list_tools()
    assert [tool.name for tool in empty_list.tools] == []
    assert any(
        row.outcome == "unauthorized" and row.actor == status_key.key_id and row.tool_name == "utility__convert_units"
        for row in store.audit_log
    )
    assert not any(row.outcome == "success" and row.actor == status_key.key_id for row in store.audit_log)
