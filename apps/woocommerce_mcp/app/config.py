"""WooCommerce REST settings.

Values are read from the environment only. They are not logged. Tools stay
unregistered until the URL, consumer key, and consumer secret are all usable.
"""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class WooSettings:
    base_url: str
    consumer_key: str
    consumer_secret: str
    timeout_seconds: float
    site_url: str = ""


def load_settings() -> WooSettings | None:
    url = os.environ.get("WOOCOMMERCE_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("WOOCOMMERCE_CONSUMER_KEY", "").strip()
    secret = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET", "").strip()
    if not url or not key or not secret:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    site_url = os.environ.get("WOOCOMMERCE_SITE_URL", "").strip().rstrip("/") or url
    return WooSettings(
        base_url=url,
        consumer_key=key,
        consumer_secret=secret,
        timeout_seconds=_timeout(),
        site_url=site_url,
    )


def api_state() -> str:
    if load_settings() is None:
        return "not_configured"
    return "configured"


def _timeout() -> float:
    raw = os.environ.get("WOOCOMMERCE_TIMEOUT_SECONDS", "8").strip()
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    if value <= 0:
        return 8.0
    return value
