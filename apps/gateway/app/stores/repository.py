"""Durable store rows.

PostgreSQL uses an advisory lock so two creates for one user cannot both pass
the store limit. The in-memory store uses one lock for the same rule. Neither
path stores Kubernetes Secret values.
"""

import asyncio
import logging
import zlib
from datetime import datetime, timezone

from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.stores.model import StoreRecord

logger = logging.getLogger(__name__)

STORE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS gateway_stores (
        store_id TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        state TEXT NOT NULL,
        phase TEXT NOT NULL,
        namespace_name TEXT NOT NULL,
        upstream_id TEXT NOT NULL,
        public_url TEXT NOT NULL DEFAULT '',
        progress TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        attempt INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        deadline_at TIMESTAMPTZ,
        CONSTRAINT gateway_stores_state CHECK (
            state IN ('provisioning', 'ready', 'failed', 'deleting', 'deleted')
        ),
        CONSTRAINT gateway_stores_owner_len CHECK (char_length(owner_user_id) <= 64),
        CONSTRAINT gateway_stores_name_len CHECK (char_length(display_name) <= 40),
        CONSTRAINT gateway_stores_progress_len CHECK (char_length(progress) <= 200),
        CONSTRAINT gateway_stores_error_len CHECK (char_length(error_message) <= 200),
        UNIQUE (owner_user_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS gateway_stores_owner_idx
    ON gateway_stores (owner_user_id, created_at DESC)
    """,
)

_INSERT = """
INSERT INTO gateway_stores (
    store_id, owner_user_id, display_name, idempotency_key, fingerprint,
    state, phase, namespace_name, upstream_id, public_url, progress,
    error_code, error_message, attempt, created_at, updated_at, deadline_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_UPDATE = """
UPDATE gateway_stores
SET display_name = %s, state = %s, phase = %s, public_url = %s, progress = %s,
    error_code = %s, error_message = %s, attempt = %s, updated_at = %s, deadline_at = %s
WHERE store_id = %s AND owner_user_id = %s
"""


class InsertResult:
    def __init__(self, kind: str, record: StoreRecord | None) -> None:
        self.kind = kind
        self.record = record


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory(store: object) -> bool:
    return hasattr(store, "_stores")


async def insert_store(store: object, record: StoreRecord, max_active: int) -> InsertResult:
    if _memory(store):
        return await _memory_insert(store, record, max_active)
    if hasattr(store, "_connection"):
        return await _postgres_insert(store, record, max_active)
    raise StoreUnavailable("Gateway database is not configured.")


async def get_store(store: object, store_id: str) -> StoreRecord | None:
    if _memory(store):
        row = store._stores.get(store_id)  # type: ignore[attr-defined]
        return row if isinstance(row, StoreRecord) else None
    if hasattr(store, "_connection"):
        return await _postgres_get(store, store_id)
    raise StoreUnavailable("Gateway database is not configured.")


async def update_store(store: object, record: StoreRecord) -> None:
    if record.state not in {"provisioning", "ready", "failed", "deleting", "deleted"}:
        raise StoreUnavailable("Gateway database write failed.")
    if len(record.error_message) > 200 or len(record.progress) > 200:
        raise StoreUnavailable("Gateway database write failed.")
    if _memory(store):
        current = store._stores.get(record.store_id)  # type: ignore[attr-defined]
        if not isinstance(current, StoreRecord) or current.owner_user_id != record.owner_user_id:
            raise StoreUnavailable("Gateway database write failed.")
        store._stores[record.store_id] = record  # type: ignore[attr-defined]
        return
    if hasattr(store, "_connection"):
        await _postgres_update(store, record)
        return
    raise StoreUnavailable("Gateway database is not configured.")


async def list_stores(store: object, owner_user_id: str, limit: int) -> list[StoreRecord]:
    bounded = min(50, max(1, limit))
    if _memory(store):
        rows = [
            row
            for row in store._stores.values()  # type: ignore[attr-defined]
            if isinstance(row, StoreRecord) and row.owner_user_id == owner_user_id and row.state != "deleted"
        ]
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows[:bounded]
    if hasattr(store, "_connection"):
        return await _postgres_list(store, owner_user_id, bounded)
    raise StoreUnavailable("Gateway database is not configured.")


async def list_operations(store: object, limit: int = 20) -> list[StoreRecord]:
    bounded = min(20, max(1, limit))
    if _memory(store):
        rows = [
            row
            for row in store._stores.values()  # type: ignore[attr-defined]
            if isinstance(row, StoreRecord) and row.state in {"provisioning", "deleting"}
        ]
        rows.sort(key=lambda row: row.updated_at)
        return rows[:bounded]
    if hasattr(store, "_connection"):
        return await _postgres_operations(store, bounded)
    raise StoreUnavailable("Gateway database is not configured.")


async def list_ready_drift(store: object, limit: int = 1) -> list[StoreRecord]:
    """Return the oldest Ready rows so a drift check cannot fill the work queue."""
    bounded = min(5, max(1, limit))
    if _memory(store):
        rows = [
            row
            for row in store._stores.values()  # type: ignore[attr-defined]
            if isinstance(row, StoreRecord) and row.state == "ready"
        ]
        rows.sort(key=lambda row: row.updated_at)
        return rows[:bounded]
    if hasattr(store, "_connection"):
        return await _postgres_ready(store, bounded)
    raise StoreUnavailable("Gateway database is not configured.")


async def count_active(store: object, owner_user_id: str) -> int:
    if _memory(store):
        return sum(
            1
            for row in store._stores.values()  # type: ignore[attr-defined]
            if isinstance(row, StoreRecord) and row.owner_user_id == owner_user_id and row.state != "deleted"
        )
    if hasattr(store, "_connection"):
        return await _postgres_count(store, owner_user_id)
    raise StoreUnavailable("Gateway database is not configured.")


def _lock_for(store: object) -> asyncio.Lock:
    lock = getattr(store, "_store_lock", None)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    setattr(store, "_store_lock", lock)
    return lock


async def _memory_insert(store: object, record: StoreRecord, max_active: int) -> InsertResult:
    async with _lock_for(store):
        for row in store._stores.values():  # type: ignore[attr-defined]
            if not isinstance(row, StoreRecord):
                continue
            if row.owner_user_id == record.owner_user_id and row.idempotency_key == record.idempotency_key:
                if row.fingerprint == record.fingerprint:
                    return InsertResult("replay", row)
                return InsertResult("conflict", row)
        active = await count_active(store, record.owner_user_id)
        if active >= max_active:
            return InsertResult("limit", None)
        store._stores[record.store_id] = record  # type: ignore[attr-defined]
        return InsertResult("created", record)


def _row(item: tuple) -> StoreRecord:
    return StoreRecord(
        store_id=item[0],
        owner_user_id=item[1],
        display_name=item[2],
        idempotency_key=item[3],
        fingerprint=item[4],
        state=item[5],
        phase=item[6],
        namespace=item[7],
        upstream_id=item[8],
        url=item[9] or "",
        progress=item[10] or "",
        error_code=item[11] or "",
        error_message=item[12] or "",
        attempt=int(item[13]),
        created_at=item[14],
        updated_at=item[15],
        deadline_at=item[16],
    )


_COLUMNS = """
store_id, owner_user_id, display_name, idempotency_key, fingerprint, state, phase,
namespace_name, upstream_id, public_url, progress, error_code, error_message, attempt,
created_at, updated_at, deadline_at
"""


async def _postgres_insert(store: object, record: StoreRecord, max_active: int) -> InsertResult:
    lock_key = zlib.crc32(record.owner_user_id.encode("utf-8")) & 0x7FFFFFFF
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
                cursor = await conn.execute(
                    f"SELECT {_COLUMNS} FROM gateway_stores WHERE owner_user_id = %s AND idempotency_key = %s",
                    (record.owner_user_id, record.idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    saved = _row(existing)
                    if saved.fingerprint == record.fingerprint:
                        return InsertResult("replay", saved)
                    return InsertResult("conflict", saved)
                counted = await conn.execute(
                    "SELECT count(*) FROM gateway_stores WHERE owner_user_id = %s AND state <> 'deleted'",
                    (record.owner_user_id,),
                )
                count_row = await counted.fetchone()
                if count_row is not None and int(count_row[0]) >= max_active:
                    return InsertResult("limit", None)
                await conn.execute(_INSERT, _insert_params(record))
    except StoreUnavailable:
        raise
    except Exception as exc:
        logger.warning("Gateway store insert failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database write failed.") from None
    return InsertResult("created", record)


def _insert_params(record: StoreRecord) -> tuple:
    return (
        record.store_id,
        record.owner_user_id,
        record.display_name,
        record.idempotency_key,
        record.fingerprint,
        record.state,
        record.phase,
        record.namespace,
        record.upstream_id,
        record.url,
        record.progress,
        record.error_code,
        record.error_message,
        record.attempt,
        record.created_at,
        record.updated_at,
        record.deadline_at,
    )


async def _postgres_get(store: object, store_id: str) -> StoreRecord | None:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM gateway_stores WHERE store_id = %s",
                (store_id,),
            )
            row = await cursor.fetchone()
    except Exception as exc:
        logger.warning("Gateway store read failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database read failed.") from None
    if row is None:
        return None
    return _row(row)


async def _postgres_update(store: object, record: StoreRecord) -> None:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                _UPDATE,
                (
                    record.display_name,
                    record.state,
                    record.phase,
                    record.url,
                    record.progress,
                    record.error_code,
                    record.error_message,
                    record.attempt,
                    record.updated_at,
                    record.deadline_at,
                    record.store_id,
                    record.owner_user_id,
                ),
            )
    except Exception as exc:
        logger.warning("Gateway store update failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database write failed.") from None
    if getattr(cursor, "rowcount", 1) == 0:
        raise StoreUnavailable("Gateway database write failed.")


async def _postgres_list(store: object, owner_user_id: str, limit: int) -> list[StoreRecord]:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                SELECT {_COLUMNS} FROM gateway_stores
                WHERE owner_user_id = %s AND state <> 'deleted'
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (owner_user_id, limit),
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.warning("Gateway store read failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database read failed.") from None
    return [_row(row) for row in rows]


async def _postgres_operations(store: object, limit: int) -> list[StoreRecord]:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                SELECT {_COLUMNS} FROM gateway_stores
                WHERE state IN ('provisioning', 'deleting')
                ORDER BY updated_at
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.warning("Gateway store read failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database read failed.") from None
    return [_row(row) for row in rows]


async def _postgres_ready(store: object, limit: int) -> list[StoreRecord]:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                SELECT {_COLUMNS} FROM gateway_stores
                WHERE state = 'ready'
                ORDER BY updated_at
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.warning("Gateway store read failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database read failed.") from None
    return [_row(row) for row in rows]


async def _postgres_count(store: object, owner_user_id: str) -> int:
    try:
        async with store._connection() as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                "SELECT count(*) FROM gateway_stores WHERE owner_user_id = %s AND state <> 'deleted'",
                (owner_user_id,),
            )
            row = await cursor.fetchone()
    except Exception as exc:
        logger.warning("Gateway store read failed: %s", type(exc).__name__)
        raise StoreUnavailable("Gateway database read failed.") from None
    if row is None:
        return 0
    return int(row[0])


def touch(record: StoreRecord, now: datetime | None = None) -> StoreRecord:
    record.updated_at = now or _now()
    return record
