#!/usr/bin/env python3
"""Record image provenance without building an image.

The record stores the Git commit, whether the worktree was dirty, and three
sha256 digests: the tracked diff, the porcelain status lines, and the files
in the build context after .dockerignore. It does not store the diff, the
status text, or file contents. Those digests do not reconstruct untracked
file contents. A dirty worktree is tagged with -dirty and is not a clean
reproduction of HEAD.

capture() freezes those digests. complete() recomputes them and stops without
writing an image id when the worktree or the context changed. This module
does not run Docker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIMITS = (
    "tracked_diff_sha256 hashes git diff HEAD and excludes untracked files. "
    "porcelain_sha256 hashes sorted status lines, not file contents. "
    "context_sha256 hashes the build-context paths and file bytes left after "
    ".dockerignore. None of these hashes reconstructs untracked file contents. "
    "dirty true means this build is not a clean reproduction of HEAD."
)
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
_FINGERPRINTS = (
    "revision",
    "dirty",
    "tracked_diff_sha256",
    "porcelain_sha256",
    "context_sha256",
    "base_image",
    "tag",
)


class ProvenanceChanged(RuntimeError):
    """The worktree or build context changed after capture."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__("Source changed after provenance capture: " + ", ".join(fields))


def capture(repo: Path, context: Path, dockerfile: Path, image_name: str) -> dict[str, object]:
    """Snapshot the source identity for one build context."""
    root = repo.resolve()
    context_path = _inside(root, context)
    dockerfile_path = _inside(root, dockerfile)
    name = _image_name(image_name)
    revision, dirty, diff_hash, status_hash = _git_identity(root)
    base_image = final_base_image(dockerfile_path.read_text(encoding="utf-8"))
    record: dict[str, object] = {
        "revision": revision,
        "dirty": dirty,
        "tracked_diff_sha256": diff_hash,
        "porcelain_sha256": status_hash,
        "context": _relative(root, context_path),
        "context_sha256": context_fingerprint(context_path),
        "dockerfile": _relative(root, dockerfile_path),
        "base_image": base_image,
        "image_name": name,
        "tag": _tag(name, revision, dirty),
        "image_id": None,
        "limits": LIMITS,
    }
    _check_consistent(record)
    return record


def complete(repo: Path, record: dict[str, object], image_id: str) -> dict[str, object]:
    """Attach a local image id when the captured source is unchanged."""
    if not _IMAGE_ID.fullmatch(image_id):
        raise ValueError("image_id must be sha256 and 64 hex characters.")
    fields = changed_fields(repo, record)
    if fields:
        raise ProvenanceChanged(fields)
    finished = dict(record)
    finished["image_id"] = image_id
    return finished


def changed_fields(repo: Path, record: dict[str, object]) -> list[str]:
    """Return fingerprint names that no longer match the captured record."""
    current = capture(
        repo,
        repo / str(record["context"]),
        repo / str(record["dockerfile"]),
        str(record["image_name"]),
    )
    return [name for name in _FINGERPRINTS if current[name] != record[name]]


def build_arg_flags(record: dict[str, object]) -> list[str]:
    """Return the Docker build arguments. This does not run Docker."""
    _check_consistent(record)
    dirty = "true" if record["dirty"] else "false"
    return [
        "--build-arg",
        f"URUMI_SOURCE_REVISION={record['revision']}",
        "--build-arg",
        f"URUMI_SOURCE_DIRTY={dirty}",
    ]


def final_base_image(dockerfile: str) -> str:
    """Return the image named by the Dockerfile's last FROM instruction."""
    bases: list[str] = []
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.upper().startswith("FROM "):
            continue
        image = _from_image(line.split()[1:])
        if image is not None:
            bases.append(image)
    if not bases:
        raise ValueError("Dockerfile has no FROM instruction.")
    return bases[-1]


def context_fingerprint(context: Path) -> str:
    """Hash included context files. The result does not contain their bytes."""
    rules = _dockerignore_rules(_read_ignore(context))
    digest = hashlib.sha256()
    for relative in _included_files(context, rules):
        path = context / relative
        if path.is_symlink():
            payload = os.readlink(path).encode()
            kind = b"link"
        else:
            payload = path.read_bytes()
            kind = b"file"
        file_hash = hashlib.sha256(payload).hexdigest().encode()
        digest.update(kind)
        digest.update(b"\0")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(file_hash)
        digest.update(b"\n")
    return digest.hexdigest()


