"""Permission persistence against a disposable PostgreSQL 16 container.

The fixture starts a container named urumi-perm-test-<random>, publishes
PostgreSQL only on 127.0.0.1, and does not mount a named volume. It never
reads GATEWAY_DATABASE_URL or the Compose gateway_db volume.
"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import psycopg
import pytest

from apps.gateway.app.audit import Auditor
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.store import PostgresStore

UNKNOWN_KEY_ID = "0123456789abcdef"


class DisposableDatabase:
    def __init__(self, url: str, container: str) -> None:
        self.url = url
        self.container = container
        self.key_ids: list[str] = []

    def __repr__(self) -> str:
        return f"DisposableDatabase(container={self.container})"

    def remember(self, key_id: str) -> None:
        if key_id not in self.key_ids:
            self.key_ids.append(key_id)


class Recording:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, arguments: dict) -> object:
        self.calls.append(name)
        raise AssertionError("authorization reached an upstream")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _docker(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = _safe_docker_detail(exc.stderr)
        message = "A disposable PostgreSQL command failed."
        if detail:
            message = f"{message} {detail}"
        raise RuntimeError(message) from None


def _safe_docker_detail(stderr: str | None) -> str:
    if not stderr:
        return ""
    kept: list[str] = []
    for line in stderr.splitlines():
        lowered = line.lower()
        if "password" in lowered or "postgresql://" in lowered or "postgres://" in lowered:
            continue
        stripped = line.strip()
        if stripped:
            kept.append(stripped)
    return " ".join(kept)[:240]


def _binding_pairs(payload: str) -> list[tuple[str, str]] | None:
    text = payload.strip()
    if text in {"", "null"}:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    pairs: list[tuple[str, str]] = []
    for bindings in parsed.values():
        if not bindings:
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                return None
            pairs.append((str(binding.get("HostIp") or ""), str(binding.get("HostPort") or "")))
    return pairs


def _concrete_bindings(payload: str) -> list[tuple[str, str]] | None:
    pairs = _binding_pairs(payload)
    if pairs is None:
        return None
    return [(host_ip, host_port) for host_ip, host_port in pairs if host_port]


def _is_loopback(host_ip: str) -> bool:
    return host_ip in {"127.0.0.1", "::1"}


def _assert_isolated(container: str) -> None:
    """Reject Compose volumes and any publish that is not loopback-only.

    Docker Engine 28 can report an IPv6 routed placeholder with an empty host
    port, or leave NetworkSettings.Ports empty for a moment while
    HostConfig.PortBindings already says 127.0.0.1. An empty host port is not
    a listen socket. A concrete wildcard address still fails this check.
    """
    mounts = _docker(["docker", "inspect", "-f", "{{range .Mounts}}{{.Name}} {{.Destination}}\n{{end}}", container])
    if "gateway_db" in mounts.stdout:
        raise RuntimeError("The disposable database mounted the Compose volume.")
    deadline = time.monotonic() + 3
    while True:
        ports = _docker(["docker", "inspect", "-f", "{{json .NetworkSettings.Ports}}", container])
        requested = _docker(["docker", "inspect", "-f", "{{json .HostConfig.PortBindings}}", container])
        actual = _concrete_bindings(ports.stdout)
        requested_bindings = _concrete_bindings(requested.stdout)
        if actual is None or requested_bindings is None:
            if time.monotonic() >= deadline:
                raise RuntimeError("The disposable database did not publish a localhost port.")
            time.sleep(0.2)
            continue
        if actual and all(_is_loopback(host_ip) for host_ip, _port in actual):
            if any(host_ip == "127.0.0.1" for host_ip, _port in actual):
                return
        wildcard = any(
            not _is_loopback(host_ip)
            for host_ip, _port in [*(actual or []), *(requested_bindings or [])]
        )
        if wildcard:
            raise RuntimeError("The disposable database is not bound to 127.0.0.1 only.")
        if time.monotonic() >= deadline:
            if (
                not actual
                and requested_bindings
                and all(_is_loopback(host_ip) for host_ip, _port in requested_bindings)
                and any(host_ip == "127.0.0.1" for host_ip, _port in requested_bindings)
            ):
                return
            raise RuntimeError("The disposable database did not publish a localhost port.")
        time.sleep(0.2)


def _wait_ready(container: str) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "permtest", "-d", "permtest"],
            capture_output=True,
            text=True,
        )
        if ready.returncode == 0:
            return
        time.sleep(0.3)
    raise RuntimeError("The disposable database did not become ready.")


def _remove_container(container: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True, text=True)


@pytest.fixture(scope="module")
def postgres_db() -> Iterator[DisposableDatabase]:
    import secrets

    container = f"urumi-perm-test-{secrets.token_hex(4)}"
    port = _free_port()
    password = secrets.token_hex(24)
    url = f"postgresql://permtest:{password}@127.0.0.1:{port}/permtest"
    database = DisposableDatabase(url, container)
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
        yield database
        _delete_recorded_rows(database)
    finally:
        _remove_container(container)


def _delete_recorded_rows(database: DisposableDatabase) -> None:
    if not database.key_ids:
        return
    try:
        with psycopg.connect(database.url, autocommit=True, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM gateway_audit_log WHERE actor = ANY(%s)",
                    (database.key_ids,),
                )
                cursor.execute(
                    "DELETE FROM gateway_key_permissions WHERE key_id = ANY(%s)",
                    (database.key_ids,),
                )
                cursor.execute(
                    "DELETE FROM gateway_api_keys WHERE key_id = ANY(%s)",
                    (database.key_ids,),
                )
    except Exception:
        return


async def _open(database: DisposableDatabase) -> PostgresStore:
    store = PostgresStore(database.url)
    try:
        await store.open()
    except StoreUnavailable:
        await store.close()
        raise AssertionError("PostgresStore could not open the disposable database.") from None
    return store


async def _query(database: DisposableDatabase, statement: str, params: tuple = ()) -> list[tuple]:
    try:
        connection = await psycopg.AsyncConnection.connect(database.url, autocommit=True, connect_timeout=3)
    except Exception:
        raise AssertionError("The disposable database could not be queried.") from None
    try:
        cursor = await connection.execute(statement, params)
        return await cursor.fetchall()
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_permission_table_has_primary_key_and_foreign_key(postgres_db: DisposableDatabase) -> None:
    store = await _open(postgres_db)
    await store.close()
    columns = await _query(
        postgres_db,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'gateway_key_permissions'
        ORDER BY ordinal_position
        """,
    )
    assert [row[0] for row in columns] == ["key_id", "tool_name"]
    constraints = await _query(
        postgres_db,
        """
        SELECT constraint_type, column_name
        FROM information_schema.table_constraints AS constraints
        JOIN information_schema.key_column_usage AS columns
          ON constraints.constraint_name = columns.constraint_name
         AND constraints.table_schema = columns.table_schema
        WHERE constraints.table_schema = 'public'
          AND constraints.table_name = 'gateway_key_permissions'
        ORDER BY constraint_type, columns.ordinal_position
        """,
    )
    assert ("PRIMARY KEY", "key_id") in constraints
    assert ("PRIMARY KEY", "tool_name") in constraints
    referenced = await _query(
        postgres_db,
        """
        SELECT referenced.table_name, referenced.column_name
        FROM information_schema.table_constraints AS constraints
        JOIN information_schema.key_column_usage AS local_columns
          ON constraints.constraint_name = local_columns.constraint_name
         AND constraints.table_schema = local_columns.table_schema
        JOIN information_schema.constraint_column_usage AS referenced
          ON constraints.constraint_name = referenced.constraint_name
         AND constraints.table_schema = referenced.table_schema
        WHERE constraints.table_schema = 'public'
          AND constraints.table_name = 'gateway_key_permissions'
          AND constraints.constraint_type = 'FOREIGN KEY'
          AND local_columns.column_name = 'key_id'
        """,
    )
    assert referenced == [("gateway_api_keys", "key_id")]


