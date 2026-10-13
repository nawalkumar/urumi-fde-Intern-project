"""Offline tests for provisioning file output. No database and no real key file in the repo."""

import asyncio
import os
import re
import stat
from pathlib import Path

import pytest

from apps.gateway.app.keys import hash_key
from apps.gateway.app.store import InMemoryStore
from apps.gateway.provision import _write_all, main

_KEY_ID = re.compile(r"^key_id: ([0-9a-f]{16})$", re.MULTILINE)


class OfflineStore(InMemoryStore):
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


class GrantFailStore(OfflineStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_new_grants = False

    async def grant(self, key_id: str, tool_name: str) -> bool:
        if self.fail_new_grants:
            return False
        return await super().grant(key_id, tool_name)


class RevokeFailStore(OfflineStore):
    async def revoke(self, key_id: str) -> bool:
        del key_id
        return False


def _install(monkeypatch: pytest.MonkeyPatch, store: OfflineStore) -> None:
    monkeypatch.setattr("apps.gateway.provision._store", lambda: store)


def _key_id(text: str) -> str:
    match = _KEY_ID.search(text)
    assert match is not None
    return match.group(1)


def _assert_secret_hidden(secret_text: str, stdout: str, stderr: str) -> None:
    secret = secret_text.split(".", 1)[1].strip()
    assert secret_text.strip() not in stdout
    assert secret_text.strip() not in stderr
    assert secret not in stdout
    assert secret not in stderr


def test_file_output_hides_the_secret_and_stores_only_the_hash(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    destination = tmp_path / "gateway.key"
    old_umask = os.umask(0)
    try:
        main(["issue", "--output", str(destination), "--grant", "utility__convert_units"])
    finally:
        os.umask(old_umask)
    captured = capsys.readouterr()
    assert destination.is_file()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    text = destination.read_text(encoding="utf-8")
    key_id = _key_id(captured.out)
    assert text == f"urk_{key_id}.{text.split('.', 1)[1]}"
    assert text.endswith("\n")
    _assert_secret_hidden(text, captured.out, captured.err)
    assert "gateway_api_key:" not in captured.out
    assert f"Granted utility__convert_units to {key_id}." in captured.out
    row = asyncio.run(store.get(key_id))
    assert row is not None
    assert row.revoked_at is None
    assert row.secret_hash == hash_key(text.strip())
    assert text.strip() not in row.secret_hash
    assert asyncio.run(store.list_permissions(key_id)) == ["utility__convert_units"]


def test_legacy_stdout_issue_still_prints_the_key(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    main(["issue"])
    captured = capsys.readouterr()
    assert "gateway_api_key: urk_" in captured.out
    assert "This key is shown once." in captured.out
    key_id = _key_id(captured.out)
    row = asyncio.run(store.get(key_id))
    assert row is not None
    printed = next(line.split(": ", 1)[1] for line in captured.out.splitlines() if line.startswith("gateway_api_key: "))
    assert row.secret_hash == hash_key(printed)
    assert printed not in row.secret_hash
    assert not any(tmp_path.iterdir())


def test_existing_file_is_rejected_without_issuing(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    destination = tmp_path / "gateway.key"
    destination.write_text("keep\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(destination)])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "Output path already exists.\n"
    assert destination.read_text(encoding="utf-8") == "keep\n"
    assert store._keys == {}
    assert "urk_" not in captured.out
    assert "urk_" not in captured.err


def test_symlink_is_rejected_without_following_it(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    target = tmp_path / "target"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(link)])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "symlink" in captured.err
    assert target.read_text(encoding="utf-8") == "keep\n"
    assert link.is_symlink()
    assert store._keys == {}
    assert "urk_" not in captured.out + captured.err


def test_repository_path_and_relative_path_are_rejected(monkeypatch, capsys, tmp_path) -> None:
    del tmp_path
    store = OfflineStore()
    _install(monkeypatch, store)
    inside = Path(__file__).resolve().parents[1] / "gateway-smoke-key-should-not-exist"
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(inside)])
    assert caught.value.code == 1
    assert not inside.exists()
    with pytest.raises(SystemExit) as relative:
        main(["issue", "--output", "gateway.key"])
    assert relative.value.code == 1
    captured = capsys.readouterr()
    assert "urk_" not in captured.out + captured.err
    assert store._keys == {}
    assert not Path("gateway.key").exists()


def test_write_failure_revokes_the_new_key_and_removes_the_file(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    destination = tmp_path / "gateway.key"

    def fail_write(fd: int, payload: bytes) -> None:
        del fd, payload
        raise OSError(0, "Could not write the key file.")

    monkeypatch.setattr("apps.gateway.provision._write_all", fail_write)
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(destination)])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "Could not write the key file." in captured.err
    assert "The new key was revoked and the new file was removed." in captured.err
    assert not destination.exists()
    assert store._keys
    assert all(row.revoked_at is not None for row in store._keys.values())
    blob = captured.out + captured.err
    for row in store._keys.values():
        assert row.secret_hash not in blob
    assert "gateway_api_key:" not in blob
    assert "urk_" not in blob


def test_grant_failure_revokes_only_the_new_key(monkeypatch, capsys, tmp_path) -> None:
    store = GrantFailStore()
    _install(monkeypatch, store)
    existing = asyncio.run(store.issue())
    existing_id = existing.key_id
    assert asyncio.run(store.grant(existing_id, "support__search_notes")) is True
    store.fail_new_grants = True
    destination = tmp_path / "gateway.key"
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(destination), "--grant", "utility__convert_units"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "No permission was granted." in captured.err
    assert "The new key was revoked and the new file was removed." in captured.err
    assert not destination.exists()
    kept = asyncio.run(store.get(existing_id))
    assert kept is not None
    assert kept.revoked_at is None
    assert asyncio.run(store.list_permissions(existing_id)) == ["support__search_notes"]
    others = [key_id for key_id in store._keys if key_id != existing_id]
    assert len(others) == 1
    assert asyncio.run(store.get(others[0])).revoked_at is not None
    assert asyncio.run(store.list_permissions(others[0])) == []
    assert existing.raw_key not in captured.out + captured.err
    assert "urk_" not in captured.out + captured.err


def test_revoke_cleanup_failure_is_not_reported_as_success(monkeypatch, capsys, tmp_path) -> None:
    store = RevokeFailStore()
    _install(monkeypatch, store)
    destination = tmp_path / "gateway.key"

    def fail_write(fd: int, payload: bytes) -> None:
        del fd, payload
        raise OSError(0, "Could not write the key file.")

    monkeypatch.setattr("apps.gateway.provision._write_all", fail_write)
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(destination)])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "Could not revoke the new key." in captured.err
    assert "The new file was removed." in captured.err
    assert "The new key was revoked and the new file was removed." not in captured.err
    assert not destination.exists()
    assert any(row.revoked_at is None for row in store._keys.values())
    assert "urk_" not in captured.out + captured.err