def write_record(path: Path, record: dict[str, object]) -> None:
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_record(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("Provenance record must be a JSON object.")
    return loaded


def _git_identity(repo: Path) -> tuple[str, bool, str, str]:
    revision = _git(repo, "rev-parse", "HEAD").decode().strip()
    if not _REVISION.fullmatch(revision):
        raise ValueError("HEAD is not a full 40-character revision.")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    lines = [line for line in status.decode().splitlines() if line]
    dirty = bool(lines)
    diff = _git(repo, "diff", "--no-ext-diff", "--no-color", "--binary", "HEAD")
    return revision, dirty, _sha256(diff), _sha256(_status_blob(lines))


def _status_blob(lines: list[str]) -> bytes:
    if not lines:
        return b""
    return ("\n".join(sorted(lines)) + "\n").encode()


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_PAGER": "cat", "LC_ALL": "C"},
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed with status {completed.returncode}.")
    return completed.stdout


def _tag(image_name: str, revision: str, dirty: bool) -> str:
    suffix = "-dirty" if dirty else ""
    return f"{image_name}:{revision}{suffix}"


def _check_consistent(record: dict[str, object]) -> None:
    revision = str(record["revision"])
    dirty = record["dirty"]
    if not isinstance(dirty, bool):
        raise ValueError("dirty must be a boolean.")
    if not _REVISION.fullmatch(revision):
        raise ValueError("revision must be a full Git commit.")
    expected = _tag(str(record["image_name"]), revision, dirty)
    if record["tag"] != expected:
        raise ValueError("tag does not match the revision and dirty flag.")
    if dirty and not str(record["tag"]).endswith("-dirty"):
        raise ValueError("A dirty build cannot use a clean tag.")
    if record["limits"] != LIMITS:
        raise ValueError("provenance limits text was changed.")


def _image_name(value: str) -> str:
    if not _IMAGE_NAME.fullmatch(value) or ":" in value:
        raise ValueError("image name must not include a tag.")
    return value


def _from_image(tokens: list[str]) -> str | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            index += 1
            continue
        if token.upper() == "AS":
            return None
        return token
    return None


def _inside(repo: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else repo / path
    full = candidate.resolve()
    if not full.is_relative_to(repo):
        raise ValueError("path escapes the repository.")
    return full


def _relative(repo: Path, path: Path) -> str:
    relative = path.relative_to(repo).as_posix()
    return relative or "."


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_ignore(context: Path) -> str:
    ignore = context / ".dockerignore"
    if not ignore.is_file():
        return ""
    return ignore.read_text(encoding="utf-8")


def _dockerignore_rules(text: str) -> list[tuple[bool, bool, re.Pattern[str]]]:
    rules: list[tuple[bool, bool, re.Pattern[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        directory_only = pattern.endswith("/")
        if directory_only:
            pattern = pattern[:-1]
        if pattern.startswith("/"):
            pattern = pattern[1:]
        if "/" not in pattern:
            pattern = "**/" + pattern
        rules.append((negate, directory_only, re.compile("^" + _glob_to_regex(pattern) + "$")))
    return rules


def _glob_to_regex(pattern: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            pieces.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
            continue
        char = pattern[index]
        if char == "*":
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                pieces.append(re.escape(char))
            else:
                group = pattern[index : end + 1].replace("[!", "[^", 1)
                pieces.append(group)
                index = end
        else:
            pieces.append(re.escape(char))
        index += 1
    return "".join(pieces)


def _included_files(context: Path, rules: list[tuple[bool, bool, re.Pattern[str]]]) -> list[str]:
    included: list[str] = []
    for directory, dirnames, filenames in os.walk(context, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(context).as_posix()
        if relative_dir == ".":
            relative_dir = ""
        kept: list[str] = []
        for name in dirnames:
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if not _ignored(relative, True, rules):
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if not _ignored(relative, False, rules):
                included.append(relative)
    return sorted(included)


def _ignored(relative: str, is_dir: bool, rules: list[tuple[bool, bool, re.Pattern[str]]]) -> bool:
    parts = relative.split("/")
    accumulated: list[str] = []
    for index, part in enumerate(parts):
        accumulated.append(part)
        prefix = "/".join(accumulated)
        prefix_is_dir = True if index < len(parts) - 1 else is_dir
        if _rule_ignores(prefix, prefix_is_dir, rules):
            return True
    return False


def _rule_ignores(relative: str, is_dir: bool, rules: list[tuple[bool, bool, re.Pattern[str]]]) -> bool:
    ignored = False
    for negate, directory_only, pattern in rules:
        if directory_only and not is_dir:
            continue
        if pattern.fullmatch(relative):
            ignored = not negate
    return ignored


def _command_capture(args: argparse.Namespace) -> int:
    record = capture(Path(args.repo), Path(args.context), Path(args.dockerfile), args.image_name)
    write_record(Path(args.output), record)
    print(record["tag"])
    return 0


def _command_complete(args: argparse.Namespace) -> int:
    path = Path(args.record)
    record = read_record(path)
    try:
        finished = complete(Path(args.repo), record, args.image_id)
    except ProvenanceChanged as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_record(path, finished)
    print(finished["image_id"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record source provenance for a later image build.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    capture_command = subcommands.add_parser("capture")
    capture_command.add_argument("--repo", default=".")
    capture_command.add_argument("--context", required=True)
    capture_command.add_argument("--dockerfile", required=True)
    capture_command.add_argument("--image-name", required=True)
    capture_command.add_argument("--output", required=True)
    capture_command.set_defaults(func=_command_capture)
    complete_command = subcommands.add_parser("complete")
    complete_command.add_argument("--repo", default=".")
    complete_command.add_argument("--record", required=True)
    complete_command.add_argument("--image-id", required=True)
    complete_command.set_defaults(func=_command_complete)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
