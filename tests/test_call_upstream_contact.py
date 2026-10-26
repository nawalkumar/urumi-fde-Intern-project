"""A tools/call reaches an upstream only after the dispatcher allows it.

MCP SDK 2.3.0 looks up a tool's input schema before dispatching a 2026-07-28
tools/call with arguments. Without a schema hook it runs the gateway's own
tools/list handler, which refreshes every enabled upstream. The gateway
supplies a hook that returns no schema, so denied calls contact no upstream.
"""

import json
from contextlib import asynccontextmanager
from typing import Annotated

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from pydantic import WithJsonSchema

from apps.common.http import build_http_app
from apps.gateway.app.config import Settings, StoreProvisionerSettings, UpstreamConfig
from apps.gateway.app.main import create_app
from apps.gateway.app.passwords import hash_password
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.stores.model import public_tool, upstream_id_for
from apps.gateway.app.stores.service import StoreService
from apps.utility_mcp.app.main import create_app as create_utility
from tests.store_cluster import FakeCluster

MODERN = "2026-07-28"
DATE_WINDOW = {"after": "2026-10-25T00:00:00+00:00", "before": "2026-10-26T00:00:00+00:00"}
UNITS = {"value": 1, "from_unit": "km", "to_unit": "m"}
REGION = Annotated[str, WithJsonSchema({"type": "string", "x-mcp-header": "Region"})]


class MethodLog:
    """Record the JSON-RPC method of every POST an upstream receives."""

    def __init__(self, app) -> None:
        self.app = app
        self.methods: list[str] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        try:
            self.methods.append(str(json.loads(body).get("method", "")))
        except (ValueError, AttributeError):
            self.methods.append("")
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _store_upstream() -> MethodLog:
    mcp = MCPServer("woocommerce")

    @mcp.tool(name="list_orders")
    def list_orders(after: str = "", before: str = "") -> str:
        return "id=10 status=pending total=11.00 currency=USD"

    @mcp.tool(name="get_order")
    def get_order(order_id: int) -> str:
        return f"order {order_id}"

    return MethodLog(build_http_app(mcp, lambda: {"status": "ok"}))


def _geo_upstream() -> MethodLog:
    mcp = MCPServer("geo")

    @mcp.tool(name="locate")
    def locate(region: REGION) -> str:
        return f"region={region}"

    return MethodLog(build_http_app(mcp, lambda: {"status": "ok"}))


async def _ready_store(memory: InMemoryStore, owner: str) -> str:
    cluster = FakeCluster()
    cluster.ready = True
    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True, max_per_user=2)),
        cluster,
        autostart=False,
    )
    record, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    await service.reconcile_store(record.store_id)
    return record.store_id


class Rig:
    def __init__(self) -> None:
        self.memory = InMemoryStore()
        self.store = _store_upstream()
        self.utility = MethodLog(create_utility())
        self.geo = _geo_upstream()

    def contacted(self) -> dict[str, list[str]]:
        return {"store": self.store.methods, "utility": self.utility.methods, "geo": self.geo.methods}

    def reset(self) -> None:
        for log in (self.store, self.utility, self.geo):
            log.methods.clear()

    def audit(self) -> list:
        """Rows written after setup. Creating the store writes admin rows first."""
        return self.memory.audit_log[self.audit_start :]


async def _rig(launch_app) -> Rig:
    rig = Rig()
    rig.owner = await rig.memory.create_user("ada", hash_password("password-value-ok"), "admin")
    rig.store_id = await _ready_store(rig.memory, rig.owner.user_id)
    rig.list_tool = public_tool(rig.store_id, "list_orders")
    rig.get_tool = public_tool(rig.store_id, "get_order")
    upstreams = [
        UpstreamConfig(upstream_id_for(rig.store_id), f"{launch_app(rig.store)}/mcp", 5),
        UpstreamConfig("utility", f"{launch_app(rig.utility)}/mcp", 5),
        UpstreamConfig("geo", f"{launch_app(rig.geo)}/mcp", 5),
    ]
    rig.url = f"{launch_app(create_app(Settings(upstreams=upstreams), store=rig.memory))}/mcp"
    rig.key = await rig.memory.issue_for_user(rig.owner.user_id)
    assert await rig.memory.grant(rig.key.key_id, rig.list_tool) is True
    rig.audit_start = len(rig.memory.audit_log)
    return rig


