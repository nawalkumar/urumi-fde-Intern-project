"""Store identity, lifecycle labels, and safe public status.

Store ids are generated here. A display name is never used as a Kubernetes
name, a hostname label, or an upstream id.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

STORE_ID = re.compile(r"[0-9a-f]{16}")
RESERVED_UPSTREAM = re.compile(r"store_[0-9a-f]{16}")
DISPLAY_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,38}[A-Za-z0-9])?")
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")
NAMESPACE_NAME = re.compile(r"st-[0-9a-f]{16}")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
_DOMAIN = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)+")

STATES = frozenset({"provisioning", "ready", "failed", "deleting", "deleted"})
STATE_LABEL = {
    "provisioning": "Provisioning",
    "ready": "Ready",
    "failed": "Failed",
    "deleting": "Deleting",
    "deleted": "Deleted",
}
ACTIVE_STATES = frozenset({"provisioning", "ready", "failed", "deleting"})
TOOL_NAMES = (
    "list_orders",
    "get_order",
    "list_products",
    "create_product",
    "update_order_status",
    "create_order",
)
PROTECTED_NAMESPACES = frozenset(
    {
        "default",
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "ingress-nginx",
        "calico-system",
    }
)

ERRORS = {
    "timeout": "Provisioning timed out before the store became ready.",
    "attempts": "Provisioning stopped after the retry limit.",
    "ownership": "A resource with this name is not owned by this store.",
    "unconfigured": "Kubernetes API is not configured.",
    "register": "The store could not be registered with the gateway.",
    "private_disabled": "Private upstream addresses are disabled.",
    "namespace_missing": "The store namespace is missing.",
    "cleanup": "Provisioning failed and owned resources were removed.",
    "limit": "This account has reached the store limit.",
    "conflict": "That idempotency key was already used for a different store.",
    "invalid": "The store name or idempotency key was rejected.",
    "disabled": "Store provisioning is not enabled.",
    "not_found": "That store was not found.",
    "retry": "That store cannot be retried.",
    "unavailable": "Store status is unavailable.",
    "delete_blocked": "That store could not be deleted.",
}


class OwnershipError(Exception):
    """Refusing to touch a resource that this store does not own."""


@dataclass
class StoreRecord:
    store_id: str
    owner_user_id: str
    display_name: str
    idempotency_key: str
    fingerprint: str
    state: str
    phase: str
    namespace: str
    upstream_id: str
    url: str
    progress: str
    error_code: str
    error_message: str
    attempt: int
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None

    def __repr__(self) -> str:
        return (
            f"StoreRecord(store_id={self.store_id!r}, state={self.state!r}, "
            f"phase={self.phase!r}, namespace={self.namespace!r})"
        )


def reserved_store_upstream(value: object) -> bool:
    """True when this id is reserved for a provisioned store.

    The admin upstream API must not create, retarget, or delete these rows.
    The provisioner is the only writer.
    """
    return isinstance(value, str) and RESERVED_UPSTREAM.fullmatch(value) is not None


def namespace_for(store_id: str) -> str:
    if STORE_ID.fullmatch(store_id) is None:
        raise OwnershipError("Store id is not valid.")
    return f"st-{store_id}"


def upstream_id_for(store_id: str) -> str:
    if STORE_ID.fullmatch(store_id) is None:
        raise OwnershipError("Store id is not valid.")
    return f"store_{store_id}"


def public_tool(store_id: str, tool: str) -> str:
    return f"{upstream_id_for(store_id)}__{tool}"


def fingerprint(display_name: str) -> str:
    return hashlib.sha256(display_name.casefold().encode("utf-8")).hexdigest()


def validate_request(name: object, idempotency_key: object) -> tuple[str, str] | None:
    if not isinstance(name, str) or not isinstance(idempotency_key, str):
        return None
    cleaned = " ".join(name.split())
    if DISPLAY_NAME.fullmatch(cleaned) is None or len(cleaned) > 40:
        return None
    if IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        return None
    return cleaned, idempotency_key


def validate_domain(value: str) -> bool:
    if not value or len(value) > 253 or value.endswith(".localhost") or value == "localhost":
        return False
    return _DOMAIN.fullmatch(value) is not None


def validate_namespace_name(value: str) -> bool:
    """Accept a DNS label that is not itself a generated store namespace."""
    if NAMESPACE_NAME.fullmatch(value):
        return False
    return _DNS_LABEL.fullmatch(value) is not None and len(value) <= 63


def owned_namespace(store_id: str, namespace: str) -> bool:
    try:
        expected = namespace_for(store_id)
    except OwnershipError:
        return False
    return namespace == expected and namespace not in PROTECTED_NAMESPACES


def public_store(record: StoreRecord) -> dict[str, object]:
    """Status for the dashboard. Secret material is not a field on the record."""
    tools = [public_tool(record.store_id, name) for name in TOOL_NAMES] if record.state == "ready" else []
    return {
        "store_id": record.store_id,
        "name": record.display_name,
        "state": STATE_LABEL[record.state],
        "url": record.url or None,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "progress": record.progress,
        "error_description": record.error_message or None,
        "namespace": record.namespace,
        "upstream_id": record.upstream_id,
        "phase": record.phase,
        "attempt": record.attempt,
        "tool_names": tools,
        "deletion_complete": record.state == "deleted",
    }


def _iso(value: datetime) -> str:
    return value.isoformat()
