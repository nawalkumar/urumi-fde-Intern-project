"""Upstream URL policy. Resolution is injected, so these tests do not use the network."""

import pytest

import httpx2

from apps.gateway.app.catalog import merge_upstreams
from apps.gateway.app.config import UpstreamConfig
from apps.gateway.app.registry import PolicyTransport, _connect, upstream_http_client
from apps.gateway.app.ssrf import UnsafeUpstreamUrl, validate_upstream_url


def _public(_host, _port, type):  # noqa: A002
    del type
    return [(None, None, None, None, ("93.184.216.34", 443))]


def _loopback(_host, _port, type):  # noqa: A002
    del type
    return [(None, None, None, None, ("127.0.0.1", 80))]


def _private(_host, _port, type):  # noqa: A002
    del type
    return [(None, None, None, None, ("10.1.2.3", 80))]


def test_public_https_url_is_accepted() -> None:
    assert validate_upstream_url("https://mcp.example.test/mcp", allow_private=False, resolve=_public) == (
        "https://mcp.example.test/mcp"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:secret@mcp.example.test/mcp",
        "http://127.0.0.1/mcp",
        "http://[::1]/mcp",
        "http://169.254.169.254/latest",
        "http://metadata.google.internal/",
        "http://printer.localhost/mcp",
        "https://mcp.example.test/mcp?token=secret",
        "https://mcp.example.test/mcp#fragment",
        "http://2130706433/mcp",
        "http://0177.0.0.1/mcp",
        "http://169.254.1.1/mcp",
        "http://100.100.100.200/",
        "http://[fd00:ec2::254]/",
        "ftp://mcp.example.test/mcp",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUpstreamUrl, match="That upstream URL is not allowed."):
        validate_upstream_url(url, allow_private=True, resolve=_public)


def test_dns_answer_pointing_at_loopback_is_rejected() -> None:
    with pytest.raises(UnsafeUpstreamUrl):
        validate_upstream_url("https://mcp.example.test/mcp", allow_private=True, resolve=_loopback)


@pytest.mark.asyncio
async def test_untrusted_loopback_is_rejected_before_a_connection() -> None:
    upstream = UpstreamConfig("notes", "http://127.0.0.1/mcp", 1, trusted=False)
    with pytest.raises(UnsafeUpstreamUrl):
        async with _connect(upstream):
            pass


@pytest.mark.asyncio
async def test_upstream_client_does_not_follow_redirects() -> None:
    client = upstream_http_client(UpstreamConfig("notes", "https://mcp.example.test/mcp", 1, trusted=True))
    try:
        assert client.follow_redirects is False
    finally:
        await client.aclose()


def test_environment_upstreams_stay_trusted_until_the_operator_changes_policy() -> None:
    static = [UpstreamConfig("utility", "http://127.0.0.1:9/mcp", 1)]
    trusted = merge_upstreams(static, [], {}, timeout_seconds=1, allow_private=True, trust_environment=True)
    untrusted = merge_upstreams(static, [], {}, timeout_seconds=1, allow_private=True, trust_environment=False)
    assert trusted[0].trusted is True
    assert untrusted[0].trusted is False


class _RecordingTransport(httpx2.AsyncBaseTransport):
    def __init__(self, status: int = 200, location: str = "") -> None:
        self.seen: list[dict[str, str]] = []
        self._status = status
        self._location = location

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(
            {
                "host": request.url.host,
                "header": request.headers.get("host") or "",
                "sni": str(request.extensions.get("sni_hostname") or ""),
            }
        )
        headers = {"location": self._location} if self._location else None
        return httpx2.Response(self._status, headers=headers)

    async def aclose(self) -> None:
        return None


def _pin_resolve(address: str):
    def resolve(_host, _port, type):  # noqa: A002
        del type
        return [(None, None, None, None, (address, 443))]

    return resolve


@pytest.mark.asyncio
async def test_connection_uses_the_validated_address_and_original_hostname() -> None:
    recorder = _RecordingTransport()
    transport = PolicyTransport(allow_private=False, resolve=_pin_resolve("93.184.216.34"), inner=recorder)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as client:
        response = await client.get("https://mcp.example.test/mcp")
    assert response.status_code == 200
    assert recorder.seen == [
        {"host": "93.184.216.34", "header": "mcp.example.test", "sni": "mcp.example.test"}
    ]


@pytest.mark.asyncio
async def test_redirect_target_cannot_bypass_the_address_policy() -> None:
    recorder = _RecordingTransport(status=302, location="http://127.0.0.1/mcp")
    transport = PolicyTransport(allow_private=True, resolve=_pin_resolve("93.184.216.34"), inner=recorder)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(UnsafeUpstreamUrl):
            await client.get("https://mcp.example.test/mcp")
    assert [item["host"] for item in recorder.seen] == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_private_cluster_address_is_pinned_when_the_policy_allows_it() -> None:
    recorder = _RecordingTransport()
    transport = PolicyTransport(allow_private=True, resolve=_pin_resolve("10.1.2.3"), inner=recorder)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as client:
        response = await client.get("https://mcp.internal/mcp")
    assert response.status_code == 200
    assert recorder.seen[0]["host"] == "10.1.2.3"
    assert recorder.seen[0]["sni"] == "mcp.internal"
    blocked = PolicyTransport(allow_private=False, resolve=_pin_resolve("10.1.2.3"), inner=_RecordingTransport())
    async with httpx2.AsyncClient(transport=blocked, follow_redirects=False) as client:
        with pytest.raises(UnsafeUpstreamUrl):
            await client.get("https://mcp.internal/mcp")


@pytest.mark.asyncio
async def test_ipv6_destination_keeps_the_original_server_name() -> None:
    recorder = _RecordingTransport()
    transport = PolicyTransport(
        allow_private=False,
        resolve=_pin_resolve("2001:4860:4860::8888"),
        inner=recorder,
    )
    async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as client:
        response = await client.get("https://mcp.example.test/mcp")
    assert response.status_code == 200
    assert recorder.seen[0]["host"] == "2001:4860:4860::8888"
    assert recorder.seen[0]["header"] == "mcp.example.test"
    assert recorder.seen[0]["sni"] == "mcp.example.test"


@pytest.mark.asyncio
async def test_untrusted_client_does_not_follow_redirects() -> None:
    client = upstream_http_client(
        UpstreamConfig("notes", "https://mcp.example.test/mcp", 1, trusted=False, allow_private=False)
    )
    try:
        assert client.follow_redirects is False
        assert isinstance(client._transport, PolicyTransport)
    finally:
        await client.aclose()


def test_private_addresses_follow_the_operator_policy() -> None:
    with pytest.raises(UnsafeUpstreamUrl):
        validate_upstream_url("http://mcp.internal/mcp", allow_private=False, resolve=_private)
    assert validate_upstream_url("http://mcp.internal/mcp", allow_private=True, resolve=_private) == (
        "http://mcp.internal/mcp"
    )
