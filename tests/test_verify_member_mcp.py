"""Unit tests for the loopback member-key check. No live network or real key."""

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_member_mcp.py"
_SPEC = importlib.util.spec_from_file_location("verify_member_mcp", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
client = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(client)

_SECRET = "A" * 43
_KEY_ID = "580f05842baff2e9"
_OTHER_ID = "0011223344556677"
_KEY = f"urk_{_KEY_ID}.{_SECRET}"
_OTHER = f"urk_{_OTHER_ID}.{_SECRET}"
_URL = "http://127.0.0.1:18000/mcp"


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.is_error = is_error
        self.content = [_Text(text)]


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _Session:
    def __init__(self, names: list[str], calls: dict[str, _Result]) -> None:
        self._names = names
        self._calls = calls

    async def list_tools(self) -> object:
        return type("Listed", (), {"tools": [_Tool(name) for name in self._names]})()

    async def call_tool(self, name: str, arguments: dict) -> _Result:
        del arguments
        return self._calls[name]


def _install_transport(monkeypatch: pytest.MonkeyPatch, session: _Session, seen: list[tuple[str, str]]) -> None:
    @asynccontextmanager
    async def fake_open(url: str, api_key: str | None = None):
        seen.append((url, api_key or ""))
        yield session

    monkeypatch.setattr(client, "open_mcp", fake_open)


def _success_session() -> _Session:
    return _Session(
        ["utility__convert_units", "support__search_notes"],
        {
            "support__search_notes": _Result("Shipping: Standard shipping takes 3 business days."),
            "utility__convert_units": _Result("2000"),
            "woocommerce__list_products": _Result("Permission denied.", is_error=True),
        },
    )


def _assert_hidden(captured: object, *secrets: str) -> None:
    output = captured.out + captured.err  # type: ignore[attr-defined]
    for secret in secrets:
        assert secret not in output
    assert "Authorization" not in output
    assert "Bearer" not in output


def test_rejects_unsafe_endpoints_before_reading_a_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail_prompt() -> str:
        raise AssertionError("prompt")

    monkeypatch.setattr(client.getpass, "getpass", fail_prompt)
    monkeypatch.delenv(client._ENV_NAME, raising=False)
    rejected = [
        "http://127.0.0.1:8000/mcp",
        "https://127.0.0.1:18000/mcp",
        "http://example.com:18000/mcp",
        "http://127.0.0.1:18000/health",
        "http://127.0.0.1:18000/mcp?x=1",
        "http://user:pass@127.0.0.1:18000/mcp",
    ]
    for url in rejected:
        assert client.main(["--url", url, "--expect-key-id", _KEY_ID]) == 2
    captured = capsys.readouterr()
    assert captured.err.count("endpoint rejected") == len(rejected)
    _assert_hidden(captured, "pass")


def test_prompt_is_hidden_and_the_environment_value_is_not_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, str]] = []
    _install_transport(monkeypatch, _success_session(), seen)
    prompted: list[str] = []

    def prompt(label: str) -> str:
        prompted.append(label)
        return _KEY

    monkeypatch.setattr(client.getpass, "getpass", prompt)
    monkeypatch.delenv(client._ENV_NAME, raising=False)
    assert client.main(["--expect-key-id", _KEY_ID]) == 0
    assert prompted == ["Gateway API key: "]
    assert seen == [(_URL, _KEY)]

    monkeypatch.setenv(client._ENV_NAME, _OTHER)
    prompted.clear()
    seen.clear()
    _install_transport(monkeypatch, _success_session(), seen)
    assert client.main(["--url", _URL, "--expect-key-id", _OTHER_ID]) == 0
    assert prompted == []
    assert seen == [(_URL, _OTHER)]
    _assert_hidden(capsys.readouterr(), _KEY, _OTHER, _SECRET)


def test_key_id_mismatch_does_not_open_a_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_open(url: str, api_key: str | None = None):
        del url, api_key
        raise AssertionError("opened")

    monkeypatch.setattr(client, "open_mcp", fail_open)
    monkeypatch.setattr(client.getpass, "getpass", lambda _label: _KEY)
    monkeypatch.delenv(client._ENV_NAME, raising=False)
    assert client.main(["--expect-key-id", _OTHER_ID]) == 2
    captured = capsys.readouterr()
    assert "key id does not match" in captured.err
    _assert_hidden(captured, _KEY, _SECRET)


def test_classifies_the_read_only_calls_and_the_product_denial(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, str]] = []
    _install_transport(monkeypatch, _success_session(), seen)
    monkeypatch.setenv(client._ENV_NAME, _KEY)
    assert client.main(["--expect-key-id", _KEY_ID]) == 0
    captured = capsys.readouterr()
    assert "key_id 580f05842baff2e9" in captured.out
    assert "tools/list PASS support__search_notes utility__convert_units" in captured.out
    assert "support__search_notes PASS" in captured.out
    assert "utility__convert_units PASS" in captured.out
    assert "woocommerce__list_products PASS" in captured.out
    assert "3 business days" not in captured.out
    assert "Permission denied." not in captured.out
    _assert_hidden(captured, _KEY, _SECRET)


def test_failure_text_is_redacted(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    @asynccontextmanager
    async def explode(url: str, api_key: str | None = None):
        del url
        raise RuntimeError(f"transport failed for {api_key}")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "open_mcp", explode)
    monkeypatch.setenv(client._ENV_NAME, _KEY)
    assert client.main(["--expect-key-id", _KEY_ID]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "verification failed"
    _assert_hidden(captured, _KEY, _SECRET)


def test_rejects_a_key_passed_as_an_argument(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(client._ENV_NAME, raising=False)
    assert client.main(["--url", _URL, _KEY]) == 2
    captured = capsys.readouterr()
    assert "cannot be passed as an argument" in captured.err
    _assert_hidden(captured, _KEY, _SECRET)
