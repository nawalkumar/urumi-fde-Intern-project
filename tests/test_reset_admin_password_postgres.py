"""Password reset against a disposable PostgreSQL 16 container.

Reuses the isolation checks from the permission tests. The container is
published only on 127.0.0.1, has no named volume, and is removed afterward.
The database URL is built in this process. These tests do not read the
project environment file, Kubernetes Secrets, or a gateway pod.
"""

import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
import pytest

from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.passwords import hash_password, verify_password
from apps.gateway.app.store import PostgresStore
from apps.gateway.provision import _reset_admin_file, main
from tests.test_postgres_permissions import (
    _assert_isolated,
    _docker,
    _free_port,
    _remove_container,
    _wait_ready,
)

ADMIN_ID = "36e2af3487daa142"
MEMBER_ID = "aaaaaaaaaaaaaaaa"
OTHER_ID = "bbbbbbbbbbbbbbbb"
ADMIN_USERNAME = "urumi_admin"
MEMBER_USERNAME = "member_one"
ADMIN_PASSWORD = "old-admin-password-value"
MEMBER_PASSWORD = "member-password-value"
ACTOR = "628d977d51bf1645"

CATALOG = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position
"""


class ResetDatabase:
    def __init__(self, url: str, empty_url: str, container: str, port: int) -> None:
        self.url = url
        self.empty_url = empty_url
        self.container = container
        self.port = port

    def __repr__(self) -> str:
        return f"ResetDatabase(container={self.container}, port={self.port})"


def _local_url(password: str, port: int, name: str) -> str:
    return f"postgresql://permtest:{password}@127.0.0.1:{port}/{name}"


def _require_loopback(url: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname != "127.0.0.1" or parsed.username != "permtest":
        raise RuntimeError("The disposable database is not bound to 127.0.0.1 only.")


@pytest.fixture(scope="module")
def reset_db():
    import secrets

    container = f"urumi-reset-test-{secrets.token_hex(4)}"
    port = _free_port()
    password = secrets.token_hex(24)
    url = _local_url(password, port, "permtest")
    empty_url = _local_url(password, port, "resetempty")
    _require_loopback(url)
    _require_loopback(empty_url)
    database = ResetDatabase(url, empty_url, container, port)
    try:
        _docker(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container,
                "-p",
                f"127.0.0.1:{port}:5432",
                "-e",
                "POSTGRES_USER=permtest",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=permtest",
                "postgres:16-alpine",
            ]
        )
        _assert_isolated(container)
        _wait_ready(container)
        _create_empty_database(url)
        schema = PostgresStore(url)
        try:
            awaitable = schema.open()
            import asyncio

            asyncio.run(awaitable)
        except StoreUnavailable:
            raise RuntimeError("The disposable database schema could not be created.") from None
        finally:
            import asyncio

            asyncio.run(schema.close())
        yield database
    finally:
        _remove_container(container)


def _create_empty_database(url: str) -> None:
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=3) as connection:
            connection.execute("CREATE DATABASE resetempty")
    except Exception:
        raise RuntimeError("The empty disposable database could not be created.") from None


def _connect(url: str) -> psycopg.Connection:
    _require_loopback(url)
    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=3)
    except Exception:
        raise AssertionError("The disposable database could not be queried.") from None


def _rows(url: str, statement: str, params: tuple = ()) -> list[tuple]:
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            if cursor.description is None:
                return []
            return cursor.fetchall()


def _catalog(url: str) -> list[tuple]:
    return _rows(url, CATALOG)


def _tables(url: str) -> list[str]:
    rows = _rows(
        url,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
        """,
    )
    return [row[0] for row in rows]


async def _open(url: str) -> PostgresStore:
    _require_loopback(url)
    store = PostgresStore(url)
    try:
        await store.open(ensure_schema=False)
    except StoreUnavailable:
        await store.close()
        raise AssertionError("PostgresStore could not open the disposable database.") from None
    return store


async def _seed(url: str) -> tuple[PostgresStore, str, str, str]:
    store = await _open(url)
    created = datetime.now(timezone.utc)
    admin_verifier = hash_password(ADMIN_PASSWORD)
    member_verifier = hash_password(MEMBER_PASSWORD)
    async with store._connection() as connection:
        await connection.execute("DELETE FROM gateway_sessions")
        await connection.execute("DELETE FROM gateway_key_permissions")
        await connection.execute("DELETE FROM gateway_api_keys")
        await connection.execute("DELETE FROM gateway_audit_log")
        await connection.execute("DELETE FROM gateway_users")
        await connection.execute(
            """
            INSERT INTO gateway_users (user_id, username, verifier, role, created_at, disabled_at)
            VALUES (%s, %s, %s, 'admin', %s, NULL), (%s, %s, %s, 'member', %s, NULL)
            """,
            (
                ADMIN_ID,
                ADMIN_USERNAME,
                admin_verifier,
                created,
                MEMBER_ID,
                MEMBER_USERNAME,
                member_verifier,
                created,
            ),
        )
    admin_session = await store.create_session(ADMIN_ID)
    member_session = await store.create_session(MEMBER_ID)
    issued = await store.issue_for_user(MEMBER_ID)
    assert await store.grant(issued.key_id, "woocommerce__list_products") is True
    await store.add_audit(ACTOR, "utility__convert_units", "unauthorized", created, user_id="")
    return store, admin_session, member_session, issued.key_id


