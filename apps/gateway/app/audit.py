"""Minimal audit log for tool dispatch, authentication failures, and admin mutations.

Authentication failures are stored as actor "unauthenticated". The presented
token, key id, and hash are not written, so an unauthenticated caller cannot
choose the actor. Tool rows are written only after Authenticator has accepted
a sealed principal. Admin rows are written only after the mutation succeeds.
Arguments, credentials, passwords, and upstream bodies are not fields on an audit row.
"""

import logging
from datetime import datetime, timezone

from apps.gateway.app.keys import KEY_ID_PATTERN, UNAUTHENTICATED

logger = logging.getLogger(__name__)

OUTCOMES = frozenset({"success", "error", "timeout", "upstream_failure", "unauthorized"})


def public_tool_name(value: object) -> str:
    """Bound the tool name that may be stored. Drop values that look like credentials."""
    return _bounded(value, 128)


def audit_target(value: object) -> str:
    """Bound an admin target. Key ids and permission names fit. Secrets do not."""
    return _bounded(value, 160)


def _bounded(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(character for character in value if 32 <= ord(character) < 127)[:limit]
    lowered = cleaned.casefold()
    if "urk_" in lowered or "bearer " in lowered or "ck_" in lowered or "cs_" in lowered:
        return "redacted"
    return cleaned


class Auditor:
    def __init__(self, store: object) -> None:
        self._store = store

    async def record(
        self,
        actor: str,
        tool_name: str,
        outcome: str,
        *,
        upstream_id: str = "",
        duration_ms: int | None = None,
        user_id: str = "",
        target: str = "",
    ) -> None:
        safe_actor = actor if actor == UNAUTHENTICATED or KEY_ID_PATTERN.fullmatch(actor) else UNAUTHENTICATED
        safe_tool = public_tool_name(tool_name)
        safe_outcome = outcome if outcome in OUTCOMES else "error"
        safe_upstream = public_tool_name(upstream_id)[:64]
        safe_user = user_id if KEY_ID_PATTERN.fullmatch(user_id) else ""
        safe_duration = duration_ms if isinstance(duration_ms, int) and 0 <= duration_ms <= 3_600_000 else None
        safe_target = audit_target(target)
        try:
            await self._store.add_audit(  # type: ignore[attr-defined]
                safe_actor,
                safe_tool,
                safe_outcome,
                datetime.now(timezone.utc),
                safe_upstream,
                safe_duration,
                safe_user,
                safe_target,
            )
        except Exception as exc:
            logger.warning("Audit write failed: %s", type(exc).__name__)
