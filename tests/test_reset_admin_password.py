"""Offline tests for resetting one administrator password. No database connection."""

import asyncio
import os
import stat
from datetime import datetime, timezone

import pytest

from apps.gateway.app.directory import UserRecord
from apps.gateway.app.keys import KeyRow
from apps.gateway.app.passwords import hash_password, verify_password
from apps.gateway.app.store import AuditRow, InMemoryStore
from apps.gateway.provision import main

ADMIN_ID = "36e2af3487daa142"
MEMBER_ID = "aaaaaaaaaaaaaaaa"
OTHER_ID = "bbbbbbbbbbbbbbbb"
ADMIN_USERNAME = "urumi_admin"
MEMBER_USERNAME = "member_one"
ADMIN_PASSWORD = "old-admin-password-value"
MEMBER_PASSWORD = "member-password-value"


class OfflineStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.schema_requested: bool | None = None

    async def open(self, ensure_schema: bool = True) -> None:
        self.schema_requested = ensure_schema

    async def close(self) -> None:
        return None


def _install(monkeypatch: pytest.MonkeyPatch, store: OfflineStore) -> None:
    monkeypatch.setattr("apps.gateway.provision._store", lambda: store)


def _plant(
    store: OfflineStore,
    user_id: str,
    username: str,
    role: str,
    password: str,
) -> str:
    created = datetime.now(timezone.utc)
    verifier = hash_password(password)
    store._users[user_id] = UserRecord(user_id, username, role, created, None, verifier)
    store._users_by_name[username] = user_id
    return verifier


def _directory(store: OfflineStore) -> tuple[str, str, KeyRow, list[AuditRow]]:
    _plant(store, ADMIN_ID, ADMIN_USERNAME, "admin", ADMIN_PASSWORD)
    _plant(store, MEMBER_ID, MEMBER_USERNAME, "member", MEMBER_PASSWORD)
    admin_session = asyncio.run(store.create_session(ADMIN_ID))
    member_session = asyncio.run(store.create_session(MEMBER_ID))
    issued = asyncio.run(store.issue_for_user(MEMBER_ID))
    key = asyncio.run(store.get(issued.key_id))
    assert key is not None
    occurred = datetime.now(timezone.utc)
    store.audit_log.append(AuditRow("628d977d51bf1645", "utility__convert_units", "unauthorized", occurred))
    return admin_session, member_session, key, list(store.audit_log)


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


def _hidden(secret: str, captured) -> None:
    assert secret not in captured.out
    assert secret not in captured.err


