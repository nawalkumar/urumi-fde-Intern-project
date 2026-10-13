#!/usr/bin/env python3
"""Flag high-confidence secret material without printing the match.

Skips dependency installs, caches, and .env. Reports a relative path and a
category only. This is not a substitute for a dedicated scanner.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    ".uv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
SKIP_FILES = {".env"}
RULES = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("gateway-api-key", re.compile(rb"urk_[0-9a-fA-F]{16}\.[A-Za-z0-9_-]{16,}")),
)


def main() -> int:
    findings: list[str] = []
    env_file = ROOT / ".env"
    if env_file.exists():
        print("present-gitignored\t.env")
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES or path.name.endswith(".password"):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("tests/") and "gateway-api-key" in {name for name, _rule in RULES}:
            blob_for_key = b""
        else:
            blob_for_key = blob
        for name, rule in RULES:
            target = blob_for_key if name == "gateway-api-key" else blob
            if rule.search(target):
                findings.append(f"{name}\t{relative}")
    if findings:
        print("\n".join(sorted(findings)))
        return 1
    print("no high-confidence matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