@asynccontextmanager
async def _session(url: str, raw_key: str, *, mode: str = "auto", event_hooks: dict | None = None):
    async with (
        httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {raw_key}"},
            timeout=httpx2.Timeout(5.0),
            event_hooks=event_hooks or {},
        ) as http,
        Client(streamable_http_client(url, http_client=http), mode=mode) as client,
    ):
        yield client


def _leaves(exc: BaseException) -> list[BaseException]:
    inner = getattr(exc, "exceptions", None)
    return [exc] if not inner else [leaf for item in inner for leaf in _leaves(item)]


def _assert_denied(rig: Rig, result, tool: str) -> None:
    assert result.is_error is True
    assert result.content[0].text == "Permission denied."
    row = rig.memory.audit_log[-1]
    assert row.actor == rig.key.key_id
    assert row.tool_name == tool
    assert row.outcome == "unauthorized"
    assert row.upstream_id == ""
    assert row.duration_ms is None


@pytest.mark.asyncio
async def test_modern_ungranted_store_tool_is_denied_before_any_upstream_contact(launch_app) -> None:
    rig = await _rig(launch_app)
    async with _session(rig.url, rig.key.raw_key) as client:
        assert client.protocol_version == MODERN
        result = await client.call_tool(rig.get_tool, {"order_id": 10})
    _assert_denied(rig, result, rig.get_tool)
    assert len(rig.audit()) == 1
    assert rig.contacted() == {"store": [], "utility": [], "geo": []}


@pytest.mark.asyncio
async def test_modern_ungranted_utility_tool_is_denied_before_any_upstream_contact(launch_app) -> None:
    rig = await _rig(launch_app)
    async with _session(rig.url, rig.key.raw_key) as client:
        assert client.protocol_version == MODERN
        result = await client.call_tool("utility__convert_units", UNITS)
    _assert_denied(rig, result, "utility__convert_units")
    assert len(rig.audit()) == 1
    assert rig.contacted() == {"store": [], "utility": [], "geo": []}


@pytest.mark.asyncio
async def test_modern_granted_store_call_executes_only_on_its_own_upstream(launch_app) -> None:
    rig = await _rig(launch_app)
    async with _session(rig.url, rig.key.raw_key) as client:
        assert client.protocol_version == MODERN
        await client.list_tools()
        rig.reset()
        result = await client.call_tool(rig.list_tool, DATE_WINDOW)
    assert result.is_error is False
    assert result.content[0].text == "id=10 status=pending total=11.00 currency=USD"
    assert rig.store.methods.count("tools/call") == 1
    assert rig.store.methods[-2:] == ["tools/list", "tools/call"]
    assert rig.utility.methods == []
    assert rig.geo.methods == []
    row = rig.memory.audit_log[-1]
    assert (row.actor, row.tool_name, row.outcome) == (rig.key.key_id, rig.list_tool, "success")
    assert row.upstream_id == upstream_id_for(rig.store_id)
    assert isinstance(row.duration_ms, int)


