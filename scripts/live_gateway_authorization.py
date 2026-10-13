"""Live per-tool authorization check against the Compose gateway.

Run from the repository root only when a disposable test key is acceptable.
This module is not part of pytest. It prints categories and public key ids,
never raw keys, command output, or database credentials.
"""

import asyncio
import http.client
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.tracebacklimit = 0
sys.path.insert(0, str(Path.cwd()))
from tests.mcp_session import open_mcp

UTILITY = "utility__convert_units"
SUPPORT = "support__search_notes"
GATEWAY = "http://127.0.0.1:8000/mcp"
KEY_ID = re.compile(r"[0-9a-f]{16}")
# generate_key() in apps/gateway/app/keys.py:
# urk_ + token_hex(8) + "." + token_urlsafe(32), which is 43 URL-safe characters.
RAW_KEY = re.compile(r"urk_[0-9a-f]{16}\.[A-Za-z0-9_-]{43}")
# apps.gateway.provision._issue prints these two lines and no others of this form.
KEY_LINE = re.compile(rf"key_id: ({KEY_ID.pattern})\Z")
RAW_LINE = re.compile(rf"gateway_api_key: ({RAW_KEY.pattern})\Z")
CREATED_LINE = "Gateway API key created."
# apps.gateway.provision prints these before issue() inserts a row.
BEFORE_CREATE = (
    "Could not reach the gateway database.",
    "Set GATEWAY_DATABASE_URL before provisioning a gateway API key.",
)
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_]{1,128}")
EXPECTED = (
    (UTILITY, "unauthorized"),
    (UTILITY, "success"),
    (SUPPORT, "unauthorized"),
)
ARGUMENT_COLUMNS = {
    "arguments",
    "args",
    "payload",
    "request",
    "request_body",
    "body",
    "secret",
    "raw_key",
    "hash",
}
SAFE_TOOL_TEXT = {"Permission denied.", "1000"}
TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}__[a-z0-9][a-z0-9_-]{0,62}")

state = {
    "provisioning_attempted": False,
    "issued": False,
    "unidentified_key": False,
    "credentials_ok": False,
    "cleanup_required": False,
    "revoked": False,
    "key_id": "",
    "raw_key": "",
    "cleanup_ok": True,
}


class SafeFailure(Exception):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _pipe_text(value: object) -> str:
    # TimeoutExpired keeps partial pipe data as bytes even when text=True.
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return ""


def _interrupted(process: subprocess.Popen[str], stdout: str, stderr: str) -> tuple[int, str, str, bool]:
    """Kill a started child and keep any captured text. Never raise after start."""
    try:
        process.kill()
    except OSError:
        pass
    try:
        extra_out, extra_err = process.communicate(timeout=5)
    except Exception:
        return -1, stdout, stderr, True
    return process.returncode or -1, extra_out or stdout or "", extra_err or stderr or "", True


def capture(args: list[str], timeout: float, category: str) -> tuple[int, str, str, bool]:
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        raise SafeFailure(category + "_could_not_start") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Partial output may already hold a new key id. Do not surface it.
        return _interrupted(process, _pipe_text(exc.stdout), _pipe_text(exc.stderr))
    except Exception:
        return _interrupted(process, "", "")
    return process.returncode or 0, stdout or "", stderr or "", False


def run(args: list[str], timeout: float, category: str) -> str:
    code, stdout, _stderr, timed_out = capture(args, timeout, category)
    if timed_out:
        raise SafeFailure(category + "_timed_out")
    if code != 0:
        raise SafeFailure(category + "_failed")
    return stdout


def gateway_container() -> str:
    ids = [line.strip() for line in run(["docker", "compose", "ps", "-q", "gateway"], 30, "container_lookup").splitlines() if line.strip()]
    if len(ids) != 1 or not SAFE_TOKEN.fullmatch(ids[0]):
        raise SafeFailure("container_lookup_failed")
    return ids[0]


