"""Policy-aware tool discovery and lexical ranking.

Authorization is decided before ranking. A score never adds a grant. There is
no cross-request cache: each call reads the current grants and registry.
The MCP tools/list request has no query field, so search is a separate route.
Search failure returns no tools.
"""

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcp.types import ListToolsResult, Tool

from apps.gateway.app.audit import public_tool_name
from apps.gateway.app.auth import Authenticator
from apps.gateway.app.keys import Principal, StoreUnavailable
from apps.gateway.app.permissions import is_tool_permission
from apps.gateway.app.stores.model import RESERVED_UPSTREAM
from apps.gateway.app.stores.routing import store_block_reason

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 200
MAX_RESULTS = 20
_TOKEN = re.compile(r"[a-z0-9_]{1,64}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass
class RankedTool:
    name: str
    description: str
    score: int
    matched: list[str]


@dataclass
class DiscoveryResult:
    tools: list[Tool] = field(default_factory=list)
    ranked: list[RankedTool] = field(default_factory=list)
    correlation_id: str = ""
    included: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    error: str = ""


class DiscoveryState:
    """Last discovery counts for the operator view. Tool names are not stored."""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None

    def note(self, event: dict[str, Any]) -> None:
        self.last = event


def correlation_id(presented: str | None) -> str:
    if isinstance(presented, str) and _REQUEST_ID.fullmatch(presented):
        return presented
    return secrets.token_hex(8)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


async def collect_authorized(
    registry: object,
    authenticator: Authenticator,
    store: object,
    principal: Principal | None,
) -> tuple[list[Tool], dict[str, int], str]:
    """Return tools this principal may see, exclusion counts, and an error code.

    Counts name categories, not tool names. Unauthorized names are not returned.
    """
    excluded: dict[str, int] = {}
    try:
        granted = await authenticator.visible_names(principal)
    except StoreUnavailable:
        return [], {"store_unavailable": 1}, "unavailable"
    if not granted:
        return [], {"unauthorized": 0}, ""
    try:
        listed = await registry.list_tools_result()  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Discovery listing failed: %s", type(exc).__name__)
        return [], {"listing_failed": 1}, "listing_failed"
    visible: list[Tool] = []
    for tool in listed.tools:
        name = tool.name
        if not is_tool_permission(name):
            _bump(excluded, "invalid_name")
            continue
        if name not in granted:
            _bump(excluded, "unauthorized")
            continue
        try:
            reason = await store_block_reason(store, name)
        except StoreUnavailable:
            return [], {"store_unavailable": 1}, "unavailable"
        if reason is not None:
            _bump(excluded, reason)
            continue
        visible.append(tool)
    return visible, excluded, ""


def rank_tools(tools: list[Tool], query: str) -> list[RankedTool]:
    """Deterministic lexical rank. Empty query keeps name order and score 0."""
    folded = query.casefold()
    tokens = _TOKEN.findall(folded)[:12]
    ranked: list[RankedTool] = []
    for tool in tools:
        name = tool.name.casefold()
        description = (tool.description or "").casefold()
        upstream = name.split("__", 1)[0]
        score = 0
        matched: list[str] = []
        if not tokens:
            ranked.append(RankedTool(tool.name, tool.description or "", 0, ["name_order"]))
            continue
        if folded == name:
            score += 100
            matched.append("exact_name")
        for token in tokens:
            if token == upstream:
                score += 30
                matched.append("upstream")
            elif token in name:
                score += 20
                matched.append("name")
            elif token in description:
                score += 10
                matched.append("description")
        if score:
            ranked.append(RankedTool(tool.name, tool.description or "", score, sorted(set(matched))))
    ranked.sort(key=lambda item: (-item.score, item.name))
    return ranked[:MAX_RESULTS]


def validate_query(value: object) -> str | None:
    """Return a safe error code, or None when the query may be ranked."""
    if not isinstance(value, str):
        return "malformed"
    if len(value) > MAX_QUERY_LENGTH:
        return "query_too_long"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "malformed"
    return None


async def discover(
    registry: object,
    authenticator: Authenticator,
    store: object,
    principal: Principal | None,
    *,
    query: str | None,
    correlation: str,
    state: DiscoveryState | None,
) -> DiscoveryResult:
    tools, excluded, error = await collect_authorized(registry, authenticator, store, principal)
    result = DiscoveryResult(
        tools=tools,
        correlation_id=correlation,
        included=len(tools),
        excluded=excluded,
        error=error,
    )
    if error:
        _note(state, result, query)
        return result
    if query is None:
        result.tools = tools
        _note(state, result, query)
        return result
    problem = validate_query(query)
    if problem:
        result.tools = []
        result.ranked = []
        result.included = 0
        result.error = problem
        _note(state, result, query)
        return result
    result.ranked = rank_tools(tools, query)
    result.tools = [tool for tool in tools if tool.name in {item.name for item in result.ranked}]
    result.included = len(result.ranked)
    _note(state, result, query)
    return result


def _note(state: DiscoveryState | None, result: DiscoveryResult, query: str | None) -> None:
    digest = ""
    if isinstance(query, str) and query:
        digest = hashlib.sha256(query.encode("utf-8", errors="replace")).hexdigest()[:12]
    event = {
        "correlation_id": result.correlation_id,
        "included": result.included,
        "excluded": dict(result.excluded),
        "error": result.error,
        "query_length": len(query) if isinstance(query, str) else 0,
        "query_digest": digest,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "discovery correlation=%s included=%s excluded=%s error=%s query_length=%s",
        event["correlation_id"],
        event["included"],
        sum(event["excluded"].values()),
        event["error"] or "none",
        event["query_length"],
    )
    if state is not None:
        state.note(event)


def client_search_body(result: DiscoveryResult) -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": public_tool_name(item.name),
                "description": public_tool_name(item.description),
                "score": item.score,
                "matched": item.matched,
            }
            for item in result.ranked
            if public_tool_name(item.name)
        ],
        "correlation_id": result.correlation_id,
    }