def _args(user_id: str, username: str, destination) -> list[str]:
    return [
        "reset-admin-password",
        "--user-id",
        user_id,
        "--username",
        username,
        "--output",
        str(destination),
    ]


def _child_env(url: str) -> dict[str, str]:
    _require_loopback(url)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GATEWAY_DATABASE_URL", "GATEWAY_DB_PASSWORD"}
    }
    env["GATEWAY_DATABASE_URL"] = url
    return env


def _command(url: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "apps.gateway.provision", *args],
        env=_child_env(url),
        capture_output=True,
        text=True,
        check=False,
    )


def _hidden(secret: str, stdout: str, stderr: str) -> None:
    assert secret not in stdout
    assert secret not in stderr


async def _identity(store: PostgresStore, user_id: str) -> tuple[str, str, str, bool]:
    user = await store.get_user(user_id)
    assert user is not None
    return user.user_id, user.username, user.role, user.disabled_at is None


@pytest.mark.asyncio
async def test_empty_database_reset_creates_no_gateway_schema(reset_db: ResetDatabase, tmp_path) -> None:
    before = _tables(reset_db.empty_url)
    destination = tmp_path / "admin.password"
    completed = _command(reset_db.empty_url, _args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Could not reset the administrator password.\n"
    assert not destination.exists()
    assert _tables(reset_db.empty_url) == before == []


@pytest.mark.asyncio
async def test_successful_reset_preserves_identity_and_unrelated_rows(
    reset_db: ResetDatabase, tmp_path
) -> None:
    store, admin_session, member_session, key_id = await _seed(reset_db.url)
    before_catalog = _catalog(reset_db.url)
    before_key = await store.get(key_id)
    assert before_key is not None
    await store.close()
    destination = tmp_path / "admin.password"
    completed = _command(reset_db.url, _args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert completed.returncode == 0
    assert completed.stdout == (
        "Gateway administrator password reset.\n"
        f"username: {ADMIN_USERNAME}\n"
        f"user_id: {ADMIN_ID}\n"
        "The new password was written to the output file and is not printed.\n"
    )
    assert completed.stderr == ""
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    password = destination.read_text(encoding="utf-8")
    assert password.endswith("\n")
    _hidden(password, completed.stdout, completed.stderr)
    again = await _open(reset_db.url)
    try:
        admin = await again.get_user(ADMIN_ID)
        member = await again.get_user(MEMBER_ID)
        assert admin is not None and member is not None
        _hidden(admin.verifier, completed.stdout, completed.stderr)
        assert await _identity(again, ADMIN_ID) == (ADMIN_ID, ADMIN_USERNAME, "admin", True)
        assert verify_password(password.strip(), admin.verifier)
        assert not verify_password(ADMIN_PASSWORD, admin.verifier)
        assert await _identity(again, MEMBER_ID) == (MEMBER_ID, MEMBER_USERNAME, "member", True)
        assert verify_password(MEMBER_PASSWORD, member.verifier)
        assert await again.authenticate_session(admin_session) is None
        member_user = await again.authenticate_session(member_session)
        assert member_user is not None and member_user.user_id == MEMBER_ID
        kept = await again.get(key_id)
        assert kept is not None
        assert kept.secret_hash == before_key.secret_hash
        assert kept.user_id == MEMBER_ID
        assert await again.list_permissions(key_id) == ["woocommerce__list_products"]
        audit = _rows(
            reset_db.url,
            "SELECT actor, tool_name, outcome FROM gateway_audit_log ORDER BY id",
        )
        assert audit == [(ACTOR, "utility__convert_units", "unauthorized")]
        assert _catalog(reset_db.url) == before_catalog
        counts = _rows(
            reset_db.url,
            """
            SELECT
                (SELECT count(*) FROM gateway_users),
                (SELECT count(*) FROM gateway_api_keys),
                (SELECT count(*) FROM gateway_key_permissions),
                (SELECT count(*) FROM gateway_audit_log),
                (SELECT count(*) FROM gateway_upstreams),
                (SELECT count(*) FROM gateway_upstream_flags)
            """,
        )
        assert counts == [(2, 1, 1, 1, 0, 0)]
    finally:
        await again.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "username"),
    [
        (OTHER_ID, ADMIN_USERNAME),
        (ADMIN_ID, "other_admin"),
        (MEMBER_ID, MEMBER_USERNAME),
    ],
)
async def test_rejected_target_changes_nothing(
    reset_db: ResetDatabase, tmp_path, user_id: str, username: str
) -> None:
    store, admin_session, member_session, key_id = await _seed(reset_db.url)
    before = await store.get_user(ADMIN_ID)
    before_key = await store.get(key_id)
    assert before is not None and before_key is not None
    before_catalog = _catalog(reset_db.url)
    await store.close()
    destination = tmp_path / "admin.password"
    completed = _command(reset_db.url, _args(user_id, username, destination))
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Could not reset the administrator password.\n"
    assert not destination.exists()
    again = await _open(reset_db.url)
    try:
        admin = await again.get_user(ADMIN_ID)
        assert admin is not None
        assert admin.verifier == before.verifier
        assert await _identity(again, ADMIN_ID) == (ADMIN_ID, ADMIN_USERNAME, "admin", True)
        assert await again.authenticate_session(admin_session) is not None
        assert await again.authenticate_session(member_session) is not None
        kept = await again.get(key_id)
        assert kept is not None and kept.secret_hash == before_key.secret_hash
        assert _catalog(reset_db.url) == before_catalog
    finally:
        await again.close()


