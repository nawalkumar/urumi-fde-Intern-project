"""Users, sessions, dynamic upstreams, and audit reads.

Gateway API key hashes stay in the existing key table. This module does not
store raw keys or raw session tokens.
"""

import copy
import hmac
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from apps.gateway.app.keys import KEY_ID_PATTERN, IssuedKey, KeyRow, StoreUnavailable, generate_key, hash_key

_ROLES = {"admin", "member"}


@dataclass
class UserRecord:
    user_id: str
    username: str
    role: str
    created_at: datetime
    disabled_at: datetime | None
    verifier: str = ""

    def __repr__(self) -> str:
        return f"UserRecord(user_id={self.user_id!r}, username={self.username!r}, role={self.role!r})"

    def public(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "disabled": self.disabled_at is not None,
        }


SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


@dataclass
class SessionRow:
    session_id: str
    secret_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass
class UpstreamRecord:
    upstream_id: str
    url: str
    enabled: bool
    credential: str = ""

    def __repr__(self) -> str:
        return f"UpstreamRecord(upstream_id={self.upstream_id!r}, enabled={self.enabled!r})"

    @property
    def has_credential(self) -> bool:
        return bool(self.credential)


def _expires_at(created: datetime) -> datetime:
    return created + timedelta(seconds=SESSION_MAX_AGE_SECONDS)


def _expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def new_session_token() -> tuple[str, str, str]:
    session_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    raw = f"urs_{session_id}.{secret}"
    return session_id, raw, hash_key(raw)


def _reset_verifier(verifier: str) -> bool:
    return isinstance(verifier, str) and verifier.startswith("pbkdf2_sha256$") and len(verifier) <= 512


def parse_session_token(raw: str | None) -> str | None:
    if raw is None or not isinstance(raw, str) or len(raw) > 200:
        return None
    prefix, dot, secret = raw.partition(".")
    if dot != "." or not prefix.startswith("urs_"):
        return None
    session_id = prefix[4:]
    if KEY_ID_PATTERN.fullmatch(session_id) is None or len(secret) < 32:
        return None
    return session_id


