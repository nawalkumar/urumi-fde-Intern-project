"""Durable store provisioning.

The HTTP handler stores the row and returns. Reconciliation is a separate
task. A restart reads provisioning and deleting rows and continues. Cleanup
deletes only the namespace whose name and store-id label match the row.
"""

import asyncio
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from apps.gateway.app.audit import Auditor
from apps.gateway.app.config import Settings
from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.stores.cluster import ClusterClient
from apps.gateway.app.stores.manifests import (
    SecretMaterial,
    StoreSpec,
    mcp_url,
    public_url,
    secret_resources,
    workload_resources,
)
from apps.gateway.app.stores.model import (
    ERRORS,
    STORE_ID,
    OwnershipError,
    StoreRecord,
    fingerprint,
    namespace_for,
    owned_namespace,
    upstream_id_for,
    validate_request,
)
from apps.gateway.app.stores.repository import (
    get_store,
    insert_store,
    list_operations,
    list_ready_drift,
    touch,
    update_store,
)

logger = logging.getLogger(__name__)

_MCP_URL = re.compile(r"http://woo-mcp\.st-[0-9a-f]{16}\.svc\.cluster\.local:8000/mcp$")


def new_secret_material() -> SecretMaterial:
    def one() -> str:
        return secrets.token_urlsafe(24)

    return SecretMaterial(
        mariadb_root=one(),
        mariadb_password=one(),
        admin_password=one(),
        consumer_key="ck_" + secrets.token_hex(20),
        consumer_secret="cs_" + secrets.token_hex(20),
    )


