"""Exact permissions for a gateway API key.

A tool permission is one public tool name, such as ``utility__convert_units``.
``gateway__read_status`` is the only non-tool permission. It authorizes
``GET /admin/status`` when that check is required. It is not an upstream tool.
A new key has no permissions. Missing permissions are not treated as access to
every tool, and a name containing ``*`` is not a wildcard. Grant and revoke
only through ``KeyStore.grant`` and ``KeyStore.revoke_permission``.
"""

import re

PERMISSION_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}__[a-z0-9][a-z0-9_-]{0,62}")
STATUS_READ_PERMISSION = "gateway__read_status"


def is_permission_name(value: object) -> bool:
    """Return whether value is one exact stored permission name.

    This accepts public tool names and the status-read permission. It does not
    accept wildcards or names that skip the ``upstream__tool`` shape.
    """
    if not isinstance(value, str) or not value or len(value) > 128:
        return False
    return PERMISSION_NAME.fullmatch(value) is not None


def is_tool_permission(value: object) -> bool:
    """Return whether value may be listed or dispatched as an upstream tool."""
    return is_permission_name(value) and value != STATUS_READ_PERMISSION
