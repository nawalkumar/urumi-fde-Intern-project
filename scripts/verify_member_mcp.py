"""Check an existing gateway API key against a loopback MCP endpoint.

The key is read from URUMI_GATEWAY_API_KEY when that variable is already set,
otherwise from a hidden prompt. It is never a command-line argument and is
never printed. This script does not issue, grant, or revoke keys and does not
open a database connection.

`tests.mcp_session.open_mcp` sends the key with the official Streamable HTTP
client. Redirects that change host or port are not followed. The transport can
follow an HTTP-to-HTTPS upgrade on the same host when both ports are normalized
default ports (`None`). The client's default endpoint,
`http://127.0.0.1:18000/mcp`, does not use that default-port upgrade case.
`_endpoint_error` currently accepts explicit port 80, so the accepted endpoint
set is broader than the default endpoint. The existing transport may follow a
same-loopback-host HTTP-to-HTTPS redirect from port 80 to the HTTPS default
port while retaining the Authorization header. POST redirects that change the
HTTP method are not followed. TLS verification is left at the transport default.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.gateway.app.keys import parse_raw_key
from tests.mcp_session import open_mcp

DEFAULT_URL = "http://127.0.0.1:18000/mcp"
_ENV_NAME = "URUMI_GATEWAY_API_KEY"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_EXPECTED_TOOLS = frozenset({"support__search_notes", "utility__convert_units"})
_SUPPORT = "support__search_notes"
_UTILITY = "utility__convert_units"
_DENIED = "woocommerce__list_products"


def _quiet_logs() -> None:
    logging.getLogger("mcp").setLevel(logging.CRITICAL)
    logging.getLogger("httpx2").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)


def _endpoint_error(url: str) -> str | None:
    """Return a fixed reason when url must not receive the gateway key."""
    parsed = urlparse(url)
    if parsed.scheme != "http":
        return "endpoint rejected"
    if parsed.hostname not in _LOOPBACK:
        return "endpoint rejected"
    if parsed.port is None or parsed.port == 8000:
        return "endpoint rejected"
    if parsed.path != "/mcp":
        return "endpoint rejected"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "endpoint rejected"
    return None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        sys.stderr.write("Invalid arguments.\n")
        raise SystemExit(2)


def _guard_arguments(argv: list[str]) -> bool:
    for argument in argv:
        if "urk_" in argument.casefold():
            sys.stderr.write("The key cannot be passed as an argument.\n")
            return False
    return True


def _read_key() -> str:
    supplied = os.environ.get(_ENV_NAME)
    if supplied is not None and supplied.strip():
        return supplied.strip()
    entered = getpass.getpass("Gateway API key: ")
    return entered.strip()


def _text(result: object) -> str:
    content = getattr(result, "content", None)
    if not content:
        return ""
    return getattr(content[0], "text", "") or ""


def _label(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


async def _verify(url: str, holder: list[str]) -> int:
    failed = False
    try:
        async with open_mcp(url, holder[0]) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            list_ok = names == _EXPECTED_TOOLS
            print("tools/list", _label(list_ok), " ".join(sorted(names)))
            failed = failed or not list_ok

            notes = await client.call_tool(_SUPPORT, {"query": "shipping"})
            notes_ok = (not notes.is_error) and ("3 business days" in _text(notes))
            print(_SUPPORT, _label(notes_ok))
            failed = failed or not notes_ok

            units = await client.call_tool(
                _UTILITY,
                {"value": 2, "from_unit": "km", "to_unit": "m"},
            )
            units_ok = (not units.is_error) and _text(units) == "2000"
            print(_UTILITY, _label(units_ok))
            failed = failed or not units_ok

            denied = await client.call_tool(_DENIED, {})
            denied_ok = bool(denied.is_error) and _text(denied) == "Permission denied."
            print(_DENIED, _label(denied_ok))
            failed = failed or not denied_ok
    except Exception:
        sys.stderr.write("verification failed\n")
        return 1
    finally:
        holder[0] = ""
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """Run the checks. The return code is 0 only when every check passes."""
    _quiet_logs()
    sys.tracebacklimit = 0
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not _guard_arguments(arguments):
        return 2
    parser = _Parser(prog="verify_member_mcp")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--expect-key-id", default="")
    try:
        args = parser.parse_args(arguments)
    except SystemExit:
        return 2
    if _endpoint_error(args.url) is not None:
        sys.stderr.write("endpoint rejected\n")
        return 2
    if args.expect_key_id and parse_raw_key(f"urk_{args.expect_key_id}.{'a' * 43}") is None:
        sys.stderr.write("expected key id rejected\n")
        return 2

    holder = [""]
    try:
        holder[0] = _read_key()
        key_id = parse_raw_key(holder[0])
        if key_id is None:
            sys.stderr.write("key rejected\n")
            return 2
        if args.expect_key_id and key_id != args.expect_key_id:
            sys.stderr.write("key id does not match\n")
            return 2
        print(f"key_id {key_id}")
        return asyncio.run(_verify(args.url, holder))
    except Exception:
        sys.stderr.write("verification failed\n")
        return 1
    finally:
        holder[0] = ""


if __name__ == "__main__":
    sys.exit(main())
