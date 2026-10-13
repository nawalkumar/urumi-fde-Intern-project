"""Gateway API key parsing, hashing, and principals.

The raw key is returned only by KeyStore.issue. It is never written to the
store. A Principal is sealed in this process after the secret hash matches.
Dashboard chat must obtain that object from Authenticator and pass it to
execute_tool. A caller-supplied string is not a principal.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

KEY_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,64}")
_DUMMY_HASH = hashlib.sha256(b"urumi-gateway-dummy-key").hexdigest()
_PROCESS_SEAL = secrets.token_bytes(32)

UNAUTHENTICATED = "unauthenticated"


class StoreUnavailable(Exception):
    """The gateway key store could not be used. The message is fixed and safe."""


@dataclass(frozen=True)
class IssuedKey:
    """Raw key material returned once to the operator who provisioned it."""

    key_id: str
    raw_key: str


@dataclass(frozen=True)
class KeyRow:
    key_id: str
    secret_hash: str
    created_at: object
    revoked_at: object
    user_id: str | None = None


@dataclass(frozen=True)
class Principal:
    """A gateway key this process has authenticated.

    key_id is the public identifier. seal is proof this process issued the
    object and is omitted from repr.
    """

    key_id: str
    seal: str

    def __repr__(self) -> str:
        return f"Principal(key_id={self.key_id!r})"


def hash_key(raw_key: str) -> str:
    """SHA-256 of the full key. The key is high-entropy, so a slow password hash is unnecessary."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return key id, raw key, and hash. The caller stores only the id and hash."""
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    raw_key = f"urk_{key_id}.{secret}"
    return key_id, raw_key, hash_key(raw_key)


def parse_raw_key(raw_key: str | None) -> str | None:
    """Return the key id when the token has the issued shape.

    A parsed id is not authentication. Unknown and revoked ids produce the same
    client error as a malformed token.
    """
    if raw_key is None or not isinstance(raw_key, str) or len(raw_key) > 200:
        return None
    prefix, dot, secret = raw_key.partition(".")
    if dot != "." or not prefix.startswith("urk_"):
        return None
    key_id = prefix[4:]
    if KEY_ID_PATTERN.fullmatch(key_id) is None or _SECRET_PATTERN.fullmatch(secret) is None:
        return None
    return key_id


def bearer_token(header: str | None) -> str | None:
    """Return the token from an Authorization header using the Bearer scheme."""
    if header is None or not isinstance(header, str) or len(header) > 300:
        return None
    scheme, separator, rest = header.partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        return None
    token = rest.strip()
    if not token or any(character.isspace() for character in token):
        return None
    return token


def seal_principal(key_id: str) -> Principal:
    digest = hmac.new(_PROCESS_SEAL, key_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return Principal(key_id=key_id, seal=digest)


def seal_matches(principal: Principal) -> bool:
    expected = hmac.new(_PROCESS_SEAL, principal.key_id.encode("utf-8"), hashlib.sha256).hexdigest()
    if len(principal.seal) != len(expected):
        return False
    return hmac.compare_digest(principal.seal, expected)


def reject_compare(presented_hash: str | None = None) -> None:
    """Spend a compare so missing and unknown keys take a similar path."""
    candidate = presented_hash if presented_hash is not None and len(presented_hash) == len(_DUMMY_HASH) else _DUMMY_HASH
    hmac.compare_digest(candidate, _DUMMY_HASH)