def test_wildcard_grant_does_not_issue_a_key(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    destination = tmp_path / "gateway.key"
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--output", str(destination), "--grant", "utility__*"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "No permission was granted.\n"
    assert not destination.exists()
    assert store._keys == {}


def test_grant_without_output_does_not_issue(monkeypatch, capsys) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    with pytest.raises(SystemExit) as caught:
        main(["issue", "--grant", "utility__convert_units"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "--grant requires --output." in captured.err
    assert store._keys == {}
    assert "urk_" not in captured.out + captured.err


def test_exact_grant_leaves_an_existing_key_unchanged(monkeypatch, capsys, tmp_path) -> None:
    store = OfflineStore()
    _install(monkeypatch, store)
    existing = asyncio.run(store.issue())
    assert asyncio.run(store.grant(existing.key_id, "support__search_notes")) is True
    destination = tmp_path / "gateway.key"
    main(["issue", "--grant", "utility__convert_units", "--output", str(destination)])
    captured = capsys.readouterr()
    new_id = _key_id(captured.out)
    assert new_id != existing.key_id
    assert asyncio.run(store.list_permissions(new_id)) == ["utility__convert_units"]
    assert asyncio.run(store.list_permissions(existing.key_id)) == ["support__search_notes"]
    assert asyncio.run(store.get(existing.key_id)).revoked_at is None
    text = destination.read_text(encoding="utf-8")
    _assert_secret_hidden(text, captured.out, captured.err)
    assert existing.raw_key not in captured.out + captured.err


def test_write_all_fsyncs_and_rejects_a_short_write(monkeypatch, tmp_path) -> None:
    del tmp_path
    calls: list[int] = []

    def record_fsync(fd: int) -> None:
        calls.append(fd)

    monkeypatch.setattr(os, "fsync", record_fsync)
    read_fd, write_fd = os.pipe()
    try:
        _write_all(write_fd, b"abc")
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 16) == b"abc"
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
    assert calls

    def short_write(fd: int, payload: bytes) -> int:
        del fd, payload
        return 0

    monkeypatch.setattr(os, "write", short_write)
    with pytest.raises(OSError):
        _write_all(1, b"abc")