class MemoryAccounts:
    async def get_user(self, user_id: str) -> UserRecord | None:
        user = self._users.get(user_id)  # type: ignore[attr-defined]
        return user if isinstance(user, UserRecord) else None

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        user_id = self._users_by_name.get(username)  # type: ignore[attr-defined]
        if user_id is None:
            return None
        return await self.get_user(user_id)

    async def create_user(self, username: str, verifier: str, role: str) -> UserRecord:
        if role not in _ROLES or username in self._users_by_name:  # type: ignore[attr-defined]
            raise StoreUnavailable("Gateway user was not created.")
        user = UserRecord(
            user_id=secrets.token_hex(8),
            username=username,
            role=role,
            created_at=datetime.now(timezone.utc),
            disabled_at=None,
            verifier=verifier,
        )
        self._users[user.user_id] = user  # type: ignore[attr-defined]
        self._users_by_name[username] = user.user_id  # type: ignore[attr-defined]
        return user

    async def list_users(self) -> list[UserRecord]:
        users = [user for user in self._users.values() if isinstance(user, UserRecord)]  # type: ignore[attr-defined]
        return sorted(users, key=lambda user: user.username)

    async def set_user_disabled(self, user_id: str, disabled: bool) -> bool:
        user = await self.get_user(user_id)
        if user is None:
            return False
        updated = UserRecord(
            user.user_id,
            user.username,
            user.role,
            user.created_at,
            datetime.now(timezone.utc) if disabled else None,
            user.verifier,
        )
        self._users[user_id] = updated  # type: ignore[attr-defined]
        if disabled:
            await self.revoke_user_sessions(user_id)
        return True

    async def set_user_role(self, user_id: str, role: str) -> bool:
        user = await self.get_user(user_id)
        if user is None or role not in _ROLES:
            return False
        self._users[user_id] = UserRecord(  # type: ignore[attr-defined]
            user.user_id, user.username, role, user.created_at, user.disabled_at, user.verifier
        )
        return True

    async def create_session(self, user_id: str) -> str:
        if await self.get_user(user_id) is None:
            raise StoreUnavailable("Gateway session was not created.")
        session_id, raw, digest = new_session_token()
        created = datetime.now(timezone.utc)
        self._sessions[session_id] = SessionRow(  # type: ignore[attr-defined]
            session_id, digest, user_id, created, _expires_at(created), None
        )
        return raw

    async def authenticate_session(self, raw: str) -> UserRecord | None:
        session_id = parse_session_token(raw)
        if session_id is None:
            return None
        row = self._sessions.get(session_id)  # type: ignore[attr-defined]
        if not isinstance(row, SessionRow) or row.revoked_at is not None or _expired(row.expires_at):
            return None
        presented = hash_key(raw)
        if not hmac.compare_digest(presented, row.secret_hash):
            return None
        user = await self.get_user(row.user_id)
        if user is None or user.disabled_at is not None:
            return None
        return user

    async def revoke_session_token(self, raw: str) -> None:
        session_id = parse_session_token(raw)
        if session_id is None:
            return
        row = self._sessions.get(session_id)  # type: ignore[attr-defined]
        if isinstance(row, SessionRow):
            self._sessions[session_id] = SessionRow(  # type: ignore[attr-defined]
                row.session_id,
                row.secret_hash,
                row.user_id,
                row.created_at,
                row.expires_at,
                datetime.now(timezone.utc),
            )

    async def revoke_user_sessions(self, user_id: str) -> None:
        for session_id, row in list(self._sessions.items()):  # type: ignore[attr-defined]
            if isinstance(row, SessionRow) and row.user_id == user_id:
                self._sessions[session_id] = SessionRow(  # type: ignore[attr-defined]
                    row.session_id,
                    row.secret_hash,
                    row.user_id,
                    row.created_at,
                    row.expires_at,
                    datetime.now(timezone.utc),
                )

    @asynccontextmanager
    async def reset_admin_password(self, user_id: str, username: str, verifier: str) -> AsyncIterator[UserRecord]:
        """Replace one enabled administrator's verifier and revoke only that user's sessions.

        The changes stay invisible to later lookups when the caller raises before the
        context exits. A failed password-file write can therefore restore the previous verifier.
        """
        if not _reset_verifier(verifier):
            raise StoreUnavailable("Gateway administrator was not updated.")
        user = self._enabled_admin(user_id, username)
        if user is None:
            raise StoreUnavailable("Gateway administrator was not updated.")
        saved_users = copy.deepcopy(self._users)  # type: ignore[attr-defined]
        saved_sessions = copy.deepcopy(self._sessions)  # type: ignore[attr-defined]
        self._users[user_id] = UserRecord(  # type: ignore[attr-defined]
            user.user_id,
            user.username,
            user.role,
            user.created_at,
            user.disabled_at,
            verifier,
        )
        await self.revoke_user_sessions(user_id)
        try:
            yield self._users[user_id]  # type: ignore[attr-defined]
        except BaseException:
            self._users = saved_users  # type: ignore[attr-defined]
            self._sessions = saved_sessions  # type: ignore[attr-defined]
            raise

    def _enabled_admin(self, user_id: str, username: str) -> UserRecord | None:
        user = self._users.get(user_id)  # type: ignore[attr-defined]
        if not isinstance(user, UserRecord):
            return None
        if user.username != username or user.role != "admin" or user.disabled_at is not None:
            return None
        named = self._users_by_name.get(username)  # type: ignore[attr-defined]
        if named != user_id:
            return None
        return user

    async def issue_for_user(self, user_id: str) -> IssuedKey:
        if await self.get_user(user_id) is None:
            raise StoreUnavailable("Gateway key store could not issue a key.")
        issued = await self.issue()  # type: ignore[attr-defined]
        row = self._keys[issued.key_id]  # type: ignore[attr-defined]
        self._keys[issued.key_id] = KeyRow(  # type: ignore[attr-defined]
            key_id=row.key_id,
            secret_hash=row.secret_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
            user_id=user_id,
        )
        return issued

    async def list_key_rows(self, user_id: str | None = None) -> list[KeyRow]:
        rows = list(self._keys.values())  # type: ignore[attr-defined]
        if user_id is not None:
            rows = [row for row in rows if row.user_id == user_id]
        return sorted(rows, key=lambda row: row.key_id)

    async def save_upstream(self, upstream_id: str, url: str, enabled: bool, credential: str) -> None:
        self._upstreams[upstream_id] = UpstreamRecord(upstream_id, url, enabled, credential)  # type: ignore[attr-defined]

    async def delete_upstream(self, upstream_id: str) -> bool:
        if upstream_id not in self._upstreams:  # type: ignore[attr-defined]
            return False
        del self._upstreams[upstream_id]  # type: ignore[attr-defined]
        return True

    async def list_upstreams(self) -> list[UpstreamRecord]:
        rows = [row for row in self._upstreams.values() if isinstance(row, UpstreamRecord)]  # type: ignore[attr-defined]
        return sorted(rows, key=lambda row: row.upstream_id)

    async def set_upstream_flag(self, upstream_id: str, enabled: bool) -> None:
        self._flags[upstream_id] = enabled  # type: ignore[attr-defined]

    async def delete_upstream_flag(self, upstream_id: str) -> None:
        self._flags.pop(upstream_id, None)  # type: ignore[attr-defined]

    async def list_upstream_flags(self) -> dict[str, bool]:
        return dict(self._flags)  # type: ignore[attr-defined]

    async def list_audit(self, *, limit: int = 100, user_id: str | None = None) -> list:
        rows = list(self.audit_log)  # type: ignore[attr-defined]
        if user_id is not None:
            rows = [row for row in rows if row.user_id == user_id]
        return list(reversed(rows[-limit:]))


