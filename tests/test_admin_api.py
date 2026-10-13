"""Administration routes, revocation, and audit attribution. In-memory store only."""

import json

import httpx2
import pytest

from apps.gateway.app.auth import Authenticator
from apps.gateway.app.directory import SessionRow
from apps.gateway.app.keys import seal_principal
from apps.gateway.app.login_limit import LoginLimiter
from apps.gateway.app.config import Settings, UpstreamConfig
from apps.gateway.app.dispatch import ToolDispatcher
from apps.gateway.app.main import create_app
from apps.gateway.app.passwords import hash_password
from apps.gateway.app.catalog import refresh_registry as real_refresh
from apps.gateway.app.registry import GatewayConfigError, Registry
from apps.gateway.app.audit import Auditor
from apps.gateway.app.ssrf import validate_upstream_url
from apps.gateway.app.store import InMemoryStore
from apps.utility_mcp.app.main import create_app as create_utility
from tests.mcp_session import open_mcp

_SECRET = "upstream-credential-sentinel"
_UPSTREAM_KEY = "unit-test-upstream-key-32-characters"


def _allow(url: str, *, allow_private: bool) -> str:
    if url.startswith("https://mcp.example.test"):
        return url
    return validate_upstream_url(url, allow_private=allow_private)


async def _admin_store() -> tuple[InMemoryStore, str, str]:
    store = InMemoryStore()
    admin_password = "admin-password-value"
    member_password = "member-password-value"
    await store.create_user("ada", hash_password(admin_password), "admin")
    await store.create_user("bea", hash_password(member_password), "member")
    return store, admin_password, member_password


def _client(base: str) -> httpx2.Client:
    return httpx2.Client(base_url=base, timeout=10, headers={"X-Urumi-Request": "1"})


@pytest.mark.asyncio
async def test_admin_session_manages_members_keys_and_upstreams(launch_app, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _UPSTREAM_KEY)
    monkeypatch.setattr("apps.gateway.app.admin_api.validate_upstream_url", _allow)
    store, admin_password, member_password = await _admin_store()
    base = launch_app(create_app(Settings(upstreams=[]), store=store))
    with _client(base) as admin:
        denied = admin.get("/admin/users", headers={"Authorization": "Bearer urk_0011223344556677.not-a-real-secret"})
        assert denied.status_code == 401
        login = admin.post("/admin/login", json={"username": "ada", "password": admin_password})
        assert login.status_code == 200
        assert "verifier" not in login.text
        created = admin.post("/admin/users", json={"username": "cam", "role": "member"})
        assert created.status_code == 201
        assert "initial_password" in created.json()
        listed = admin.get("/admin/users")
        assert "initial_password" not in listed.text
        assert "verifier" not in listed.text
        member_id = next(row["user_id"] for row in listed.json()["users"] if row["username"] == "bea")
        issued = admin.post("/admin/keys", json={"user_id": member_id})
        assert issued.status_code == 201
        raw_key = issued.json()["api_key"]
        key_id = issued.json()["key_id"]
        assert raw_key not in admin.get("/admin/keys").text
        granted = admin.post(f"/admin/keys/{key_id}/permissions", json={"tool_name": "utility__convert_units"})
        assert granted.status_code == 200
        upstream = admin.post(
            "/admin/upstreams",
            json={"id": "notes", "url": "https://mcp.example.test/mcp", "enabled": False, "credential": _SECRET},
        )
        assert upstream.status_code == 201
        catalog = admin.get("/admin/upstreams")
        body = catalog.text
        assert _SECRET not in body
        assert "credential_ciphertext" not in body
        saved = next(row for row in catalog.json()["upstreams"] if row["id"] == "notes")
        assert saved["has_credential"] is True
        assert saved["credential_readable"] is True
        assert saved["url"] == "https://mcp.example.test/mcp"
        blocked = admin.post("/admin/upstreams", json={"id": "local", "url": "http://127.0.0.1/mcp", "enabled": False})
        assert blocked.status_code == 400
        admin.post("/admin/logout")
        assert admin.get("/admin/me").status_code == 401

    with _client(base) as member:
        signed_in = member.post("/admin/login", json={"username": "bea", "password": member_password})
        assert signed_in.status_code == 200
        assert member.get("/admin/users").status_code == 403
        assert member.post("/admin/upstreams", json={"id": "extra", "url": "https://mcp.example.test/mcp"}).status_code == 403
        assert member.post(f"/admin/keys/{key_id}/permissions", json={"tool_name": "utility__ping"}).status_code == 403
        own = member.get("/admin/keys").json()["keys"]
        assert [row["key_id"] for row in own] == [key_id]
        assert member.post(f"/admin/keys/{key_id}/revoke").status_code == 200
        authenticator = Authenticator(store)
        assert await authenticator.authenticate(raw_key) is None