@pytest.mark.asyncio
async def test_grant_and_removal_persist_across_store_instances(postgres_db: DisposableDatabase) -> None:
    first = await _open(postgres_db)
    issued = await first.issue()
    postgres_db.remember(issued.key_id)
    assert await first.grant(issued.key_id, "utility__convert_units") is True
    await first.close()

    second = await _open(postgres_db)
    assert await second.list_permissions(issued.key_id) == ["utility__convert_units"]
    assert await second.permits(issued.key_id, "utility__convert_units") is True
    assert await second.revoke_permission(issued.key_id, "utility__convert_units") is True
    await second.close()

    third = await _open(postgres_db)
    assert await third.list_permissions(issued.key_id) == []
    assert await third.permits(issued.key_id, "utility__convert_units") is False
    await third.close()


@pytest.mark.asyncio
async def test_grants_are_isolated_by_key_id(postgres_db: DisposableDatabase) -> None:
    store = await _open(postgres_db)
    first = await store.issue()
    second = await store.issue()
    postgres_db.remember(first.key_id)
    postgres_db.remember(second.key_id)
    assert await store.grant(first.key_id, "utility__convert_units") is True
    assert await store.grant(second.key_id, "support__search_notes") is True
    await store.close()

    again = await _open(postgres_db)
    assert await again.list_permissions(first.key_id) == ["utility__convert_units"]
    assert await again.list_permissions(second.key_id) == ["support__search_notes"]
    assert await again.permits(first.key_id, "support__search_notes") is False
    assert await again.permits(second.key_id, "utility__convert_units") is False
    await again.close()


