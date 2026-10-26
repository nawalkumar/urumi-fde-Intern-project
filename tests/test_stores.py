"""Store lifecycle against the in-memory gateway store and a fake cluster."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx2
import pytest

from apps.gateway.app.config import Settings, StoreProvisionerSettings
from apps.gateway.app.main import create_app
from apps.gateway.app.passwords import hash_password
from apps.gateway.app.registry import Registry
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.stores.manifests import SecretMaterial, mcp_url
from apps.gateway.app.stores.model import OwnershipError, public_store, public_tool
from apps.gateway.app.stores.routing import store_call_allowed
from apps.gateway.app.stores.repository import get_store, list_operations, list_ready_drift, update_store
from apps.gateway.app.stores.service import StoreService
from tests.store_cluster import FakeCluster

SENTINEL = "db-secret-sentinel-value"


def _settings(**kwargs: object) -> Settings:
    values = {
        "enabled": True,
        "max_per_user": 2,
        "timeout_seconds": 60,
        "concurrency": 2,
        "max_attempts": 3,
    }
    values.update(kwargs)
    return Settings(upstreams=[], stores=StoreProvisionerSettings(**values))  # type: ignore[arg-type]


def _service(
    store: InMemoryStore,
    cluster: FakeCluster | None,
    settings: Settings | None = None,
    *,
    autostart: bool = False,
    now=None,
    sleep=None,
    delete_poll_seconds: float = 0.2,
    delete_wait_seconds: float = 1.0,
) -> StoreService:
    return StoreService(
        store,
        Registry([]),
        settings or _settings(),
        cluster,
        autostart=autostart,
        now=now,
        sleep=sleep,
        delete_poll_seconds=delete_poll_seconds,
        delete_wait_seconds=delete_wait_seconds,
    )


def _clock() -> tuple[object, object, dict[str, datetime]]:
    current = {"at": datetime(2026, 10, 22, tzinfo=timezone.utc)}

    def now() -> datetime:
        return current["at"]

    async def sleep(seconds: float) -> None:
        current["at"] = current["at"] + timedelta(seconds=seconds)

    return now, sleep, current


async def _owner(store: InMemoryStore, username: str = "ada") -> str:
    user = await store.create_user(username, hash_password("password-value-ok"), "admin")
    return user.user_id


def _material() -> SecretMaterial:
    return SecretMaterial(
        mariadb_root="root-secret-sentinel-value",
        mariadb_password=SENTINEL,
        admin_password="admin-secret-sentinel-value",
        consumer_key="ck_sentinelkeyvalue00000000000000000000",
        consumer_secret="cs_sentinelsecretvalue000000000000000000",
    )


@pytest.mark.asyncio
async def test_create_persists_provisioning_before_any_resource() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    seen: dict[str, str] = {}

    async def on_apply(resource: dict) -> None:
        row = await get_store(store, resource["metadata"]["labels"]["urumi.dev/store-id"])
        assert row is not None
        seen["state"] = row.state

    cluster.on_apply = on_apply
    service = _service(store, cluster)
    record, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created"
    assert record is not None
    assert record.state == "provisioning"
    assert cluster.items == {}
    await service.reconcile_store(record.store_id)
    assert seen["state"] == "provisioning"
    saved = await get_store(store, record.store_id)
    assert saved is not None
    assert saved.state == "provisioning"
    assert (None, "Namespace", saved.namespace) in cluster.items


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_create_another_namespace() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    first, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created" and first is not None
    replay, replay_kind = await service.create(owner, "North Shop", "idem-north-1")
    assert replay_kind == "replay"
    assert replay is not None and replay.store_id == first.store_id
    conflict, conflict_kind = await service.create(owner, "Other Shop", "idem-north-1")
    assert conflict_kind == "conflict"
    assert conflict is not None and conflict.store_id == first.store_id
    await service.reconcile_store(first.store_id)
    namespaces = [key for key in cluster.items if key[1] == "Namespace"]
    assert namespaces == [(None, "Namespace", first.namespace)]


@pytest.mark.asyncio
async def test_concurrent_creates_stop_at_the_per_user_limit() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    service = _service(store, FakeCluster(), _settings(max_per_user=2))

    async def one(index: int) -> str:
        _record, kind = await service.create(owner, f"Shop {index}", f"idem-key-{index}")
        return kind

    kinds = await asyncio.gather(*(one(index) for index in range(5)))
    assert kinds.count("created") == 2
    assert kinds.count("limit") == 3
    assert len(store._stores) == 2


@pytest.mark.asyncio
async def test_another_user_cannot_read_or_delete_a_store() -> None:
    store = InMemoryStore()
    owner = await _owner(store, "ada")
    other = await _owner(store, "bea")
    service = _service(store, FakeCluster())
    record, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    hidden, code = await service.get_owned(other, record.store_id)
    assert hidden is None and code == "not_found"
    denied, delete_code = await service.request_delete(other, record.store_id)
    assert denied is None and delete_code == "not_found"
    kept = await get_store(store, record.store_id)
    assert kept is not None and kept.state == "provisioning"
    assert store.audit_log[-1].tool_name == "admin:store_denied"
    assert store.audit_log[-1].outcome == "unauthorized"


@pytest.mark.asyncio
async def test_ready_registers_one_upstream_without_changing_grants() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    issued = await store.issue()
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "ready"
    assert saved.url == f"http://{saved.store_id}.stores.local"
    rows = await store.list_upstreams()
    assert [row.upstream_id for row in rows] == [saved.upstream_id]
    assert rows[0].url == f"http://woo-mcp.{saved.namespace}.svc.cluster.local:8000/mcp"
    assert rows[0].credential == ""
    assert await store.list_permissions(issued.key_id) == ["utility__convert_units"]
    view = public_store(saved)
    assert view["state"] == "Ready"
    assert f"{saved.upstream_id}__list_orders" in view["tool_names"]
    blob = json.dumps(view)
    assert SENTINEL not in blob
    assert "ck_sentinel" not in blob


@pytest.mark.asyncio
async def test_deployment_failure_cleans_only_that_store() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.fail_deployments = True
    cluster.items[(None, "Namespace", "default")] = {
        "kind": "Namespace",
        "metadata": {"name": "default", "labels": {}},
    }
    service = _service(store, cluster, _settings(max_attempts=1))
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None
    assert saved.state == "failed"
    assert saved.error_message == "Provisioning stopped after the retry limit."
    assert (None, "Namespace", saved.namespace) not in cluster.items
    assert (None, "Namespace", "default") in cluster.items
    assert await store.list_upstreams() == []


@pytest.mark.asyncio
async def test_timeout_records_a_safe_reason_and_deletes_the_owned_namespace() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    service = _service(store, cluster)
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    assert (None, "Namespace", record.namespace) in cluster.items
    current = await get_store(store, record.store_id)
    assert current is not None
    current.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await update_store(store, current)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None
    assert saved.state == "failed"
    assert saved.error_code == "timeout"
    assert "secret" not in saved.error_message.casefold()
    assert (None, "Namespace", record.namespace) not in cluster.items


@pytest.mark.asyncio
async def test_restart_continues_the_same_namespace() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    first = _service(store, cluster)
    record, _kind = await first.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await first.reconcile_store(record.store_id)
    assert (await get_store(store, record.store_id)).state == "provisioning"  # type: ignore[union-attr]
    cluster.ready = True
    second = _service(store, cluster)
    await second.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "ready"
    namespaces = [key for key in cluster.items if key[1] == "Namespace"]
    assert namespaces == [(None, "Namespace", record.namespace)]


@pytest.mark.asyncio
async def test_deletion_is_idempotent_and_drops_registration_immediately(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    secret = cluster.items[(record.namespace, "Secret", "store-db")]
    assert SENTINEL not in json.dumps(secret["metadata"])
    deleting, code = await service.request_delete(owner, record.store_id)
    assert code == "deleting" and deleting is not None
    assert await store.list_upstreams() == []
    cluster.items.pop((record.namespace, "Secret", "woocommerce-credentials"), None)
    await service.reconcile_store(record.store_id)
    assert (await get_store(store, record.store_id)).state == "deleted"  # type: ignore[union-attr]
    again, again_code = await service.request_delete(owner, record.store_id)
    assert again_code == "deleted" and again is not None and again.state == "deleted"
    await service.reconcile_store(record.store_id)
    assert (None, "Namespace", record.namespace) not in cluster.items
    assert SENTINEL not in caplog.text
    assert "ck_sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_unowned_namespace_is_not_deleted() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    service = _service(store, cluster)
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    cluster.items[(None, "Namespace", record.namespace)] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": record.namespace,
            "labels": {"urumi.dev/store-id": "ffffffffffffffff"},
        },
    }
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "failed"
    assert saved.error_code == "ownership"
    assert cluster.delete_requests == []
    assert cluster.items[(None, "Namespace", record.namespace)]["metadata"]["labels"]["urumi.dev/store-id"] == "ffffffffffffffff"


@pytest.mark.asyncio
async def test_two_stores_provision_concurrently_with_distinct_names() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster, _settings(max_per_user=2, concurrency=2))
    first, _kind = await service.create(owner, "North Shop", "idem-north-1")
    second, _kind = await service.create(owner, "South Shop", "idem-south-1")
    assert first is not None and second is not None
    await asyncio.gather(service.reconcile_store(first.store_id), service.reconcile_store(second.store_id))
    left = await get_store(store, first.store_id)
    right = await get_store(store, second.store_id)
    assert left is not None and right is not None
    assert left.state == right.state == "ready"
    assert left.namespace != right.namespace
    assert left.upstream_id != right.upstream_id
    urls = sorted(row.url for row in await store.list_upstreams())
    assert urls[0] != urls[1]


@pytest.mark.asyncio
async def test_missing_namespace_removes_a_stale_registration() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    await cluster.delete_namespace(record.namespace, record.store_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "failed"
    assert saved.error_code == "namespace_missing"
    assert await store.list_upstreams() == []


@pytest.mark.asyncio
async def test_missing_registration_is_restored_for_a_ready_store() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    await store.delete_upstream(record.upstream_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "ready"
    assert [row.upstream_id for row in await store.list_upstreams()] == [record.upstream_id]


@pytest.mark.asyncio
async def test_retry_after_failure_reaches_ready() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.fail_deployments = True
    service = _service(store, cluster, _settings(max_attempts=1))
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    assert (await get_store(store, record.store_id)).state == "failed"  # type: ignore[union-attr]
    cluster.fail_deployments = False
    cluster.ready = True
    retried, code = await service.request_retry(owner, record.store_id)
    assert code == "provisioning" and retried is not None
    await service.reconcile_store(record.store_id)
    assert (await get_store(store, record.store_id)).state == "ready"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_store_api_scopes_owners_and_hides_secrets(launch_app) -> None:
    memory = InMemoryStore()
    ada = await memory.create_user("ada", hash_password("admin-password-value"), "admin")
    await memory.create_user("bea", hash_password("member-password-value"), "member")
    cluster = FakeCluster()
    app = create_app(
        _settings(),
        store=memory,
        cluster_client=cluster,
        start_store_worker=False,
        store_autostart=False,
    )
    app.state.store_service.material_factory = _material
    base = launch_app(app)
    with httpx2.Client(base_url=base, timeout=10, headers={"X-Urumi-Request": "1"}) as client:
        assert client.get("/admin/stores").status_code == 401
        assert client.post("/admin/login", json={"username": "ada", "password": "admin-password-value"}).status_code == 200
        rejected = client.post("/admin/stores", json={"name": "../default", "idempotency_key": "idem-bad-name"})
        assert rejected.status_code == 400
        created = client.post("/admin/stores", json={"name": "North Shop", "idempotency_key": "idem-north-1"})
        assert created.status_code == 202
        body = created.json()
        assert body["state"] == "Provisioning"
        assert body["url"] is None
        assert SENTINEL not in created.text
        replay = client.post("/admin/stores", json={"name": "North Shop", "idempotency_key": "idem-north-1"})
        assert replay.status_code == 200
        assert replay.json()["store_id"] == body["store_id"]
        client.post("/admin/logout")
        assert client.post("/admin/login", json={"username": "bea", "password": "member-password-value"}).status_code == 200
        assert client.get(f"/admin/stores/{body['store_id']}").status_code == 404
        assert client.post(f"/admin/stores/{body['store_id']}/delete").status_code == 404
        own = client.get("/admin/stores")
        assert own.status_code == 200
        assert own.json()["stores"] == []
    assert cluster.items == {}
    assert ada.user_id


@pytest.mark.asyncio
async def test_existing_secrets_do_not_skip_deployment_recovery() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.fail_deployments = True
    service = _service(store, cluster)
    created = {"n": 0}

    def factory() -> SecretMaterial:
        created["n"] += 1
        return _material()

    service.material_factory = factory
    record, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    await service.reconcile_store(record.store_id)
    secret_key = (record.namespace, "Secret", "store-db")
    saved_secret = cluster.items[secret_key]
    assert created["n"] == 1
    assert [name for (_ns, kind_name, name) in cluster.items if kind_name == "Deployment"] == []
    cluster.fail_deployments = False
    cluster.ready = True
    await service.reconcile_store(record.store_id)
    recovered = await get_store(store, record.store_id)
    assert recovered is not None
    assert recovered.store_id == record.store_id
    assert recovered.namespace == record.namespace
    assert recovered.state == "ready"
    assert (record.namespace, "Deployment", "wordpress") in cluster.items
    assert cluster.items[secret_key] is saved_secret
    assert created["n"] == 1


@pytest.mark.asyncio
async def test_gateway_process_does_not_apply_cluster_resources() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    service = _service(store, cluster)
    service.cluster_reconciler = False
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    assert cluster.items == {}
    assert (await get_store(store, record.store_id)).state == "provisioning"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_ready_rows_stay_out_of_the_provisioning_queue() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    service = _service(store, FakeCluster(), _settings(max_per_user=3))
    ready, _kind = await service.create(owner, "North Shop", "idem-north-1")
    waiting, _kind = await service.create(owner, "South Shop", "idem-south-1")
    assert ready is not None and waiting is not None
    ready.state = "ready"
    await update_store(store, ready)
    states = [row.state for row in await list_operations(store)]
    assert states == ["provisioning"]
    drift = await list_ready_drift(store, limit=1)
    assert [row.store_id for row in drift] == [ready.store_id]


@pytest.mark.asyncio
async def test_delete_revokes_only_that_stores_grants() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster)
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    issued = await store.issue()
    own_tool = public_tool(record.store_id, "list_orders")
    other_tool = public_tool("fedcba9876543210", "list_orders")
    assert await store.grant(issued.key_id, own_tool)
    assert await store.grant(issued.key_id, other_tool)
    assert await store.grant(issued.key_id, "utility__convert_units")
    await service.request_delete(owner, record.store_id)
    assert await store.permits(issued.key_id, own_tool) is False
    assert await store.permits(issued.key_id, other_tool) is True
    assert await store.permits(issued.key_id, "utility__convert_units") is True


@pytest.mark.asyncio
async def test_ready_store_url_is_pinned_and_admin_cannot_retarget_it(launch_app) -> None:
    memory = InMemoryStore()
    await memory.create_user("ada", hash_password("admin-password-value"), "admin")
    cluster = FakeCluster()
    cluster.ready = True
    service = StoreService(memory, Registry([]), _settings(), cluster, autostart=False)
    service.material_factory = _material
    owner = (await memory.get_user_by_username("ada")).user_id  # type: ignore[union-attr]
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    canonical = mcp_url(record.namespace)
    await memory.save_upstream(record.upstream_id, "http://10.1.2.3/mcp", True, "")
    await service.reconcile_store(record.store_id)
    saved = next(row for row in await memory.list_upstreams() if row.upstream_id == record.upstream_id)
    assert saved.url == canonical
    app = create_app(_settings(), store=memory, start_store_worker=False, store_autostart=False)
    base = launch_app(app)
    with httpx2.Client(base_url=base, timeout=10, headers={"X-Urumi-Request": "1"}) as client:
        assert client.post("/admin/login", json={"username": "ada", "password": "admin-password-value"}).status_code == 200
        created = client.post(
            "/admin/upstreams",
            json={"id": "store_0123456789abcdef", "url": "http://10.1.2.3/mcp", "enabled": True},
        )
        assert created.status_code == 400
        changed = client.post(
            f"/admin/upstreams/{record.upstream_id}",
            json={"enabled": True, "url": "http://10.1.2.3/mcp"},
        )
        assert changed.status_code == 400
        removed = client.post(f"/admin/upstreams/{record.upstream_id}/delete")
        assert removed.status_code == 400
    again = next(row for row in await memory.list_upstreams() if row.upstream_id == record.upstream_id)
    assert again.url == canonical
    assert all(row.upstream_id != "store_0123456789abcdef" for row in await memory.list_upstreams())


def _namespace(record: object) -> tuple[None, str, str]:
    return (None, "Namespace", getattr(record, "namespace"))


@pytest.mark.asyncio
async def test_accepted_namespace_delete_is_not_completion() -> None:
    now, sleep, clock = _clock()
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    cluster.hold_namespace_deletion = True
    service = _service(store, cluster, now=now, sleep=sleep, delete_wait_seconds=0)
    service.material_factory = _material
    record, kind = await service.create(owner, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    await service.reconcile_store(record.store_id)
    await service.request_delete(owner, record.store_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None
    assert saved.state == "deleting"
    assert saved.phase == "terminating"
    assert public_store(saved)["deletion_complete"] is False
    assert public_store(saved)["tool_names"] == []
    assert _namespace(record) in cluster.items
    assert cluster.delete_requests == [(record.namespace, record.store_id)]
    assert clock["at"] == datetime(2026, 10, 22, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_namespace_absence_completes_a_held_deletion() -> None:
    now, sleep, _clock_state = _clock()
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    cluster.hold_namespace_deletion = True
    cluster.release_namespace_after_reads = 2
    service = _service(store, cluster, now=now, sleep=sleep, delete_poll_seconds=0.2, delete_wait_seconds=1)
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    await service.request_delete(owner, record.store_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "deleted"
    assert saved.phase == "deleted"
    assert public_store(saved)["deletion_complete"] is True
    assert _namespace(record) not in cluster.items
    assert cluster.delete_requests == [(record.namespace, record.store_id)]


@pytest.mark.asyncio
async def test_terminating_namespace_times_out_without_claiming_deletion() -> None:
    now, sleep, clock = _clock()
    started = clock["at"]
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    cluster.hold_namespace_deletion = True
    service = _service(
        store,
        cluster,
        _settings(max_attempts=1),
        now=now,
        sleep=sleep,
        delete_poll_seconds=0.2,
        delete_wait_seconds=0.4,
    )
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    await service.request_delete(owner, record.store_id)
    await service.reconcile_store(record.store_id)
    waiting = await get_store(store, record.store_id)
    assert waiting is not None
    assert waiting.state == "deleting"
    assert waiting.error_code == ""
    assert waiting.attempt == 0
    assert clock["at"] > started
    clock["at"] = clock["at"] + timedelta(seconds=61)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None
    assert saved.state == "deleting"
    assert saved.phase == "terminating"
    assert saved.error_code == "delete_blocked"
    assert saved.attempt == 0
    assert public_store(saved)["deletion_complete"] is False
    assert _namespace(record) in cluster.items
    cluster.hold_namespace_deletion = False
    await service.reconcile_store(record.store_id)
    finished = await get_store(store, record.store_id)
    assert finished is not None and finished.state == "deleted"
    assert _namespace(record) not in cluster.items


async def _ready_for_delete(cluster: FakeCluster, settings: Settings | None = None):
    now, sleep, clock = _clock()
    store = InMemoryStore()
    owner = await _owner(store)
    cluster.ready = True
    service = _service(store, cluster, settings, now=now, sleep=sleep, delete_wait_seconds=0)
    service.material_factory = _material
    record, _kind = await service.create(owner, "North Shop", "idem-north-1")
    assert record is not None
    await service.reconcile_store(record.store_id)
    return store, owner, service, record, clock


@pytest.mark.asyncio
async def test_already_absent_namespace_deletes_without_an_attempt() -> None:
    cluster = FakeCluster()
    store, owner, service, record, _clock_state = await _ready_for_delete(cluster)
    cluster._drop_namespace(record.namespace)
    await service.request_delete(owner, record.store_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(store, record.store_id)
    assert saved is not None and saved.state == "deleted"
    assert saved.attempt == 0 and saved.error_code == "" and saved.error_message == ""
    assert public_store(saved)["deletion_complete"] is True
    assert cluster.delete_requests == []


@pytest.mark.asyncio
async def test_terminating_passes_do_not_count_as_failed_attempts() -> None:
    cluster = FakeCluster()
    cluster.hold_namespace_deletion = True
    store, owner, service, record, _clock_state = await _ready_for_delete(cluster, _settings(max_attempts=3))
    await service.request_delete(owner, record.store_id)
    for _ in range(8):
        await service.reconcile_store(record.store_id)
        waiting = await get_store(store, record.store_id)
        assert waiting is not None and waiting.state == "deleting"
        assert waiting.attempt == 0
        assert waiting.error_code == "" and waiting.error_message == ""
        assert public_store(waiting)["deletion_complete"] is False
    assert waiting.progress == "Waiting for the store namespace and volumes to be removed."
    cluster.hold_namespace_deletion = False
    await service.reconcile_store(record.store_id)
    finished = await get_store(store, record.store_id)
    assert finished is not None and finished.state == "deleted"
    assert finished.error_code == ""
    assert public_store(finished)["deletion_complete"] is True


@pytest.mark.asyncio
async def test_failed_deletes_stop_at_the_attempt_limit_then_recover() -> None:
    cluster = FakeCluster()
    cluster.fail_namespace_deletion = True
    store, owner, service, record, _clock_state = await _ready_for_delete(cluster, _settings(max_attempts=3))
    await service.request_delete(owner, record.store_id)
    seen: list[tuple[int, str]] = []
    for _ in range(7):
        await service.reconcile_store(record.store_id)
        row = await get_store(store, record.store_id)
        assert row is not None and row.state == "deleting"
        seen.append((row.attempt, row.error_code))
    assert seen[:3] == [(1, ""), (2, ""), (3, "delete_blocked")]
    assert seen[3:] == [(3, "delete_blocked")] * 4
    assert row.error_message == "That store could not be deleted."
    assert public_store(row)["deletion_complete"] is False
    assert _namespace(record) in cluster.items
    cluster.fail_namespace_deletion = False
    await service.reconcile_store(record.store_id)
    finished = await get_store(store, record.store_id)
    assert finished is not None and finished.state == "deleted"
    assert finished.error_code == "" and finished.error_message == ""
    assert public_store(finished)["deletion_complete"] is True


@pytest.mark.asyncio
async def test_duplicate_delete_requests_keep_the_deletion_in_progress() -> None:
    cluster = FakeCluster()
    cluster.hold_namespace_deletion = True
    store, owner, service, record, _clock_state = await _ready_for_delete(cluster)
    first, first_code = await service.request_delete(owner, record.store_id)
    assert first_code == "deleting" and first is not None
    deadline = first.deadline_at
    await service.reconcile_store(record.store_id)
    second, second_code = await service.request_delete(owner, record.store_id)
    assert second_code == "deleting" and second is not None
    assert second.state == "deleting" and second.phase == "terminating"
    assert second.deadline_at == deadline
    await service.reconcile_store(record.store_id)
    cluster.hold_namespace_deletion = False
    await service.reconcile_store(record.store_id)
    finished = await get_store(store, record.store_id)
    assert finished is not None and finished.state == "deleted"
    third, third_code = await service.request_delete(owner, record.store_id)
    assert third_code == "deleted" and third is not None and third.state == "deleted"
    assert len(cluster.delete_requests) == 3


@pytest.mark.asyncio
async def test_failed_namespace_delete_retries_without_exposing_tools_or_the_other_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    cluster.fail_namespace_deletion = True
    cluster.namespace_delete_error = SENTINEL
    service = _service(store, cluster, _settings(max_per_user=2), delete_wait_seconds=0)
    service.material_factory = _material
    left, _kind = await service.create(owner, "North Shop", "idem-north-1")
    right, _kind = await service.create(owner, "South Shop", "idem-south-1")
    assert left is not None and right is not None
    await service.reconcile_store(left.store_id)
    await service.reconcile_store(right.store_id)
    issued = await store.issue_for_user(owner)
    left_tool = public_tool(left.store_id, "list_orders")
    right_tool = public_tool(right.store_id, "list_orders")
    assert await store.grant(issued.key_id, left_tool)
    assert await store.grant(issued.key_id, right_tool)
    assert await store.grant(issued.key_id, "utility__convert_units")
    await service.request_delete(owner, left.store_id)
    await service.reconcile_store(left.store_id)
    failed = await get_store(store, left.store_id)
    kept = await get_store(store, right.store_id)
    assert failed is not None and failed.state == "deleting" and failed.phase == "terminating"
    assert public_store(failed)["deletion_complete"] is False
    assert kept is not None and kept.state == "ready"
    assert await store.permits(issued.key_id, left_tool) is False
    assert await store.permits(issued.key_id, right_tool) is True
    assert await store.permits(issued.key_id, "utility__convert_units") is True
    assert await store_call_allowed(store, left_tool, owner_user_id=owner) is False
    assert await store_call_allowed(store, right_tool, owner_user_id=owner) is True
    assert _namespace(left) in cluster.items
    assert _namespace(right) in cluster.items
    assert cluster.delete_requests == []
    assert SENTINEL not in caplog.text
    cluster.fail_namespace_deletion = False
    await service.reconcile_store(left.store_id)
    finished = await get_store(store, left.store_id)
    assert finished is not None and finished.state == "deleted"
    assert _namespace(left) not in cluster.items
    assert _namespace(right) in cluster.items
    assert await store.permits(issued.key_id, right_tool) is True
    assert await store_call_allowed(store, right_tool, owner_user_id=owner) is True
    assert cluster.delete_requests == [(left.namespace, left.store_id)]


@pytest.mark.asyncio
async def test_absent_namespace_delete_does_not_touch_the_other_store() -> None:
    store = InMemoryStore()
    owner = await _owner(store)
    cluster = FakeCluster()
    cluster.ready = True
    service = _service(store, cluster, _settings(max_per_user=2), delete_wait_seconds=0)
    service.material_factory = _material
    left, _kind = await service.create(owner, "North Shop", "idem-north-1")
    right, _kind = await service.create(owner, "South Shop", "idem-south-1")
    assert left is not None and right is not None
    await service.reconcile_store(left.store_id)
    await service.reconcile_store(right.store_id)
    issued = await store.issue_for_user(owner)
    right_tool = public_tool(right.store_id, "list_orders")
    assert await store.grant(issued.key_id, right_tool)
    await service.request_delete(owner, left.store_id)
    cluster._drop_namespace(left.namespace)
    await service.reconcile_store(left.store_id)
    finished = await get_store(store, left.store_id)
    kept = await get_store(store, right.store_id)
    assert finished is not None and finished.state == "deleted"
    assert kept is not None and kept.state == "ready"
    assert cluster.delete_requests == []
    assert _namespace(right) in cluster.items
    await service.reconcile_store(left.store_id)
    again, code = await service.request_delete(owner, left.store_id)
    assert code == "deleted" and again is not None and again.state == "deleted"
    with pytest.raises(OwnershipError):
        await cluster.delete_namespace(right.namespace, left.store_id)
    assert _namespace(right) in cluster.items
    assert await store.permits(issued.key_id, right_tool) is True
    assert await store_call_allowed(store, right_tool, owner_user_id=owner) is True
    assert cluster.delete_requests == []
