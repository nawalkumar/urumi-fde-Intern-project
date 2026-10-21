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
