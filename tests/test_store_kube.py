"""HTTP Kubernetes client path guards. No cluster is contacted."""

import json

import pytest

from apps.gateway.app.stores.cluster import HttpClusterClient, OwnershipError
from apps.gateway.app.stores.model import namespace_for


class _Response:
    def __init__(self, status: int, payload: dict | None) -> None:
        self.status = status
        self._payload = b"" if payload is None else json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def open(self, request: object, timeout: float | None = None) -> _Response:
        method = str(getattr(request, "method"))
        url = str(getattr(request, "full_url"))
        self.calls.append((method, url))
        token = request.get_header("Authorization")  # type: ignore[attr-defined]
        assert token == "Bearer super-secret-token"
        if url.endswith("/namespaces/st-0123456789abcdef") and method == "GET":
            return _Response(404, {"code": 404})
        if method == "POST":
            return _Response(201, {"metadata": {"name": "st-0123456789abcdef"}})
        return _Response(404, {"code": 404})


@pytest.mark.asyncio
async def test_client_refuses_protected_namespaces_and_hides_the_token() -> None:
    opener = _Opener()
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    assert "super-secret-token" not in repr(client)
    with pytest.raises(OwnershipError):
        await client.delete_namespace("default", "0123456789abcdef")
    with pytest.raises(OwnershipError):
        await client.delete_namespace("kube-system", "0123456789abcdef")
    with pytest.raises(OwnershipError):
        await client.delete_namespace("urumi-disposable", "0123456789abcdef")
    assert opener.calls == []
    resource = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace_for("0123456789abcdef"),
            "labels": {"urumi.dev/store-id": "0123456789abcdef"},
        },
    }
    await client.apply(resource)
    assert any(method == "POST" for method, _url in opener.calls)
    assert all("default" not in url for _method, url in opener.calls)
    joined = " ".join(url for _method, url in opener.calls)
    assert "super-secret-token" not in joined


class _Failing:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def open(self, request: object, timeout: float | None = None) -> _Response:
        raise self._error


@pytest.mark.asyncio
async def test_api_failure_logs_the_cause_class_without_url_or_token(caplog: pytest.LogCaptureFixture) -> None:
    import ssl
    import urllib.error

    url = "https://10.96.0.1:443"
    cases = [
        (urllib.error.URLError(TimeoutError("timed out")), "URLError (TimeoutError)"),
        (urllib.error.URLError(ssl.SSLCertVerificationError(f"verify failed for {url}")), "URLError (SSLCertVerificationError)"),
        (urllib.error.URLError(f"refused by {url}"), "URLError (none)"),
        (TimeoutError("timed out"), "TimeoutError (none)"),
    ]
    for error, expected in cases:
        caplog.clear()
        client = HttpClusterClient(url, "super-secret-token", opener=_Failing(error))
        with (
            caplog.at_level("WARNING", logger="apps.gateway.app.stores.cluster"),
            pytest.raises(RuntimeError, match="Kubernetes API call failed."),
        ):
            await client.namespace_exists(namespace_for("0123456789abcdef"), "0123456789abcdef")
        text = caplog.text
        assert f"Kubernetes API call failed: {expected}" in text
        assert "10.96.0.1" not in text
        assert "super-secret-token" not in text


