"""Shared tool execution path.

HTTP clients and the dashboard chat both call execute_tool. The principal
must be a Principal sealed by Authenticator after the key hash matches.
A live key may call a tool only when that exact public name is stored for
that key id. A key with no grants is denied. gateway__read_status is not a
tool. There is no second execution implementation.
"""

import asyncio
import logging
import time
from typing import Any

from mcp.types import CallToolResult, TextContent

from apps.gateway.app.audit import Auditor, public_tool_name
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.keys import UNAUTHENTICATED, StoreUnavailable
from apps.gateway.app.permissions import is_tool_permission
from apps.gateway.app.registry import Registry

logger = logging.getLogger(__name__)


class ToolDispatcher:
    def __init__(self, registry: Registry, authenticator: Authenticator, auditor: Auditor) -> None:
        self.registry = registry
        self._authenticator = authenticator
        self._auditor = auditor

    async def execute_tool(
        self,
        principal: object,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> CallToolResult:
        """Run one namespaced tool for an authenticated principal.

        arguments are forwarded to the upstream and are not written to the audit log.
        """
        tool = public_tool_name(tool_name)
        try:
            decision = await self._authenticator.authorize(principal)
        except StoreUnavailable:
            logger.warning("Authorization check failed: StoreUnavailable")
            await self._auditor.record(UNAUTHENTICATED, tool, "unauthorized")
            return _auth_error()
        if not decision.active or decision.actor is None:
            actor = decision.actor if decision.actor is not None else UNAUTHENTICATED
            await self._auditor.record(actor, tool, "unauthorized", user_id=decision.user_id)
            return _auth_error()

        if not is_tool_permission(tool):
            await self._auditor.record(decision.actor, tool, "unauthorized", user_id=decision.user_id)
            return _error("Permission denied.")
        try:
            allowed = await self._authenticator.permits(decision.actor, tool)
        except StoreUnavailable:
            logger.warning("Permission check failed: StoreUnavailable")
            await self._auditor.record(decision.actor, tool, "unauthorized", user_id=decision.user_id)
            return _auth_error()
        if not allowed:
            await self._auditor.record(decision.actor, tool, "unauthorized", user_id=decision.user_id)
            return _error("Permission denied.")

        started = time.perf_counter()
        upstream_id, separator, _original = tool.partition("__")
        if not separator:
            upstream_id = ""
        try:
            result = await self.registry.call(tool, arguments or {})
        except (TimeoutError, asyncio.TimeoutError):
            await self._auditor.record(
                decision.actor,
                tool,
                "timeout",
                upstream_id=upstream_id,
                duration_ms=_elapsed_ms(started),
                user_id=decision.user_id,
            )
            return _error(f"Upstream timed out while calling {tool}.")
        except Exception as exc:
            await self._auditor.record(
                decision.actor,
                tool,
                "upstream_failure",
                upstream_id=upstream_id,
                duration_ms=_elapsed_ms(started),
                user_id=decision.user_id,
            )
            return _error(f"Upstream call failed for {tool}: {_error_name(exc)}.")

        outcome = "success" if not result.is_error else "error"
        await self._auditor.record(
            decision.actor,
            tool,
            outcome,
            upstream_id=upstream_id,
            duration_ms=_elapsed_ms(started),
            user_id=decision.user_id,
        )
        return result


def _auth_error() -> CallToolResult:
    return _error("Authentication required.")


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _elapsed_ms(started: float) -> int:
    return max(0, min(3_600_000, int((time.perf_counter() - started) * 1000)))


def _error_name(exc: BaseException) -> str:
    """Use the leaf exception type. Do not include its message; that can carry a URL."""
    current = exc
    for _ in range(8):
        nested = getattr(current, "exceptions", None)
        if not nested:
            break
        current = nested[0]
    return type(current).__name__
