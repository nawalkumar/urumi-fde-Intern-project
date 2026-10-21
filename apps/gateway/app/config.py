"""Upstream addresses for the gateway. No credentials are read here."""

import ipaddress
import logging
import os
import re
from dataclasses import dataclass, field

from apps.gateway.app.stores.model import validate_domain, validate_namespace_name

_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,180}:[A-Za-z0-9._-]{1,64}")

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
class StoreProvisionerSettings:
    """Process configuration for store provisioning. Defaults keep it off."""

    enabled: bool = False
    max_per_user: int = 3
    timeout_seconds: int = 600
    concurrency: int = 2
    max_attempts: int = 5
    ingress_base_domain: str = "stores.local"
    gateway_namespace: str = "default"
    ingress_namespace: str = "ingress-nginx"
    ingress_class: str = "nginx"
    allow_plugin_egress: bool = False
    storage_class: str = ""
    wordpress_image: str = "wordpress:6.8-php8.2-apache"
    mariadb_image: str = "mariadb:11.4"
    cli_image: str = "wordpress:cli-php8.2"
    mcp_image: str = "urumi-woocommerce-mcp:local"
    probe_cidrs: tuple[str, ...] = ()
    cluster_reconciler: bool = False


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
    stores: StoreProvisionerSettings = field(default_factory=StoreProvisionerSettings)

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
        stores = _store_settings()
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
            stores=stores,
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


def _store_settings() -> StoreProvisionerSettings:
    domain = os.environ.get("STORE_INGRESS_BASE_DOMAIN", "").strip().lower()
    if not validate_domain(domain):
        domain = "stores.local"
    gateway_namespace = _namespace_setting("STORE_GATEWAY_NAMESPACE", "default")
    ingress_namespace = _namespace_setting("STORE_INGRESS_NAMESPACE", "ingress-nginx")
    storage = os.environ.get("STORE_STORAGE_CLASS", "").strip()
    if storage and not validate_namespace_name(storage):
        storage = ""
    return StoreProvisionerSettings(
        enabled=_flag(os.environ.get("STORE_PROVISIONER_ENABLED"), default=False),
        max_per_user=_bounded_int(os.environ.get("STORE_MAX_PER_USER"), default=3, low=1, high=20),
        timeout_seconds=_bounded_int(os.environ.get("STORE_PROVISION_TIMEOUT_SECONDS"), default=600, low=30, high=3600),
        concurrency=_bounded_int(os.environ.get("STORE_PROVISION_CONCURRENCY"), default=2, low=1, high=8),
        max_attempts=_bounded_int(os.environ.get("STORE_PROVISION_MAX_ATTEMPTS"), default=5, low=1, high=20),
        ingress_base_domain=domain,
        gateway_namespace=gateway_namespace,
        ingress_namespace=ingress_namespace,
        ingress_class=_label_setting("STORE_INGRESS_CLASS", "nginx"),
        allow_plugin_egress=_flag(os.environ.get("STORE_ALLOW_PLUGIN_EGRESS"), default=False),
        storage_class=storage,
        wordpress_image=_image("STORE_IMAGE_WORDPRESS", "wordpress:6.8-php8.2-apache"),
        mariadb_image=_image("STORE_IMAGE_MARIADB", "mariadb:11.4"),
        cli_image=_image("STORE_IMAGE_WORDPRESS_CLI", "wordpress:cli-php8.2"),
        mcp_image=_image("STORE_IMAGE_WOO_MCP", "urumi-woocommerce-mcp:local"),
        probe_cidrs=_cidrs(os.environ.get("STORE_PROBE_CIDRS")),
        cluster_reconciler=_flag(os.environ.get("STORE_CLUSTER_RECONCILER"), default=False),
    )


def _namespace_setting(variable: str, default: str) -> str:
    raw = os.environ.get(variable, "").strip()
    if raw and validate_namespace_name(raw):
        return raw
    return default


def _label_setting(variable: str, default: str) -> str:
    raw = os.environ.get(variable, "").strip()
    if raw and validate_namespace_name(raw):
        return raw
    return default


def _image(variable: str, default: str) -> str:
    raw = os.environ.get(variable, "").strip()
    if raw and _IMAGE.fullmatch(raw) and ".." not in raw and "@" not in raw:
        return raw
    return default


def _probe_network(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    """Accept a kubelet-sized probe route and reject a default or wide network.

    IPv4 must be /24 or longer. IPv6 must be /120 or longer. 0.0.0.0/0 and
    ::/0 fail that test. These routes are not added to MariaDB ingress.
    """
    if network.version == 4:
        return network.prefixlen >= 24
    return network.prefixlen >= 120


def _cidrs(raw: str | None) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return ()
    values: list[str] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            continue
        if not _probe_network(network):
            logger.warning("Ignoring a store probe network that is too broad.")
            continue
        values.append(str(network))
    return tuple(values[:8])


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
