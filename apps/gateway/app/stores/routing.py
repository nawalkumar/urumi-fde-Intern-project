"""Decide whether a public tool name may be advertised or called for a store.

Non-store tools are left to the existing grant check. A store tool is allowed
only when the trusted caller owns that Ready store. The tool name is the
tenant selector. A display name is not. An empty caller id fails closed.
"""

from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.stores.manifests import mcp_url
from apps.gateway.app.stores.model import STORE_ID, upstream_id_for
from apps.gateway.app.stores.repository import get_store


def store_id_from_tool(tool: str) -> str | None:
    """Return the store id, '' when the prefix is malformed, or None when this is not a store tool."""
    if not isinstance(tool, str):
        return ""
    upstream, separator, rest = tool.partition("__")
    if not separator or not rest or not upstream.startswith("store_"):
        return None
    store_id = upstream[len("store_") :]
    if STORE_ID.fullmatch(store_id) is None or "__" in upstream:
        return ""
    return store_id


async def store_block_reason(store: object | None, tool: str, *, owner_user_id: str) -> str | None:
    """Return why a store tool is unavailable, or None when it may be used.

    A non-store tool returns None. A store tool is allowed only when the
    trusted caller owns that store, the store is Ready, and the saved upstream
    is enabled at that store's canonical MCP URL. The URL and owner id are not
    returned. An empty caller id fails closed.
    """
    store_id = store_id_from_tool(tool)
    if store_id is None:
        return None
    if store_id == "" or store is None:
        return "store_malformed"
    try:
        record = await get_store(store, store_id)
    except StoreUnavailable:
        return "store_unavailable"
    if record is None:
        return "store_not_ready"
    if not owner_user_id or record.owner_user_id != owner_user_id:
        return "store_not_owner"
    if record.state != "ready":
        return "store_not_ready"
    if record.upstream_id != upstream_id_for(store_id):
        return "store_upstream_mismatch"
    try:
        expected = mcp_url(record.namespace)
        rows = await store.list_upstreams()  # type: ignore[attr-defined]
    except StoreUnavailable:
        return "store_unavailable"
    except Exception:
        return "store_upstream_mismatch"
    saved = next((row for row in rows if row.upstream_id == record.upstream_id and row.enabled), None)
    if saved is None or saved.url != expected:
        return "store_upstream_mismatch"
    return None


async def store_call_allowed(store: object | None, tool: str, *, owner_user_id: str) -> bool:
    return await store_block_reason(store, tool, owner_user_id=owner_user_id) is None
