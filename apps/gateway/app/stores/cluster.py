"""Cluster client boundary.

Production uses HttpClusterClient. Tests pass their own object. This module
does not contain a fake cluster.
"""

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from apps.gateway.app.stores.model import NAMESPACE_NAME, PROTECTED_NAMESPACES, OwnershipError

logger = logging.getLogger(__name__)

# The job controller writes these onto a live Job. They are not part of the
# desired template, and spec.template cannot be updated in place.
_JOB_INJECTED_LABELS = frozenset(
    {
        "batch.kubernetes.io/controller-uid",
        "batch.kubernetes.io/job-name",
        "controller-uid",
    }
)
_PV_COLLECTION = "/api/v1/persistentvolumes"
_PV_ITEM = re.compile(r"^/api/v1/persistentvolumes/[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_PV_RECLAIM_PHASES = frozenset({"Released", "Failed", "Available"})

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

    async def namespace_absent(self, name: str, store_id: str) -> bool: ...

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
        if kind in {"Secret", "Namespace", "PersistentVolumeClaim"}:
            return
        if kind == "Job":
            # spec.template and spec.selector are immutable. A full PUT fails
            # once the controller has filled them in, and that failure used to
            # burn provision attempts until the store was deleted.
            await self._reconcile_existing_job(resource, current, item)
            return
        version = str((current.get("metadata") or {}).get("resourceVersion", ""))
        desired = json.loads(json.dumps(resource))
        if version:
            desired["metadata"]["resourceVersion"] = version
        if kind == "StatefulSet":
            # volumeClaimTemplates cannot change after create. A store made
            # before a template change keeps its original claim template.
            live_templates = (current.get("spec") or {}).get("volumeClaimTemplates")
            if isinstance(live_templates, list):
                desired["spec"]["volumeClaimTemplates"] = live_templates
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
        # 200 and 202 accept the request. The namespace can still be terminating.
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

    async def namespace_absent(self, name: str, store_id: str) -> bool:
        """True only when this store's namespace is gone.

        A terminating namespace is not absent. A different store-id label is
        refused rather than treated as deletion.
        """
        assert_store_namespace(name, store_id)
        status, current = await self._request("GET", f"/api/v1/namespaces/{name}", None)
        if status == 404:
            return True
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError("Kubernetes namespace read failed.")
        if _labels(current).get("urumi.dev/store-id") != store_id:
            raise OwnershipError("Store namespace is not owned by this store.")
        return False

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

    async def release_store_volumes(self, namespace: str, store_id: str) -> bool:
        """Delete leftover volumes whose claim belonged only to this store.

        Namespace deletion removes the claims. It does not delete a volume
        whose StorageClass reclaim policy is Retain. Those volumes stay
        Released, with claimRef.namespace still set to this store. This method
        deletes only that set. A Bound or Pending volume is left in place and
        reported as unfinished so the caller keeps retrying. Volumes for any
        other namespace are not requested for deletion.
        """
        assert_store_namespace(namespace, store_id)
        pending = False
        token = ""
        for _page in range(8):
            path = _PV_COLLECTION + "?limit=100"
            if token:
                path += "&continue=" + urllib.parse.quote(token, safe="")
            status, body = await self._request("GET", path, None)
            if status != 200 or not isinstance(body, dict):
                raise RuntimeError("Kubernetes persistent volume list failed.")
            items = body.get("items") or []
            if not isinstance(items, list):
                raise RuntimeError("Kubernetes persistent volume list failed.")
            for item in items:
                if not isinstance(item, dict):
                    pending = True
                    continue
                spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
                claim = spec.get("claimRef") if isinstance(spec.get("claimRef"), dict) else {}
                if claim.get("namespace") != namespace:
                    continue
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                name = str(metadata.get("name", ""))
                status_body = item.get("status") if isinstance(item.get("status"), dict) else {}
                phase = str(status_body.get("phase", ""))
                if not _PV_ITEM.fullmatch(f"{_PV_COLLECTION}/{name}"):
                    logger.warning("Skipping a store volume with an unexpected name.")
                    pending = True
                    continue
                if phase not in _PV_RECLAIM_PHASES:
                    pending = True
                    continue
                deleted, _payload = await self._request("DELETE", f"{_PV_COLLECTION}/{name}", None)
                if deleted not in {200, 202, 404}:
                    raise RuntimeError("Kubernetes persistent volume delete failed.")
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            token = str(metadata.get("continue") or "")
            if not token:
                return not pending
        raise RuntimeError("Kubernetes persistent volume list failed.")

    async def _reconcile_existing_job(self, desired: dict, current: dict, item: str) -> None:
        if _job_succeeded(current):
            return
        if not _job_failed(current):
            return
        deleted, _body = await self._request("DELETE", item, {"propagationPolicy": "Background"})
        if deleted not in {200, 202, 404}:
            raise RuntimeError("Kubernetes job replace failed.")
        created, _created = await self._request("POST", _collection_path(desired), desired)
        if created in {200, 201, 202}:
            return
        if created != 409:
            raise RuntimeError("Kubernetes create failed for Job.")
        status, exists = await self._request("GET", item, None)
        if status != 200 or not isinstance(exists, dict):
            raise RuntimeError("Kubernetes create failed for Job.")

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
            logger.warning("Kubernetes API call failed: %s (%s)", type(exc).__name__, _cause_name(exc))
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


def _cause_name(exc: BaseException) -> str:
    """Class name of the underlying failure. The message is not used; it can carry the URL."""
    reason = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else None
    cause = reason if isinstance(reason, BaseException) else exc.__cause__ or exc.__context__
    return type(cause).__name__ if cause is not None else "none"


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


def _job_succeeded(body: dict) -> bool:
    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    return int(status.get("succeeded") or 0) >= 1


def _job_failed(body: dict) -> bool:
    if _job_succeeded(body):
        return False
    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    conditions = status.get("conditions") or []
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, dict) and item.get("type") == "Failed" and item.get("status") == "True" for item in conditions
    )


def _job_template_matches(current: dict, desired: dict) -> bool:
    live_spec = current.get("spec") if isinstance(current.get("spec"), dict) else {}
    want_spec = desired.get("spec") if isinstance(desired.get("spec"), dict) else {}
    return _strip_job_template(live_spec.get("template")) == _strip_job_template(want_spec.get("template"))


def _strip_job_template(template: object) -> object:
    if not isinstance(template, dict):
        return template
    copied = json.loads(json.dumps(template))
    metadata = copied.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("labels"), dict):
        for key in _JOB_INJECTED_LABELS:
            metadata["labels"].pop(key, None)
    return copied


def _persistent_volume_request(method: str, path: str, payload: dict | None) -> bool:
    base = path.split("?", 1)[0]
    if method == "GET" and payload is None and base == _PV_COLLECTION:
        return True
    if method == "DELETE" and payload is None and _PV_ITEM.fullmatch(base) and len(base) <= len(_PV_COLLECTION) + 1 + 253:
        return True
    return False


def _guard_path(method: str, path: str, payload: dict | None) -> None:
    _reject_dot_segments(path.split("?", 1)[0])
    if "?" in path:
        _reject_dot_segments("/" + path.split("?", 1)[1].replace("&", "/").replace("=", "/"))
    if _persistent_volume_request(method, path, payload):
        return
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
