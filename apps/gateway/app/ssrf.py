"""Reject upstream URLs that can reach the local machine or cloud metadata.

Private addresses are allowed only when the operator sets that policy, because
in-cluster MCP services use cluster addresses. Loopback, link-local, multicast,
and known metadata destinations are always rejected.

Untrusted clients connect to an address returned by this check. The original
hostname stays on the Host header and on TLS SNI. A later lookup cannot move
the socket. Redirects are disabled, and any request the client does send is
checked again. Environment-defined upstreams stay outside this check unless
the operator turns trust off. Local Compose uses loopback, so that default
is what keeps the current stack working.
"""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}

_METADATA_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.google.com",
}


class UnsafeUpstreamUrl(Exception):
    """The message is fixed so a rejected URL is not echoed."""


def validate_upstream_url(url: str, *, allow_private: bool, resolve=None) -> str:
    parsed = _parsed(url)
    _permitted(parsed, allow_private=allow_private, resolve=resolve)
    return parsed.geturl()


def destination_addresses(url: str, *, allow_private: bool, resolve=None) -> list[str]:
    """Return the addresses a connection may use, in resolver order."""
    return _permitted(_parsed(url), allow_private=allow_private, resolve=resolve)


def _parsed(url: str):
    if not isinstance(url, str) or not url.strip() or len(url) > 300:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    return parsed


def _permitted(parsed, *, allow_private: bool, resolve) -> list[str]:
    host = parsed.hostname.rstrip(".").lower()
    if host in _METADATA_HOSTS or host.endswith(".localhost") or _ambiguous_address(host):
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _blocked(literal, allow_private=allow_private):
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    resolver = resolve if resolve is not None else socket.getaddrinfo
    try:
        answers = resolver(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except Exception as exc:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.") from exc
    permitted: list[str] = []
    for answer in answers:
        sockaddr = answer[-1]
        if not sockaddr:
            continue
        try:
            parsed_address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise UnsafeUpstreamUrl("That upstream URL is not allowed.") from exc
        if parsed_address.version == 6 and parsed_address.ipv4_mapped is not None:
            parsed_address = parsed_address.ipv4_mapped
        if _blocked(parsed_address, allow_private=allow_private):
            raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
        permitted.append(str(parsed_address))
    if not permitted:
        raise UnsafeUpstreamUrl("That upstream URL is not allowed.")
    return permitted


def _blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> bool:
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True
    if address in _METADATA_ADDRESSES:
        return True
    if not allow_private and (address.is_private or address.is_global is False):
        return True
    return False


def _ambiguous_address(host: str) -> bool:
    """Reject integer and padded spellings that some clients treat as addresses."""
    if host.isdigit() or host.startswith("0x") or re.fullmatch(r"0x[0-9a-f]+", host):
        return True
    labels = host.split(".")
    if len(labels) == 4 and all(label.isdigit() for label in labels):
        if any(len(label) > 1 and label.startswith("0") for label in labels):
            return True
    return False