@pytest.mark.asyncio
async def test_revoked_and_disabled_callers_cannot_use_tools(launch_app) -> None:
    utility = launch_app(create_utility())
    store = InMemoryStore()
    user = await store.create_user("bea", hash_password("member-password-value"), "member")
    issued = await store.issue_for_user(user.user_id)
    assert await store.grant(issued.key_id, "utility__convert_units") is True
    other = await store.issue_for_user(user.user_id)
    assert await store.grant(other.key_id, "utility__convert_units") is True
    base = launch_app(
        create_app(
            Settings(upstreams=[UpstreamConfig("utility", f"{utility}/mcp", 5, trusted=True)]),
            store=store,
        )
    )
    async with open_mcp(f"{base}/mcp", issued.raw_key) as client:
        visible = await client.list_tools()
        assert [tool.name for tool in visible.tools] == ["utility__convert_units"]
        called = await client.call_tool("utility__convert_units", {"value": 2, "from_unit": "km", "to_unit": "m"})
    assert called.is_error is False
    row = store.audit_log[-1]
    assert row.actor == issued.key_id
    assert row.user_id == user.user_id
    assert row.upstream_id == "utility"
    assert row.tool_name == "utility__convert_units"
    assert row.outcome == "success"
    assert isinstance(row.duration_ms, int) and row.duration_ms >= 0
    assert issued.raw_key not in repr(row)
    assert await store.revoke(issued.key_id) is True
    authenticator = Authenticator(store)
    assert await authenticator.authenticate(issued.raw_key) is None
    unknown = authenticator.authenticate("urk_0011223344556677." + ("a" * 32))
    assert await unknown is None
    await store.set_user_disabled(user.user_id, True)
    assert await authenticator.authenticate(other.raw_key) is None
    sealed = seal_principal(other.key_id)
    decision = await authenticator.authorize(sealed)
    assert decision.active is False
    registry = Registry([UpstreamConfig("utility", f"{utility}/mcp", 5)])
    dispatcher = ToolDispatcher(registry, authenticator, Auditor(store))
    denied = await dispatcher.execute_tool(sealed, "utility__convert_units", {"value": 1, "from_unit": "m", "to_unit": "m"})
    assert denied.is_error is True
    visible = await authenticator.visible_names(sealed)
    assert visible is None
    with httpx2.Client(timeout=5) as client:
        rejected = client.post(
            f"{base}/mcp",
            headers={"Authorization": f"Bearer {other.raw_key}"},
            content=b"{}",
        )
    assert rejected.status_code == 401
    assert "disabled" not in rejected.text
    assert other.raw_key not in rejected.text
    assert await store.grant(other.key_id, "utility__*") is False


