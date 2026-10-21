"""Cluster client boundary.

Production uses HttpClusterClient. Tests pass their own object. This module
does not contain a fake cluster.
"""

import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from apps.gateway.app.stores.model import NAMESPACE_NAME, PROTECTED_NAMESPACES, OwnershipError

logger = logging.getLogger(__name__)

_PLURALS = {
    "Secret": "secrets",
    "ConfigMap": "configmaps",
    "Service": "services",
    "PersistentVolumeClaim": "persistentvolumeclaims",
    "ServiceAccount": "serviceaccounts",
    "ResourceQuota": "resourcequotas",
    "LimitRange": "limitranges",
    "Deployment": "deployments",
    "StatefulSet": "statefulsets",
    "Job": "jobs",
    "NetworkPolicy": "networkpolicies",
    "Ingress": "ingresses",
}


class ClusterClient(Protocol):
    async def apply(self, resource: dict) -> None: ...

    async def delete_namespace(self, name: str, store_id: str) -> None: ...

    async def namespace_exists(self, name: str, store_id: str) -> bool: ...

    async def secret_exists(self, namespace: str, name: str, store_id: str) -> bool: ...

    async def workloads_ready(self, namespace: str, store_id: str) -> bool: ...


def assert_store_namespace(name: str, store_id: str) -> None:
    expected = f"st-{store_id}"
    if name in PROTECTED_NAMESPACES or name != expected or NAMESPACE_NAME.fullmatch(name) is None:
        raise OwnershipError("Store namespace is not owned by this store.")


def _labels(resource: dict) -> dict[str, str]:
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    labels = metadata.get("labels")
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def assert_owned_resource(resource: dict) -> tuple[str, str]:
    """Return (namespace or '', store id) or refuse the object."""
    labels = _labels(resource)
    store_id = labels.get("urumi.dev/store-id", "")
    kind = str(resource.get("kind", ""))
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        raise OwnershipError("Store resource is missing metadata.")
    name = str(metadata.get("name", ""))
    namespace = str(metadata.get("namespace", ""))
    if kind == "Namespace":
        assert_store_namespace(name, store_id)
        return "", store_id
    assert_store_namespace(namespace, store_id)
    if not name or name in PROTECTED_NAMESPACES:
        raise OwnershipError("Store resource name is not valid.")
    return namespace, store_id


class HttpClusterClient:
    """In-cluster Kubernetes client. The bearer token is not logged."""

    def __init__(self, base_url: str, token: str, opener: object | None = None, cafile: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._opener = opener
        self._cafile = cafile

    def __repr__(self) -> str:
        return "HttpClusterClient(token=<redacted>)"

    async def apply(self, resource: dict) -> None:
        namespace, store_id = assert_owned_resource(resource)
        kind = str(resource["kind"])
        name = str(resource["metadata"]["name"])
        item = _item_path(resource)
        status, current = await self._request("GET", item, None)
        if status == 404:
            collection = _collection_path(resource)
            created, _body = await self._request("POST", collection, _without_secrets_log(resource))
            if created not in {200, 201, 202}:
                raise RuntimeError(f"Kubernetes create failed for {kind}.")
            return
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(f"Kubernetes read failed for {kind}.")
        current_id = _labels(current).get("urumi.dev/store-id", "")
        if current_id != store_id:
            raise OwnershipError("Store resource is not owned by this store.")
        if kind == "Secret":
            return
        version = str((current.get("metadata") or {}).get("resourceVersion", ""))
        desired = json.loads(json.dumps(resource))
        if version:
            desired["metadata"]["resourceVersion"] = version
        updated, _body = await self._request("PUT", item, desired)
        if updated not in {200, 201}:
            raise RuntimeError(f"Kubernetes update failed for {kind}.")
        del namespace, name

    async def delete_namespace(self, name: str, store_id: str) -> None:
        assert_store_namespace(name, store_id)
        path = f"/api/v1/namespaces/{name}"
        status, current = await self._request("GET", path, None)
        if status == 404:
            return
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError("Kubernetes namespace read failed.")
        if _labels(current).get("urumi.dev/store-id") != store_id:
            raise OwnershipError("Store namespace is not owned by this store.")
        deleted, _body = await self._request("DELETE", path, None)
        if deleted not in {200, 202, 404}:
            raise RuntimeError("Kubernetes namespace delete failed.")

    async def namespace_exists(self, name: str, store_id: str) -> bool:
        assert_store_namespace(name, store_id)
        status, current = await self._request("GET", f"/api/v1/namespaces/{name}", None)
        if status == 404:
            return False
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError("Kubernetes namespace read failed.")
        return _labels(current).get("urumi.dev/store-id") == store_id

    async def secret_exists(self, namespace: str, name: str, store_id: str) -> bool:
        assert_store_namespace(namespace, store_id)
        if name not in {"store-db", "woocommerce-credentials"}:
            raise OwnershipError("Store secret name is not valid.")
        status, current = await self._request("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}", None)
        if status == 404:
            return False
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError("Kubernetes secret read failed.")
        return _labels(current).get("urumi.dev/store-id") == store_id

    async def workloads_ready(self, namespace: str, store_id: str) -> bool:
        assert_store_namespace(namespace, store_id)
        wordpress = await self._ready_deployment(namespace, "wordpress")
        database = await self._ready_statefulset(namespace, "store-db")
        mcp = await self._ready_deployment(namespace, "woo-mcp")
        job = await self._job_succeeded(namespace, "wordpress-init")
        return wordpress and database and mcp and job

    async def _ready_deployment(self, namespace: str, name: str) -> bool:
        status, body = await self._request("GET", f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}", None)
        if status != 200 or not isinstance(body, dict):
            return False
        ready = ((body.get("status") or {}).get("readyReplicas"))
        return ready == 1

    async def _ready_statefulset(self, namespace: str, name: str) -> bool:
        status, body = await self._request("GET", f"/apis/apps/v1/namespaces/{namespace}/statefulsets/{name}", None)
        if status != 200 or not isinstance(body, dict):
            return False
        return ((body.get("status") or {}).get("readyReplicas")) == 1

    async def _job_succeeded(self, namespace: str, name: str) -> bool:
        status, body = await self._request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}", None)
        if status != 200 or not isinstance(body, dict):
            return False
        return ((body.get("status") or {}).get("succeeded") or 0) >= 1

    async def _request(self, method: str, path: str, payload: dict | None) -> tuple[int, dict | None]:
        _guard_path(method, path, payload)
        import asyncio

        return await asyncio.to_thread(self._request_sync, method, path, payload)

    def _request_sync(self, method: str, path: str, payload: dict | None) -> tuple[int, dict | None]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self._base_url + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        opener = self._opener or self._build_opener()
        try:
            with opener.open(request, timeout=10) as response:  # type: ignore[union-attr]
                raw = response.read()
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = int(exc.code)
        except Exception as exc:
            logger.warning("Kubernetes API call failed: %s", type(exc).__name__)
            raise RuntimeError("Kubernetes API call failed.") from None
        if not raw:
            return status, None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kubernetes API returned an unreadable body.") from exc
        if isinstance(parsed, dict):
            parsed.pop("data", None)
            parsed.pop("stringData", None)
            return status, parsed
        return status, None

    def _build_opener(self) -> urllib.request.OpenerDirector:
        context = ssl.create_default_context()
        if self._cafile:
            context.load_verify_locations(self._cafile)
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def incluster_client() -> HttpClusterClient | None:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
    if not host:
        return None
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    if not os.path.isfile(token_path):
        return None
    try:
        token = open(token_path, encoding="utf-8").read().strip()
    except OSError:
        logger.warning("Kubernetes service account token could not be read.")
        return None
    if not token:
        return None
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443").strip() or "443"
    cafile = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    if not os.path.isfile(cafile):
        cafile_value: str | None = None
    else:
        cafile_value = cafile
    return HttpClusterClient(f"https://{host}:{port}", token, cafile=cafile_value)