class StoreService:
    def __init__(
        self,
        store: object,
        registry: object,
        settings: Settings,
        cluster: ClusterClient | None,
        *,
        autostart: bool = True,
        now=None,
        refresh=None,
        cluster_reconciler: bool | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.settings = settings
        self.cluster = cluster
        self.autostart = autostart
        self.cluster_reconciler = cluster is not None if cluster_reconciler is None else cluster_reconciler
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.refresh = refresh or self._refresh_registry
        self.auditor = Auditor(store)
        self.material_factory = new_secret_material
        self._semaphore = asyncio.Semaphore(settings.stores.concurrency)
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._stopped = asyncio.Event()

    async def create(self, owner_user_id: str, name: object, idempotency_key: object) -> tuple[StoreRecord | None, str]:
        if not self.settings.stores.enabled:
            return None, "disabled"
        parsed = validate_request(name, idempotency_key)
        if parsed is None:
            return None, "invalid"
        display_name, key = parsed
        store_id = secrets.token_hex(8)
        current = self.now()
        record = StoreRecord(
            store_id=store_id,
            owner_user_id=owner_user_id,
            display_name=display_name,
            idempotency_key=key,
            fingerprint=fingerprint(display_name),
            state="provisioning",
            phase="persisted",
            namespace=namespace_for(store_id),
            upstream_id=upstream_id_for(store_id),
            url="",
            progress="Provisioning has been requested.",
            error_code="",
            error_message="",
            attempt=0,
            created_at=current,
            updated_at=current,
            deadline_at=current + timedelta(seconds=self.settings.stores.timeout_seconds),
        )
        try:
            result = await insert_store(self.store, record, self.settings.stores.max_per_user)
        except StoreUnavailable:
            return None, "unavailable"
        if result.kind == "created" and result.record is not None:
            await self.auditor.record(
                owner_user_id,
                "admin:create_store",
                "success",
                target=result.record.store_id,
                user_id=owner_user_id,
                upstream_id=result.record.upstream_id,
            )
        if (
            result.record is not None
            and result.record.state in {"provisioning", "deleting"}
            and self.autostart
            and self.cluster_reconciler
        ):
            self.schedule(result.record.store_id)
        return result.record, result.kind

    async def get_owned(self, owner_user_id: str, store_id: str) -> tuple[StoreRecord | None, str]:
        if STORE_ID.fullmatch(store_id) is None:
            return None, "not_found"
        try:
            record = await get_store(self.store, store_id)
        except StoreUnavailable:
            return None, "unavailable"
        if record is None:
            return None, "not_found"
        if record.owner_user_id != owner_user_id:
            await self.auditor.record(
                owner_user_id,
                "admin:store_denied",
                "unauthorized",
                target=store_id,
                user_id=owner_user_id,
            )
            return None, "not_found"
        return record, "ok"

    async def request_delete(self, owner_user_id: str, store_id: str) -> tuple[StoreRecord | None, str]:
        record, code = await self.get_owned(owner_user_id, store_id)
        if record is None:
            return None, code
        if record.state == "deleted":
            await self._revoke_store_grants(record)
            return record, "deleted"
        if record.state != "deleting":
            record.state = "deleting"
            record.phase = "unregister"
            record.progress = "Removing the store registration."
            record.url = ""
            record.error_code = ""
            record.error_message = ""
            touch(record, self.now())
            try:
                await update_store(self.store, record)
            except StoreUnavailable:
                return None, "unavailable"
        try:
            await self._revoke_store_grants(record)
            await self._drop_upstream(record)
        except Exception as exc:
            logger.warning("Store deregistration failed: %s", type(exc).__name__)
            record.progress = "Registration removal will be retried."
            touch(record, self.now())
            await update_store(self.store, record)
        await self.auditor.record(
            owner_user_id,
            "admin:delete_store",
            "success",
            target=record.store_id,
            user_id=owner_user_id,
            upstream_id=record.upstream_id,
        )
        if self.autostart and self.cluster_reconciler:
            self.schedule(record.store_id)
        return record, "deleting"

    async def request_retry(self, owner_user_id: str, store_id: str) -> tuple[StoreRecord | None, str]:
        record, code = await self.get_owned(owner_user_id, store_id)
        if record is None:
            return None, code
        if record.state != "failed":
            return record, "retry"
        current = self.now()
        record.state = "provisioning"
        record.phase = "persisted"
        record.progress = "Provisioning was requested again."
        record.error_code = ""
        record.error_message = ""
        record.attempt = 0
        record.deadline_at = current + timedelta(seconds=self.settings.stores.timeout_seconds)
        touch(record, current)
        try:
            await update_store(self.store, record)
        except StoreUnavailable:
            return None, "unavailable"
        await self.auditor.record(
            owner_user_id,
            "admin:retry_store",
            "success",
            target=record.store_id,
            user_id=owner_user_id,
            upstream_id=record.upstream_id,
        )
        if self.autostart and self.cluster_reconciler:
            self.schedule(record.store_id)
        return record, "provisioning"

    def schedule(self, store_id: str, *, limited: bool = True) -> None:
        if store_id in self._inflight:
            return
        self._inflight.add(store_id)

        async def run() -> None:
            try:
                if limited:
                    async with self._semaphore:
                        await self.reconcile_store(store_id)
                else:
                    await self.reconcile_store(store_id)
            finally:
                self._inflight.discard(store_id)
                self._tasks.discard(asyncio.current_task())

        task = asyncio.create_task(run())
        self._tasks.add(task)

    async def wait_idle(self) -> None:
        while self._tasks:
            pending = [task for task in list(self._tasks) if not task.done()]
            if not pending:
                self._tasks.clear()
                return
            await asyncio.gather(*pending, return_exceptions=True)

    async def reconcile_due(self) -> None:
        try:
            rows = await list_operations(self.store)
        except StoreUnavailable:
            logger.warning("Store sweep failed: StoreUnavailable")
            return
        for row in rows:
            self.schedule(row.store_id)
        try:
            ready = await list_ready_drift(self.store, limit=1)
        except StoreUnavailable:
            logger.warning("Store sweep failed: StoreUnavailable")
            return
        for row in ready:
            self.schedule(row.store_id, limited=False)

    async def worker(self) -> None:
        while not self._stopped.is_set():
            if self.cluster_reconciler:
                await self.reconcile_due()
            else:
                try:
                    await self.refresh()
                except Exception as exc:
                    logger.warning("Store catalog refresh failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=2)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stopped.set()

    async def reconcile_store(self, store_id: str) -> None:
        if STORE_ID.fullmatch(store_id) is None:
            return
        lock = self._locks.setdefault(store_id, asyncio.Lock())
        async with lock:
            try:
                record = await get_store(self.store, store_id)
            except StoreUnavailable:
                logger.warning("Store reconcile failed: StoreUnavailable")
                return
            if record is None or not owned_namespace(record.store_id, record.namespace):
                if record is not None and self.cluster_reconciler:
                    await self._fail(record, "ownership", cleanup=False)
                return
            if not self.cluster_reconciler:
                if record.state == "deleting":
                    await self._revoke_store_grants(record)
                    try:
                        await self._drop_upstream(record)
                    except Exception as exc:
                        logger.warning("Store deregistration failed: %s", type(exc).__name__)
                elif record.state == "ready":
                    await self._pin_upstream(record)
                return
            try:
                if record.state == "provisioning":
                    await self._provision(record)
                elif record.state == "deleting":
                    await self._teardown(record)
                elif record.state == "ready":
                    await self._check_ready(record)
            except Exception as exc:
                logger.warning("Store reconcile failed: %s", type(exc).__name__)

    async def _provision(self, record: StoreRecord) -> None:
        if self.cluster is None:
            await self._fail(record, "unconfigured", cleanup=False)
            return
        if self._expired(record):
            await self._fail(record, "timeout", cleanup=True)
            return
        if record.attempt >= self.settings.stores.max_attempts:
            await self._fail(record, "attempts", cleanup=True)
            return
        try:
            record.phase = "namespace"
            record.progress = "Creating the store namespace."
            touch(record, self.now())
            await update_store(self.store, record)
            await self._apply(record)
            ready = await self.cluster.workloads_ready(record.namespace, record.store_id)
            if not ready:
                record.phase = "waiting"
                record.progress = "Waiting for the store workloads."
                touch(record, self.now())
                await update_store(self.store, record)
                return
            await self._register(record)
        except OwnershipError:
            logger.warning("Store reconcile refused an unowned resource.")
            await self._fail(record, "ownership", cleanup=False)
        except Exception as exc:
            logger.warning("Store provisioning step failed: %s", type(exc).__name__)
            record.attempt += 1
            record.phase = "retry"
            record.progress = "Provisioning will be retried."
            touch(record, self.now())
            await update_store(self.store, record)
            if record.attempt >= self.settings.stores.max_attempts or self._expired(record):
                code = "timeout" if self._expired(record) else "attempts"
                await self._fail(record, code, cleanup=True)

    async def _apply(self, record: StoreRecord) -> None:
        if self.cluster is None:
            raise RuntimeError("Kubernetes API is not configured.")
        spec = self._spec(record)
        resources = workload_resources(spec)
        await self.cluster.apply(resources[0])
        missing_db = not await self.cluster.secret_exists(record.namespace, "store-db", record.store_id)
        missing_woo = not await self.cluster.secret_exists(
            record.namespace, "woocommerce-credentials", record.store_id
        )
        if missing_db or missing_woo:
            material = self.material_factory()
            try:
                secrets = {item["metadata"]["name"]: item for item in secret_resources(spec, material)}  # type: ignore[index]
                if missing_db:
                    await self.cluster.apply(secrets["store-db"])
                if missing_woo:
                    await self.cluster.apply(secrets["woocommerce-credentials"])
            finally:
                del material
        for resource in resources[1:]:
            await self.cluster.apply(resource)

    async def _register(self, record: StoreRecord) -> None:
        if not self.settings.allow_private_upstreams:
            await self._fail(record, "private_disabled", cleanup=False)
            return
        url = mcp_url(record.namespace)
        if _MCP_URL.fullmatch(url) is None:
            await self._fail(record, "register", cleanup=False)
            return
        if any(item.name == record.upstream_id for item in self.settings.upstreams):
            await self._fail(record, "register", cleanup=False)
            return
        record.phase = "register"
        record.progress = "Registering the store MCP server."
        touch(record, self.now())
        await update_store(self.store, record)
        await self.store.save_upstream(record.upstream_id, url, True, "")  # type: ignore[attr-defined]
        try:
            await self.refresh()
        except Exception as exc:
            logger.warning("Store registration failed: %s", type(exc).__name__)
            record.attempt += 1
            record.progress = "Registration will be retried."
            touch(record, self.now())
            await update_store(self.store, record)
            if record.attempt >= self.settings.stores.max_attempts:
                await self._fail(record, "register", cleanup=True)
            return
        record.state = "ready"
        record.phase = "ready"
        record.url = public_url(self._host(record))
        record.progress = "The store is ready."
        record.error_code = ""
        record.error_message = ""
        touch(record, self.now())
        await update_store(self.store, record)
        await self.auditor.record(
            record.owner_user_id,
            "admin:provision_store",
            "success",
            target=record.store_id,
            user_id=record.owner_user_id,
            upstream_id=record.upstream_id,
        )

    async def _check_ready(self, record: StoreRecord) -> None:
        if self.cluster is None:
            return
        try:
            exists = await self.cluster.namespace_exists(record.namespace, record.store_id)
        except OwnershipError:
            await self._fail(record, "ownership", cleanup=False)
            return
        if not exists:
            await self._drop_upstream(record)
            await self._fail(record, "namespace_missing", cleanup=False)
            return
        await self._pin_upstream(record)

    async def _pin_upstream(self, record: StoreRecord) -> None:
        """Keep the saved upstream on this store's canonical MCP URL."""
        expected = mcp_url(record.namespace)
        if _MCP_URL.fullmatch(expected) is None:
            await self._fail(record, "register", cleanup=False)
            return
        rows = await self.store.list_upstreams()  # type: ignore[attr-defined]
        current = next((row for row in rows if row.upstream_id == record.upstream_id), None)
        if current is None or not current.enabled or current.url != expected:
            if current is None or not current.enabled:
                await self._register(record)
                return
            await self.store.save_upstream(record.upstream_id, expected, True, current.credential or "")  # type: ignore[attr-defined]
            try:
                await self.refresh()
            except Exception as exc:
                logger.warning("Store registration failed: %s", type(exc).__name__)
                return
        touch(record, self.now())
        await update_store(self.store, record)

    async def _teardown(self, record: StoreRecord) -> None:
        try:
            await self._revoke_store_grants(record)
            await self._drop_upstream(record)
        except Exception as exc:
            logger.warning("Store deregistration failed: %s", type(exc).__name__)
            record.progress = "Registration removal will be retried."
            touch(record, self.now())
            await update_store(self.store, record)
            return
        if self.cluster is not None:
            try:
                await self.cluster.delete_namespace(record.namespace, record.store_id)
            except OwnershipError:
                await self._fail(record, "ownership", cleanup=False)
                return
            except Exception as exc:
                logger.warning("Store namespace delete failed: %s", type(exc).__name__)
                record.attempt += 1
                record.progress = "Namespace deletion will be retried."
                touch(record, self.now())
                await update_store(self.store, record)
                return
        record.state = "deleted"
        record.phase = "deleted"
        record.url = ""
        record.progress = "The store has been deleted."
        record.error_code = ""
        record.error_message = ""
        touch(record, self.now())
        await update_store(self.store, record)

    async def _fail(self, record: StoreRecord, code: str, *, cleanup: bool) -> None:
        if cleanup and self.cluster is not None:
            try:
                await self.cluster.delete_namespace(record.namespace, record.store_id)
            except OwnershipError:
                code = "ownership"
            except Exception as exc:
                logger.warning("Store cleanup failed: %s", type(exc).__name__)
        try:
            await self._drop_upstream(record)
        except Exception as exc:
            logger.warning("Store deregistration failed: %s", type(exc).__name__)
        record.state = "failed"
        record.phase = "failed"
        record.url = ""
        record.error_code = code
        record.error_message = ERRORS.get(code, ERRORS["cleanup"])
        record.progress = record.error_message
        touch(record, self.now())
        await update_store(self.store, record)
        await self.auditor.record(
            record.owner_user_id,
            "admin:provision_failed",
            "error",
            target=record.store_id,
            user_id=record.owner_user_id,
            upstream_id=record.upstream_id,
        )

    async def _revoke_store_grants(self, record: StoreRecord) -> None:
        if not hasattr(self.store, "revoke_store_grants"):
            return
        await self.store.revoke_store_grants(record.store_id)  # type: ignore[attr-defined]

    async def _drop_upstream(self, record: StoreRecord) -> None:
        await self.store.delete_upstream(record.upstream_id)  # type: ignore[attr-defined]
        await self.refresh()

    async def _refresh_registry(self) -> None:
        from apps.gateway.app.catalog import refresh_registry

        await refresh_registry(self.registry, self.store, self.settings)

    def _expired(self, record: StoreRecord) -> bool:
        if record.deadline_at is None:
            return False
        deadline = record.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return self.now() >= deadline

    def _host(self, record: StoreRecord) -> str:
        host = f"{record.store_id}.{self.settings.stores.ingress_base_domain}"
        if host == "store.local" or ".." in host or "/" in host or len(host) > 253:
            raise OwnershipError("Store hostname is not valid.")
        return host

    def _spec(self, record: StoreRecord) -> StoreSpec:
        configured = self.settings.stores
        return StoreSpec(
            store_id=record.store_id,
            owner_id=record.owner_user_id,
            namespace=record.namespace,
            host=self._host(record),
            title=record.display_name,
            wordpress_image=configured.wordpress_image,
            mariadb_image=configured.mariadb_image,
            cli_image=configured.cli_image,
            mcp_image=configured.mcp_image,
            gateway_namespace=configured.gateway_namespace,
            ingress_namespace=configured.ingress_namespace,
            ingress_class=configured.ingress_class,
            storage_class=configured.storage_class,
            allow_plugin_egress=configured.allow_plugin_egress,
            probe_cidrs=configured.probe_cidrs,
        )
