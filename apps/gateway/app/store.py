"""Key and audit storage.

PostgreSQL is the gateway's authentication database. Tests use an in-memory
store with the same rules. Neither store keeps the raw API key.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from apps.gateway.app.directory import MemoryAccounts, PostgresAccounts, UnavailableAccounts
from apps.gateway.app.keys import IssuedKey, KeyRow, StoreUnavailable, generate_key
from apps.gateway.app.permissions import is_permission_name

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS gateway_api_keys (
        key_id TEXT PRIMARY KEY,
        secret_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_audit_log (
        id BIGSERIAL PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL,
        actor TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        outcome TEXT NOT NULL,
        CONSTRAINT gateway_audit_log_actor_len CHECK (char_length(actor) <= 64),
        CONSTRAINT gateway_audit_log_tool_len CHECK (char_length(tool_name) <= 128),
        CONSTRAINT gateway_audit_log_outcome CHECK (
            outcome IN ('success', 'error', 'timeout', 'upstream_failure', 'unauthorized')
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS gateway_audit_log_occurred_at_idx
    ON gateway_audit_log (occurred_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_key_permissions (
        key_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        PRIMARY KEY (key_id, tool_name),
        CONSTRAINT gateway_key_permissions_tool_len CHECK (char_length(tool_name) <= 128),
        CONSTRAINT gateway_key_permissions_key_fk FOREIGN KEY (key_id)
            REFERENCES gateway_api_keys (key_id)
    )
    """,
)

INSERT_KEY = """
INSERT INTO gateway_api_keys (key_id, secret_hash, created_at, revoked_at)
VALUES (%s, %s, %s, NULL)
"""

SELECT_KEY = """
SELECT key_id, secret_hash, created_at, revoked_at, user_id
FROM gateway_api_keys
WHERE key_id = %s
"""

REVOKE_KEY = """
UPDATE gateway_api_keys
SET revoked_at = %s
WHERE key_id = %s AND revoked_at IS NULL
"""

