"""Dashboard chat uses a mocked provider and mocked upstream calls."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from apps.gateway.app.chat_provider import ModelTurn, ProviderError, ToolCall
from apps.gateway.app.config import Settings
from apps.gateway.app.directory import SessionRow
from apps.gateway.app.main import create_app
from apps.gateway.app.passwords import hash_password
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.stores.model import public_tool
from tests.test_store_routing import _ready_store

_SENTINEL = "upstream-payload-must-not-reach-the-browser"


class ScriptProvider:
    def __init__(self, turns: list[object]) -> None:
        self.turns = list(turns)
        self.tools: list[list[str]] = []
        self.prompts: list[str] = []

    async def complete(self, *, messages: list[dict[str, str]], tools: list[dict[str, object]]) -> ModelTurn:
        self.tools.append([str(tool["name"]) for tool in tools])
        self.prompts.append(str(messages[0]["content"]) if messages else "")
        if not self.turns:
            return ModelTurn("done")
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        if item == "wait":
            await asyncio.sleep(5)
        if isinstance(item, ModelTurn):
            return item
        raise AssertionError("script exhausted")


def _tools() -> list[Tool]:
    return [
        Tool(name="woocommerce__list_orders", description="List orders", input_schema={"type": "object", "properties": {}}),
        Tool(
            name="woocommerce__create_product",
            description="Create a product",
            input_schema={"type": "object", "properties": {}},
        ),
    ]


def _install(app, calls: list[tuple[str, dict]]) -> None:
    registry = app.state.registry

    async def list_tools_result() -> ListToolsResult:
        return ListToolsResult(tools=_tools())

    async def call(name: str, arguments: dict) -> CallToolResult:
        calls.append((name, arguments))
        if name == "woocommerce__list_orders":
            return CallToolResult(content=[TextContent(type="text", text=_SENTINEL)], is_error=False)
        if arguments.get("fail") == "timeout":
            raise TimeoutError("slow")
        return CallToolResult(content=[TextContent(type="text", text="created")], is_error=False)

    registry.list_tools_result = list_tools_result
    registry.call = call


async def _member(store: InMemoryStore, grants: list[str]) -> tuple[str, str]:
    user = await store.create_user("bea", hash_password("member-password-value"), "member")
    issued = await store.issue_for_user(user.user_id)
    for name in grants:
        assert await store.grant(issued.key_id, name) is True
    return user.user_id, issued.key_id


def _client(base: str) -> httpx2.Client:
    return httpx2.Client(base_url=base, timeout=10, headers={"X-Urumi-Request": "1"})


def _replace_sessions(store: InMemoryStore, *, expires_at: datetime, revoked_at: datetime | None) -> None:
    for session_id, row in list(store._sessions.items()):
        if isinstance(row, SessionRow):
            store._sessions[session_id] = SessionRow(
                row.session_id,
                row.secret_hash,
                row.user_id,
                row.created_at,
                expires_at,
                revoked_at,
            )


def _login(client: httpx2.Client) -> None:
    response = client.post("/admin/login", json={"username": "bea", "password": "member-password-value"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_read_only_chat_hides_upstream_text_and_ungranted_tools(launch_app) -> None:
    store = InMemoryStore()
    user_id, key_id = await _member(store, ["woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider(
        [
            ModelTurn("", [ToolCall("woocommerce__list_orders", {"after": "2026-10-11T00:00:00+05:30", "before": "2026-10-12T00:00:00+05:30"})]),
            ModelTurn("One order is in range."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post(
            "/admin/chat",
            json={"messages": [{"role": "user", "content": "What arrived today?"}], "user_id": "0011223344556677"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "One order is in range."
    assert body["activity"] == [{"tool": "woocommerce__list_orders", "outcome": "success"}]
    assert _SENTINEL not in response.text
    assert calls[0][1]["after"] == "2026-10-11T00:00:00+05:30"
    assert provider.tools[0] == ["woocommerce__list_orders"]
    assert "woocommerce__list_orders" in provider.prompts[0]
    tool_row = next(row for row in store.audit_log if row.tool_name == "woocommerce__list_orders")
    chat_row = next(row for row in store.audit_log if row.tool_name == "chat")
    assert tool_row.actor == key_id
    assert tool_row.user_id == user_id
    assert chat_row.user_id == user_id
    assert chat_row.actor == key_id


@pytest.mark.asyncio
async def test_product_creation_arguments_reach_the_dispatcher(launch_app) -> None:
    store = InMemoryStore()
    user_id, key_id = await _member(store, ["woocommerce__create_product"])
    calls: list[tuple[str, dict]] = []
    arguments = {"name": "Assessment hat", "regular_price": "12.00", "status": "publish"}
    provider = ScriptProvider([ModelTurn("", [ToolCall("woocommerce__create_product", arguments)]), ModelTurn("The product was created.")])
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Publish a hat for 12."}]})
    assert response.status_code == 200
    assert calls == [("woocommerce__create_product", arguments)]
    assert response.json()["activity"][0]["outcome"] == "success"
    assert store.audit_log[0].user_id == user_id
    assert store.audit_log[0].actor == key_id


@pytest.mark.asyncio
async def test_status_permission_is_not_a_chat_tool(launch_app) -> None:
    store = InMemoryStore()
    user_id, key_id = await _member(store, ["woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider(
        [ModelTurn("", [ToolCall("gateway__read_status", {})]), ModelTurn("I could not do that.")]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Read status."}]})
    assert response.status_code == 200
    assert calls == []
    assert response.json()["activity"][0] == {"tool": "gateway__read_status", "outcome": "denied"}
    denied = next(row for row in store.audit_log if row.tool_name == "gateway__read_status")
    assert denied.outcome == "unauthorized"
    assert denied.actor == key_id
    assert denied.user_id == user_id


@pytest.mark.asyncio
async def test_chat_without_grants_explains_that_no_tools_are_available(launch_app) -> None:
    store = InMemoryStore()
    user_id, key_id = await _member(store, [])
    provider = ScriptProvider([ModelTurn("should not run")])
    base = launch_app(create_app(Settings(upstreams=[]), store=store, chat_provider=provider))
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List the latest orders."}]})
    assert response.status_code == 403
    assert response.json()["error_description"] == "No tools are granted to your API keys, so chat cannot call the store."
    assert provider.tools == []
    denied = next(row for row in store.audit_log if row.tool_name == "chat")
    assert denied.outcome == "unauthorized"
    assert denied.actor == key_id
    assert denied.user_id == user_id

    other = InMemoryStore()
    await other.create_user("cara", hash_password("member-password-value"), "member")
    provider = ScriptProvider([ModelTurn("should not run")])
    base = launch_app(create_app(Settings(upstreams=[]), store=other, chat_provider=provider))
    with _client(base) as client:
        logged_in = client.post("/admin/login", json={"username": "cara", "password": "member-password-value"})
        assert logged_in.status_code == 200
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List the latest orders."}]})
    assert response.status_code == 403
    assert response.json()["error_description"] == "This account has no API key, so chat cannot call any tool."
    assert provider.tools == []


@pytest.mark.asyncio
async def test_disabled_user_and_expired_session_are_rejected(launch_app) -> None:
    store = InMemoryStore()
    await _member(store, [])
    provider = ScriptProvider([ModelTurn("should not run")])
    base = launch_app(create_app(Settings(upstreams=[]), store=store, chat_provider=provider))
    with _client(base) as client:
        missing = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        assert missing.status_code == 401
        _login(client)
        user_id = next(iter(store._users))
        await store.set_user_disabled(user_id, True)
        disabled = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        assert disabled.status_code == 401
    with _client(base) as client:
        await store.set_user_disabled(user_id, False)
        _login(client)
        _replace_sessions(store, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1), revoked_at=None)
        expired = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        assert expired.status_code == 401
        _replace_sessions(store, expires_at=datetime.now(timezone.utc) + timedelta(hours=1), revoked_at=datetime.now(timezone.utc))
        revoked = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        assert revoked.status_code == 401
    assert provider.tools == []
    malformed = httpx2.post(
        f"{base}/admin/chat",
        headers={"X-Urumi-Request": "1"},
        content=b"{",
    )
    assert malformed.status_code == 401


@pytest.mark.asyncio
async def test_malformed_tool_call_provider_timeout_upstream_timeout_and_call_limit(launch_app) -> None:
    store = InMemoryStore()
    user_id, _key_id = await _member(store, ["woocommerce__create_product", "woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    malformed = ScriptProvider(
        [ModelTurn("", [ToolCall("woocommerce__list_orders", ["not-an-object"])]), ModelTurn("I could not use that call.")]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=malformed)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
        assert response.status_code == 200
        assert calls == []
        assert response.json()["activity"][0]["outcome"] == "error"
        rejected = client.post("/admin/chat", content=b"{")
        assert rejected.status_code == 400

    waiting = ScriptProvider(["wait"])
    app = create_app(
        Settings(upstreams=[], chat_provider_timeout_seconds=0.05),
        store=store,
        chat_provider=waiting,
    )
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        timed = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert timed.status_code == 504
    assert timed.json()["error_description"] == "The chat provider timed out."

    failing = ScriptProvider([ProviderError("hidden provider body")])
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=failing)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        failed = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert failed.status_code == 502
    assert "hidden provider body" not in failed.text

    slow = ScriptProvider(
        [ModelTurn("", [ToolCall("woocommerce__create_product", {"name": "Hat", "fail": "timeout"})]), ModelTurn("The write timed out.")]
    )
    calls.clear()
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=slow)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        upstream = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Create the hat."}]})
    assert upstream.status_code == 200
    assert upstream.json()["activity"][0]["outcome"] == "timeout"
    timeout_row = next(row for row in reversed(store.audit_log) if row.outcome == "timeout" and row.tool_name != "chat")
    assert timeout_row.user_id == user_id
    assert len(calls) == 1

    repeat = ScriptProvider([ModelTurn("", [ToolCall("woocommerce__list_orders", {})]) for _ in range(3)])
    calls.clear()
    app = create_app(Settings(upstreams=[], chat_max_tool_calls=2), store=store, chat_provider=repeat)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        limited = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Keep listing."}]})
    assert limited.status_code == 200
    assert limited.json()["message"] == "The tool-call limit was reached."
    assert len(calls) == 2
    assert len(limited.json()["activity"]) == 2


@pytest.mark.asyncio
async def test_failed_write_is_not_called_again(launch_app) -> None:
    store = InMemoryStore()
    await _member(store, ["woocommerce__create_product"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider(
        [
            ModelTurn("", [ToolCall("woocommerce__create_product", {"name": "Hat", "fail": "timeout"})]),
            ModelTurn("", [ToolCall("woocommerce__create_product", {"name": "Hat"})]),
            ModelTurn("Stopped."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Create it."}]})
    assert response.status_code == 200
    assert len(calls) == 1
    assert [item["outcome"] for item in response.json()["activity"]] == ["timeout", "error"]


@pytest.mark.asyncio
async def test_one_prompt_calls_two_upstreams_and_a_disabled_tool_stays_hidden(launch_app) -> None:
    """One chat turn executes two upstreams, then a disabled upstream disappears."""
    store = InMemoryStore()
    user_id, key_id = await _member(store, ["woocommerce__list_orders", "support__search_notes"])
    calls: list[tuple[str, dict]] = []
    offered: list[list[str]] = []
    visible = ["woocommerce__list_orders", "support__search_notes"]

    class TwoUpstream(ScriptProvider):
        async def complete(self, *, messages, tools):
            offered.append([str(tool["name"]) for tool in tools])
            return await super().complete(messages=messages, tools=tools)

    provider = TwoUpstream(
        [
            ModelTurn(
                "",
                [
                    ToolCall("woocommerce__list_orders", {"after": "2026-10-14T00:00:00+00:00"}),
                    ToolCall("support__search_notes", {"query": "shipping"}),
                ],
            ),
            ModelTurn("Order 14 is processing. Shipping takes 3 business days."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    registry = app.state.registry

    async def list_tools_result() -> ListToolsResult:
        tools = [
            Tool(name="woocommerce__list_orders", description="List orders", input_schema={"type": "object", "properties": {}}),
            Tool(name="support__search_notes", description="Search notes", input_schema={"type": "object", "properties": {}}),
        ]
        return ListToolsResult(tools=[tool for tool in tools if tool.name in visible])

    async def call(name: str, arguments: dict) -> CallToolResult:
        calls.append((name, arguments))
        if name not in visible:
            return CallToolResult(content=[TextContent(type="text", text=f"Upstream {name.split('__')[0]} is disabled.")], is_error=True)
        if name == "woocommerce__list_orders":
            return CallToolResult(content=[TextContent(type="text", text="id=14 status=processing")], is_error=False)
        return CallToolResult(content=[TextContent(type="text", text="Shipping: Standard shipping takes 3 business days.")], is_error=False)

    registry.list_tools_result = list_tools_result
    registry.call = call
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Summarize today's orders and the shipping note."}]})
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Order 14 is processing. Shipping takes 3 business days."
    assert [item["tool"] for item in body["activity"]] == ["woocommerce__list_orders", "support__search_notes"]
    assert [item["outcome"] for item in body["activity"]] == ["success", "success"]
    assert [name for name, _arguments in calls] == ["woocommerce__list_orders", "support__search_notes"]
    tool_rows = [row for row in store.audit_log if row.tool_name in {"woocommerce__list_orders", "support__search_notes"}]
    assert {row.tool_name for row in tool_rows} == {"woocommerce__list_orders", "support__search_notes"}
    assert {row.user_id for row in tool_rows} == {user_id}
    assert {row.actor for row in tool_rows} == {key_id}
    assert {row.upstream_id for row in tool_rows} == {"woocommerce", "support"}

    visible.remove("support__search_notes")
    provider.turns.append(ModelTurn("", [ToolCall("support__search_notes", {"query": "shipping"})]))
    provider.turns.append(ModelTurn("Only the store answered."))
    calls.clear()
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Check shipping again."}]})
    assert response.status_code == 200
    assert offered[-1] == ["woocommerce__list_orders"]
    assert calls == [("support__search_notes", {"query": "shipping"})]
    assert response.json()["activity"][0] == {"tool": "support__search_notes", "outcome": "error"}


@pytest.mark.asyncio
async def test_execution_uses_the_key_that_holds_the_grant(launch_app) -> None:
    store = InMemoryStore()
    user = await store.create_user("bea", hash_password("member-password-value"), "member")
    other = await store.issue_for_user(user.user_id)
    granted = await store.issue_for_user(user.user_id)
    assert await store.grant(granted.key_id, "woocommerce__list_orders") is True
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider(
        [ModelTurn("", [ToolCall("woocommerce__list_orders", {})]), ModelTurn("Listed.")]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert response.status_code == 200
    assert calls[0][0] == "woocommerce__list_orders"
    row = next(item for item in store.audit_log if item.tool_name == "woocommerce__list_orders")
    assert row.actor == granted.key_id
    assert row.actor != other.key_id
    assert row.user_id == user.user_id


@pytest.mark.asyncio
async def test_provider_failure_before_tool_activity_stays_a_gateway_error(launch_app) -> None:
    store = InMemoryStore()
    await _member(store, ["woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider([ProviderError("hidden provider body")])
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert response.status_code == 502
    body = response.json()
    assert body["error_description"] == "The chat provider could not answer."
    assert body["activity"] == []
    assert "partial" not in body
    assert "hidden provider body" not in response.text
    assert calls == []
    chat_row = next(row for row in store.audit_log if row.tool_name == "chat")
    assert chat_row.outcome == "error"


@pytest.mark.asyncio
async def test_provider_failure_after_tool_activity_is_a_partial_result(launch_app) -> None:
    store = InMemoryStore()
    await _member(store, ["woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider(
        [ModelTurn("", [ToolCall("woocommerce__list_orders", {})]), ProviderError("hidden provider body")]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is True
    assert body["message"] == "Partial result: the chat provider stopped after a tool ran. This is not a completed answer."
    assert body["activity"] == [{"tool": "woocommerce__list_orders", "outcome": "success"}]
    assert "hidden provider body" not in response.text
    assert calls == [("woocommerce__list_orders", {})]
    chat_row = next(row for row in store.audit_log if row.tool_name == "chat")
    assert chat_row.outcome == "error"


@pytest.mark.asyncio
async def test_timeout_after_tool_activity_keeps_the_timeout_response(launch_app) -> None:
    store = InMemoryStore()
    await _member(store, ["woocommerce__list_orders"])
    calls: list[tuple[str, dict]] = []
    provider = ScriptProvider([ModelTurn("", [ToolCall("woocommerce__list_orders", {})]), "wait"])
    app = create_app(Settings(upstreams=[], chat_provider_timeout_seconds=0.05), store=store, chat_provider=provider)
    _install(app, calls)
    base = launch_app(app)
    with _client(base) as client:
        _login(client)
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "List orders."}]})
    assert response.status_code == 504
    body = response.json()
    assert body["error_description"] == "The chat provider timed out."
    assert body["activity"] == []
    assert "partial" not in body
    assert calls == [("woocommerce__list_orders", {})]
    chat_row = next(row for row in store.audit_log if row.tool_name == "chat")
    assert chat_row.outcome == "timeout"


@pytest.mark.asyncio
async def test_chat_rejects_another_users_store_tool_when_the_key_has_a_grant(launch_app) -> None:
    store = InMemoryStore()
    owner = await store.create_user("ada", hash_password("admin-password-value"), "admin")
    other = await store.create_user("bea", hash_password("member-password-value"), "member")
    store_id = await _ready_store(store, owner.user_id, "North Shop", "idem-north-1")
    tool_name = public_tool(store_id, "list_orders")
    other_key = await store.issue_for_user(other.user_id)
    assert await store.grant(other_key.key_id, tool_name) is True
    calls: list[str] = []

    async def list_tools_result() -> ListToolsResult:
        return ListToolsResult(
            tools=[Tool(name=tool_name, description="orders", input_schema={"type": "object", "properties": {}})]
        )

    async def call(name: str, arguments: dict) -> CallToolResult:
        calls.append(name)
        return CallToolResult(content=[TextContent(type="text", text=_SENTINEL)], is_error=False)

    provider = ScriptProvider(
        [
            ModelTurn("", [ToolCall(tool_name, {})]),
            ModelTurn("No foreign orders were read."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    registry = app.state.registry
    registry.list_tools_result = list_tools_result
    registry.call = call
    base = launch_app(app)
    with _client(base) as client:
        signed_in = client.post(
            "/admin/login",
            json={"username": "bea", "password": "member-password-value"},
        )
        assert signed_in.status_code == 200
        response = client.post(
            "/admin/chat",
            json={
                "messages": [{"role": "user", "content": "List the other store's orders."}],
                "user_id": owner.user_id,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "No foreign orders were read."
    assert body["activity"] == [{"tool": tool_name, "outcome": "denied"}]
    assert provider.tools == [[], []]
    assert calls == []
    assert registry.call is call
    assert _SENTINEL not in response.text
    assert owner.user_id not in response.text
    assert await store.list_permissions(other_key.key_id) == [tool_name]
    denied = next(row for row in store.audit_log if row.tool_name == tool_name)
    assert denied.outcome == "unauthorized"
    assert denied.actor == other_key.key_id
    assert denied.user_id == other.user_id


@pytest.mark.asyncio
async def test_one_request_calls_both_store_list_orders_tools(launch_app) -> None:
    store = InMemoryStore()
    owner = await store.create_user("ada", hash_password("admin-password-value"), "admin")
    left = await _ready_store(store, owner.user_id, "Store A", "idem-store-a")
    right = await _ready_store(store, owner.user_id, "Store B", "idem-store-b")
    issued = await store.issue_for_user(owner.user_id)
    left_tool = public_tool(left, "list_orders")
    right_tool = public_tool(right, "list_orders")
    assert await store.grant(issued.key_id, left_tool) is True
    assert await store.grant(issued.key_id, right_tool) is True
    calls: list[tuple[str, dict]] = []

    async def list_tools_result() -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(name=left_tool, description="orders for store A", input_schema={"type": "object", "properties": {}}),
                Tool(name=right_tool, description="orders for store B", input_schema={"type": "object", "properties": {}}),
                Tool(name="woocommerce__list_orders", description="demo orders", input_schema={"type": "object", "properties": {}}),
            ]
        )

    async def call(name: str, arguments: dict) -> CallToolResult:
        calls.append((name, arguments))
        label = "Store A" if name == left_tool else "Store B"
        return CallToolResult(content=[TextContent(type="text", text=f"{label} order 7")], is_error=False)

    provider = ScriptProvider(
        [
            ModelTurn(
                "",
                [
                    ToolCall(left_tool, {"after": "2026-10-24T00:00:00+05:30", "before": "2026-10-25T00:00:00+05:30"}),
                    ToolCall(right_tool, {"after": "2026-10-24T00:00:00+05:30", "before": "2026-10-25T00:00:00+05:30"}),
                ],
            ),
            ModelTurn("Store A has order 7. Store B has order 7."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    registry = app.state.registry
    registry.list_tools_result = list_tools_result
    registry.call = call
    base = launch_app(app)
    with _client(base) as client:
        signed_in = client.post("/admin/login", json={"username": "ada", "password": "admin-password-value"})
        assert signed_in.status_code == 200
        response = client.post(
            "/admin/chat",
            json={"messages": [{"role": "user", "content": "Compare today's orders across both stores."}]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Store A has order 7. Store B has order 7."
    assert body["activity"] == [
        {"tool": left_tool, "outcome": "success"},
        {"tool": right_tool, "outcome": "success"},
    ]
    assert [name for name, _arguments in calls] == [left_tool, right_tool]
    assert "woocommerce__list_orders" not in provider.prompts[0]
    assert left_tool in provider.prompts[0] and right_tool in provider.prompts[0]
    assert "Do not rename" in provider.prompts[0]
    assert provider.tools[0] == [left_tool, right_tool]
    rows = [row for row in store.audit_log if row.tool_name in {left_tool, right_tool}]
    assert {row.tool_name for row in rows} == {left_tool, right_tool}
    assert {row.outcome for row in rows} == {"success"}
    assert {row.user_id for row in rows} == {owner.user_id}
    assert {row.actor for row in rows} == {issued.key_id}
    assert {row.upstream_id for row in rows} == {left_tool.split("__", 1)[0], right_tool.split("__", 1)[0]}
    assert all(isinstance(row.duration_ms, int) for row in rows)


async def _two_store_chat(store: InMemoryStore) -> tuple[str, str, str]:
    owner = await store.create_user("ada", hash_password("admin-password-value"), "admin")
    left = await _ready_store(store, owner.user_id, "Store B", "idem-store-b")
    right = await _ready_store(store, owner.user_id, "Store C", "idem-store-c")
    issued = await store.issue_for_user(owner.user_id)
    left_tool = public_tool(left, "list_orders")
    right_tool = public_tool(right, "list_orders")
    assert await store.grant(issued.key_id, left_tool) is True
    assert await store.grant(issued.key_id, right_tool) is True
    return owner.user_id, left_tool, right_tool


def _two_store_registry(app, left_tool: str, right_tool: str, calls: list[tuple[str, dict]], left_failure) -> None:
    registry = app.state.registry

    async def list_tools_result() -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(name=left_tool, description="orders for store B", input_schema={"type": "object", "properties": {}}),
                Tool(name=right_tool, description="orders for store C", input_schema={"type": "object", "properties": {}}),
            ]
        )

    async def call(name: str, arguments: dict) -> CallToolResult:
        calls.append((name, arguments))
        if name == left_tool:
            return left_failure()
        return CallToolResult(content=[TextContent(type="text", text=f"{_SENTINEL} id=10")], is_error=False)

    registry.list_tools_result = list_tools_result
    registry.call = call


_DAY = {"after": "2026-10-25T00:00:00+00:00", "before": "2026-10-26T00:00:00+00:00"}


@pytest.mark.asyncio
async def test_failing_store_read_does_not_use_up_the_budget_for_the_other_store(launch_app) -> None:
    store = InMemoryStore()
    _user_id, left_tool, right_tool = await _two_store_chat(store)
    calls: list[tuple[str, dict]] = []
    upstream_error = f"Error executing tool list_orders: WordPress is not installed. {_SENTINEL}"

    def left_failure() -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=upstream_error)], is_error=True)

    provider = ScriptProvider(
        [
            ModelTurn("", [ToolCall(left_tool, _DAY)]),
            ModelTurn("", [ToolCall(left_tool, _DAY)]),
            ModelTurn("", [ToolCall(left_tool, {**_DAY, "per_page": 100})]),
            ModelTurn("", [ToolCall(left_tool, {**_DAY, "per_page": 50})]),
            ModelTurn("", [ToolCall(right_tool, _DAY)]),
            ModelTurn("Store B failed. Store C has order 10."),
        ]
    )
    app = create_app(Settings(upstreams=[], chat_max_tool_calls=4), store=store, chat_provider=provider)
    _two_store_registry(app, left_tool, right_tool, calls, left_failure)
    base = launch_app(app)
    with _client(base) as client:
        assert client.post("/admin/login", json={"username": "ada", "password": "admin-password-value"}).status_code == 200
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Compare Oct 25 orders."}]})
    assert response.status_code == 200
    body = response.json()
    assert [name for name, _arguments in calls] == [left_tool, left_tool, right_tool]
    assert calls[1][1] == {**_DAY, "per_page": 100}
    assert [item["outcome"] for item in body["activity"]] == ["error", "skipped", "error", "skipped", "success"]
    assert body["message"].startswith(f"Not every tool call succeeded, so this answer may be incomplete. Failed: {left_tool}.")
    assert body["message"].endswith("Store B failed. Store C has order 10.")
    assert right_tool not in body["message"]
    assert _SENTINEL not in response.text
    assert "partial" not in body
    assert "do not repeat the same call" in provider.prompts[0]
    audited = [row.tool_name for row in store.audit_log if row.tool_name in {left_tool, right_tool}]
    assert audited == [left_tool, left_tool, right_tool]


@pytest.mark.asyncio
async def test_timed_out_store_read_is_not_retried_and_a_later_success_clears_the_note(launch_app) -> None:
    store = InMemoryStore()
    _user_id, left_tool, right_tool = await _two_store_chat(store)
    calls: list[tuple[str, dict]] = []

    def left_failure() -> CallToolResult:
        raise TimeoutError("slow store")

    provider = ScriptProvider(
        [
            ModelTurn("", [ToolCall(left_tool, _DAY), ToolCall(right_tool, {"per_page": 0})]),
            ModelTurn("", [ToolCall(left_tool, {**_DAY, "per_page": 5}), ToolCall(right_tool, _DAY)]),
            ModelTurn("Store B timed out. Store C has order 10."),
        ]
    )
    app = create_app(Settings(upstreams=[]), store=store, chat_provider=provider)
    _two_store_registry(app, left_tool, right_tool, calls, left_failure)
    registry = app.state.registry
    passing = registry.call

    async def call(name: str, arguments: dict) -> CallToolResult:
        if name == right_tool and arguments.get("per_page") == 0:
            calls.append((name, arguments))
            return CallToolResult(content=[TextContent(type="text", text="per_page must be an integer from 1 to 100.")], is_error=True)
        return await passing(name, arguments)

    registry.call = call
    base = launch_app(app)
    with _client(base) as client:
        assert client.post("/admin/login", json={"username": "ada", "password": "admin-password-value"}).status_code == 200
        response = client.post("/admin/chat", json={"messages": [{"role": "user", "content": "Compare Oct 25 orders."}]})
    assert response.status_code == 200
    body = response.json()
    assert [name for name, _arguments in calls] == [left_tool, right_tool, right_tool]
    assert [item["outcome"] for item in body["activity"]] == ["timeout", "error", "skipped", "success"]
    assert body["message"].startswith(f"Not every tool call succeeded, so this answer may be incomplete. Failed: {left_tool}.")