def _without_secrets_log(resource: dict) -> dict:
    """Return the object to send. Callers must not log it."""
    return resource


def _collection_path(resource: dict) -> str:
    kind = str(resource["kind"])
    if kind == "Namespace":
        return "/api/v1/namespaces"
    namespace = str(resource["metadata"]["namespace"])
    assert_store_namespace(namespace, _labels(resource)["urumi.dev/store-id"])
    return _prefix(resource) + "/" + _PLURALS[kind]


def _item_path(resource: dict) -> str:
    name = str(resource["metadata"]["name"])
    if str(resource["kind"]) == "Namespace":
        assert_store_namespace(name, _labels(resource)["urumi.dev/store-id"])
        return f"/api/v1/namespaces/{name}"
    return _collection_path(resource) + "/" + name


def _prefix(resource: dict) -> str:
    api = str(resource["apiVersion"])
    namespace = str(resource["metadata"]["namespace"])
    root = "/api/v1" if api == "v1" else f"/apis/{api}"
    return f"{root}/namespaces/{namespace}"


def _guard_path(method: str, path: str, payload: dict | None) -> None:
    _reject_dot_segments(path)
    if path == "/api/v1/namespaces" and method == "POST":
        if not isinstance(payload, dict):
            raise OwnershipError("Store namespace is not owned by this store.")
        assert_owned_resource(payload)
        return
    marker = "/namespaces/"
    if marker not in path:
        raise OwnershipError("Store namespace is not owned by this store.")
    name = path.split(marker, 1)[1].split("/", 1)[0]
    if name in PROTECTED_NAMESPACES or NAMESPACE_NAME.fullmatch(name) is None:
        raise OwnershipError("Store namespace is not owned by this store.")


def _reject_dot_segments(path: str) -> None:
    """Reject '.' and '..' before the request is sent.

    The namespace check looks only at the first segment after `/namespaces/`.
    A later '..' segment would still pass that check. Decoding is repeated so
    a single layer of percent-encoding cannot hide the segment.
    """
    if not isinstance(path, str) or not path.startswith("/") or "\\" in path:
        raise OwnershipError("Store namespace is not owned by this store.")
    current = path
    for _ in range(3):
        if "\\" in current or "\x00" in current:
            raise OwnershipError("Store namespace is not owned by this store.")
        segments = [segment for segment in current.split("/") if segment != ""]
        if any(segment in {".", ".."} for segment in segments):
            raise OwnershipError("Store namespace is not owned by this store.")
        decoded = urllib.parse.unquote(current)
        if decoded == current:
            return
        current = decoded
    raise OwnershipError("Store namespace is not owned by this store.")
