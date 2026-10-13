"""Upstream addresses for the gateway. No credentials are read here."""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ENABLED = {"1", "true", "yes", "on"}
_DISABLED = {"0", "false", "no", "off"}
_STATUS_OPEN = {"", "open"}
_STATUS_REQUIRED = {"required"}


class StatusPermissionConfigError(Exception):
    """GATEWAY_STATUS_PERMISSION is not a recognized mode. The message is fixed."""


@dataclass(frozen=True)
class UpstreamConfig:
    name: str
    url: str
    timeout_seconds: float
    enabled: bool = True
    authorization: str = ""
    trusted: bool = True
    allow_private: bool = True
    credential_unreadable: bool = False

    def __repr__(self) -> str:
        return f"UpstreamConfig(name={self.name!r}, enabled={self.enabled!r}, trusted={self.trusted!r})"


@dataclass(frozen=True)
class Settings:
    upstreams: list[UpstreamConfig]
    database_url: str = ""
    status_permission: str = "open"
    upstream_timeout_seconds: float = 5
    allow_private_upstreams: bool = True
    cookie_secure: bool = False
    trust_environment_upstreams: bool = True
    chat_max_tool_calls: int = 4
    chat_provider_timeout_seconds: float = 20

    @classmethod
    def from_env(cls) -> "Settings":
        timeout = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "5"))
        allow_private = _flag(os.environ.get("UPSTREAM_ALLOW_PRIVATE"), default=True)
        cookie_secure = _flag(os.environ.get("GATEWAY_COOKIE_SECURE"), default=False)
        trust_environment = _flag(os.environ.get("UPSTREAM_TRUST_ENVIRONMENT"), default=True)
        chat_max_tool_calls = _bounded_int(os.environ.get("CHAT_MAX_TOOL_CALLS"), default=4, low=1, high=8)
        chat_timeout = _bounded_float(
            os.environ.get("CHAT_PROVIDER_TIMEOUT_SECONDS"),
            default=20,
            low=1,
            high=60,
        )
        upstreams: list[UpstreamConfig] = []
        for name, variable in (
            ("woocommerce", "WOOCOMMERCE_MCP_URL"),
            ("support", "SUPPORT_MCP_URL"),
            ("utility", "UTILITY_MCP_URL"),
        ):
            url = os.environ.get(variable, "").strip()
            if url:
                upstreams.append(
                    UpstreamConfig(
                        name=name,
                        url=url,
                        timeout_seconds=timeout,
                        enabled=_enabled(f"{variable.removesuffix('_URL')}_ENABLED"),
                    )
                )
        database_url = os.environ.get("GATEWAY_DATABASE_URL", "").strip()
        return cls(
            upstreams=upstreams,
            database_url=database_url,
            status_permission=status_permission_mode(os.environ.get("GATEWAY_STATUS_PERMISSION")),
            upstream_timeout_seconds=timeout,
            allow_private_upstreams=allow_private,
            cookie_secure=cookie_secure,
            trust_environment_upstreams=trust_environment,
            chat_max_tool_calls=chat_max_tool_calls,
            chat_provider_timeout_seconds=chat_timeout,
        )


def status_permission_mode(raw: str | None) -> str:
    """Return open or required. Unset and empty stay open.

    An unrecognized value raises. It must not fall through to open access,
    and the raw value is not included in the error.
    """
    if raw is None:
        return "open"
    value = raw.strip().casefold()
    if value in _STATUS_OPEN:
        return "open"
    if value in _STATUS_REQUIRED:
        return "required"
    raise StatusPermissionConfigError("GATEWAY_STATUS_PERMISSION must be empty, open, or required.")


def _bounded_int(raw: str | None, *, default: int, low: int, high: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return min(high, max(low, value))


def _bounded_float(raw: str | None, *, default: float, low: float, high: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return min(high, max(low, value))


def _flag(raw: str | None, *, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in _ENABLED:
        return True
    if value in _DISABLED:
        return False
    return default


def _enabled(variable: str) -> bool:
    """Read an upstream enable flag. Empty means enabled. Unrecognized values stay disabled."""
    raw = os.environ.get(variable)
    if raw is None or not raw.strip():
        return True
    value = raw.strip().casefold()
    if value in _ENABLED:
        return True
    if value in _DISABLED:
        return False
    logger.warning("Unrecognized enable flag %s; that upstream stays disabled.", variable)
    return False