@pytest.mark.asyncio
async def test_audit_is_limited_to_the_signed_in_member(launch_app) -> None:
    store, _admin_password, member_password = await _admin_store()
    admin = await store.get_user_by_username("ada")
    member = await store.get_user_by_username("bea")
    assert admin is not None and member is not None
    await store.add_audit("aaaaaaaaaaaaaaaa", "utility__convert_units", "success", _stamp(), "utility", 4, admin.user_id)
    await store.add_audit("bbbbbbbbbbbbbbbb", "utility__convert_units", "error", _stamp(), "utility", 9, member.user_id)
    base = launch_app(create_app(Settings(upstreams=[]), store=store))
    with _client(base) as client:
        client.post("/admin/login", json={"username": "bea", "password": member_password})
        entries = client.get("/admin/audit").json()["entries"]
    assert [row["user_id"] for row in entries] == [member.user_id]
    assert entries[0]["duration_ms"] == 9
    assert entries[0]["upstream_id"] == "utility"
    blob = json.dumps(entries)
    assert "password" not in blob


@pytest.mark.asyncio
async def test_login_throttle_csrf_expiry_and_disabled_session(launch_app) -> None:
    store, admin_password, member_password = await _admin_store()
    base = launch_app(create_app(Settings(upstreams=[]), store=store))
    with _client(base) as client:
        for _ in range(8):
            failed = client.post("/admin/login", json={"username": "ada", "password": "wrong-password"})
            assert failed.status_code == 401
            assert "ada" not in failed.text
        locked = client.post("/admin/login", json={"username": "ada", "password": admin_password})
        assert locked.status_code == 429
        still_open = client.post("/admin/login", json={"username": "bea", "password": member_password})
        assert still_open.status_code == 200
        bare = httpx2.Client(base_url=base, timeout=10)
        bare.cookies = client.cookies
        assert bare.post("/admin/logout").status_code == 403
        crossed = client.post("/admin/logout", headers={"Sec-Fetch-Site": "cross-site"})
        assert crossed.status_code == 403
        assert client.get("/admin/me").status_code == 200
        now = _stamp()
        for session_id, row in list(store._sessions.items()):
            store._sessions[session_id] = SessionRow(
                row.session_id,
                row.secret_hash,
                row.user_id,
                row.created_at,
                now,
                row.revoked_at,
            )
        assert client.get("/admin/me").status_code == 401
    member = await store.get_user_by_username("bea")
    assert member is not None
    await store.set_user_disabled(member.user_id, False)
    with _client(base) as client:
        signed_in = client.post("/admin/login", json={"username": "bea", "password": member_password})
        assert signed_in.status_code == 200
        await store.set_user_disabled(member.user_id, True)
        assert client.get("/admin/me").status_code == 401
        disabled = client.post("/admin/login", json={"username": "bea", "password": member_password})
        missing = client.post("/admin/login", json={"username": "no-such-user", "password": member_password})
    assert disabled.status_code == 401
    assert missing.status_code == 401
    assert disabled.json() == missing.json()