def psql_lines(statement: str, key_id: str = "") -> list[str]:
    if key_id and KEY_ID.fullmatch(key_id) is None:
        raise SafeFailure("invalid_key_id")
    script = """
import os, re, sys
import psycopg
key_id = os.environ.get("TEST_KEY_ID", "")
statement = os.environ["TEST_SQL"]
if key_id and re.fullmatch(r"[0-9a-f]{16}", key_id) is None:
    sys.exit(2)
try:
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"], connect_timeout=3) as conn:
        with conn.cursor() as cur:
            if key_id:
                cur.execute(statement, (key_id,))
            else:
                cur.execute(statement)
            for row in cur.fetchall():
                print("|".join(str(part) for part in row))
except Exception as exc:
    print(type(exc).__name__)
    sys.exit(1)
"""
    env = ["-e", "TEST_SQL=" + statement]
    if key_id:
        env.extend(["-e", "TEST_KEY_ID=" + key_id])
    code, _stdout, _stderr, timed_out = capture(
        ["docker", "exec", *env, gateway_container(), "python", "-c", script],
        30,
        "query",
    )
    if timed_out or code != 0:
        raise SafeFailure("database_query_failed")
    return [line.strip() for line in _stdout.splitlines() if line.strip()]


def existing_key_ids() -> set[str]:
    found = set()
    for line in psql_lines("SELECT key_id FROM gateway_api_keys"):
        if KEY_ID.fullmatch(line) is None:
            raise SafeFailure("database_query_failed")
        found.add(line)
    return found


def audit_columns() -> list[str]:
    return psql_lines(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'gateway_audit_log' "
        "ORDER BY ordinal_position"
    )


def audit_rows(key_id: str) -> list[tuple[str, str]]:
    rows = []
    for line in psql_lines(
        "SELECT tool_name, outcome FROM gateway_audit_log WHERE actor = %s ORDER BY id",
        key_id,
    ):
        tool_name, separator, outcome = line.partition("|")
        if separator != "|":
            raise SafeFailure("database_query_failed")
        rows.append((tool_name, outcome))
    return rows


def valid_secret(raw_key: str, key_id: str) -> bool:
    if RAW_KEY.fullmatch(raw_key) is None:
        return False
    prefix, dot, secret = raw_key.partition(".")
    return dot == "." and prefix == "urk_" + key_id and len(secret) == 43


def _require_cleanup(*, unidentified: bool) -> None:
    state["cleanup_required"] = True
    state["cleanup_ok"] = False
    if unidentified:
        state["unidentified_key"] = True


def parse_issue(stdout: str, before: set[str]) -> None:
    """Parse the key lines printed by apps.gateway.provision._issue.

    A new id is captured only when there is exactly one well-formed key_id
    line and it was not in the initial snapshot. Duplicate, malformed, or
    missing key_id lines capture nothing. A bad secret line does not discard
    that single new id; the secret is simply not accepted.
    """
    key_ids: list[str] = []
    raw_keys: list[str] = []
    malformed_key = False
    malformed_raw = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("key_id:"):
            match = KEY_LINE.fullmatch(line)
            if match is None:
                malformed_key = True
            else:
                key_ids.append(match.group(1))
        elif line.startswith("gateway_api_key:"):
            match = RAW_LINE.fullmatch(line)
            if match is None:
                malformed_raw = True
            else:
                raw_keys.append(match.group(1))
    if malformed_key or len(key_ids) != 1 or key_ids[0] in before:
        return
    key_id = key_ids[0]
    state["key_id"] = key_id
    state["issued"] = True
    if malformed_raw or len(raw_keys) != 1 or not valid_secret(raw_keys[0], key_id):
        return
    state["raw_key"] = raw_keys[0]
    state["credentials_ok"] = True


def _stdout_claims_created(stdout: str) -> bool:
    return any(line.strip() == CREATED_LINE for line in stdout.splitlines())


def _proved_no_key(stdout: str, stderr: str, code: int, timed_out: bool) -> bool:
    """True only when stderr is exactly one known pre-insert failure line.

    Any other stdout or stderr text leaves the outcome uncertain.
    """
    if timed_out or code == 0 or _stdout_claims_created(stdout):
        return False
    if any(line.strip() for line in stdout.splitlines()):
        return False
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return len(stderr_lines) == 1 and stderr_lines[0] in BEFORE_CREATE