@pytest.mark.asyncio
async def test_revoked_key_cannot_use_a_stored_permission(postgres_db: DisposableDatabase) -> None:
    store = await _open(postgres_db)
    issued = await store.issue()
    postgres_db.remember(issued.key_id)
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    authenticator = Authenticator(store)
    principal = await authenticator.authenticate(issued.raw_key)
    assert principal is not None
    assert await store.revoke(issued.key_id) is True
    registry = Recording()
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(store))
    result = await dispatcher.execute_tool(principal, "utility__convert_units", {"note": "not-stored"})
    assert result.is_error is True
    assert result.content[0].text == "Authentication required."
    assert registry.calls == []
    assert await store.list_permissions(issued.key_id) == ["utility__convert_units"]
    assert await authenticator.visible_names(principal) is None
    await store.close()


@pytest.mark.asyncio
async def test_unknown_key_and_wildcard_names_create_no_permission_row(
    postgres_db: DisposableDatabase,
) -> None:
    store = await _open(postgres_db)
    issued = await store.issue()
    postgres_db.remember(issued.key_id)
    assert await store.grant(UNKNOWN_KEY_ID, "utility__convert_units") is False
    assert await store.grant(issued.key_id, "utility__*") is False
    await store.close()
    rows = await _query(
        postgres_db,
        "SELECT key_id, tool_name FROM gateway_key_permissions WHERE key_id = ANY(%s)",
        ( [UNKNOWN_KEY_ID, issued.key_id], ),
    )
    assert rows == []

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GATEWAY_DATABASE_URL", "GATEWAY_DB_PASSWORD"}
    }
    env["GATEWAY_DATABASE_URL"] = postgres_db.url
    completed = subprocess.run(
        [sys.executable, "-m", "apps.gateway.provision", "grant", UNKNOWN_KEY_ID, "utility__convert_units"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "No permission was granted." in completed.stderr
    assert "urk_" not in completed.stdout
    assert "urk_" not in completed.stderr
    remaining = await _query(
        postgres_db,
        "SELECT count(*) FROM gateway_key_permissions WHERE key_id = %s",
        (UNKNOWN_KEY_ID,),
    )
    assert remaining == [(0,)]


@pytest.mark.asyncio
async def test_closed_pool_rejects_later_operations(postgres_db: DisposableDatabase) -> None:
    store = await _open(postgres_db)
    issued = await store.issue()
    postgres_db.remember(issued.key_id)
    await store.close()
    assert store._pool is None
    with pytest.raises(StoreUnavailable, match="not open"):
        await store.list_permissions(issued.key_id)
    assert await store.ping() is False


@pytest.mark.asyncio
async def test_cleanup_deletes_only_recorded_keys(postgres_db: DisposableDatabase) -> None:
    store = await _open(postgres_db)
    keep = await store.issue()
    drop = await store.issue()
    postgres_db.remember(keep.key_id)
    postgres_db.remember(drop.key_id)
    assert await store.grant(keep.key_id, "utility__convert_units") is True
    assert await store.grant(drop.key_id, "support__search_notes") is True
    await store.close()

    limited = DisposableDatabase(postgres_db.url, postgres_db.container)
    limited.remember(drop.key_id)
    _delete_recorded_rows(limited)

    again = await _open(postgres_db)
    assert await again.list_permissions(keep.key_id) == ["utility__convert_units"]
    assert await again.get(drop.key_id) is None
    assert await again.list_permissions(drop.key_id) == []
    await again.close()