class _Scripted:
    def __init__(self, responses: list[tuple[int, dict | None]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def open(self, request: object, timeout: float | None = None) -> _Response:
        method = str(getattr(request, "method"))
        url = str(getattr(request, "full_url"))
        self.calls.append((method, url))
        status, payload = self._responses.pop(0)
        return _Response(status, payload)


def _namespace_body(store_id: str, name: str) -> dict[str, object]:
    return {"metadata": {"name": name, "labels": {"urumi.dev/store-id": store_id}}}


@pytest.mark.asyncio
async def test_delete_202_is_not_absence_and_a_missing_namespace_is_not_deleted_again() -> None:
    store_id = "0123456789abcdef"
    name = namespace_for(store_id)
    other = namespace_for("fedcba9876543210")
    opener = _Scripted(
        [
            (200, _namespace_body(store_id, name)),
            (202, _namespace_body(store_id, name)),
            (200, _namespace_body(store_id, name)),
            (404, {"code": 404}),
            (404, {"code": 404}),
        ]
    )
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    await client.delete_namespace(name, store_id)
    assert await client.namespace_absent(name, store_id) is False
    assert [method for method, _url in opener.calls] == ["GET", "DELETE", "GET"]
    assert await client.namespace_absent(name, store_id) is True
    await client.delete_namespace(name, store_id)
    assert [method for method, _url in opener.calls] == ["GET", "DELETE", "GET", "GET", "GET"]
    assert all(other not in url and "default" not in url and "urumi-disposable" not in url for _method, url in opener.calls)
    assert "super-secret-token" not in " ".join(url for _method, url in opener.calls)


@pytest.mark.asyncio
async def test_wrong_store_label_sends_no_delete() -> None:
    store_id = "0123456789abcdef"
    name = namespace_for(store_id)
    opener = _Scripted([(200, _namespace_body("ffffffffffffffff", name))])
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    with pytest.raises(OwnershipError):
        await client.delete_namespace(name, store_id)
    assert [method for method, _url in opener.calls] == ["GET"]


@pytest.mark.asyncio
async def test_store_row_stays_deleting_after_http_202_until_the_namespace_is_gone() -> None:
    from datetime import datetime, timedelta, timezone

    from apps.gateway.app.config import Settings, StoreProvisionerSettings
    from apps.gateway.app.passwords import hash_password
    from apps.gateway.app.registry import Registry
    from apps.gateway.app.store import InMemoryStore
    from apps.gateway.app.stores.model import public_store
    from apps.gateway.app.stores.repository import get_store
    from apps.gateway.app.stores.service import StoreService

    memory = InMemoryStore()
    user = await memory.create_user("ada", hash_password("password-value-ok"), "admin")
    holder = {"namespace": "", "store_id": "", "present": True}
    opener = _TrackingNamespace(holder)
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    current = {"at": datetime(2026, 10, 22, tzinfo=timezone.utc)}

    def now() -> datetime:
        return current["at"]

    async def sleep(seconds: float) -> None:
        current["at"] = current["at"] + timedelta(seconds=seconds)

    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True)),
        client,
        autostart=False,
        now=now,
        sleep=sleep,
        delete_wait_seconds=0,
    )
    record, kind = await service.create(user.user_id, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    holder["namespace"] = record.namespace
    holder["store_id"] = record.store_id
    await service.request_delete(user.user_id, record.store_id)
    await service.reconcile_store(record.store_id)
    saved = await get_store(memory, record.store_id)
    assert saved is not None
    assert saved.state == "deleting"
    assert saved.phase == "terminating"
    assert public_store(saved)["deletion_complete"] is False
    assert [method for method, _url in opener.calls] == ["GET", "DELETE", "GET"]
    holder["present"] = False
    await service.reconcile_store(record.store_id)
    finished = await get_store(memory, record.store_id)
    assert finished is not None and finished.state == "deleted"
    assert public_store(finished)["deletion_complete"] is True
    assert [method for method, _url in opener.calls] == ["GET", "DELETE", "GET", "GET", "GET", "GET"]
    assert opener.calls[-1][1].endswith("/api/v1/persistentvolumes?limit=100")
    assert all(record.namespace in url or url.endswith("/api/v1/persistentvolumes?limit=100") for _method, url in opener.calls)


class _TrackingNamespace:
    def __init__(self, holder: dict[str, object]) -> None:
        self.holder = holder
        self.calls: list[tuple[str, str]] = []

    def open(self, request: object, timeout: float | None = None) -> _Response:
        method = str(getattr(request, "method"))
        url = str(getattr(request, "full_url"))
        self.calls.append((method, url))
        if url.endswith("/api/v1/persistentvolumes?limit=100") and method == "GET":
            return _Response(200, {"items": []})
        name = str(self.holder["namespace"])
        store_id = str(self.holder["store_id"])
        assert name and name in url
        if method == "DELETE":
            return _Response(202, _namespace_body(store_id, name))
        if self.holder["present"]:
            return _Response(200, _namespace_body(store_id, name))
        return _Response(404, {"code": 404})


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/namespaces/st-0123456789abcdef/../default/secrets/urumi-dev-chat",
        "/api/v1/namespaces/st-0123456789abcdef/%2e%2e/default/secrets/urumi-dev-chat",
        "/api/v1/namespaces/./st-0123456789abcdef",
        "/api/v1/namespaces/st-0123456789abcdef/%2E%2E/default",
    ],
)
def test_guard_rejects_dot_segments(path: str) -> None:
    from apps.gateway.app.stores.cluster import _guard_path

    with pytest.raises(OwnershipError):
        _guard_path("GET", path, None)


def _job(store_id: str) -> dict:
    from apps.gateway.app.stores.manifests import StoreSpec, workload_resources
    from apps.gateway.app.stores.model import namespace_for

    spec = StoreSpec(
        store_id=store_id,
        owner_id="abc123def4567890",
        namespace=namespace_for(store_id),
        host=f"{store_id}.stores.local",
        title="North Shop",
        wordpress_image="wordpress:6.8-php8.2-apache",
        mariadb_image="mariadb:11.4",
        cli_image="wordpress:cli-php8.2",
        mcp_image="urumi-woocommerce-mcp:local",
        gateway_namespace="default",
        ingress_namespace="ingress-nginx",
        ingress_class="nginx",
        storage_class="",
        allow_plugin_egress=False,
        probe_cidrs=(),
    )
    return next(item for item in workload_resources(spec) if item["kind"] == "Job")