@pytest.mark.asyncio
async def test_successful_admin_mutations_are_audited(launch_app, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _UPSTREAM_KEY)
    monkeypatch.setattr("apps.gateway.app.admin_api.validate_upstream_url", _allow)
    store, admin_password, member_password = await _admin_store()
    utility = UpstreamConfig("utility", "http://utility.example/mcp", 5, trusted=True)
    base = launch_app(create_app(Settings(upstreams=[utility]), store=store))
    member = await store.get_user_by_username("bea")
    admin = await store.get_user_by_username("ada")
    assert member is not None and admin is not None

    with _client(base) as client:
        assert client.post("/admin/users/not-a-user/role", json={"role": "member"}).status_code == 401
        signed_in = client.post("/admin/login", json={"username": "ada", "password": admin_password})
        assert signed_in.status_code == 200

    with _client(base) as member_client:
        assert member_client.post("/admin/login", json={"username": "bea", "password": member_password}).status_code == 200
        forbidden = member_client.post(f"/admin/users/{admin.user_id}/role", json={"role": "member"})
        assert forbidden.status_code == 403

    with _client(base) as client:
        assert client.post("/admin/login", json={"username": "ada", "password": admin_password}).status_code == 200
        rejected = client.post("/admin/users/not-a-user/role", json={"role": "member"})
        assert rejected.status_code == 400
        assert client.post(f"/admin/users/{member.user_id}/disable").status_code == 200
        assert client.post(f"/admin/users/{member.user_id}/enable").status_code == 200
        assert client.post(f"/admin/keys/{'0' * 16}/revoke").status_code == 400
        assert client.post("/admin/keys/not-a-key/permissions", json={"tool_name": "utility__convert_units"}).status_code == 400
        role = client.post(f"/admin/users/{member.user_id}/role", json={"role": "admin"})
        assert role.status_code == 200
        issued = client.post("/admin/keys", json={"user_id": member.user_id})
        assert issued.status_code == 201
        raw_key = issued.json()["api_key"]
        key_id = issued.json()["key_id"]
        granted = client.post(
            f"/admin/keys/{key_id}/permissions",
            json={"tool_name": "utility__convert_units"},
        )
        assert granted.status_code == 200
        removed = client.post(
            f"/admin/keys/{key_id}/permissions/revoke",
            json={"tool_name": "utility__convert_units"},
        )
        assert removed.status_code == 200
        revoked = client.post(f"/admin/keys/{key_id}/revoke")
        assert revoked.status_code == 200
        disabled = client.post("/admin/upstreams/utility", json={"enabled": False})
        assert disabled.status_code == 200
        created = client.post(
            "/admin/upstreams",
            json={"id": "notes", "url": "https://mcp.example.test/mcp", "enabled": True, "credential": _SECRET},
        )
        assert created.status_code == 201
        edited = client.post(
            "/admin/upstreams/notes",
            json={"enabled": True, "url": "https://mcp.example.test/other"},
        )
        assert edited.status_code == 200
        deleted = client.post("/admin/upstreams/notes/delete")
        assert deleted.status_code == 200
        enabled = client.post("/admin/upstreams/utility", json={"enabled": True})
        assert enabled.status_code == 200
        entries = client.get("/admin/audit").json()["entries"]

    successes = [row for row in store.audit_log if row.outcome == "success" and row.tool_name.startswith("admin:")]
    observed = {(row.tool_name, row.target, row.upstream_id, row.actor, row.user_id) for row in successes}
    assert ("admin:disable_user", member.user_id, "", admin.user_id, admin.user_id) in observed
    assert ("admin:enable_user", member.user_id, "", admin.user_id, admin.user_id) in observed
    assert ("admin:set_role", f"{member.user_id} admin", "", admin.user_id, admin.user_id) in observed
    assert ("admin:issue_key", key_id, "", admin.user_id, admin.user_id) in observed
    assert ("admin:grant_permission", f"{key_id} utility__convert_units", "", admin.user_id, admin.user_id) in observed
    assert ("admin:revoke_permission", f"{key_id} utility__convert_units", "", admin.user_id, admin.user_id) in observed
    assert ("admin:revoke_key", key_id, "", admin.user_id, admin.user_id) in observed
    assert ("admin:disable_upstream", "utility", "utility", admin.user_id, admin.user_id) in observed
    assert ("admin:enable_upstream", "utility", "utility", admin.user_id, admin.user_id) in observed
    assert ("admin:create_upstream", "notes", "notes", admin.user_id, admin.user_id) in observed
    assert ("admin:update_upstream", "notes", "notes", admin.user_id, admin.user_id) in observed
    assert ("admin:delete_upstream", "notes", "notes", admin.user_id, admin.user_id) in observed
    assert not any(row.tool_name == "admin:set_role" and row.target.startswith("not-a-user") for row in store.audit_log)
    assert not any(row.tool_name == "admin:revoke_key" and row.target == "0" * 16 for row in store.audit_log)
    assert sum(1 for row in store.audit_log if row.tool_name == "admin:set_role" and row.outcome == "success") == 1
    blob = "\n".join(repr(row) for row in store.audit_log)
    assert raw_key not in blob
    assert _SECRET not in blob
    assert "https://mcp.example.test" not in blob
    assert admin_password not in blob
    assert member_password not in blob
    public = json.dumps(entries)
    assert raw_key not in public
    assert _SECRET not in public
    assert any(row["tool_name"] == "admin:revoke_key" and row["target"] == key_id for row in entries)


