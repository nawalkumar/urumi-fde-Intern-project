"""HttpChatProvider logs a status and a sanitized detail, never a raw body or key."""

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
    folded = text.casefold()
    assert _KEY not in text
    assert _BODY not in text
    assert _PAYLOAD not in text
    assert "authorization" not in folded
    assert "bearer" not in folded


@pytest.mark.asyncio
async def test_provider_http_error_logs_a_sanitized_detail(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        assert isinstance(payload, dict)
        assert _PAYLOAD in str(payload["messages"])
        return _Response(
            429,
            {"error": {"message": "  model \n not \t found  ", "body": _BODY}, "request": _PAYLOAD},
        )

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer") as caught:
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert str(caught.value) == "The chat provider could not answer."
    assert "status=429" in caplog.text
    assert "detail=model not found" in caplog.text
    _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_http_error_caps_the_detail(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        del payload
        return _Response(400, {"error": {"message": "n" * 180, "body": _BODY}})

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer"):
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert "detail=" + ("n" * 160) in caplog.text
    assert "n" * 161 not in caplog.text
    _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_malformed_error_json_logs_unreadable(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def post(payload: object) -> _Response:
        del payload
        return _Response(400, broken=True)

    _install(monkeypatch, post)
    with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
        with pytest.raises(ProviderError, match="could not answer"):
            await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
    assert "status=400" in caplog.text
    assert "detail=unreadable" in caplog.text
    _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_http_error_redacts_sensitive_details(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    samples = (
        f"rejected {_KEY} after 160",
        ("n" * 160) + _KEY,
        "Authorization header was rejected",
        "Bearer token was rejected",
    )
    for sample in samples:
        caplog.clear()

        def post(payload: object, sample: str = sample) -> _Response:
            del payload
            return _Response(400, {"error": {"message": sample, "body": _BODY}})

        _install(monkeypatch, post)
        with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
            with pytest.raises(ProviderError, match="could not answer"):
                await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
        assert "status=400" in caplog.text
        assert "detail=redacted" in caplog.text
        assert sample not in caplog.text
        _assert_log_is_safe(caplog)


@pytest.mark.asyncio
async def test_provider_http_error_redacts_credential_shapes(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    samples = (
        "bearer token was rejected",
        "authorization header was rejected",
        "rejected token sk-other-value",
    )
    for sample in samples:
        caplog.clear()

        def post(payload: object, sample: str = sample) -> _Response:
            del payload
            return _Response(400, {"error": {"message": sample, "body": _BODY}})

        _install(monkeypatch, post)
        with caplog.at_level(logging.WARNING, logger="apps.gateway.app.chat_provider"):
            with pytest.raises(ProviderError, match="could not answer") as caught:
                await _provider().complete(messages=[{"role": "user", "content": _PAYLOAD}], tools=[])
        assert str(caught.value) == "The chat provider could not answer."
        assert "status=400" in caplog.text
        assert "detail=redacted" in caplog.text
        assert sample not in caplog.text
        assert "sk-other-value" not in caplog.text
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