async def operator_summary(registry: object, store: object, state: DiscoveryState | None) -> dict[str, Any]:
    """Counts and collision records for an administrator. No URLs or secrets."""
    report = registry.status_report()  # type: ignore[attr-defined]
    candidates = 0
    advertised = 0
    excluded = {"collision": 0, "disabled_upstream": 0, "listing_failed": 0}
    upstreams: list[dict[str, Any]] = []
    for row in report.get("upstreams", []):
        advertised_count = row.get("advertised_tool_count")
        excluded_count = row.get("excluded_tool_count")
        if isinstance(advertised_count, int):
            advertised += advertised_count
            candidates += advertised_count
        if isinstance(excluded_count, int):
            excluded["collision"] += excluded_count
            candidates += excluded_count
        if row.get("listing_state") == "disabled":
            excluded["disabled_upstream"] += 1
        if row.get("listing_state") == "listing_failed":
            excluded["listing_failed"] += 1
        upstreams.append(
            {
                "id": row.get("id"),
                "enabled": row.get("enabled"),
                "listing_state": row.get("listing_state"),
                "advertised_tool_count": advertised_count,
                "excluded_tool_count": excluded_count,
            }
        )
    registrations: list[dict[str, Any]] = []
    try:
        rows = await store.list_upstreams()  # type: ignore[attr-defined]
    except Exception:
        rows = []
    for row in rows:
        if RESERVED_UPSTREAM.fullmatch(row.upstream_id) is None:
            continue
        from apps.gateway.app.stores.manifests import _MCP_URL

        registrations.append(
            {
                "upstream_id": row.upstream_id,
                "enabled": row.enabled,
                "registration": "canonical" if _MCP_URL.fullmatch(row.url) else "not_canonical",
            }
        )
    return {
        "candidate_count": candidates,
        "advertised_count": advertised,
        "excluded": excluded,
        "collisions": registry.collision_records(),  # type: ignore[attr-defined]
        "upstreams": upstreams,
        "store_registrations": registrations,
        "last": None if state is None else state.last,
    }


def list_result(tools: list[Tool]) -> ListToolsResult:
    return ListToolsResult(tools=tools)
