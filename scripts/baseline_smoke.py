#!/usr/bin/env python3
"""Fail-closed HTTP smoke checks for a Urumi process that is already running.

This script does not start Kubernetes, Docker, Helm, or a tunnel. Every
requested URL must answer with the expected status and body text. A refused
connection, a timeout, or a missing body is a failure. No targets is a
failure, so an empty invocation cannot print success.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 5


def check(name: str, url: str, expect_status: int, body_text: str) -> str | None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read(65536).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"{name} request failed ({type(exc).__name__})"
    if status != expect_status:
        return f"{name} status {status}, expected {expect_status}"
    if body_text not in body:
        return f"{name} body missing {body_text!r}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if a Urumi HTTP check does not match.")
    parser.add_argument("--gateway", help="Gateway origin, for example http://127.0.0.1:8000")
    parser.add_argument("--dashboard", help="Dashboard origin, for example http://127.0.0.1:5173")
    parser.add_argument("--store", help="Store origin, for example http://127.0.0.1:8080")
    args = parser.parse_args(argv)
    targets: list[tuple[str, str, int, str]] = []
    if args.gateway:
        targets.append(("gateway-health", args.gateway.rstrip("/") + "/health", 200, '"status"'))
        targets.append(("gateway-health-ok", args.gateway.rstrip("/") + "/health", 200, "ok"))
    if args.dashboard:
        targets.append(("dashboard", args.dashboard.rstrip("/") + "/", 200, "<"))
    if args.store:
        targets.append(("store", args.store.rstrip("/") + "/", 200, "<"))
    if not targets:
        print("FAIL no smoke targets were provided")
        return 2
    failures = [item for item in (check(*target) for target in targets) if item]
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS smoke checks matched the running process")
    return 0


if __name__ == "__main__":
    sys.exit(main())
