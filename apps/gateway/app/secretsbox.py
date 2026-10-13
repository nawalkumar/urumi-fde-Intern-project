"""Seal an upstream credential with AES-GCM.

GATEWAY_UPSTREAM_KEY is the only secret used to derive the AES key. Ciphertext
is versioned `v1.` so a later format can be distinguished. A previous key, when
set, can open ciphertext that has not been sealed again. Plaintext, keys, and
ciphertext are not written to logs.
"""

import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = "v1"
_PREFIX = "v1."
_NONCE_LENGTH = 12
_AAD = b"urumi-upstream-credential-v1"
_MIN_KEY_LENGTH = 32


class CredentialKeyMissing(Exception):
    """A credential was stored or supplied, but no encryption key is configured."""


class CredentialUnreadable(Exception):
    """The stored credential cannot be opened. The message is fixed."""


def seal_secret(plaintext: str) -> str:
    key = _require_key("GATEWAY_UPSTREAM_KEY")
    nonce = secrets.token_bytes(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    body = urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return f"{_PREFIX}{body}"


def open_secret(sealed: str) -> str:
    """Open versioned ciphertext. Try the current key, then the previous key."""
    current = _optional_key("GATEWAY_UPSTREAM_KEY")
    previous = _optional_key("GATEWAY_UPSTREAM_KEY_PREVIOUS")
    if current is None and previous is None:
        raise CredentialKeyMissing("Upstream credential storage is not configured.")
    raw = _decode(sealed)
    for key in (current, previous):
        if key is None:
            continue
        try:
            return _decrypt(key, raw)
        except Exception:
            continue
    raise CredentialUnreadable("Upstream credential could not be read.")


def sealed_with_current_key(sealed: str) -> bool:
    """Return whether the current key opens this ciphertext."""
    key = _optional_key("GATEWAY_UPSTREAM_KEY")
    if key is None:
        return False
    try:
        _decrypt(key, _decode(sealed))
    except Exception:
        return False
    return True


def credential_key_configured() -> bool:
    return _optional_key("GATEWAY_UPSTREAM_KEY") is not None


def _decrypt(key: bytes, raw: bytes) -> str:
    nonce, ciphertext = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    return AESGCM(key).decrypt(nonce, ciphertext, _AAD).decode("utf-8")


def _decode(sealed: str) -> bytes:
    if not isinstance(sealed, str) or not sealed.startswith(_PREFIX) or len(sealed) > 4096:
        raise CredentialUnreadable("Upstream credential could not be read.")
    try:
        raw = urlsafe_b64decode(sealed[len(_PREFIX) :].encode("ascii"))
    except Exception as exc:
        raise CredentialUnreadable("Upstream credential could not be read.") from exc
    if len(raw) <= _NONCE_LENGTH:
        raise CredentialUnreadable("Upstream credential could not be read.")
    return raw


def _require_key(name: str) -> bytes:
    key = _optional_key(name)
    if key is None:
        raise CredentialKeyMissing("Upstream credential storage is not configured.")
    return key


def _optional_key(name: str) -> bytes | None:
    raw = os.environ.get(name, "").strip()
    if len(raw) < _MIN_KEY_LENGTH:
        return None
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"urumi-gateway-upstream",
        info=f"aes-gcm-{_VERSION}".encode("ascii"),
    ).derive(raw.encode("utf-8"))
