"""Discovery ranking cannot grant tools or leak another key's catalog."""

import httpx2
import pytest

from apps.gateway.app.config import Settings, StoreProvisionerSettings, UpstreamConfig
from apps.gateway.app.discovery import rank_tools
from apps.gateway.app.main import create_app
from apps.gateway.app.passwords import hash_password
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.stores.manifests import mcp_url
from apps.gateway.app.stores.model import namespace_for, public_tool
from apps.gateway.app.stores.service import StoreService
from apps.support_mcp.app.main import create_app as create_support
from apps.utility_mcp.app.main import create_app as create_utility
from mcp.types import Tool
from tests.mcp_session import open_mcp
from tests.store_cluster import FakeCluster
from tests.test_upstream_controls import _duplicate_names_app, _marker_app

_SECRET_QUERY = "find urk_secret.example and Bearer hunter2 ck_woo cs_woo"


def _client(base: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(base_url=base, timeout=10, headers={"X-Urumi-Request": "1"})


async def _names(url: str, api_key: str) -> list[str]:
    async with open_mcp(url, api_key) as client:
        listed = await client.list_tools()
    return [tool.name for tool in listed.tools]


def test_rank_prefers_the_closer_authorized_name() -> None:
    tools = [
        Tool(name="utility__convert_units", description="search a measurement", input_schema={"type": "object"}),
        Tool(name="support__search_notes", description="search notes", input_schema={"type": "object"}),
    ]
    ranked = rank_tools(tools, "search")
    assert [item.name for item in ranked] == ["support__search_notes", "utility__convert_units"]
    assert ranked[0].score > ranked[1].score


@pytest.mark.asyncio
async def test_search_cannot_reveal_or_call_an_ungranted_tool(launch_app, caplog) -> None:
    utility = launch_app(create_utility())
    support = launch_app(create_support())
    calls: list[str] = []
    extra = launch_app(_marker_app("extra", calls))
    store = InMemoryStore()
    allowed = await store.issue()
    other = await store.issue()
    assert await store.grant(allowed.key_id, "utility__convert_units") is True
    assert await store.grant(other.key_id, "support__search_notes") is True
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
    async with _client(gateway) as client:
        found = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}"},
            json={"query": "convert extra echo notes"},
        )
        empty = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}"},
            json={"query": ""},
        )
        malformed = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}"},
            json={"query": 1},
        )
        too_long = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}"},
            json={"query": "a" * 201},
        )
        missed = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}"},
            json={"query": "zzzz-no-such-tool"},
        )
        leaked = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {allowed.raw_key}", "X-Request-Id": "corr-1"},
            json={"query": _SECRET_QUERY},
        )
        other_view = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {other.raw_key}"},
            json={"query": "notes"},
        )
    assert found.status_code == 200
    names = [row["name"] for row in found.json()["tools"]]
    assert names == ["utility__convert_units"]
    assert "support__search_notes" not in found.text
    assert "extra__echo" not in found.text
    assert empty.status_code == 200
    assert [row["name"] for row in empty.json()["tools"]] == ["utility__convert_units"]
    assert malformed.status_code == 400 and malformed.json()["tools"] == []
    assert too_long.status_code == 400 and too_long.json()["tools"] == []
    assert missed.status_code == 200 and missed.json()["tools"] == []
    assert leaked.status_code == 200 and leaked.json()["tools"] == []
    assert "urk_" not in leaked.text
    assert "Bearer" not in leaked.text
    assert "ck_" not in leaked.text
    assert "cs_" not in leaked.text
    assert other_view.json()["tools"][0]["name"] == "support__search_notes"
    assert "utility__convert_units" not in other_view.text
    assert await _names(f"{gateway}/mcp", allowed.raw_key) == ["utility__convert_units"]
    async with open_mcp(f"{gateway}/mcp", allowed.raw_key) as mcp:
        denied = await mcp.call_tool("support__search_notes", {})
        guessed = await mcp.call_tool("extra__echo", {})
    assert denied.is_error is True and denied.content[0].text == "Permission denied."
    assert guessed.is_error is True
    assert calls == []
    blob = caplog.text + "\n".join(repr(row) for row in store.audit_log)
    assert allowed.raw_key not in blob
    assert other.raw_key not in blob
    assert _SECRET_QUERY not in blob


