"""HttpChatProvider logs a status or a failure class, never a provider body or key."""

import logging

import pytest

from apps.gateway.app.chat_provider import HttpChatProvider, ProviderError

_KEY = "test-provider-key"
_BODY = "provider-body-sentinel"
_PAYLOAD = "payload-sentinel"


class _Response:
    def __init__(self, status_code: object, payload: object | None = None, broken: bool = False) -> None:
        self.status_code = status_code
        self.text = _BODY
        self._payload = payload
        self._broken = broken

    def json(self) -> object:
        if self._broken:
            raise ValueError(_BODY)
        return self._payload


def _install(monkeypatch: pytest.MonkeyPatch, post) -> None:
    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> bool:
            del args
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object | None = None) -> _Response:
            del url, headers
            return post(json)

    monkeypatch.setattr("apps.gateway.app.chat_provider.httpx2.AsyncClient", _Client)


def _provider() -> HttpChatProvider:
    return HttpChatProvider(url="https://provider.example/v1/chat/completions", api_key=_KEY, model="test-model", timeout=1)


def _assert_log_is_safe(caplog: pytest.LogCaptureFixture) -> None:
    text = caplog.text
    assert _KEY not in text
    assert _BODY not in text
    assert _PAYLOAD not in text
    assert "Authorization" not in text


@pytest.mark.asyncio
async def test_provider_http_error_logs_only_the_status(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        assert isinstance(payload, dict)
        assert _PAYLOAD in str(payload["messages"])
        return _Response(429, {"error": _BODY})

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer"):
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert "status=429" in caplog.text
    _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_transport_failure_logs_only_the_class(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        del payload
        raise RuntimeError(_BODY)

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer"):
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert "Chat provider failed: transport" in caplog.text
    _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_unreadable_response_logs_only_the_class(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        del payload
        return _Response(200, broken=True)

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer"):
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert "Chat provider failed: unreadable" in caplog.text
    _assert_log_is_safe(caplog)
