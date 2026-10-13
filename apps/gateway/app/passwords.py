"""Password verifiers for dashboard users. The verifier is not a gateway API key."""

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS,
        urlsafe_b64encode(salt).decode("ascii"),
        urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, verifier: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = verifier.split("$")
        iterations = int(iterations_text)
        salt = urlsafe_b64decode(salt_text.encode("ascii"))
        expected = urlsafe_b64decode(digest_text.encode("ascii"))
    except Exception:
        return False
    if algorithm != "pbkdf2_sha256" or iterations < 100_000 or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    if len(digest) != len(expected):
        return False
    return hmac.compare_digest(digest, expected)


def generate_password() -> str:
    return secrets.token_urlsafe(18)