@pytest.mark.asyncio
async def test_search_failure_returns_no_tools(launch_app) -> None:
    utility = launch_app(create_utility())
    store = InMemoryStore()
    issued = await store.issue()
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    app = create_app(Settings(upstreams=[UpstreamConfig("utility", f"{utility}/mcp", 5)]), store=store)
    gateway = launch_app(app)

    async def boom() -> None:
        raise RuntimeError("listing down")

    app.state.registry.list_tools_result = boom
    async with _client(gateway) as client:
        response = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {issued.raw_key}"},
            json={"query": "convert"},
        )
    assert response.status_code == 503
    assert response.json()["tools"] == []
    assert "utility__convert_units" not in response.text


@pytest.mark.asyncio
async def test_collision_is_excluded_from_search_and_named_for_an_admin(launch_app) -> None:
    calls: list[str] = []
    duplicate = launch_app(_duplicate_names_app(calls))
    store = InMemoryStore()
    user = await store.create_user("ada", hash_password("password-value-ok"), "admin")
    member = await store.create_user("bea", hash_password("member-password-value"), "member")
    assert user.role == "admin" and member.role == "member"
    issued = await store.issue()
    assert await store.grant(issued.key_id, "dup__echo") is True
    app = create_app(Settings(upstreams=[UpstreamConfig("dup", f"{duplicate}/mcp", 5)]), store=store)
    gateway = launch_app(app)
    async with _client(gateway) as client:
        searched = await client.post(
            "/discovery/search",
            headers={"Authorization": f"Bearer {issued.raw_key}"},
            json={"query": "echo"},
        )
        denied = await client.get("/admin/discovery", headers={"Authorization": f"Bearer {issued.raw_key}"})
    async with _client(gateway) as member_client:
        assert (await member_client.post("/admin/login", json={"username": "bea", "password": "member-password-value"})).status_code == 200
        member_view = await member_client.get("/admin/discovery")
    async with _client(gateway) as admin_client:
        assert (await admin_client.post("/admin/login", json={"username": "ada", "password": "password-value-ok"})).status_code == 200
        summary = await admin_client.get("/admin/discovery")
        admin_search = await admin_client.post("/admin/discovery/search", json={"query": "echo"})
    assert searched.status_code == 200 and searched.json()["tools"] == []
    assert "dup__echo" not in searched.text
    assert denied.status_code == 401
    assert member_view.status_code == 403
    assert "dup__echo" not in member_view.text
    body = summary.json()
    assert summary.status_code == 200
    assert body["collisions"][0]["tool_name"] == "dup__echo"
    assert body["collisions"][0]["upstream_ids"] == ["dup"]
    assert body["collisions"][0]["resolution"] == "excluded"
    assert body["excluded"]["collision"] == 1
    assert admin_search.status_code == 200 and admin_search.json()["tools"] == []
    assert "http://" not in summary.text
    async with open_mcp(f"{gateway}/mcp", issued.raw_key) as mcp:
        conflict = await mcp.call_tool("dup__echo", {})
    assert conflict.is_error is True
    assert calls == []
    assert app.state.registry.collision_records()[0]["upstream_ids"] == ["dup"]


class _Listing:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = tools
        self.calls: list[str] = []

    async def list_tools_result(self):
        from mcp.types import ListToolsResult

        return ListToolsResult(tools=self.tools)

    async def call(self, name: str, arguments: dict) -> object:
        self.calls.append(name)
        from mcp.types import CallToolResult

        return CallToolResult(content=[], is_error=False)


