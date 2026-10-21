"""Connect to upstream MCP servers, namespace their tools, and isolate failures.

Every public tool name is `{upstream_id}__{original_tool_name}`. Upstream ids
must be unique. A repeated public name is left out of discovery and is never
called. Other tools are still listed and called. Disabled upstreams are skipped
and are not contacted.

Collision state is derived from the latest successful registration of each
enabled upstream. A failed list keeps that upstream's previous registration,
so a public name does not become unique merely because one participant could
not be listed. Discovery still omits an upstream whose latest list failed.
Diagnostics are replaced from that same state on every refresh.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from apps.gateway.app.config import UpstreamConfig
from apps.gateway.app.secretsbox import CredentialUnreadable
from apps.gateway.app.ssrf import UnsafeUpstreamUrl, destination_addresses, validate_upstream_url

logger = logging.getLogger(__name__)


class GatewayConfigError(Exception):
    """The upstream configuration cannot be registered safely."""


@dataclass(frozen=True)
class Diagnostic:
    """One safe status record for a future operator dashboard.

    Messages name the upstream or public tool. They do not include URLs,
    secrets, or tool arguments.
    """

    code: str
    upstream_id: str
    tool_name: str
    message: str


class Registry:
    def __init__(self, upstreams: list[UpstreamConfig]) -> None:
        self.upstreams = _unique_upstreams(upstreams)
        self._registrations: dict[str, list[Tool]] = {}
        self._list_failed: set[str] = set()
        self._collisions: set[str] = set()
        self._collision_upstreams: dict[str, list[str]] = {}
        self.diagnostics: list[Diagnostic] = []

    def snapshot(self) -> tuple[object, ...]:
        """Copy the running configuration so a failed refresh can put it back."""
        return (
            list(self.upstreams),
            {name: list(tools) for name, tools in self._registrations.items()},
            set(self._list_failed),
            set(self._collisions),
            {name: list(owners) for name, owners in self._collision_upstreams.items()},
            list(self.diagnostics),
        )

    def restore(self, snapshot: tuple[object, ...]) -> None:
        """Replace the running configuration with a snapshot from this process."""
        upstreams, registrations, failed, collisions, collision_upstreams, diagnostics = snapshot
        self.upstreams = list(upstreams)  # type: ignore[arg-type]
        self._registrations = {name: list(tools) for name, tools in registrations.items()}  # type: ignore[attr-defined]
        self._list_failed = set(failed)  # type: ignore[arg-type]
        self._collisions = set(collisions)  # type: ignore[arg-type]
        self._collision_upstreams = {name: list(owners) for name, owners in collision_upstreams.items()}  # type: ignore[attr-defined]
        self.diagnostics = list(diagnostics)  # type: ignore[arg-type]

    def configure(self, upstreams: list[UpstreamConfig]) -> None:
        """Replace the upstream list. Drop cached tools when the target changes.

        A failure leaves the previous configuration in place.
        """
        snapshot = self.snapshot()
        try:
            self._configure(upstreams)
        except Exception:
            self.restore(snapshot)
            raise

    def _configure(self, upstreams: list[UpstreamConfig]) -> None:
        previous = {item.name: item for item in self.upstreams}
        updated = _unique_upstreams(upstreams)
        current = {item.name for item in updated}
        for name in list(self._registrations):
            if name not in current:
                self._registrations.pop(name, None)
                self._list_failed.discard(name)
        for item in updated:
            old = previous.get(item.name)
            if old is None or old.url != item.url or old.authorization != item.authorization:
                self._registrations.pop(item.name, None)
                self._list_failed.discard(item.name)
        self.upstreams = updated
        self._refresh_derived_state()

    def registered_names(self, upstream_id: str) -> list[str]:
        tools = self._registrations.get(upstream_id, [])
        return sorted(tool.name for tool in tools if tool.name not in self._collisions)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Change the enabled flag for every upstream with this name.

        List and call both read the flag. Disabling drops that upstream's
        registration so it no longer participates in collisions, and the
        diagnostics are rebuilt from the flags that apply now.
        """
        self.upstreams = [
            replace(upstream, enabled=enabled) if upstream.name == name else upstream
            for upstream in self.upstreams
        ]
        if not enabled:
            self._registrations.pop(name, None)
            self._list_failed.discard(name)
        self._refresh_derived_state()

    def _enabled(self) -> list[UpstreamConfig]:
        return [upstream for upstream in self.upstreams if upstream.enabled]

    def _owner(self, name: str) -> tuple[str, UpstreamConfig | None]:
        """Return missing, disabled, or the single upstream with this id."""
        matches = [upstream for upstream in self.upstreams if upstream.name == name]
        if len(matches) > 1:
            raise GatewayConfigError(
                f"Duplicate upstream identifier {name!r}. Each upstream id must be unique."
            )
        if not matches:
            return "missing", None
        if not matches[0].enabled:
            return "disabled", None
        return "enabled", matches[0]

    def _snapshots(self) -> dict[str, list[str]]:
        """Latest successful public names for enabled upstreams.

        A failed list does not delete the previous snapshot. An enabled upstream
        with no successful list yet contributes nothing.
        """
        enabled = {upstream.name for upstream in self.upstreams if upstream.enabled}
        return {
            name: [tool.name for tool in tools]
            for name, tools in self._registrations.items()
            if name in enabled
        }

    def _refresh_derived_state(self) -> None:
        """Replace collisions and diagnostics from the current snapshots and flags."""
        snapshots = self._snapshots()
        self._collisions = _colliding_public_names(snapshots)
        self._collision_upstreams = _collision_owners(snapshots)
        diagnostics: list[Diagnostic] = []
        for upstream in self.upstreams:
            if upstream.credential_unreadable:
                diagnostics.append(
                    Diagnostic(
                        code="upstream_credential_unreadable",
                        upstream_id=upstream.name,
                        tool_name="",
                        message=f"Upstream {upstream.name} credential could not be read.",
                    )
                )
            elif not upstream.enabled:
                diagnostics.append(
                    Diagnostic(
                        code="upstream_disabled",
                        upstream_id=upstream.name,
                        tool_name="",
                        message=f"Upstream {upstream.name} is disabled.",
                    )
                )
            elif upstream.name in self._list_failed:
                diagnostics.append(
                    Diagnostic(
                        code="upstream_list_failed",
                        upstream_id=upstream.name,
                        tool_name="",
                        message=f"Upstream {upstream.name} was omitted from tool discovery.",
                    )
                )
        for name in sorted(self._collisions):
            message = _conflict_message(name)
            logger.error(message)
            upstream_id, separator, _tool = name.partition("__")
            diagnostics.append(
                Diagnostic(
                    code="tool_excluded",
                    upstream_id=upstream_id if separator else "",
                    tool_name=name,
                    message=message,
                )
            )
        self.diagnostics = diagnostics

    async def list_tools_result(self) -> ListToolsResult:
        for upstream in self.upstreams:
            if not upstream.enabled:
                self._registrations.pop(upstream.name, None)
                self._list_failed.discard(upstream.name)
                continue
            await self._refresh_upstream(upstream)
        self._refresh_derived_state()
        tools: list[Tool] = []
        for upstream in self._enabled():
            if upstream.name in self._list_failed:
                continue
            tools.extend(
                tool for tool in self._registrations.get(upstream.name, []) if tool.name not in self._collisions
            )
        return ListToolsResult(tools=tools)

    async def call(self, namespaced_name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call one namespaced tool on its owning upstream.

        The owner is listed again before the call. A name that was unique can
        therefore be rejected when a later registration collides, even if the
        client did not list tools again. A disabled upstream is not contacted.
        """
        upstream_name, separator, tool_name = namespaced_name.partition("__")
        if not separator or not upstream_name or not tool_name:
            return _error(f"Unknown tool {namespaced_name}.")
        status, upstream = self._owner(upstream_name)
        if status == "disabled":
            self._registrations.pop(upstream_name, None)
            self._list_failed.discard(upstream_name)
            self._refresh_derived_state()
            return _error(f"Upstream {upstream_name} is disabled.")
        if upstream is None:
            return _error(f"Unknown upstream in tool {namespaced_name}.")

        async def operation() -> CallToolResult:
            listed_ok = False
            try:
                async with _connect(upstream) as client:
                    listed = await client.list_tools()
                    listed_ok = True
                    public = [_namespace(upstream.name, tool) for tool in listed.tools]
                    self._registrations[upstream.name] = public
                    self._list_failed.discard(upstream.name)
                    self._refresh_derived_state()
                    if namespaced_name in self._collisions or _name_count(public, namespaced_name) != 1:
                        if _name_count(public, namespaced_name) == 0 and namespaced_name not in self._collisions:
                            return _error(f"Unknown tool {namespaced_name}.")
                        return _conflict(namespaced_name)
                    return await client.call_tool(
                        tool_name,
                        arguments,
                        read_timeout_seconds=upstream.timeout_seconds,
                    )
            except Exception:
                if not listed_ok:
                    self._list_failed.add(upstream.name)
                    self._refresh_derived_state()
                    if namespaced_name in self._collisions:
                        return _conflict(namespaced_name)
                raise

        return await asyncio.wait_for(operation(), upstream.timeout_seconds)

    async def _refresh_upstream(self, upstream: UpstreamConfig) -> None:
        """Replace one snapshot, or keep it when this list fails."""

        async def operation() -> list[Tool]:
            async with _connect(upstream) as client:
                listed = await client.list_tools()
                return [_namespace(upstream.name, tool) for tool in listed.tools]

        try:
            self._registrations[upstream.name] = await asyncio.wait_for(operation(), upstream.timeout_seconds)
        except Exception:
            logger.warning("Skipping upstream %s during tool listing.", upstream.name)
            self._list_failed.add(upstream.name)
            return
        self._list_failed.discard(upstream.name)

    def status_report(self) -> dict[str, Any]:
        """Safe operator snapshot. It does not include URLs, secrets, or tool bodies.

        listing_state is the latest list attempt, not a general health verdict.
        advertised_tool_count is omitted when this process has no current inventory.
        """
        upstreams: list[dict[str, Any]] = []
        advertised_total = 0
        for upstream in self.upstreams:
            state = self._listing_state(upstream)
            advertised, excluded = self._tool_counts(upstream.name, state)
            if advertised is not None:
                advertised_total += advertised
            upstreams.append(
                {
                    "id": upstream.name,
                    "enabled": upstream.enabled,
                    "listing_state": state,
                    "advertised_tool_count": advertised,
                    "excluded_tool_count": excluded,
                }
            )
        return {
            "status": "ok",
            "advertised_tool_count": advertised_total,
            "upstreams": upstreams,
            "diagnostics": [_diagnostic_payload(item) for item in self.diagnostics],
            "collisions": self.collision_records(),
        }

    def collision_records(self) -> list[dict[str, Any]]:
        """Operator view of excluded public names. No URLs or credentials."""
        return [
            {
                "category": "duplicate_public_name",
                "tool_name": name,
                "upstream_ids": list(self._collision_upstreams.get(name, [])),
                "resolution": "excluded",
                "affects": ["discovery", "dispatch"],
            }
            for name in sorted(self._collisions)
        ]

    def _listing_state(self, upstream: UpstreamConfig) -> str:
        if not upstream.enabled:
            return "disabled"
        if upstream.name in self._list_failed:
            return "listing_failed"
        if upstream.name in self._registrations:
            return "listing_ok"
        return "unknown"

    def _tool_counts(self, upstream_id: str, state: str) -> tuple[int | None, int | None]:
        if state == "unknown":
            return None, None
        tools = self._registrations.get(upstream_id, [])
        excluded = len({tool.name for tool in tools if tool.name in self._collisions})
        if state == "listing_failed":
            return None, excluded
        if state == "disabled":
            return 0, 0
        advertised = len({tool.name for tool in tools if tool.name not in self._collisions})
        return advertised, excluded


def _diagnostic_payload(item: Diagnostic) -> dict[str, Any]:
    return {
        "code": item.code,
        "upstream_id": item.upstream_id or None,
        "tool_name": item.tool_name or None,
        "message": item.message,
    }


def _unique_upstreams(upstreams: list[UpstreamConfig]) -> list[UpstreamConfig]:
    seen: set[str] = set()
    unique: list[UpstreamConfig] = []
    for upstream in upstreams:
        name = upstream.name
        if not name or "__" in name:
            raise GatewayConfigError(
                f"Upstream identifier {name!r} is invalid. "
                "Use a non-empty id that does not contain '__'."
            )
        if name in seen:
            raise GatewayConfigError(
                f"Duplicate upstream identifier {name!r}. Each upstream id must be unique."
            )
        seen.add(name)
        unique.append(upstream)
    return unique


def _duplicate_names(names: Any) -> list[str]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def _collision_owners(registrations: dict[str, list[str]]) -> dict[str, list[str]]:
    """Upstream ids that contributed each public name appearing more than once."""
    owners: dict[str, list[str]] = {}
    for upstream_id, names in registrations.items():
        for name in names:
            owners.setdefault(name, []).append(upstream_id)
    return {name: sorted(set(ids)) for name, ids in owners.items() if len(ids) > 1}


def _colliding_public_names(registrations: dict[str, list[str]]) -> set[str]:
    """Public names that appear more than once in these snapshots.

    Pass the latest successful snapshot for every enabled upstream, including
    an upstream whose newest list failed. Omitting that snapshot would treat
    the partial refresh as complete and could dispatch an ambiguous name.
    """
    combined: list[str] = []
    for names in registrations.values():
        combined.extend(names)
    return set(_duplicate_names(combined))


def _name_count(tools: list[Tool], public_name: str) -> int:
    return sum(1 for tool in tools if tool.name == public_name)


def upstream_http_client(upstream: UpstreamConfig) -> httpx2.AsyncClient:
    """Open the upstream client. Redirects stay off so a response cannot retarget it.

    An untrusted client connects only to an address this process just allowed.
    The original hostname remains the HTTP Host and the TLS server name.
    """
    headers = {}
    if upstream.authorization:
        headers["Authorization"] = upstream.authorization
    transport = None if upstream.trusted else PolicyTransport(allow_private=upstream.allow_private)
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(upstream.timeout_seconds),
        follow_redirects=False,
        headers=headers,
        transport=transport,
    )


class PolicyTransport(httpx2.AsyncBaseTransport):
    """Connect to a permitted address without a second, unchecked DNS lookup."""

    def __init__(self, *, allow_private: bool, resolve=None, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        self._allow_private = allow_private
        self._resolve = resolve
        self._inner = inner if inner is not None else httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        original_host = request.url.host
        host_header = request.headers.get("host")
        try:
            chosen = destination_addresses(
                str(request.url),
                allow_private=self._allow_private,
                resolve=self._resolve,
            )[0]
        except UnsafeUpstreamUrl:
            raise
        if chosen != original_host:
            request.extensions["sni_hostname"] = original_host
            request.url = request.url.copy_with(host=chosen)
        if host_header:
            request.headers["Host"] = host_header
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()


def _namespace(upstream_name: str, tool: Tool) -> Tool:
    return tool.model_copy(update={"name": f"{upstream_name}__{tool.name}"})


def _conflict_message(public_name: str) -> str:
    return f"Duplicate public tool name {public_name}. Conflicting registration excluded."


def _conflict(public_name: str) -> CallToolResult:
    return _error(_conflict_message(public_name))


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


class _connect:
    """Open one short-lived Streamable HTTP session."""

    def __init__(self, upstream: UpstreamConfig) -> None:
        self.upstream = upstream
        self._http: httpx2.AsyncClient | None = None
        self._client: Client | None = None

    async def __aenter__(self) -> Client:
        if self.upstream.credential_unreadable:
            raise CredentialUnreadable("Upstream credential could not be read.")
        if not self.upstream.trusted:
            validate_upstream_url(self.upstream.url, allow_private=self.upstream.allow_private)
        self._http = upstream_http_client(self.upstream)
        transport = streamable_http_client(self.upstream.url, http_client=self._http)
        self._client = Client(transport, read_timeout_seconds=self.upstream.timeout_seconds)
        await self._client.__aenter__()
        return self._client

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, traceback)
        if self._http is not None:
            await self._http.aclose()
