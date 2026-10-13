"""Authenticate Bearer tokens and re-check revocation for sealed principals."""

import hmac
from dataclasses import dataclass

from apps.gateway.app.keys import (
    Principal,
    StoreUnavailable,
    hash_key,
    parse_raw_key,
    reject_compare,
    seal_matches,
    seal_principal,
)
from apps.gateway.app.permissions import is_permission_name, is_tool_permission


@dataclass(frozen=True)
class Decision:
    """authorize result. actor is a stored key id, or None when the id must not be logged."""

    actor: str | None
    active: bool
    user_id: str = ""


class Authenticator:
    def __init__(self, store: object) -> None:
        self._store = store

    async def authenticate(self, raw_key: str | None) -> Principal | None:
        """Return a sealed principal only when the hash matches a live key.

        Missing, malformed, unknown, and revoked tokens all return None.
        """
        key_id = parse_raw_key(raw_key)
        if key_id is None or raw_key is None:
            reject_compare()
            return None
        presented = hash_key(raw_key)
        row = await self._store.get(key_id)  # type: ignore[attr-defined]
        if row is None:
            reject_compare(presented)
            return None
        if len(row.secret_hash) != len(presented) or not hmac.compare_digest(presented, row.secret_hash):
            return None
        if row.revoked_at is not None:
            return None
        if row.user_id:
            user = await self._store.get_user(row.user_id)  # type: ignore[attr-defined]
            if user is None or user.disabled_at is not None:
                return None
        return seal_principal(row.key_id)

    async def authorize(self, principal: object) -> Decision:
        """Confirm a sealed principal is still an active key.

        Raises StoreUnavailable when the store cannot be read.
        """
        if not isinstance(principal, Principal) or not seal_matches(principal):
            return Decision(actor=None, active=False)
        row = await self._store.get(principal.key_id)  # type: ignore[attr-defined]
        if row is None:
            return Decision(actor=None, active=False)
        if row.revoked_at is not None:
            return Decision(actor=row.key_id, active=False, user_id=row.user_id or "")
        if row.user_id:
            user = await self._store.get_user(row.user_id)  # type: ignore[attr-defined]
            if user is None or user.disabled_at is not None:
                return Decision(actor=row.key_id, active=False, user_id=row.user_id)
        return Decision(actor=row.key_id, active=True, user_id=row.user_id or "")

    async def permits(self, key_id: str, tool_name: str) -> bool:
        """Return whether an active key may call this exact public tool name.

        This does not authenticate the caller. execute_tool checks the sealed
        principal first. Raises StoreUnavailable when the store cannot be read.
        """
        if not is_permission_name(tool_name):
            return False
        return await self._store.permits(key_id, tool_name)  # type: ignore[attr-defined]

    async def visible_names(self, principal: object) -> set[str] | None:
        """Return this key's current exact grants, or None when it is not active.

        The store is read on every call. Raises StoreUnavailable when it cannot be read.
        """
        decision = await self.authorize(principal)
        if not decision.active or decision.actor is None:
            return None
        names = await self._store.list_permissions(decision.actor)  # type: ignore[attr-defined]
        return {name for name in names if is_tool_permission(name)}


__all__ = ["Authenticator", "Decision", "StoreUnavailable"]