def issue_key(before: set[str]) -> None:
    code, stdout, stderr, timed_out = capture(
        ["docker", "compose", "run", "--rm", "--no-deps", "gateway-key", "issue"],
        120,
        "issue",
    )
    state["provisioning_attempted"] = True
    parse_issue(stdout, before)
    if state["key_id"] in before:
        state["issued"] = False
        state["key_id"] = ""
        state["raw_key"] = ""
        state["credentials_ok"] = False
    if state["issued"]:
        _require_cleanup(unidentified=False)
        print("test_key_id", state["key_id"])
    elif not _proved_no_key(stdout, stderr, code, timed_out):
        # Started, but no safe new id. Do not query the database for one.
        _require_cleanup(unidentified=True)
    if timed_out:
        raise SafeFailure("issue_timed_out")
    if code != 0:
        raise SafeFailure("issue_failed")
    if not state["issued"]:
        if not state["unidentified_key"]:
            _require_cleanup(unidentified=True)
        raise SafeFailure("issue_output_rejected")
    if not state["credentials_ok"]:
        raise SafeFailure("issue_output_rejected")


def safe_tool_text(text: str) -> str:
    if text in SAFE_TOOL_TEXT:
        return text
    return "unexpected"


def safe_names(names: list[str]) -> str:
    if any(TOOL_NAME.fullmatch(name) is None for name in names):
        return "unexpected"
    return ",".join(names)


async def list_and_call(tool_name: str, arguments: dict) -> tuple[list[str], str, bool]:
    async with open_mcp(GATEWAY, state["raw_key"]) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
        result = await client.call_tool(tool_name, arguments)
    return names, result.content[0].text, bool(result.is_error)


def _auth_body_matches(payload: str) -> bool:
    # apps.gateway.app.http_auth._AUTH_BODY is HTTP 401 JSON with these fields.
    return "invalid_token" in payload and "Authentication required." in payload


def revoked_status() -> int:
    # The MCP SDK does not preserve this HTTP status. A non-JSON-RPC 401 becomes
    # JSON-RPC internal error "Server returned an error response".
    request = urllib.request.Request(
        GATEWAY,
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer " + state["raw_key"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = exc.read().decode("utf-8", "replace")
        except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, ValueError):
            raise SafeFailure("revoked_key_http_failed") from None
        finally:
            try:
                exc.close()
            except OSError:
                pass
    except (urllib.error.URLError, TimeoutError, socket.timeout, http.client.HTTPException, OSError):
        raise SafeFailure("revoked_key_http_failed") from None
    secret_in_body = bool(state["raw_key"]) and state["raw_key"] in payload
    generic = _auth_body_matches(payload) and not secret_in_body
    print("revoked_status", status)
    print("generic_auth_error", generic)
    if secret_in_body:
        raise SafeFailure("response_included_secret")
    if status != 401 or not _auth_body_matches(payload):
        raise SafeFailure("revoked_key_not_http_401")
    return status


def revocation_confirmed(key_id: str) -> bool:
    """True only when gateway_api_keys.revoked_at is non-null for this id."""
    if KEY_ID.fullmatch(key_id) is None:
        return False
    try:
        lines = psql_lines(
            "SELECT CASE WHEN revoked_at IS NOT NULL THEN 'revoked' ELSE 'active' END "
            "FROM gateway_api_keys WHERE key_id = %s",
            key_id,
        )
    except SafeFailure:
        return False
    return lines == ["revoked"]


def revoke_command(key_id: str) -> None:
    """Run revoke. A zero exit code does not prove revoked_at was set."""
    try:
        capture(
            ["docker", "compose", "run", "--rm", "--no-deps", "gateway-key", "revoke", key_id],
            60,
            "revoke",
        )
    except SafeFailure:
        return


def ensure_revoked(key_id: str, before: set[str]) -> bool:
    if state["revoked"]:
        return True
    if KEY_ID.fullmatch(key_id) is None or key_id in before:
        return False
    for _attempt in (1, 2):
        revoke_command(key_id)
        if revocation_confirmed(key_id):
            state["revoked"] = True
            return True
    return False


def finish_cleanup(before: set[str]) -> None:
    try:
        if state["issued"] and not state["revoked"]:
            ensure_revoked(state["key_id"], before)
    except Exception as exc:
        state["cleanup_ok"] = False
        print("test_failed", type(exc).__name__)
    print("provisioning_attempted", state["provisioning_attempted"])
    print("key_id_captured", state["issued"])
    print("cleanup_required", state["cleanup_required"])
    print("revocation_confirmed", state["revoked"])
    if not state["cleanup_required"]:
        return
    confirmed = (
        state["provisioning_attempted"]
        and state["revoked"]
        and state["issued"]
        and not state["unidentified_key"]
        and state["key_id"] not in before
        and KEY_ID.fullmatch(state["key_id"]) is not None
    )
    if confirmed:
        state["cleanup_ok"] = True
        print("cleanup_confirmed")
        return
    state["cleanup_ok"] = False
    print("cleanup_failed", "unconfirmed_key_may_remain_active")