def test_reset_replaces_only_the_admin_verifier(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    admin_session, member_session, key, audit_rows = _directory(store)
    destination = tmp_path / "admin.password"
    old_umask = os.umask(0)
    try:
        main(_args(ADMIN_ID, ADMIN_USERNAME, destination))
    finally:
        os.umask(old_umask)
    captured = capsys.readouterr()
    assert captured.out == (
        "Gateway administrator password reset.\n"
        f"username: {ADMIN_USERNAME}\n"
        f"user_id: {ADMIN_ID}\n"
        "The new password was written to the output file and is not printed.\n"
    )
    assert captured.err == ""
    assert store.schema_requested is False
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    password = destination.read_text(encoding="utf-8")
    assert password.endswith("\n")
    _hidden(password, captured)
    admin = asyncio.run(store.get_user(ADMIN_ID))
    member = asyncio.run(store.get_user(MEMBER_ID))
    assert admin is not None and member is not None
    assert admin.user_id == ADMIN_ID
    assert admin.username == ADMIN_USERNAME
    assert admin.role == "admin"
    assert admin.disabled_at is None
    assert verify_password(password.strip(), admin.verifier)
    assert not verify_password(ADMIN_PASSWORD, admin.verifier)
    _hidden(admin.verifier, captured)
    assert verify_password(MEMBER_PASSWORD, member.verifier)
    assert member.role == "member"
    assert asyncio.run(store.authenticate_session(admin_session)) is None
    assert asyncio.run(store.authenticate_session(member_session)) is not None
    kept = asyncio.run(store.get(key.key_id))
    assert kept is not None
    assert kept.secret_hash == key.secret_hash
    assert kept.user_id == MEMBER_ID
    assert store.audit_log == audit_rows


def test_incorrect_user_id_changes_nothing(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    admin_session, member_session, key, audit_rows = _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        main(_args(OTHER_ID, ADMIN_USERNAME, destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not reset the administrator password.\n"
    assert not destination.exists()
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None
    assert admin.user_id == ADMIN_ID
    assert admin.verifier == before.verifier
    assert asyncio.run(store.authenticate_session(admin_session)) is not None
    assert asyncio.run(store.authenticate_session(member_session)) is not None
    assert store.audit_log == audit_rows
    kept = asyncio.run(store.get(key.key_id))
    assert kept is not None and kept.secret_hash == key.secret_hash


def test_incorrect_username_changes_nothing(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    admin_session, _, _, _ = _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        main(_args(ADMIN_ID, "other_admin", destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "Could not reset the administrator password.\n"
    assert captured.out == ""
    assert not destination.exists()
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None and admin.verifier == before.verifier
    assert asyncio.run(store.authenticate_session(admin_session)) is not None


def test_disabled_admin_is_rejected(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    _directory(store)
    assert asyncio.run(store.set_user_disabled(ADMIN_ID, True))
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None and before.disabled_at is not None
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        main(_args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "Could not reset the administrator password.\n"
    assert not destination.exists()
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None
    assert admin.user_id == ADMIN_ID
    assert admin.role == "admin"
    assert admin.disabled_at == before.disabled_at
    assert admin.verifier == before.verifier
    member = asyncio.run(store.get_user(MEMBER_ID))
    assert member is not None and member.role == "member"


def test_member_is_rejected(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    _, member_session, _, _ = _directory(store)
    before = asyncio.run(store.get_user(MEMBER_ID))
    assert before is not None
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        main(_args(MEMBER_ID, MEMBER_USERNAME, destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "Could not reset the administrator password.\n"
    assert not destination.exists()
    member = asyncio.run(store.get_user(MEMBER_ID))
    assert member is not None
    assert member.user_id == MEMBER_ID
    assert member.role == "member"
    assert member.verifier == before.verifier
    assert asyncio.run(store.authenticate_session(member_session)) is not None


def test_existing_output_file_is_rejected_before_opening_the_store(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    calls = {"count": 0}

    def factory() -> OfflineStore:
        calls["count"] += 1
        return store

    monkeypatch.setattr("apps.gateway.provision._store", factory)
    _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None
    destination = tmp_path / "admin.password"
    destination.write_text("keep\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(_args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "Output path already exists.\n"
    assert captured.out == ""
    assert calls["count"] == 0
    assert destination.read_text(encoding="utf-8") == "keep\n"
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None and admin.verifier == before.verifier


def test_output_write_failure_rolls_back_and_removes_the_file(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    admin_session, member_session, key, audit_rows = _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None

    def fail_write(fd: int, payload: bytes) -> None:
        del fd, payload
        raise OSError(0, "Could not write the key file.")

    monkeypatch.setattr("apps.gateway.provision._write_all", fail_write)
    destination = tmp_path / "admin.password"
    with pytest.raises(SystemExit) as caught:
        main(_args(ADMIN_ID, ADMIN_USERNAME, destination))
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Could not write the password file.\n"
        "The password change was rolled back.\n"
        "The new password file was removed.\n"
    )
    assert not destination.exists()
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None
    assert admin.user_id == ADMIN_ID
    assert admin.verifier == before.verifier
    assert verify_password(ADMIN_PASSWORD, admin.verifier)
    assert asyncio.run(store.authenticate_session(admin_session)) is not None
    assert asyncio.run(store.authenticate_session(member_session)) is not None
    kept = asyncio.run(store.get(key.key_id))
    assert kept is not None and kept.secret_hash == key.secret_hash
    assert store.audit_log == audit_rows


def test_interrupt_during_write_removes_the_file_and_restores_the_account(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    admin_session, member_session, _, _ = _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None

    def interrupt(fd: int, payload: bytes) -> None:
        del fd, payload
        raise KeyboardInterrupt()

    monkeypatch.setattr("apps.gateway.provision._write_all", interrupt)
    destination = tmp_path / "admin.password"
    with pytest.raises(KeyboardInterrupt):
        main(_args(ADMIN_ID, ADMIN_USERNAME, destination))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Could not write the password file.\n"
        "The password change was rolled back.\n"
        "The new password file was removed.\n"
    )
    assert not destination.exists()
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None
    assert admin.user_id == ADMIN_ID
    assert admin.verifier == before.verifier
    assert asyncio.run(store.authenticate_session(admin_session)) is not None
    assert asyncio.run(store.authenticate_session(member_session)) is not None


def test_transaction_rollback_restores_the_verifier_and_only_marks_target_sessions() -> None:
    store = OfflineStore()
    admin_session, member_session, key, _ = _directory(store)
    before = asyncio.run(store.get_user(ADMIN_ID))
    assert before is not None

    async def attempt() -> None:
        async with store.reset_admin_password(ADMIN_ID, ADMIN_USERNAME, hash_password("replacement-password-value")):
            assert await store.authenticate_session(admin_session) is None
            assert await store.authenticate_session(member_session) is not None
            raise OSError(0, "Could not write the key file.")

    with pytest.raises(OSError):
        asyncio.run(attempt())
    admin = asyncio.run(store.get_user(ADMIN_ID))
    assert admin is not None
    assert admin.user_id == ADMIN_ID
    assert admin.verifier == before.verifier
    assert verify_password(ADMIN_PASSWORD, admin.verifier)
    assert asyncio.run(store.authenticate_session(admin_session)) is not None
    assert asyncio.run(store.authenticate_session(member_session)) is not None
    kept = asyncio.run(store.get(key.key_id))
    assert kept is not None and kept.secret_hash == key.secret_hash