@pytest.mark.asyncio
async def test_disabled_admin_is_rejected(reset_db: ResetDatabase, tmp_path) -> None:
    store, _, member_session, key_id = await _seed(reset_db.url)
    assert await store.set_user_disabled(ADMIN_ID, True) is True
    before = await store.get_user(ADMIN_ID)
    before_key = await store.get(key_id)
    assert before is not None and before.disabled_at is not None and before_key is not None
    before_catalog = _catalog(reset_db.url)
    await store.close()
    destination = tmp_path / "admin.password"
    completed = _command(reset_db.url, _args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert completed.returncode == 1
    assert completed.stderr == "Could not reset the administrator password.\n"
    assert not destination.exists()
    again = await _open(reset_db.url)
    try:
        admin = await again.get_user(ADMIN_ID)
        member = await again.get_user(MEMBER_ID)
        assert admin is not None and member is not None
        assert admin.user_id == ADMIN_ID
        assert admin.verifier == before.verifier
        assert admin.disabled_at == before.disabled_at
        assert member.role == "member" and member.disabled_at is None
        assert await again.authenticate_session(member_session) is not None
        kept = await again.get(key_id)
        assert kept is not None and kept.secret_hash == before_key.secret_hash
        assert _catalog(reset_db.url) == before_catalog
    finally:
        await again.close()


@pytest.mark.asyncio
async def test_write_failure_rolls_back_and_later_commit_works(
    reset_db: ResetDatabase, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, admin_session, member_session, key_id = await _seed(reset_db.url)
    before = await store.get_user(ADMIN_ID)
    before_key = await store.get(key_id)
    assert before is not None and before_key is not None
    before_catalog = _catalog(reset_db.url)

    def fail_write(fd: int, payload: bytes) -> None:
        raise OSError(0, "Could not write the key file.")

    monkeypatch.setattr("apps.gateway.provision._write_all", fail_write)
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        await _reset_admin_file(store, ADMIN_ID, ADMIN_USERNAME, destination)
    assert caught.value.code == 1
    assert not destination.exists()
    assert await store.authenticate_session(admin_session) is not None
    assert await store.authenticate_session(member_session) is not None
    admin = await store.get_user(ADMIN_ID)
    assert admin is not None and admin.verifier == before.verifier
    assert _catalog(reset_db.url) == before_catalog

    monkeypatch.undo()
    success = tmp_path / "admin-after-rollback.password"
    await _reset_admin_file(store, ADMIN_ID, ADMIN_USERNAME, success)
    password = success.read_text(encoding="utf-8")
    updated = await store.get_user(ADMIN_ID)
    assert updated is not None
    assert updated.user_id == ADMIN_ID
    assert verify_password(password.strip(), updated.verifier)
    assert not verify_password(ADMIN_PASSWORD, updated.verifier)
    assert await store.authenticate_session(admin_session) is None
    assert await store.authenticate_session(member_session) is not None
    kept = await store.get(key_id)
    assert kept is not None and kept.secret_hash == before_key.secret_hash
    assert _catalog(reset_db.url) == before_catalog
    await store.close()


@pytest.mark.asyncio
async def test_interrupt_rolls_back_and_removes_the_file(
    reset_db: ResetDatabase, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, admin_session, member_session, key_id = await _seed(reset_db.url)
    before = await store.get_user(ADMIN_ID)
    before_key = await store.get(key_id)
    assert before is not None and before_key is not None
    before_catalog = _catalog(reset_db.url)

    def interrupt_write(fd: int, payload: bytes) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("apps.gateway.provision._write_all", interrupt_write)
    destination = tmp_path / "admin.password"
    with pytest.raises(KeyboardInterrupt):
        await _reset_admin_file(store, ADMIN_ID, ADMIN_USERNAME, destination)
    assert not destination.exists()
    assert await store.authenticate_session(admin_session) is not None
    assert await store.authenticate_session(member_session) is not None
    admin = await store.get_user(ADMIN_ID)
    assert admin is not None and admin.verifier == before.verifier
    kept = await store.get(key_id)
    assert kept is not None and kept.secret_hash == before_key.secret_hash
    assert await store.list_permissions(key_id) == ["woocommerce__list_products"]
    assert _catalog(reset_db.url) == before_catalog
    await store.close()