INSERT_AUDIT = """
INSERT INTO gateway_audit_log (occurred_at, actor, tool_name, outcome, upstream_id, duration_ms, user_id, target)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

ADMIN_SCHEMA = (
    "ALTER TABLE gateway_api_keys ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE gateway_audit_log ADD COLUMN IF NOT EXISTS upstream_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE gateway_audit_log ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
    "ALTER TABLE gateway_audit_log ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE gateway_audit_log ADD COLUMN IF NOT EXISTS target TEXT NOT NULL DEFAULT ''",
    """
    CREATE TABLE IF NOT EXISTS gateway_users (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        verifier TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        disabled_at TIMESTAMPTZ,
        CONSTRAINT gateway_users_role CHECK (role IN ('admin', 'member'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_sessions (
        session_id TEXT PRIMARY KEY,
        secret_hash TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ,
        CONSTRAINT gateway_sessions_user_fk FOREIGN KEY (user_id)
            REFERENCES gateway_users (user_id)
    )
    """,
    "ALTER TABLE gateway_sessions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    """
    CREATE TABLE IF NOT EXISTS gateway_upstreams (
        upstream_id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        enabled BOOLEAN NOT NULL,
        credential_ciphertext TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_upstream_flags (
        upstream_id TEXT PRIMARY KEY,
        enabled BOOLEAN NOT NULL
    )
    """,
)

INSERT_PERMISSION = """
INSERT INTO gateway_key_permissions (key_id, tool_name)
SELECT %s, %s
WHERE EXISTS (SELECT 1 FROM gateway_api_keys WHERE key_id = %s)
ON CONFLICT (key_id, tool_name) DO NOTHING
"""

SELECT_PERMISSION = """
SELECT 1
FROM gateway_key_permissions
WHERE key_id = %s AND tool_name = %s
"""

DELETE_PERMISSION = """
DELETE FROM gateway_key_permissions
WHERE key_id = %s AND tool_name = %s
"""

LIST_PERMISSIONS = """
SELECT tool_name
FROM gateway_key_permissions
WHERE key_id = %s
ORDER BY tool_name
"""

logger = logging.getLogger(__name__)


class AuditRow:
    def __init__(
        self,
        actor: str,
        tool_name: str,
        outcome: str,
        occurred_at: datetime,
        upstream_id: str = "",
        duration_ms: int | None = None,
        user_id: str = "",
        target: str = "",
    ) -> None:
        self.actor = actor
        self.tool_name = tool_name
        self.outcome = outcome
        self.occurred_at = occurred_at
        self.upstream_id = upstream_id
        self.duration_ms = duration_ms
        self.user_id = user_id
        self.target = target

    def __repr__(self) -> str:
        return (
            f"AuditRow(actor={self.actor!r}, tool_name={self.tool_name!r}, "
            f"outcome={self.outcome!r}, occurred_at={self.occurred_at!r}, "
            f"upstream_id={self.upstream_id!r}, duration_ms={self.duration_ms!r}, "
            f"user_id={self.user_id!r}, target={self.target!r})"
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore(MemoryAccounts):
    """Process-local stand-in used by tests. It does not accept a raw key for storage."""

    def __init__(self) -> None:
        self._keys: dict[str, KeyRow] = {}
        self._permissions: dict[str, set[str]] = {}
        self.audit_log: list[AuditRow] = []
        self._users: dict[str, object] = {}
        self._users_by_name: dict[str, str] = {}
        self._sessions: dict[str, object] = {}
        self._upstreams: dict[str, object] = {}
        self._flags: dict[str, bool] = {}

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        yield

    async def ping(self) -> bool:
        return True

    async def issue(self) -> IssuedKey:
        for _ in range(5):
            key_id, raw_key, secret_hash = generate_key()
            if key_id not in self._keys:
                self._keys[key_id] = KeyRow(
                    key_id=key_id,
                    secret_hash=secret_hash,
                    created_at=_now(),
                    revoked_at=None,
                )
                return IssuedKey(key_id=key_id, raw_key=raw_key)
        raise StoreUnavailable("Gateway key store could not issue a key.")

    async def get(self, key_id: str) -> KeyRow | None:
        return self._keys.get(key_id)

    async def revoke(self, key_id: str) -> bool:
        row = self._keys.get(key_id)
        if row is None or row.revoked_at is not None:
            return False
        self._keys[key_id] = KeyRow(
            key_id=row.key_id,
            secret_hash=row.secret_hash,
            created_at=row.created_at,
            revoked_at=_now(),
            user_id=row.user_id,
        )
        return True

    async def add_audit(
        self,
        actor: str,
        tool_name: str,
        outcome: str,
        occurred_at: datetime,
        upstream_id: str = "",
        duration_ms: int | None = None,
        user_id: str = "",
        target: str = "",
    ) -> None:
        self.audit_log.append(
            AuditRow(actor, tool_name, outcome, occurred_at, upstream_id, duration_ms, user_id, target)
        )

    async def grant(self, key_id: str, tool_name: str) -> bool:
        if key_id not in self._keys or not is_permission_name(tool_name):
            return False
        self._permissions.setdefault(key_id, set()).add(tool_name)
        return True

    async def revoke_permission(self, key_id: str, tool_name: str) -> bool:
        names = self._permissions.get(key_id)
        if names is None or tool_name not in names:
            return False
        names.remove(tool_name)
        return True

    async def permits(self, key_id: str, tool_name: str) -> bool:
        if not is_permission_name(tool_name):
            return False
        return tool_name in self._permissions.get(key_id, set())

    async def list_permissions(self, key_id: str) -> list[str]:
        return sorted(self._permissions.get(key_id, ()))


class UnconfiguredStore(UnavailableAccounts):
    """Used when GATEWAY_DATABASE_URL is unset. It never authenticates a caller."""

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        yield

    async def ping(self) -> bool:
        return False

    async def issue(self) -> IssuedKey:
        raise StoreUnavailable("Gateway key store is not configured.")

    async def get(self, key_id: str) -> KeyRow | None:
        del key_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def revoke(self, key_id: str) -> bool:
        del key_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def add_audit(
        self,
        actor: str,
        tool_name: str,
        outcome: str,
        occurred_at: datetime,
        upstream_id: str = "",
        duration_ms: int | None = None,
        user_id: str = "",
        target: str = "",
    ) -> None:
        del actor, tool_name, outcome, occurred_at, upstream_id, duration_ms, user_id, target
        raise StoreUnavailable("Gateway key store is not configured.")

    async def grant(self, key_id: str, tool_name: str) -> bool:
        del key_id, tool_name
        raise StoreUnavailable("Gateway key store is not configured.")

    async def revoke_permission(self, key_id: str, tool_name: str) -> bool:
        del key_id, tool_name
        raise StoreUnavailable("Gateway key store is not configured.")

    async def permits(self, key_id: str, tool_name: str) -> bool:
        del key_id, tool_name
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_permissions(self, key_id: str) -> list[str]:
        del key_id
        raise StoreUnavailable("Gateway key store is not configured.")


class PostgresStore(PostgresAccounts):
    """PostgreSQL store for gateway keys and audit rows. The URL is never logged."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: object | None = None

    def __repr__(self) -> str:
        return "PostgresStore(url=<redacted>)"

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await self.open()
        try:
            yield
        finally:
            await self.close()

    async def open(self, *, ensure_schema: bool = True) -> None:
        if self._pool is not None:
            return
        pool = None
        try:
            from psycopg_pool import AsyncConnectionPool

            pool = AsyncConnectionPool(
                self._database_url,
                min_size=1,
                max_size=4,
                timeout=3,
                open=False,
                kwargs={"autocommit": True, "connect_timeout": 3},
            )
            await pool.open()
            self._pool = pool
            if ensure_schema:
                await self._ensure_schema()
        except StoreUnavailable:
            await self._discard(pool)
            raise
        except Exception as exc:
            await self._discard(pool)
            logger.warning("Gateway database setup failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database setup failed.") from None

    async def close(self) -> None:
        await self._discard(self._pool)

    async def ping(self) -> bool:
        try:
            async with self._connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as exc:
            logger.warning("Gateway database health check failed: %s", type(exc).__name__)
            return False

    async def issue(self) -> IssuedKey:
        for _ in range(5):
            key_id, raw_key, secret_hash = generate_key()
            try:
                async with self._connection() as conn:
                    await conn.execute(INSERT_KEY, (key_id, secret_hash, _now()))
                return IssuedKey(key_id=key_id, raw_key=raw_key)
            except Exception as exc:
                if _is_unique_violation(exc):
                    continue
                logger.warning("Gateway database write failed: %s", type(exc).__name__)
                raise StoreUnavailable("Gateway database write failed.") from None
        raise StoreUnavailable("Gateway key store could not issue a key.")

    async def get(self, key_id: str) -> KeyRow | None:
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(SELECT_KEY, (key_id,))
                row = await cursor.fetchone()
        except StoreUnavailable:
            raise
        except Exception as exc:
            logger.warning("Gateway database read failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database read failed.") from None
        if row is None:
            return None
        user_id = row[4] if len(row) > 4 else None
        return KeyRow(
            key_id=row[0],
            secret_hash=row[1],
            created_at=row[2],
            revoked_at=row[3],
            user_id=user_id,
        )

    async def revoke(self, key_id: str) -> bool:
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(REVOKE_KEY, (_now(), key_id))
                return cursor.rowcount == 1
        except Exception as exc:
            logger.warning("Gateway database write failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database write failed.") from None

    async def add_audit(
        self,
        actor: str,
        tool_name: str,
        outcome: str,
        occurred_at: datetime,
        upstream_id: str = "",
        duration_ms: int | None = None,
        user_id: str = "",
        target: str = "",
    ) -> None:
        try:
            async with self._connection() as conn:
                await conn.execute(
                    INSERT_AUDIT,
                    (occurred_at, actor, tool_name, outcome, upstream_id, duration_ms, user_id, target),
                )
        except Exception as exc:
            logger.warning("Gateway database write failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database write failed.") from None

    async def grant(self, key_id: str, tool_name: str) -> bool:
        if not is_permission_name(tool_name):
            return False
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(INSERT_PERMISSION, (key_id, tool_name, key_id))
                if cursor.rowcount == 1:
                    return True
        except Exception as exc:
            logger.warning("Gateway database write failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database write failed.") from None
        return await self.permits(key_id, tool_name)

    async def revoke_permission(self, key_id: str, tool_name: str) -> bool:
        if not is_permission_name(tool_name):
            return False
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(DELETE_PERMISSION, (key_id, tool_name))
                return cursor.rowcount == 1
        except Exception as exc:
            logger.warning("Gateway database write failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database write failed.") from None

    async def permits(self, key_id: str, tool_name: str) -> bool:
        if not is_permission_name(tool_name):
            return False
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(SELECT_PERMISSION, (key_id, tool_name))
                row = await cursor.fetchone()
        except StoreUnavailable:
            raise
        except Exception as exc:
            logger.warning("Gateway database read failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database read failed.") from None
        return row is not None

    async def list_permissions(self, key_id: str) -> list[str]:
        try:
            async with self._connection() as conn:
                cursor = await conn.execute(LIST_PERMISSIONS, (key_id,))
                rows = await cursor.fetchall()
        except StoreUnavailable:
            raise
        except Exception as exc:
            logger.warning("Gateway database read failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database read failed.") from None
        return [row[0] for row in rows]

    async def _ensure_schema(self) -> None:
        try:
            async with self._connection() as conn:
                for statement in (*SCHEMA, *ADMIN_SCHEMA):
                    await conn.execute(statement)
        except Exception as exc:
            logger.warning("Gateway database setup failed: %s", type(exc).__name__)
            raise StoreUnavailable("Gateway database setup failed.") from None

    async def _discard(self, pool: object) -> None:
        self._pool = None
        if pool is None:
            return
        try:
            await pool.close()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Gateway database close failed: %s", type(exc).__name__)

    def _connection(self):
        pool = self._pool
        if pool is None:
            raise StoreUnavailable("Gateway database is not open.")
        return pool.connection()  # type: ignore[attr-defined]


def _is_unique_violation(exc: BaseException) -> bool:
    return type(exc).__name__ == "UniqueViolation"