def _upstream(registry: Registry, name: str) -> UpstreamConfig:
    return next(item for item in registry.upstreams if item.name == name)


@pytest.mark.asyncio
async def test_failed_registry_refresh_is_not_reported_as_success(launch_app, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_UPSTREAM_KEY", _UPSTREAM_KEY)
    monkeypatch.setattr("apps.gateway.app.admin_api.validate_upstream_url", _allow)
    store, admin_password, _member_password = await _admin_store()
    utility = UpstreamConfig("utility", "http://utility.example/mcp", 5, trusted=True)
    app = create_app(Settings(upstreams=[utility]), store=store)
    base = launch_app(app)
    registry = app.state.registry
    assert isinstance(registry, Registry)
    before = [item.name for item in registry.upstreams]
    with pytest.raises(GatewayConfigError):
        registry.configure([UpstreamConfig("bad__id", "http://127.0.0.1:9/mcp", 1)])
    assert [item.name for item in registry.upstreams] == before
    failing = {"on": False}

    async def refresh(registry_arg: object, store_arg: object, settings: Settings) -> None:
        if failing["on"]:
            raise GatewayConfigError("refresh failed")
        await real_refresh(registry_arg, store_arg, settings)

    monkeypatch.setattr("apps.gateway.app.admin_api.refresh_registry", refresh)
    admin = await store.get_user_by_username("ada")
    assert admin is not None

    with _client(base) as client:
        assert client.post("/admin/login", json={"username": "ada", "password": admin_password}).status_code == 200
        rejected = client.post("/admin/upstreams", json={"id": "bad__id", "url": "https://mcp.example.test/mcp"})
        assert rejected.status_code == 400
        assert store.audit_log == []

        failing["on"] = True
        denied = client.post("/admin/upstreams/utility", json={"enabled": False})
        assert denied.status_code == 503
        assert denied.json() == {
            "error": "registry_refresh_failed",
            "error_description": "The upstream change was not kept because the registry could not be refreshed.",
            "persisted": False,
        }
        assert await store.list_upstream_flags() == {}
        assert _upstream(registry, "utility").enabled is True
        assert store.audit_log == []

        failing["on"] = False
        disabled = client.post("/admin/upstreams/utility", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json() == {"status": "ok"}
        assert await store.list_upstream_flags() == {"utility": False}
        assert _upstream(registry, "utility").enabled is False

        failing["on"] = True
        kept_disabled = client.post("/admin/upstreams/utility", json={"enabled": True})
        assert kept_disabled.status_code == 503
        assert kept_disabled.json()["persisted"] is False
        assert await store.list_upstream_flags() == {"utility": False}
        assert _upstream(registry, "utility").enabled is False

        failing["on"] = False
        created = client.post(
            "/admin/upstreams",
            json={"id": "notes", "url": "https://mcp.example.test/mcp", "enabled": True},
        )
        assert created.status_code == 201
        assert _upstream(registry, "notes").url == "https://mcp.example.test/mcp"

        failing["on"] = True
        edited = client.post(
            "/admin/upstreams/notes",
            json={"enabled": False, "url": "https://mcp.example.test/other"},
        )
        assert edited.status_code == 503
        assert edited.json()["persisted"] is False
        saved = next(row for row in await store.list_upstreams() if row.upstream_id == "notes")
        assert saved.url == "https://mcp.example.test/mcp"
        assert saved.enabled is True
        assert _upstream(registry, "notes").url == "https://mcp.example.test/mcp"
        assert _upstream(registry, "notes").enabled is True

        removed = client.post("/admin/upstreams/notes/delete")
        assert removed.status_code == 503
        assert removed.json()["persisted"] is False
        assert any(row.upstream_id == "notes" for row in await store.list_upstreams())
        assert _upstream(registry, "notes").name == "notes"

        original_save = store.save_upstream

        async def save_once(upstream_id: str, url: str, enabled: bool, credential: str) -> None:
            store.save_upstream = original_save
            await original_save(upstream_id, url, enabled, credential)
            store.save_upstream = refuse_save

        async def refuse_save(upstream_id: str, url: str, enabled: bool, credential: str) -> None:
            del upstream_id, url, enabled, credential
            raise RuntimeError("undo failed")

        store.save_upstream = save_once
        stuck = client.post(
            "/admin/upstreams/notes",
            json={"enabled": True, "url": "https://mcp.example.test/stuck"},
        )
        assert stuck.status_code == 500
        assert stuck.json()["persisted"] is True
        assert stuck.json()["registry_updated"] is True
        assert stuck.json()["deleted"] is False
        assert stuck.json()["id"] == "notes"
        assert stuck.json()["enabled"] is True
        stuck_row = next(row for row in await store.list_upstreams() if row.upstream_id == "notes")
        assert stuck_row.url == "https://mcp.example.test/stuck"
        assert _upstream(registry, "notes").url == "https://mcp.example.test/stuck"

        store.save_upstream = refuse_save

        async def refuse_delete(upstream_id: str) -> bool:
            del upstream_id
            raise RuntimeError("undo failed")

        deleted = client.post("/admin/upstreams/notes/delete")
        assert deleted.status_code == 500
        assert deleted.json()["persisted"] is True
        assert deleted.json()["deleted"] is True
        assert deleted.json()["registry_updated"] is True
        assert all(row.upstream_id != "notes" for row in await store.list_upstreams())
        assert all(item.name != "notes" for item in registry.upstreams)

        store.save_upstream = original_save
        store.delete_upstream = refuse_delete  # type: ignore[method-assign]
        created_stuck = client.post(
            "/admin/upstreams",
            json={"id": "extra", "url": "https://mcp.example.test/mcp", "enabled": True, "credential": _SECRET},
        )
        assert created_stuck.status_code == 500
        assert created_stuck.json()["persisted"] is True
        assert created_stuck.json()["registry_updated"] is True
        assert created_stuck.json()["enabled"] is True
        assert any(row.upstream_id == "extra" for row in await store.list_upstreams())
        assert _upstream(registry, "extra").enabled is True
        assert _SECRET not in created_stuck.text

    successes = [row for row in store.audit_log if row.outcome == "success" and row.tool_name.startswith("admin:")]
    assert [(row.tool_name, row.target) for row in successes] == [
        ("admin:disable_upstream", "utility"),
        ("admin:create_upstream", "notes"),
    ]
    failures = [row for row in store.audit_log if row.outcome == "error"]
    assert [(row.tool_name, row.target, row.upstream_id, row.actor, row.user_id) for row in failures] == [
        ("admin:update_upstream", "notes", "notes", admin.user_id, admin.user_id),
        ("admin:delete_upstream", "notes", "notes", admin.user_id, admin.user_id),
        ("admin:create_upstream", "extra", "extra", admin.user_id, admin.user_id),
    ]
    assert _SECRET not in "\n".join(repr(row) for row in store.audit_log)


def test_login_limit_expires_after_the_window() -> None:
    now = {"value": 0.0}

    def clock() -> float:
        return now["value"]

    limiter = LoginLimiter(limit=2, window_seconds=10, clock=clock)
    limiter.record_failure("ada")
    limiter.record_failure("ada")
    assert limiter.blocked("ada") is True
    now["value"] = 11
    assert limiter.blocked("ada") is False


def _stamp():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
