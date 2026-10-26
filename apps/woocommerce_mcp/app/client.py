"""WooCommerce REST API client.

Plain HTTP stores use OAuth 1.0a in the Authorization header. The consumer
secret is only used to sign the request and is not sent. HTTPS stores use
HTTP basic auth. Credentials are never placed in the URL.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2

from apps.woocommerce_mcp.app.config import WooSettings


class WooError(Exception):
    """A WooCommerce call failed in a way that is safe to show to a client."""


class WooClient:
    def __init__(self, settings: WooSettings, sender: Any | None = None) -> None:
        self._settings = settings
        self._sender = sender if sender is not None else _HttpxSender()
        self._use_oauth = urlsplit(settings.base_url).scheme == "http"

    def get_list(self, path: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._request("GET", path, query=query)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise WooError("WooCommerce returned a malformed response.")
        return payload

    def get_object(self, path: str) -> dict[str, Any]:
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise WooError("WooCommerce returned a malformed response.")
        return payload

    def post_object(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._request("POST", path, body=body)
        if not isinstance(payload, dict):
            raise WooError("WooCommerce returned a malformed response.")
        return payload

    def put_object(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._request("PUT", path, body=body)
        if not isinstance(payload, dict):
            raise WooError("WooCommerce returned a malformed response.")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        params = {key: str(value) for key, value in (query or {}).items()}
        url = f"{self._settings.base_url}/wp-json/wc/v3{path}"
        if self._use_oauth:
            headers = {"Authorization": _oauth_header(method, _signature_url(self._settings, url), params, self._settings)}
            auth = None
        else:
            headers = {}
            auth = (self._settings.consumer_key, self._settings.consumer_secret)
        try:
            response = self._sender.request(
                method,
                url,
                params=params,
                json=body,
                auth=auth,
                headers=headers,
                timeout=self._settings.timeout_seconds,
            )
        except httpx2.TimeoutException as exc:
            raise WooError("WooCommerce request timed out.") from exc
        except httpx2.HTTPError as exc:
            raise WooError("WooCommerce could not be reached.") from exc

        payload = _decode(response)
        if 200 <= response.status_code < 300:
            return payload
        if 300 <= response.status_code < 400:
            raise WooError(_redirect_message(response))
        if response.status_code in {401, 403}:
            raise WooError("WooCommerce rejected the API credentials.")
        if response.status_code == 404:
            raise WooError("WooCommerce could not find that resource.")
        detail = _safe_detail(payload, self._settings.consumer_key, self._settings.consumer_secret)
        if detail is None:
            raise WooError(f"WooCommerce returned HTTP {response.status_code}.")
        return _raise_http(response.status_code, detail)


def _redirect_message(response: Any) -> str:
    """Name the WordPress installer redirect. The Location value is not echoed."""
    headers = getattr(response, "headers", None) or {}
    location = headers.get("location") or ""
    path = urlsplit(location).path if isinstance(location, str) else ""
    if path.rstrip("/").endswith("/wp-admin/install.php"):
        return (
            "WordPress is not installed, so WooCommerce cannot answer "
            f"(HTTP {response.status_code} to the installer). The store database may be empty."
        )
    return f"WooCommerce redirected the request (HTTP {response.status_code}). Redirects are not followed."


def _raise_http(status_code: int, detail: str) -> Any:
    raise WooError(f"WooCommerce returned HTTP {status_code}: {detail}")


def _decode(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        if 200 <= response.status_code < 300:
            raise WooError("WooCommerce returned a malformed response.") from None
        return None


def _safe_detail(payload: Any, key: str, secret: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    text = " ".join(message.split())
    if not text or len(text) > 240:
        return None
    lowered = text.casefold()
    if key.casefold() in lowered or secret.casefold() in lowered:
        return None
    if "ck_" in lowered or "cs_" in lowered:
        return None
    return text


def _signature_url(settings: WooSettings, request_url: str) -> str:
    site = (settings.site_url or settings.base_url).rstrip("/")
    return site + urlsplit(request_url).path


def _oauth_header(method: str, signature_url: str, query: dict[str, str], settings: WooSettings) -> str:
    oauth = {
        "oauth_consumer_key": settings.consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(time.time())),
    }
    signature = _oauth_signature(method, signature_url, {**query, **oauth}, settings.consumer_secret)
    oauth["oauth_signature"] = signature
    pairs = ", ".join(f'{key}="{_rfc3986(value)}"' for key, value in oauth.items())
    return f"OAuth {pairs}"


def _oauth_signature(method: str, signature_url: str, params: dict[str, str], secret: str) -> str:
    normalized = {_rfc3986(key): _rfc3986(value) for key, value in params.items()}
    encoded_pairs = [_rfc3986(f"{key}={normalized[key]}") for key in sorted(normalized)]
    string_to_sign = "&".join((method.upper(), _rfc3986(signature_url), "%26".join(encoded_pairs)))
    digest = hmac.new(f"{secret}&".encode(), string_to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _rfc3986(value: str) -> str:
    return quote(str(value), safe="~")


class _HttpxSender:
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict,
        json: dict | None,
        auth: object,
        headers: dict,
        timeout: float,
    ) -> Any:
        with httpx2.Client(timeout=timeout) as client:
            return client.request(method, url, params=params, json=json, auth=auth, headers=headers)
