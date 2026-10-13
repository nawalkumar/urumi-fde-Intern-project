"""Merge operator-configured upstreams with upstreams saved by an administrator."""

from dataclasses import replace

from apps.gateway.app.config import Settings, UpstreamConfig
from apps.gateway.app.directory import UpstreamRecord
from apps.gateway.app.secretsbox import (
    CredentialKeyMissing,
    CredentialUnreadable,
    open_secret,
    seal_secret,
    sealed_with_current_key,
)


def merge_upstreams(
    static: list[UpstreamConfig],
    dynamic: list[UpstreamRecord],
    flags: dict[str, bool],
    *,
    timeout_seconds: float,
    allow_private: bool,
    trust_environment: bool = True,
) -> list[UpstreamConfig]:
    merged: list[UpstreamConfig] = []
    seen: set[str] = set()
    for item in static:
        enabled = flags.get(item.name, item.enabled)
        merged.append(
            replace(
                item,
                enabled=enabled,
                trusted=trust_environment,
                allow_private=allow_private,
            )
        )
        seen.add(item.name)
    for row in dynamic:
        if row.upstream_id in seen:
            continue
        credential, unreadable = _open_credential(row.credential)
        enabled = False if unreadable else flags.get(row.upstream_id, row.enabled)
        merged.append(
            UpstreamConfig(
                name=row.upstream_id,
                url=row.url,
                timeout_seconds=timeout_seconds,
                enabled=enabled,
                authorization=f"Bearer {credential}" if credential else "",
                trusted=False,
                allow_private=allow_private,
                credential_unreadable=unreadable,
            )
        )
    return merged


def _open_credential(sealed: str) -> tuple[str, bool]:
    """Return plaintext and whether a stored credential could not be opened.

    An empty store value is not a failure. A present value that cannot be
    opened must not be treated as an upstream with no credential.
    """
    if not sealed:
        return "", False
    try:
        return open_secret(sealed), False
    except (CredentialUnreadable, CredentialKeyMissing):
        return "", True


async def persist_rotated_credentials(store: object) -> None:
    """Rewrite ciphertext that only the previous key can open.

    Unreadable values are left unchanged. The caller disables those upstreams
    and does not call them. Plaintext is not logged.
    """
    rows = await store.list_upstreams()  # type: ignore[attr-defined]
    for row in rows:
        if not row.credential or sealed_with_current_key(row.credential):
            continue
        try:
            plaintext = open_secret(row.credential)
        except (CredentialUnreadable, CredentialKeyMissing):
            continue
        await store.save_upstream(row.upstream_id, row.url, row.enabled, seal_secret(plaintext))  # type: ignore[attr-defined]


async def refresh_registry(registry: object, store: object, settings: Settings) -> None:
    await persist_rotated_credentials(store)
    dynamic = await store.list_upstreams()  # type: ignore[attr-defined]
    flags = await store.list_upstream_flags()  # type: ignore[attr-defined]
    registry.configure(  # type: ignore[attr-defined]
        merge_upstreams(
            list(settings.upstreams),
            dynamic,
            flags,
            timeout_seconds=settings.upstream_timeout_seconds,
            allow_private=settings.allow_private_upstreams,
            trust_environment=settings.trust_environment_upstreams,
        )
    )