def split_events(rows: list[tuple[str, str]]) -> tuple[bool, bool, int, bool]:
    """Return complete, in_order, unexpected count, and marker presence.

    Rows are already in id order. A later expected pair, or a pair that was
    already consumed, makes the sequence out of order. Other rows are counted
    only.
    """
    index = 0
    disordered = False
    unexpected = 0
    markers = False
    for tool_name, outcome in rows:
        blob = f"{tool_name} {outcome}"
        if any(marker in blob for marker in ("kg", "shipping", "from_unit", "urk_", "bearer ")):
            markers = True
        row = (tool_name, outcome)
        if index < len(EXPECTED) and row == EXPECTED[index]:
            index += 1
            continue
        unexpected += 1
        if row in EXPECTED[:index] or row in EXPECTED[index:]:
            disordered = True
    return index == len(EXPECTED), not disordered, unexpected, markers


async def main() -> None:
    failed = False
    before: set[str] = set()
    try:
        before = existing_key_ids()
        issue_key(before)
        if not state["credentials_ok"] or state["key_id"] in before:
            raise SafeFailure("issue_output_rejected")

        names, text, is_error = await list_and_call(UTILITY, {"value": 1, "from_unit": "kg", "to_unit": "g"})
        print("before_names", safe_names(names))
        print("before_denied", is_error, safe_tool_text(text))
        if names != [] or not is_error or text != "Permission denied.":
            raise SafeFailure("ungranted_key_not_denied")

        run(
            ["docker", "compose", "run", "--rm", "--no-deps", "gateway-key", "grant", state["key_id"], UTILITY],
            60,
            "grant",
        )
        names, text, is_error = await list_and_call(UTILITY, {"value": 1, "from_unit": "kg", "to_unit": "g"})
        print("after_names", safe_names(names))
        print("utility_ok", (not is_error) and text == "1000")
        if names != [UTILITY] or is_error or text != "1000":
            raise SafeFailure("granted_utility_call_failed")

        _names, blocked, blocked_error = await list_and_call(SUPPORT, {"query": "shipping"})
        print("support_denied", blocked_error, safe_tool_text(blocked))
        if not blocked_error or blocked != "Permission denied.":
            raise SafeFailure("support_call_not_denied")

        columns = audit_columns()
        printable: list[str] = []
        hidden_column = False
        for name in columns:
            if SAFE_TOKEN.fullmatch(name) is None:
                hidden_column = True
                continue
            printable.append(name)
        if "tool_name" not in printable or "outcome" not in printable:
            raise SafeFailure("audit_required_columns_missing")
        argument_like = [
            name
            for name in printable
            if name.casefold() in ARGUMENT_COLUMNS
            or "argument" in name.casefold()
            or "payload" in name.casefold()
        ]
        print("audit_marker_check", "covers_only_tool_name_and_outcome_for_this_test_key")
        print("audit_argument_like_columns", ",".join(argument_like) if argument_like else "absent")
        print("audit_unprintable_column", "present" if hidden_column else "absent")
        rows = audit_rows(state["key_id"])
        complete, in_order, unexpected, markers = split_events(rows)
        print("audit_actor_filter", "test_key")
        print("audit_order_ok", in_order)
        print("audit_unexpected_count", unexpected)
        print("audit_markers_in_tool_name_and_outcome", markers)
        if markers:
            raise SafeFailure("audit_selected_fields_marked")
        if not in_order:
            raise SafeFailure("audit_events_out_of_order")
        if not complete:
            raise SafeFailure("audit_expected_events_missing")

        if not ensure_revoked(state["key_id"], before):
            raise SafeFailure("revoke_unconfirmed")
        if revoked_status() != 401:
            raise SafeFailure("revoked_key_not_http_401")
    except SafeFailure as exc:
        failed = True
        print("test_failed", exc.category)
    except Exception as exc:
        failed = True
        print("test_failed", type(exc).__name__)
    finally:
        finish_cleanup(before)
    if failed or not state["cleanup_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print("test_failed", type(exc).__name__)
        raise SystemExit(1) from None