@pytest.mark.asyncio
async def test_store_tool_stays_hidden_until_ready_and_pinned() -> None:
    from apps.gateway.app.audit import Auditor
    from apps.gateway.app.auth import Authenticator
    from apps.gateway.app.discovery import collect_authorized
    from apps.gateway.app.dispatch import ToolDispatcher

    memory = InMemoryStore()
    user = await memory.create_user("ada", hash_password("password-value-ok"), "admin")
    cluster = FakeCluster()
    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True, max_per_user=1)),
        cluster,
        autostart=False,
    )
    record, kind = await service.create(user.user_id, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    issued = await memory.issue_for_user(user.user_id)
    tool_name = public_tool(record.store_id, "list_orders")
    assert await memory.grant(issued.key_id, tool_name) is True
    authenticator = Authenticator(memory)
    principal = await authenticator.authenticate(issued.raw_key)
    listing = _Listing([Tool(name=tool_name, description="orders", input_schema={"type": "object"})])

    async def visible() -> list[str]:
        tools, _excluded, error = await collect_authorized(listing, authenticator, memory, principal)
        assert error == ""
        return [item.name for item in tools]

    assert await visible() == []
    cluster.ready = True
    await service.reconcile_store(record.store_id)
    assert await visible() == [tool_name]
    saved = await memory.list_upstreams()
    assert saved[0].url == mcp_url(namespace_for(record.store_id))
    await memory.save_upstream(saved[0].upstream_id, "http://10.1.2.3/mcp", True, saved[0].credential)
    assert await visible() == []
    dispatcher = ToolDispatcher(listing, authenticator, Auditor(memory), memory)
    denied = await dispatcher.execute_tool(principal, tool_name, {})
    assert denied.is_error is True
    assert denied.content[0].text == "Permission denied."
    assert listing.calls == []
    await memory.save_upstream(saved[0].upstream_id, mcp_url(namespace_for(record.store_id)), True, saved[0].credential)
    await service.request_delete(user.user_id, record.store_id)
    assert await visible() == []


@pytest.mark.asyncio
async def test_two_store_names_stay_distinct_and_a_revoked_grant_is_not_called() -> None:
    from apps.gateway.app.audit import Auditor
    from apps.gateway.app.auth import Authenticator
    from apps.gateway.app.discovery import collect_authorized
    from apps.gateway.app.dispatch import ToolDispatcher

    memory = InMemoryStore()
    user = await memory.create_user("ada", hash_password("password-value-ok"), "admin")
    cluster = FakeCluster()
    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True, max_per_user=2)),
        cluster,
        autostart=False,
    )
    left, left_kind = await service.create(user.user_id, "North Shop", "idem-north-1")
    right, right_kind = await service.create(user.user_id, "South Shop", "idem-south-1")
    assert left_kind == right_kind == "created"
    assert left is not None and right is not None
    cluster.ready = True
    await service.reconcile_store(left.store_id)
    await service.reconcile_store(right.store_id)
    left_tool = public_tool(left.store_id, "list_orders")
    right_tool = public_tool(right.store_id, "list_orders")
    assert left_tool != right_tool
    assert left_tool.endswith("__list_orders") and right_tool.endswith("__list_orders")
    issued = await memory.issue_for_user(user.user_id)
    assert await memory.grant(issued.key_id, left_tool) is True
    assert await memory.grant(issued.key_id, right_tool) is True
    authenticator = Authenticator(memory)
    principal = await authenticator.authenticate(issued.raw_key)
    listing = _Listing(
        [
            Tool(name=left_tool, description="orders", input_schema={"type": "object"}),
            Tool(name=right_tool, description="orders", input_schema={"type": "object"}),
        ]
    )
    dispatcher = ToolDispatcher(listing, authenticator, Auditor(memory), memory)

    async def visible() -> list[str]:
        tools, _excluded, error = await collect_authorized(listing, authenticator, memory, principal)
        assert error == ""
        return [item.name for item in tools]

    assert await visible() == [left_tool, right_tool]
    assert (await dispatcher.execute_tool(principal, left_tool, {})).is_error is False
    assert (await dispatcher.execute_tool(principal, right_tool, {})).is_error is False
    assert listing.calls == [left_tool, right_tool]

    assert await memory.revoke_permission(issued.key_id, left_tool) is True
    assert await visible() == [right_tool]
    denied = await dispatcher.execute_tool(principal, left_tool, {})
    assert denied.is_error is True
    assert denied.content[0].text == "Permission denied."
    assert listing.calls == [left_tool, right_tool]
    kept = await dispatcher.execute_tool(principal, right_tool, {})
    assert kept.is_error is False
    assert listing.calls == [left_tool, right_tool, right_tool]


@pytest.mark.asyncio
async def test_admin_discovery_summary_lists_dynamic_store_registrations(launch_app) -> None:
    store = InMemoryStore()
    await store.create_user("ada", hash_password("password-value-ok"), "admin")
    canonical = "0123456789abcdef"
    await store.save_upstream(f"store_{canonical}", mcp_url(namespace_for(canonical)), False, "")
    await store.save_upstream("store_fedcba9876543210", "http://10.1.2.3/mcp", False, "")
    gateway = launch_app(create_app(Settings(upstreams=[]), store=store))
    async with _client(gateway) as admin_client:
        assert (await admin_client.post("/admin/login", json={"username": "ada", "password": "password-value-ok"})).status_code == 200
        summary = await admin_client.get("/admin/discovery")
    assert summary.status_code == 200
    body = summary.json()
    assert set(body) == {
        "candidate_count",
        "advertised_count",
        "excluded",
        "collisions",
        "upstreams",
        "store_registrations",
        "last",
    }
    assert body["store_registrations"] == [
        {"upstream_id": "store_0123456789abcdef", "enabled": False, "registration": "canonical"},
        {"upstream_id": "store_fedcba9876543210", "enabled": False, "registration": "not_canonical"},
    ]
    assert "http://" not in summary.text