class PostgresAccounts:
    async def get_user(self, user_id: str) -> UserRecord | None:
        return await self._one_user("SELECT user_id, username, verifier, role, created_at, disabled_at FROM gateway_users WHERE user_id = %s", (user_id,))

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        return await self._one_user("SELECT user_id, username, verifier, role, created_at, disabled_at FROM gateway_users WHERE username = %s", (username,))

    async def _one_user(self, statement: str, params: tuple) -> UserRecord | None:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(statement, params)
                row = await cursor.fetchone()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        if row is None:
            return None
        return UserRecord(row[0], row[1], row[3], row[4], row[5], row[2])

    async def create_user(self, username: str, verifier: str, role: str) -> UserRecord:
        if role not in _ROLES:
            raise StoreUnavailable("Gateway user was not created.")
        user_id = secrets.token_hex(8)
        created = datetime.now(timezone.utc)
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "INSERT INTO gateway_users (user_id, username, verifier, role, created_at, disabled_at) VALUES (%s, %s, %s, %s, %s, NULL)",
                    (user_id, username, verifier, role, created),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway user was not created.") from exc
        return UserRecord(user_id, username, role, created, None, verifier)

    async def list_users(self) -> list[UserRecord]:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(
                    "SELECT user_id, username, verifier, role, created_at, disabled_at FROM gateway_users ORDER BY username"
                )
                rows = await cursor.fetchall()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        return [UserRecord(row[0], row[1], row[3], row[4], row[5], row[2]) for row in rows]

    async def set_user_disabled(self, user_id: str, disabled: bool) -> bool:
        stamp = datetime.now(timezone.utc) if disabled else None
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(
                    "UPDATE gateway_users SET disabled_at = %s WHERE user_id = %s",
                    (stamp, user_id),
                )
                changed = cursor.rowcount == 1
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc
        if changed and disabled:
            await self.revoke_user_sessions(user_id)
        return changed

    async def set_user_role(self, user_id: str, role: str) -> bool:
        if role not in _ROLES:
            return False
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(
                    "UPDATE gateway_users SET role = %s WHERE user_id = %s",
                    (role, user_id),
                )
                return cursor.rowcount == 1
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def create_session(self, user_id: str) -> str:
        if await self.get_user(user_id) is None:
            raise StoreUnavailable("Gateway session was not created.")
        session_id, raw, digest = new_session_token()
        created = datetime.now(timezone.utc)
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "INSERT INTO gateway_sessions (session_id, secret_hash, user_id, created_at, expires_at, revoked_at) VALUES (%s, %s, %s, %s, %s, NULL)",
                    (session_id, digest, user_id, created, _expires_at(created)),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway session was not created.") from exc
        return raw

    async def authenticate_session(self, raw: str) -> UserRecord | None:
        session_id = parse_session_token(raw)
        if session_id is None:
            return None
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(
                    "SELECT secret_hash, user_id, revoked_at, expires_at FROM gateway_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = await cursor.fetchone()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        if row is None or row[2] is not None or _expired(row[3]):
            return None
        if not hmac.compare_digest(hash_key(raw), row[0]):
            return None
        user = await self.get_user(row[1])
        if user is None or user.disabled_at is not None:
            return None
        return user

    async def revoke_session_token(self, raw: str) -> None:
        session_id = parse_session_token(raw)
        if session_id is None:
            return
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "UPDATE gateway_sessions SET revoked_at = %s WHERE session_id = %s AND revoked_at IS NULL",
                    (datetime.now(timezone.utc), session_id),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def revoke_user_sessions(self, user_id: str) -> None:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "UPDATE gateway_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
                    (datetime.now(timezone.utc), user_id),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    @asynccontextmanager
    async def reset_admin_password(self, user_id: str, username: str, verifier: str) -> AsyncIterator[UserRecord]:
        """Update one enabled administrator and revoke only that user's sessions in one transaction."""
        if not _reset_verifier(verifier):
            raise StoreUnavailable("Gateway administrator was not updated.")
        try:
            connection = self._connection()  # type: ignore[attr-defined]
        except StoreUnavailable:
            raise
        except Exception:
            raise StoreUnavailable("Gateway administrator was not updated.") from None
        async with connection as conn:
            async with conn.transaction():
                user = await self._replace_admin_verifier(conn, user_id, username, verifier)
                yield user

    async def _replace_admin_verifier(self, conn: object, user_id: str, username: str, verifier: str) -> UserRecord:
        try:
            cursor = await conn.execute(  # type: ignore[attr-defined]
                """
                SELECT user_id, username, role, created_at, disabled_at
                FROM gateway_users
                WHERE user_id = %s AND username = %s AND role = 'admin' AND disabled_at IS NULL
                FOR UPDATE
                """,
                (user_id, username),
            )
            rows = await cursor.fetchall()
            if len(rows) != 1:
                raise StoreUnavailable("Gateway administrator was not updated.")
            updated = await conn.execute(  # type: ignore[attr-defined]
                """
                UPDATE gateway_users
                SET verifier = %s
                WHERE user_id = %s AND username = %s AND role = 'admin' AND disabled_at IS NULL
                """,
                (verifier, user_id, username),
            )
            if updated.rowcount != 1:
                raise StoreUnavailable("Gateway administrator was not updated.")
            await conn.execute(  # type: ignore[attr-defined]
                """
                UPDATE gateway_sessions
                SET revoked_at = %s
                WHERE user_id = %s AND revoked_at IS NULL
                """,
                (datetime.now(timezone.utc), user_id),
            )
        except StoreUnavailable:
            raise
        except Exception:
            raise StoreUnavailable("Gateway administrator was not updated.") from None
        row = rows[0]
        return UserRecord(row[0], row[1], row[2], row[3], row[4], verifier)

    async def issue_for_user(self, user_id: str) -> IssuedKey:
        if await self.get_user(user_id) is None:
            raise StoreUnavailable("Gateway key store could not issue a key.")
        issued = await self.issue()  # type: ignore[attr-defined]
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "UPDATE gateway_api_keys SET user_id = %s WHERE key_id = %s",
                    (user_id, issued.key_id),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc
        return issued

    async def list_key_rows(self, user_id: str | None = None) -> list[KeyRow]:
        statement = "SELECT key_id, secret_hash, created_at, revoked_at, user_id FROM gateway_api_keys"
        params: tuple = ()
        if user_id is not None:
            statement += " WHERE user_id = %s"
            params = (user_id,)
        statement += " ORDER BY key_id"
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(statement, params)
                rows = await cursor.fetchall()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        return [
            KeyRow(key_id=row[0], secret_hash=row[1], created_at=row[2], revoked_at=row[3], user_id=row[4])
            for row in rows
        ]

    async def save_upstream(self, upstream_id: str, url: str, enabled: bool, credential: str) -> None:
        now = datetime.now(timezone.utc)
        stored = credential or None
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    """
                    INSERT INTO gateway_upstreams (upstream_id, url, enabled, credential_ciphertext, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (upstream_id) DO UPDATE
                    SET url = EXCLUDED.url, enabled = EXCLUDED.enabled,
                        credential_ciphertext = EXCLUDED.credential_ciphertext, updated_at = EXCLUDED.updated_at
                    """,
                    (upstream_id, url, enabled, stored, now, now),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def delete_upstream(self, upstream_id: str) -> bool:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute("DELETE FROM gateway_upstreams WHERE upstream_id = %s", (upstream_id,))
                return cursor.rowcount == 1
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def list_upstreams(self) -> list[UpstreamRecord]:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(
                    "SELECT upstream_id, url, enabled, credential_ciphertext FROM gateway_upstreams ORDER BY upstream_id"
                )
                rows = await cursor.fetchall()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        return [
            UpstreamRecord(row[0], row[1], bool(row[2]), row[3] or "")
            for row in rows
        ]

    async def set_upstream_flag(self, upstream_id: str, enabled: bool) -> None:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    """
                    INSERT INTO gateway_upstream_flags (upstream_id, enabled) VALUES (%s, %s)
                    ON CONFLICT (upstream_id) DO UPDATE SET enabled = EXCLUDED.enabled
                    """,
                    (upstream_id, enabled),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def delete_upstream_flag(self, upstream_id: str) -> None:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                await conn.execute(
                    "DELETE FROM gateway_upstream_flags WHERE upstream_id = %s",
                    (upstream_id,),
                )
        except Exception as exc:
            raise StoreUnavailable("Gateway database write failed.") from exc

    async def list_upstream_flags(self) -> dict[str, bool]:
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute("SELECT upstream_id, enabled FROM gateway_upstream_flags")
                rows = await cursor.fetchall()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        return {row[0]: bool(row[1]) for row in rows}

    async def list_audit(self, *, limit: int = 100, user_id: str | None = None) -> list:
        from apps.gateway.app.store import AuditRow

        statement = """
        SELECT actor, tool_name, outcome, occurred_at, upstream_id, duration_ms, user_id, target
        FROM gateway_audit_log
        """
        params: tuple = ()
        if user_id is not None:
            statement += " WHERE user_id = %s"
            params = (user_id,)
        statement += " ORDER BY occurred_at DESC LIMIT %s"
        params = (*params, limit)
        try:
            async with self._connection() as conn:  # type: ignore[attr-defined]
                cursor = await conn.execute(statement, params)
                rows = await cursor.fetchall()
        except Exception as exc:
            raise StoreUnavailable("Gateway database read failed.") from exc
        return [
            AuditRow(row[0], row[1], row[2], row[3], row[4] or "", row[5], row[6] or "", row[7] or "")
            for row in rows
        ]


class UnavailableAccounts:
    async def get_user(self, user_id: str) -> UserRecord | None:
        del user_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        del username
        raise StoreUnavailable("Gateway key store is not configured.")

    async def create_user(self, username: str, verifier: str, role: str) -> UserRecord:
        del username, verifier, role
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_users(self) -> list[UserRecord]:
        raise StoreUnavailable("Gateway key store is not configured.")

    async def set_user_disabled(self, user_id: str, disabled: bool) -> bool:
        del user_id, disabled
        raise StoreUnavailable("Gateway key store is not configured.")

    async def set_user_role(self, user_id: str, role: str) -> bool:
        del user_id, role
        raise StoreUnavailable("Gateway key store is not configured.")

    async def create_session(self, user_id: str) -> str:
        del user_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def authenticate_session(self, raw: str) -> UserRecord | None:
        del raw
        raise StoreUnavailable("Gateway key store is not configured.")

    async def revoke_session_token(self, raw: str) -> None:
        del raw
        raise StoreUnavailable("Gateway key store is not configured.")

    async def revoke_user_sessions(self, user_id: str) -> None:
        del user_id
        raise StoreUnavailable("Gateway key store is not configured.")

    @asynccontextmanager
    async def reset_admin_password(self, user_id: str, username: str, verifier: str) -> AsyncIterator[UserRecord]:
        del user_id, username, verifier
        raise StoreUnavailable("Gateway key store is not configured.")
        yield  # pragma: no cover

    async def issue_for_user(self, user_id: str) -> IssuedKey:
        del user_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_key_rows(self, user_id: str | None = None) -> list[KeyRow]:
        del user_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def save_upstream(self, upstream_id: str, url: str, enabled: bool, credential: str) -> None:
        del upstream_id, url, enabled, credential
        raise StoreUnavailable("Gateway key store is not configured.")

    async def delete_upstream(self, upstream_id: str) -> bool:
        del upstream_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_upstreams(self) -> list[UpstreamRecord]:
        raise StoreUnavailable("Gateway key store is not configured.")

    async def set_upstream_flag(self, upstream_id: str, enabled: bool) -> None:
        del upstream_id, enabled
        raise StoreUnavailable("Gateway key store is not configured.")

    async def delete_upstream_flag(self, upstream_id: str) -> None:
        del upstream_id
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_upstream_flags(self) -> dict[str, bool]:
        raise StoreUnavailable("Gateway key store is not configured.")

    async def list_audit(self, *, limit: int = 100, user_id: str | None = None) -> list:
        del limit, user_id
        raise StoreUnavailable("Gateway key store is not configured.")