class _Handler:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def open(self, request: object, timeout: float | None = None) -> _Response:
        method = str(getattr(request, "method"))
        url = str(getattr(request, "full_url"))
        self.calls.append((method, url))
        status, payload = self.handler(method, url)
        return _Response(status, payload)


@pytest.mark.asyncio
async def test_succeeded_job_is_not_replaced_and_failed_job_is_recreated() -> None:
    store_id = "0123456789abcdef"
    desired = _job(store_id)
    live = json.loads(json.dumps(desired))
    live["metadata"]["resourceVersion"] = "7"
    live["spec"]["selector"] = {"matchLabels": {"controller-uid": "abc"}}
    live["spec"]["template"]["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = "abc"
    live["status"] = {"succeeded": 1}
    state = {"body": live}

    def handler(method: str, url: str) -> tuple[int, dict | None]:
        if method == "GET":
            return 200, state["body"]
        if method == "DELETE":
            return 200, {}
        if method == "POST":
            return 201, {"metadata": {"name": "wordpress-init"}}
        return 500, {"code": 500}

    opener = _Handler(handler)
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    await client.apply(desired)
    assert [method for method, _url in opener.calls] == ["GET"]
    state["body"] = {
        **live,
        "status": {"succeeded": 0, "conditions": [{"type": "Failed", "status": "True"}]},
    }
    opener.calls.clear()
    await client.apply(desired)
    assert [method for method, _url in opener.calls] == ["GET", "DELETE", "POST"]
    assert all(method != "PUT" for method, _url in opener.calls)
    assert "super-secret-token" not in " ".join(url for _method, url in opener.calls)


@pytest.mark.asyncio
async def test_running_job_with_the_same_template_is_left_alone() -> None:
    store_id = "0123456789abcdef"
    desired = _job(store_id)
    live = json.loads(json.dumps(desired))
    live["status"] = {"active": 1, "succeeded": 0}
    opener = _Handler(lambda method, url: (200, live) if method == "GET" else (500, {"code": 500}))
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    await client.apply(desired)
    assert [method for method, _url in opener.calls] == ["GET"]
    live["spec"]["template"]["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = "abc"
    opener.calls.clear()
    await client.apply(desired)
    assert [method for method, _url in opener.calls] == ["GET"]


@pytest.mark.asyncio
async def test_existing_statefulset_keeps_its_live_claim_template() -> None:
    from apps.gateway.app.stores.manifests import StoreSpec, workload_resources

    store_id = "0123456789abcdef"
    spec = StoreSpec(
        store_id=store_id,
        owner_id="abc123def4567890",
        namespace=namespace_for(store_id),
        host=f"{store_id}.stores.local",
        title="North Shop",
        wordpress_image="wordpress:6.8-php8.2-apache",
        mariadb_image="mariadb:11.4",
        cli_image="wordpress:cli-php8.2",
        mcp_image="urumi-woocommerce-mcp:local",
        gateway_namespace="default",
        ingress_namespace="ingress-nginx",
        ingress_class="nginx",
        storage_class="standard",
        allow_plugin_egress=False,
        probe_cidrs=(),
    )
    desired = next(item for item in workload_resources(spec) if item["kind"] == "StatefulSet")
    live = json.loads(json.dumps(desired))
    live["metadata"]["resourceVersion"] = "11"
    live_templates = [
        {
            "metadata": {"name": "data", "labels": {"app.kubernetes.io/component": "store-db"}},
            "spec": {"accessModes": ["ReadWriteOnce"], "volumeMode": "Filesystem"},
        }
    ]
    live["spec"]["volumeClaimTemplates"] = live_templates
    sent: list[dict] = []

    class _Capture:
        def open(self, request: object, timeout: float | None = None) -> _Response:
            method = str(getattr(request, "method"))
            if method == "GET":
                return _Response(200, live)
            if method == "PUT":
                sent.append(json.loads(getattr(request, "data").decode()))
                return _Response(200, live)
            return _Response(500, {"code": 500})

    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=_Capture())
    await client.apply(desired)
    assert len(sent) == 1
    assert sent[0]["spec"]["volumeClaimTemplates"] == live_templates
    assert sent[0]["metadata"]["resourceVersion"] == "11"
    assert desired["spec"]["volumeClaimTemplates"][0]["metadata"]["labels"]["urumi.dev/store-id"] == store_id


@pytest.mark.asyncio
async def test_existing_namespace_and_pvc_are_not_replaced() -> None:
    store_id = "0123456789abcdef"
    namespace = namespace_for(store_id)
    labels = {"urumi.dev/store-id": store_id}

    def handler(method: str, url: str) -> tuple[int, dict | None]:
        if method == "GET" and url.endswith(f"/namespaces/{namespace}"):
            return 200, {"kind": "Namespace", "metadata": {"name": namespace, "labels": labels}}
        if method == "GET" and url.endswith("/persistentvolumeclaims/wordpress-data"):
            return 200, {
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "wordpress-data", "namespace": namespace, "labels": labels},
            }
        return 500, {"code": 500}

    opener = _Handler(handler)
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    await client.apply({"kind": "Namespace", "metadata": {"name": namespace, "labels": labels}})
    await client.apply(
        {
            "kind": "PersistentVolumeClaim",
            "apiVersion": "v1",
            "metadata": {"name": "wordpress-data", "namespace": namespace, "labels": labels},
        }
    )
    assert [method for method, _url in opener.calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_volume_cleanup_deletes_only_released_volumes_for_that_store() -> None:
    store_id = "0123456789abcdef"
    namespace = namespace_for(store_id)
    other = namespace_for("fedcba9876543210")
    listed = {
        "items": [
            {
                "metadata": {"name": "pvc-demo"},
                "spec": {"claimRef": {"namespace": "default", "name": "wordpress-data"}},
                "status": {"phase": "Released"},
            },
            {
                "metadata": {"name": "pvc-other-store"},
                "spec": {"claimRef": {"namespace": other, "name": "wordpress-data"}},
                "status": {"phase": "Released"},
            },
            {
                "metadata": {"name": "pvc-store-released"},
                "spec": {"claimRef": {"namespace": namespace, "name": "wordpress-data"}},
                "status": {"phase": "Released"},
            },
            {
                "metadata": {"name": "pvc-store-bound"},
                "spec": {"claimRef": {"namespace": namespace, "name": "data-store-db-0"}},
                "status": {"phase": "Bound"},
            },
        ]
    }

    def handler(method: str, url: str) -> tuple[int, dict | None]:
        if method == "GET" and url.endswith("/api/v1/persistentvolumes?limit=100"):
            return 200, listed
        if method == "DELETE" and url.endswith("/pvc-store-released"):
            return 200, {}
        return 500, {"code": 500}

    opener = _Handler(handler)
    client = HttpClusterClient("https://kubernetes.example", "super-secret-token", opener=opener)
    finished = await client.release_store_volumes(namespace, store_id)
    assert finished is False
    deleted = [url for method, url in opener.calls if method == "DELETE"]
    assert len(deleted) == 1 and deleted[0].endswith("/pvc-store-released")
    assert all("default" not in url.split("/persistentvolumes/", 1)[-1] for url in deleted)
    assert all(other not in url for url in deleted)
    from apps.gateway.app.stores.cluster import _guard_path

    with pytest.raises(OwnershipError):
        _guard_path("GET", "/api/v1/secrets", None)
    with pytest.raises(OwnershipError):
        _guard_path("DELETE", "/api/v1/persistentvolumes/../default", None)


@pytest.mark.asyncio
async def test_store_stays_deleting_until_volume_cleanup_finishes() -> None:
    from datetime import datetime, timezone

    from apps.gateway.app.config import Settings, StoreProvisionerSettings
    from apps.gateway.app.passwords import hash_password
    from apps.gateway.app.registry import Registry
    from apps.gateway.app.store import InMemoryStore
    from apps.gateway.app.stores.repository import get_store
    from apps.gateway.app.stores.service import StoreService
    from tests.store_cluster import FakeCluster

    class Volumes(FakeCluster):
        def __init__(self) -> None:
            super().__init__()
            self.clean = False
            self.sweeps: list[str] = []

        async def release_store_volumes(self, namespace: str, store_id: str) -> bool:
            from apps.gateway.app.stores.cluster import assert_store_namespace

            assert_store_namespace(namespace, store_id)
            self.sweeps.append(namespace)
            return self.clean

    memory = InMemoryStore()
    user = await memory.create_user("ada", hash_password("password-value-ok"), "admin")
    cluster = Volumes()
    service = StoreService(
        memory,
        Registry([]),
        Settings(upstreams=[], stores=StoreProvisionerSettings(enabled=True)),
        cluster,
        autostart=False,
        now=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
    )
    record, kind = await service.create(user.user_id, "North Shop", "idem-north-1")
    assert kind == "created" and record is not None
    await service.request_delete(user.user_id, record.store_id)
    await service.reconcile_store(record.store_id)
    waiting = await get_store(memory, record.store_id)
    assert waiting is not None and waiting.state == "deleting"
    assert cluster.sweeps == [record.namespace]
    cluster.clean = True
    await service.reconcile_store(record.store_id)
    finished = await get_store(memory, record.store_id)
    assert finished is not None and finished.state == "deleted"
    assert cluster.sweeps == [record.namespace, record.namespace]
