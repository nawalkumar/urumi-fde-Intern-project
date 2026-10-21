"""Store tool routing stays on the shared dispatcher and exact grants."""

import pytest
from mcp.types import CallToolResult

from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.config import Settings, StoreProvisionerSettings
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.stores.model import public_tool
from apps.gateway.app.stores.service import StoreService
from tests.store_cluster import FakeCluster


class Recording:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append(name)
        return CallToolResult(content=[], is_error=False)


async def _ready_store(memory: InMemoryStore, owner: str, name: str, key: str) -> str:
    cluster = FakeCluster()
    cluster.ready = True
    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True, max_per_user=2)),
        cluster,
        autostart=False,
    )
    record, kind = await service.create(owner, name, key)
    assert kind == "created" and record is not None
    await service.reconcile_store(record.store_id)
    return record.store_id


@pytest.mark.asyncio
async def test_deleting_store_is_not_called_and_another_store_cannot_be_selected() -> None:
    memory = InMemoryStore()
    from apps.gateway.app.passwords import hash_password

    user = await memory.create_user("ada", hash_password("password-value-ok"), "admin")
    left = await _ready_store(memory, user.user_id, "North Shop", "idem-north-1")
    right = await _ready_store(memory, user.user_id, "South Shop", "idem-south-1")
    issued = await memory.issue()
    left_tool = public_tool(left, "list_orders")
    right_tool = public_tool(right, "list_orders")
    assert left_tool != right_tool
    assert await memory.grant(issued.key_id, left_tool) is True
    assert await memory.grant(issued.key_id, right_tool) is True
    assert await memory.grant(issued.key_id, "utility__convert_units") is True
    authenticator = Authenticator(memory)
    principal = await authenticator.authenticate(issued.raw_key)
    registry = Recording()
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(memory), memory)
    both = await dispatcher.execute_tool(principal, left_tool, {})
    other = await dispatcher.execute_tool(principal, right_tool, {})
    assert both.is_error is False and other.is_error is False
    assert registry.calls == [left_tool, right_tool]

    from apps.gateway.app.stores.repository import get_store, update_store

    row = await get_store(memory, left)
    assert row is not None
    row.state = "deleting"
    await update_store(memory, row)
    denied = await dispatcher.execute_tool(principal, left_tool, {})
    assert denied.is_error is True
    assert denied.content[0].text == "Permission denied."
    assert registry.calls == [left_tool, right_tool]
    utility = await dispatcher.execute_tool(principal, "utility__convert_units", {})
    assert utility.is_error is False
    assert registry.calls[-1] == "utility__convert_units"
    kept = await dispatcher.execute_tool(principal, right_tool, {})
    assert kept.is_error is False
    assert registry.calls[-1] == right_tool
    assert left_tool not in registry.calls[2:]


@pytest.mark.asyncio
async def test_static_woocommerce_grant_is_unchanged_by_store_routing() -> None:
    memory = InMemoryStore()
    issued = await memory.issue()
    assert await memory.grant(issued.key_id, "woocommerce__list_orders") is True
    authenticator = Authenticator(memory)
    principal = await authenticator.authenticate(issued.raw_key)
    registry = Recording()
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(memory), memory)
    result = await dispatcher.execute_tool(principal, "woocommerce__list_orders", {})
    assert result.is_error is False
    assert registry.calls == ["woocommerce__list_orders"]
    assert await memory.list_permissions(issued.key_id) == ["woocommerce__list_orders"]