@pytest.mark.asyncio
async def test_invalid_and_revoked_keys_cannot_trigger_upstream_discovery(launch_app) -> None:
    rig = await _rig(launch_app)
    revoked = await rig.memory.issue_for_user(rig.owner.user_id)
    assert await rig.memory.grant(revoked.key_id, rig.list_tool) is True
    assert await rig.memory.revoke(revoked.key_id) is True
    invalid = f"urk_{'ab' * 8}.{'c' * 43}"
    statuses: list[int] = []

    async def record(response) -> None:
        statuses.append(response.status_code)

    hooks = {"response": [record]}
    for raw_key in (invalid, revoked.raw_key):
        statuses.clear()
        with pytest.raises(Exception) as caught:
            async with _session(rig.url, raw_key, event_hooks=hooks) as client:
                await client.call_tool(rig.list_tool, DATE_WINDOW)
        assert all(isinstance(leaf, MCPError) for leaf in _leaves(caught.value))
        assert statuses and set(statuses) == {401}

    later = await rig.memory.issue_for_user(rig.owner.user_id)
    assert await rig.memory.grant(later.key_id, rig.list_tool) is True
    statuses.clear()
    with pytest.raises(Exception) as caught:
        async with _session(rig.url, later.raw_key, event_hooks=hooks) as client:
            assert await rig.memory.revoke(later.key_id) is True
            await client.call_tool(rig.list_tool, DATE_WINDOW)
    assert all(isinstance(leaf, MCPError) for leaf in _leaves(caught.value))
    assert statuses[0] == 200
    assert statuses[-1] == 401

    assert rig.contacted() == {"store": [], "utility": [], "geo": []}
    assert rig.audit()
    assert {row.actor for row in rig.audit()} == {"unauthenticated"}
    assert {row.outcome for row in rig.audit()} == {"unauthorized"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_tools_list_still_returns_only_granted_tools(launch_app, mode) -> None:
    rig = await _rig(launch_app)
    async with _session(rig.url, rig.key.raw_key, mode=mode) as client:
        listed = await client.list_tools()
    assert [tool.name for tool in listed.tools] == [rig.list_tool]
    assert rig.audit() == []


@pytest.mark.asyncio
async def test_legacy_protocol_denies_and_routes_as_before(launch_app) -> None:
    rig = await _rig(launch_app)
    async with _session(rig.url, rig.key.raw_key, mode="legacy") as client:
        assert client.protocol_version != MODERN
        denied = await client.call_tool(rig.get_tool, {"order_id": 10})
        _assert_denied(rig, denied, rig.get_tool)
        assert rig.contacted() == {"store": [], "utility": [], "geo": []}
        await client.list_tools()
        rig.reset()
        allowed = await client.call_tool(rig.list_tool, DATE_WINDOW)
    assert allowed.is_error is False
    assert rig.store.methods.count("tools/call") == 1
    assert rig.utility.methods == [] and rig.geo.methods == []
    assert [row.outcome for row in rig.audit()] == ["unauthorized", "success"]


@pytest.mark.asyncio
async def test_gateway_does_not_validate_mcp_param_headers(launch_app) -> None:
    """Deliberate trade-off: the gateway skips Mcp-Param-* header checks.

    A tampered header on a granted x-mcp-header argument is not rejected with
    HEADER_MISMATCH. The upstream still receives the body value with a header
    the gateway's own client derives from it. An ungranted tool with the same
    stray header gets the ordinary audited denial, so the header cannot be used
    to learn whether that tool exists.
    """
    rig = await _rig(launch_app)
    assert await rig.memory.grant(rig.key.key_id, "geo__locate") is True
    mirrored: list[str] = []

    async def tamper(request) -> None:
        try:
            method = json.loads(request.content).get("method")
        except (ValueError, AttributeError):
            return
        if method == "tools/call":
            mirrored.append(request.headers.get("mcp-param-region", ""))
            request.headers["Mcp-Param-Region"] = "tampered"

    async with _session(rig.url, rig.key.raw_key, event_hooks={"request": [tamper]}) as client:
        assert client.protocol_version == MODERN
        listed = await client.list_tools()
        assert sorted(tool.name for tool in listed.tools) == sorted(["geo__locate", rig.list_tool])
        rig.reset()
        located = await client.call_tool("geo__locate", {"region": "eu"})
        assert mirrored == ["eu"]
        assert located.is_error is False
        assert located.content[0].text == "region=eu"
        assert rig.geo.methods[-1] == "tools/call"
        assert rig.store.methods == [] and rig.utility.methods == []

        rig.reset()
        denied = await client.call_tool("utility__convert_units", UNITS)
    _assert_denied(rig, denied, "utility__convert_units")
    assert rig.contacted() == {"store": [], "utility": [], "geo": []}
