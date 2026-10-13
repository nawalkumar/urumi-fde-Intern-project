"""AES-GCM credential sealing. These tests do not print key material."""

import pytest

from apps.gateway.app.catalog import merge_upstreams, persist_rotated_credentials
from apps.gateway.app.directory import UpstreamRecord
from apps.gateway.app.store import InMemoryStore
from apps.gateway.app.secretsbox import (
    CredentialKeyMissing,
    CredentialUnreadable,
    open_secret,
    seal_secret,
)

_KEY = "unit-test-upstream-key-32-characters"
_OTHER = "another-unit-test-upstream-key-value"
_SECRET = "upstream-credential-sentinel"


def test_round_trip_uses_a_fresh_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    first = seal_secret(_SECRET)
    second = seal_secret(_SECRET)
    assert first.startswith("v1.")
    assert first != second
    assert open_secret(first) == _SECRET
    assert open_secret(second) == _SECRET


def test_tampering_wrong_key_and_malformed_ciphertext_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    sealed = seal_secret(_SECRET)
    tampered = sealed[:-2] + ("A" if sealed[-1] != "A" else "B")
    with pytest.raises(CredentialUnreadable):
        open_secret(tampered)
    with pytest.raises(CredentialUnreadable):
        open_secret("v1.not-valid")
    with pytest.raises(CredentialUnreadable):
        open_secret("legacy-ciphertext")
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _OTHER)
    with pytest.raises(CredentialUnreadable):
        open_secret(sealed)


def test_missing_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    with pytest.raises(CredentialKeyMissing):
        seal_secret(_SECRET)
    with pytest.raises(CredentialKeyMissing):
        open_secret("v1.aaaa")


def test_previous_key_opens_old_ciphertext_until_it_is_sealed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    sealed = seal_secret(_SECRET)
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _OTHER)
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", _KEY)
    assert open_secret(sealed) == _SECRET
    resealed = seal_secret(_SECRET)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    assert open_secret(resealed) == _SECRET
    with pytest.raises(CredentialUnreadable):
        open_secret(sealed)


@pytest.mark.asyncio
async def test_rotation_persists_ciphertext_under_the_current_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    store = InMemoryStore()
    await store.save_upstream("notes", "https://mcp.example.test/mcp", True, seal_secret(_SECRET))
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _OTHER)
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", _KEY)
    await persist_rotated_credentials(store)
    stored = (await store.list_upstreams())[0].credential
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    assert open_secret(stored) == _SECRET
    merged = merge_upstreams(
        [],
        await store.list_upstreams(),
        {},
        timeout_seconds=1,
        allow_private=False,
    )
    assert merged[0].enabled is True
    assert merged[0].credential_unreadable is False
    assert merged[0].authorization == f"Bearer {_SECRET}"


@pytest.mark.asyncio
async def test_failed_rotation_keeps_the_upstream_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _OTHER)
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", _KEY)
    store = InMemoryStore()
    await store.save_upstream("notes", "https://mcp.example.test/mcp", True, "v1.not-valid")
    await persist_rotated_credentials(store)
    assert (await store.list_upstreams())[0].credential == "v1.not-valid"
    merged = merge_upstreams([], await store.list_upstreams(), {}, timeout_seconds=1, allow_private=False)
    assert merged[0].enabled is False
    assert merged[0].authorization == ""
    assert merged[0].credential_unreadable is True


@pytest.mark.asyncio
async def test_missing_key_does_not_call_with_an_empty_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    sealed = seal_secret(_SECRET)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY", raising=False)
    store = InMemoryStore()
    await store.save_upstream("notes", "https://mcp.example.test/mcp", True, sealed)
    await persist_rotated_credentials(store)
    assert (await store.list_upstreams())[0].credential == sealed
    merged = merge_upstreams([], await store.list_upstreams(), {}, timeout_seconds=1, allow_private=False)
    assert merged[0].enabled is False
    assert merged[0].authorization == ""


def test_unreadable_credential_disables_the_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _KEY)
    monkeypatch.delenv("GATEWAY_UPSTREAM_KEY_PREVIOUS", raising=False)
    sealed = seal_secret(_SECRET)
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _OTHER)
    merged = merge_upstreams(
        [],
        [UpstreamRecord("notes", "https://mcp.example.test/mcp", True, sealed)],
        {},
        timeout_seconds=1,
        allow_private=False,
    )
    assert merged[0].enabled is False
    assert merged[0].credential_unreadable is True
    assert merged[0].authorization == ""
    assert _SECRET not in repr(merged[0])
