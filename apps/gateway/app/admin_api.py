"""Dashboard administration routes.

Sessions authorize these routes. A gateway API key does not. Tool execution
stays on the existing bearer-key path. Upstream credentials are not returned.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.gateway.app.audit import Auditor
from apps.gateway.app.catalog import merge_upstreams, refresh_registry
from apps.gateway.app.config import Settings, UpstreamConfig
from apps.gateway.app.directory import SESSION_MAX_AGE_SECONDS, UpstreamRecord, UserRecord
from apps.gateway.app.keys import StoreUnavailable, bearer_token
from apps.gateway.app.login_limit import LoginLimiter
from apps.gateway.app.passwords import generate_password, hash_password, verify_password
from apps.gateway.app.permissions import is_permission_name
from apps.gateway.app.secretsbox import CredentialKeyMissing, CredentialUnreadable, credential_key_configured, seal_secret
from apps.gateway.app.ssrf import UnsafeUpstreamUrl, validate_upstream_url

_USERNAME = re.compile(r"[a-z][a-z0-9_-]{2,31}")
_UPSTREAM_ID = re.compile(r"[a-z][a-z0-9_-]{0,62}")
_COOKIE = "urumi_session"
_FORBIDDEN = {"error": "forbidden", "error_description": "That action is not allowed."}
_UNAUTHORIZED = {"error": "invalid_token", "error_description": "Authentication required."}
_BAD = {"error": "invalid_request", "error_description": "The request was rejected."}
_THROTTLED = {"error": "invalid_token", "error_description": "Too many attempts. Try again later."}
logger = logging.getLogger(__name__)


def register_admin_routes(app: FastAPI, store: object, registry: object, settings: Settings) -> None:
    limiter = LoginLimiter()
    auditor = Auditor(store)

    @app.middleware("http")
    async def reject_cross_site_admin_posts(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/admin") and request.method == "POST" and not _same_site_request(request):
            return JSONResponse(_FORBIDDEN, status_code=403)
        return await call_next(request)

    @app.post("/admin/login")
    async def login(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return _bad()
        username = body.get("username")
        password = body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return _bad()
        presented = username.strip().casefold()
        if limiter.blocked(presented):
            return JSONResponse(_THROTTLED, status_code=429)
        try:
            user = await store.get_user_by_username(presented)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if user is None or user.disabled_at is not None or not verify_password(password, user.verifier):
            limiter.record_failure(presented)
            return JSONResponse(_UNAUTHORIZED, status_code=401)
        limiter.record_success(presented)
        try:
            raw = await store.create_session(user.user_id)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        response = JSONResponse(user.public())
        response.set_cookie(
            _COOKIE,
            raw,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
            max_age=SESSION_MAX_AGE_SECONDS,
        )
        return response

    @app.post("/admin/logout")
    async def logout(request: Request) -> JSONResponse:
        raw = _session_token(request)
        if raw:
            try:
                await store.revoke_session_token(raw)  # type: ignore[attr-defined]
            except StoreUnavailable:
                return _unavailable()
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(_COOKIE, path="/")
        return response

    @app.get("/admin/me")
    async def me(request: Request) -> JSONResponse:
        user = await _user(store, request)
        if isinstance(user, JSONResponse):
            return user
        return JSONResponse(user.public())

    @app.get("/admin/users")
    async def users(request: Request) -> JSONResponse:
        user = await _admin(store, request)
        if isinstance(user, JSONResponse):
            return user
        try:
            rows = await store.list_users()  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        return JSONResponse({"users": [row.public() for row in rows]})

    @app.post("/admin/users")
    async def create_user(request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await _body(request)
        if body is None:
            return _bad()
        username = body.get("username")
        role = body.get("role")
        if not isinstance(username, str) or not isinstance(role, str):
            return _bad()
        username = username.strip().casefold()
        if _USERNAME.fullmatch(username) is None or role not in {"admin", "member"}:
            return _bad()
        password = generate_password()
        try:
            created = await store.create_user(username, hash_password(password), role)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _bad()
        payload = created.public()
        payload["initial_password"] = password
        await _audit_mutation(auditor, actor, "admin:create_user", created.user_id)
        return JSONResponse(payload, status_code=201)

    @app.post("/admin/users/{user_id}/disable")
    async def disable_user(user_id: str, request: Request) -> JSONResponse:
        return await _set_disabled(store, request, user_id, True, auditor)

    @app.post("/admin/users/{user_id}/enable")
    async def enable_user(user_id: str, request: Request) -> JSONResponse:
        return await _set_disabled(store, request, user_id, False, auditor)

    @app.post("/admin/users/{user_id}/role")
    async def set_role(user_id: str, request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await _body(request)
        if body is None or not isinstance(body.get("role"), str):
            return _bad()
        try:
            changed = await store.set_user_role(user_id, body["role"])  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if not changed:
            return _bad()
        await _audit_mutation(auditor, actor, "admin:set_role", f"{user_id} {body['role']}")
        return JSONResponse({"status": "ok"})

    @app.get("/admin/keys")
    async def keys(request: Request) -> JSONResponse:
        user = await _user(store, request)
        if isinstance(user, JSONResponse):
            return user
        owner = None if user.role == "admin" else user.user_id
        return await _key_list(store, owner)

    @app.post("/admin/keys")
    async def issue_key(request: Request) -> JSONResponse:
        actor = await _user(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await _body(request)
        target_id = _key_owner(actor, body)
        if isinstance(target_id, JSONResponse):
            return target_id
        try:
            issued = await store.issue_for_user(target_id)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _bad()
        await _audit_mutation(auditor, actor, "admin:issue_key", issued.key_id)
        return JSONResponse(
            {"key_id": issued.key_id, "api_key": issued.raw_key, "user_id": target_id},
            status_code=201,
        )

    @app.post("/admin/keys/{key_id}/revoke")
    async def revoke_key(key_id: str, request: Request) -> JSONResponse:
        user = await _user(store, request)
        if isinstance(user, JSONResponse):
            return user
        if not await _owns_key(store, user, key_id):
            return JSONResponse(_FORBIDDEN, status_code=403)
        try:
            changed = await store.revoke(key_id)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if not changed:
            return _bad()
        await _audit_mutation(auditor, user, "admin:revoke_key", key_id)
        return JSONResponse({"status": "ok"})

    @app.post("/admin/keys/{key_id}/permissions")
    async def grant_permission(key_id: str, request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await _body(request)
        if body is None or not is_permission_name(body.get("tool_name")):
            return _bad()
        try:
            changed = await store.grant(key_id, body["tool_name"])  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if not changed:
            return _bad()
        await _audit_mutation(auditor, actor, "admin:grant_permission", f"{key_id} {body['tool_name']}")
        return JSONResponse({"status": "ok"})

    @app.post("/admin/keys/{key_id}/permissions/revoke")
    async def revoke_permission(key_id: str, request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await _body(request)
        if body is None or not isinstance(body.get("tool_name"), str):
            return _bad()
        try:
            changed = await store.revoke_permission(key_id, body["tool_name"])  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if not changed:
            return _bad()
        await _audit_mutation(auditor, actor, "admin:revoke_permission", f"{key_id} {body['tool_name']}")
        return JSONResponse({"status": "ok"})

    @app.get("/admin/upstreams")
    async def upstreams(request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        try:
            await refresh_registry(registry, store, settings)
            await registry.list_tools_result()  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        except Exception:
            logger.warning("Upstream listing failed: %s", "Exception")
        dynamic = {row.upstream_id: row for row in await store.list_upstreams()}  # type: ignore[attr-defined]
        report = registry.status_report()  # type: ignore[attr-defined]
        listed = []
        for item in registry.upstreams:  # type: ignore[attr-defined]
            state = next((row for row in report["upstreams"] if row["id"] == item.name), None)
            row = dynamic.get(item.name)
            listed.append(
                {
                    "id": item.name,
                    "url": item.url,
                    "enabled": item.enabled,
                    "source": "dynamic" if row is not None else "static",
                    "has_credential": row.has_credential if row is not None else bool(item.authorization),
                    "credential_readable": not item.credential_unreadable,
                    "listing_state": None if state is None else state["listing_state"],
                    "advertised_tool_count": None if state is None else state["advertised_tool_count"],
                    "tools": registry.registered_names(item.name),  # type: ignore[attr-defined]
                }
            )
        return JSONResponse({"upstreams": listed, "diagnostics": report["diagnostics"]})

    @app.post("/admin/upstreams")
    async def create_upstream(request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        parsed = await _upstream_body(request, settings)
        if isinstance(parsed, JSONResponse):
            return parsed
        upstream_id, url, enabled, credential = parsed
        if any(item.name == upstream_id for item in settings.upstreams):
            return _bad()
        try:
            sealed = _seal(credential)
        except (CredentialKeyMissing, CredentialUnreadable):
            return _bad()
        record = UpstreamRecord(upstream_id, url, enabled, sealed)
        try:
            await store.save_upstream(upstream_id, url, enabled, sealed)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _bad()

        async def undo_create() -> None:
            await store.delete_upstream(upstream_id)  # type: ignore[attr-defined]

        failed = await _refresh_committed(
            registry,
            store,
            settings,
            auditor,
            actor,
            "admin:create_upstream",
            upstream_id,
            undo_create,
            _SavedUpstream("save", upstream_id, enabled, record),
        )
        if failed is not None:
            return failed
        await _audit_mutation(auditor, actor, "admin:create_upstream", upstream_id, upstream_id=upstream_id)
        return JSONResponse({"id": upstream_id, "enabled": enabled, "has_credential": bool(credential)}, status_code=201)

    @app.post("/admin/upstreams/{upstream_id}")
    async def update_upstream(upstream_id: str, request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        if _UPSTREAM_ID.fullmatch(upstream_id) is None or "__" in upstream_id:
            return _bad()
        body = await _body(request)
        if body is None or not isinstance(body.get("enabled"), bool):
            return _bad()
        static = any(item.name == upstream_id for item in settings.upstreams)
        action = "admin:update_upstream"
        if "url" not in body and "credential" not in body:
            action = "admin:enable_upstream" if body["enabled"] else "admin:disable_upstream"
        if static:
            try:
                flags = await store.list_upstream_flags()  # type: ignore[attr-defined]
                await store.set_upstream_flag(upstream_id, body["enabled"])  # type: ignore[attr-defined]
            except StoreUnavailable:
                return _bad()
            had_flag = upstream_id in flags
            prior_flag = flags.get(upstream_id, False)

            async def undo_flag() -> None:
                if had_flag:
                    await store.set_upstream_flag(upstream_id, prior_flag)  # type: ignore[attr-defined]
                else:
                    await store.delete_upstream_flag(upstream_id)  # type: ignore[attr-defined]

            change = _SavedUpstream("flag", upstream_id, body["enabled"], None)
            undo: Callable[[], Awaitable[None]] = undo_flag
        else:
            try:
                current = next((row for row in await store.list_upstreams() if row.upstream_id == upstream_id), None)  # type: ignore[attr-defined]
            except StoreUnavailable:
                return _bad()
            if current is None:
                return _bad()
            url = current.url
            credential = current.credential
            if "url" in body:
                checked = _check_url(body.get("url"), settings)
                if isinstance(checked, JSONResponse):
                    return checked
                url = checked
            if "credential" in body:
                if body["credential"] is None:
                    credential = ""
                elif not isinstance(body["credential"], str) or not body["credential"].strip():
                    return _bad()
                else:
                    try:
                        credential = _seal(body["credential"].strip())
                    except (CredentialKeyMissing, CredentialUnreadable):
                        return _bad()
            try:
                await store.save_upstream(upstream_id, url, body["enabled"], credential)  # type: ignore[attr-defined]
            except StoreUnavailable:
                return _bad()
            saved = UpstreamRecord(upstream_id, url, body["enabled"], credential)

            async def undo_save() -> None:
                await store.save_upstream(current.upstream_id, current.url, current.enabled, current.credential)  # type: ignore[attr-defined]

            change = _SavedUpstream("save", upstream_id, body["enabled"], saved)
            undo = undo_save
        failed = await _refresh_committed(
            registry, store, settings, auditor, actor, action, upstream_id, undo, change
        )
        if failed is not None:
            return failed
        await _audit_mutation(auditor, actor, action, upstream_id, upstream_id=upstream_id)
        return JSONResponse({"status": "ok"})

    @app.post("/admin/upstreams/{upstream_id}/delete")
    async def delete_upstream(upstream_id: str, request: Request) -> JSONResponse:
        actor = await _admin(store, request)
        if isinstance(actor, JSONResponse):
            return actor
        if any(item.name == upstream_id for item in settings.upstreams):
            return _bad()
        try:
            current = next((row for row in await store.list_upstreams() if row.upstream_id == upstream_id), None)  # type: ignore[attr-defined]
            deleted = await store.delete_upstream(upstream_id)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        if current is None or not deleted:
            return _bad()

        async def undo_delete() -> None:
            await store.save_upstream(current.upstream_id, current.url, current.enabled, current.credential)  # type: ignore[attr-defined]

        failed = await _refresh_committed(
            registry,
            store,
            settings,
            auditor,
            actor,
            "admin:delete_upstream",
            upstream_id,
            undo_delete,
            _SavedUpstream("delete", upstream_id, None, current),
        )
        if failed is not None:
            return failed
        await _audit_mutation(auditor, actor, "admin:delete_upstream", upstream_id, upstream_id=upstream_id)
        return JSONResponse({"status": "ok"})

    @app.get("/admin/audit")
    async def audit(request: Request) -> JSONResponse:
        user = await _user(store, request)
        if isinstance(user, JSONResponse):
            return user
        owner = None if user.role == "admin" else user.user_id
        try:
            rows = await store.list_audit(limit=100, user_id=owner)  # type: ignore[attr-defined]
        except StoreUnavailable:
            return _unavailable()
        return JSONResponse({"entries": [_audit_public(row) for row in rows]})


class _SavedUpstream:
    """The upstream write that must be undone or copied into the registry."""

    def __init__(
        self,
        kind: str,
        upstream_id: str,
        enabled: bool | None,
        record: UpstreamRecord | None,
    ) -> None:
        self.kind = kind
        self.upstream_id = upstream_id
        self.enabled = enabled
        self.record = record


async def _refresh_committed(
    registry: object,
    store: object,
    settings: Settings,
    auditor: Auditor,
    actor: UserRecord,
    action: str,
    upstream_id: str,
    undo: Callable[[], Awaitable[None]],
    change: _SavedUpstream,
) -> JSONResponse | None:
    """Refresh after a committed write. Undo it when refresh fails.

    A successful undo leaves both the database and the running registry as they
    were. When the undo fails, the saved change stays and is recorded as an
    error, not a success. The registry is then aligned with that saved change
    when doing so does not require another full refresh.
    """
    snapshot = registry.snapshot()  # type: ignore[attr-defined]
    try:
        await refresh_registry(registry, store, settings)
    except Exception as exc:
        logger.warning("Upstream registry refresh failed: %s", type(exc).__name__)
        registry.restore(snapshot)  # type: ignore[attr-defined]
        try:
            await undo()
        except Exception as undo_exc:
            logger.warning("Persisted upstream change could not be restored: %s", type(undo_exc).__name__)
            updated = _reconcile_saved_change(registry, snapshot, settings, change)
            await auditor.record(
                actor.user_id,
                action,
                "error",
                upstream_id=upstream_id,
                user_id=actor.user_id,
                target=upstream_id,
            )
            return _refresh_failed(
                persisted=True,
                registry_updated=updated,
                upstream_id=upstream_id,
                enabled=change.enabled,
                deleted=change.kind == "delete",
            )
        return _refresh_failed(
            persisted=False,
            registry_updated=False,
            upstream_id=upstream_id,
            enabled=None,
            deleted=False,
        )
    return None


def _reconcile_saved_change(
    registry: object,
    snapshot: tuple[object, ...],
    settings: Settings,
    change: _SavedUpstream,
) -> bool:
    """Copy one saved upstream into the restored registry. Return whether it stuck."""
    registry.restore(snapshot)  # type: ignore[attr-defined]
    try:
        if change.kind == "flag":
            registry.set_enabled(change.upstream_id, bool(change.enabled))  # type: ignore[attr-defined]
            return any(
                item.name == change.upstream_id and item.enabled is change.enabled
                for item in registry.upstreams  # type: ignore[attr-defined]
            )
        previous = [item for item in snapshot[0] if getattr(item, "name", None) != change.upstream_id]  # type: ignore[union-attr]
        if change.kind == "save" and change.record is not None:
            previous.append(_dynamic_config(change.record, settings))
        registry.configure(previous)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Upstream registry reconcile failed: %s", type(exc).__name__)
        registry.restore(snapshot)  # type: ignore[attr-defined]
        return False
    if change.kind == "delete":
        return all(item.name != change.upstream_id for item in registry.upstreams)  # type: ignore[attr-defined]
    return any(item.name == change.upstream_id for item in registry.upstreams)  # type: ignore[attr-defined]


def _dynamic_config(record: UpstreamRecord, settings: Settings) -> UpstreamConfig:
    return merge_upstreams(
        [],
        [record],
        {},
        timeout_seconds=settings.upstream_timeout_seconds,
        allow_private=settings.allow_private_upstreams,
        trust_environment=False,
    )[0]


def _refresh_failed(
    *,
    persisted: bool,
    registry_updated: bool,
    upstream_id: str,
    enabled: bool | None,
    deleted: bool,
) -> JSONResponse:
    if persisted and registry_updated:
        description = (
            "The upstream change was saved, but the registry could not be refreshed from storage. "
            "The running registry was updated to match the saved change."
        )
    elif persisted:
        description = (
            "The upstream change was saved, but the registry could not be refreshed "
            "and still has the previous configuration."
        )
    else:
        description = "The upstream change was not kept because the registry could not be refreshed."
    body: dict[str, object] = {
        "error": "registry_refresh_failed",
        "error_description": description,
        "persisted": persisted,
    }
    if persisted:
        body["registry_updated"] = registry_updated
        body["id"] = upstream_id
        body["enabled"] = enabled
        body["deleted"] = deleted
    return JSONResponse(body, status_code=500 if persisted else 503)


async def _audit_mutation(
    auditor: Auditor,
    actor: UserRecord,
    action: str,
    target: str,
    *,
    upstream_id: str = "",
) -> None:
    """Record one successful admin change. The caller has already committed it."""
    await auditor.record(
        actor.user_id,
        action,
        "success",
        upstream_id=upstream_id,
        user_id=actor.user_id,
        target=target,
    )


def _audit_public(row: object) -> dict[str, object]:
    occurred = getattr(row, "occurred_at")
    stamp = occurred.isoformat() if isinstance(occurred, datetime) else ""
    return {
        "actor": getattr(row, "actor"),
        "user_id": getattr(row, "user_id") or None,
        "upstream_id": getattr(row, "upstream_id") or None,
        "tool_name": getattr(row, "tool_name"),
        "outcome": getattr(row, "outcome"),
        "occurred_at": stamp,
        "duration_ms": getattr(row, "duration_ms"),
        "target": getattr(row, "target", "") or None,
    }


async def _set_disabled(
    store: object,
    request: Request,
    user_id: str,
    disabled: bool,
    auditor: Auditor | None = None,
) -> JSONResponse:
    actor = await _admin(store, request)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        changed = await store.set_user_disabled(user_id, disabled)  # type: ignore[attr-defined]
    except StoreUnavailable:
        return _unavailable()
    if not changed:
        return _bad()
    if auditor is not None:
        action = "admin:disable_user" if disabled else "admin:enable_user"
        await _audit_mutation(auditor, actor, action, user_id)
    return JSONResponse({"status": "ok"})


async def _key_list(store: object, user_id: str | None) -> JSONResponse:
    try:
        rows = await store.list_key_rows(user_id)  # type: ignore[attr-defined]
        payload = []
        for row in rows:
            payload.append(
                {
                    "key_id": row.key_id,
                    "user_id": row.user_id,
                    "revoked": row.revoked_at is not None,
                    "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else "",
                    "permissions": await store.list_permissions(row.key_id),  # type: ignore[attr-defined]
                }
            )
    except StoreUnavailable:
        return _unavailable()
    return JSONResponse({"keys": payload})


def _key_owner(actor: UserRecord, body: dict | None) -> str | JSONResponse:
    """Admins name the owner. Members can issue a key only for themselves."""
    if actor.role == "admin":
        if body is None or not isinstance(body.get("user_id"), str) or body["user_id"] == "":
            return _bad()
        return body["user_id"]
    if body is not None and "user_id" in body:
        supplied = body.get("user_id")
        if not isinstance(supplied, str):
            return _bad()
        if supplied != actor.user_id:
            return JSONResponse(_FORBIDDEN, status_code=403)
    return actor.user_id


async def _owns_key(store: object, user: UserRecord, key_id: str) -> bool:
    if user.role == "admin":
        return True
    try:
        rows = await store.list_key_rows(user.user_id)  # type: ignore[attr-defined]
    except StoreUnavailable:
        return False
    return any(row.key_id == key_id and row.revoked_at is None for row in rows)


async def _upstream_body(request: Request, settings: Settings) -> tuple[str, str, bool, str] | JSONResponse:
    body = await _body(request)
    if body is None:
        return _bad()
    upstream_id = body.get("id")
    if not isinstance(upstream_id, str) or _UPSTREAM_ID.fullmatch(upstream_id) is None or "__" in upstream_id:
        return _bad()
    checked = _check_url(body.get("url"), settings)
    if isinstance(checked, JSONResponse):
        return checked
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        enabled = True
    credential = body.get("credential") or ""
    if not isinstance(credential, str):
        return _bad()
    return upstream_id, checked, enabled, credential.strip()


def _check_url(value: object, settings: Settings) -> str | JSONResponse:
    if not isinstance(value, str):
        return _bad()
    try:
        return validate_upstream_url(value, allow_private=settings.allow_private_upstreams)
    except UnsafeUpstreamUrl:
        return _bad()


def _same_site_request(request: Request) -> bool:
    """Cookie-authenticated writes must come from the dashboard, not another site.

    Browsers will not add this header on a cross-site form post. They also send
    Sec-Fetch-Site on their own requests. A missing fetch-site header is allowed
    so the local proxy and tests can call the gateway.
    """
    if request.headers.get("x-urumi-request") != "1":
        return False
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.strip().casefold() == "cross-site":
        return False
    return True


def _seal(credential: str) -> str:
    if not credential:
        return ""
    if not credential_key_configured():
        raise CredentialKeyMissing("Upstream credential storage is not configured.")
    return seal_secret(credential)


def _session_token(request: Request) -> str | None:
    cookie = request.cookies.get(_COOKIE)
    if cookie:
        return cookie
    token = bearer_token(request.headers.get("authorization"))
    if token and token.startswith("urs_"):
        return token
    return None


async def _user(store: object, request: Request) -> UserRecord | JSONResponse:
    raw = _session_token(request)
    if not raw:
        return JSONResponse(_UNAUTHORIZED, status_code=401)
    try:
        user = await store.authenticate_session(raw)  # type: ignore[attr-defined]
    except StoreUnavailable:
        return _unavailable()
    if user is None:
        return JSONResponse(_UNAUTHORIZED, status_code=401)
    return user


async def _admin(store: object, request: Request) -> UserRecord | JSONResponse:
    user = await _user(store, request)
    if isinstance(user, JSONResponse):
        return user
    if user.role != "admin":
        return JSONResponse(_FORBIDDEN, status_code=403)
    return user


async def _body(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _bad() -> JSONResponse:
    return JSONResponse(_BAD, status_code=400)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"error": "unavailable", "error_description": "Authentication is unavailable."},
        status_code=503,
    )
