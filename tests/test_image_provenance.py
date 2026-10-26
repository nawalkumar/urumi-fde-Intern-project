"""Source provenance records the commit and dirty state without image builds."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("image_provenance", ROOT / "scripts" / "image_provenance.py")
assert _SPEC is not None and _SPEC.loader is not None
provenance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(provenance)

_IMAGE_ID = "sha256:" + "ab" * 32
_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Provenance Test",
            "-c",
            "user.email=provenance@example.com",
            *args,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repo(path: Path, dockerignore: str = "") -> Path:
    _git(path, "init", "-b", "main")
    (path / "Dockerfile").write_text("FROM example-base:1\n", encoding="utf-8")
    (path / "app.txt").write_text("committed\n", encoding="utf-8")
    added = ["Dockerfile", "app.txt"]
    if dockerignore:
        (path / ".dockerignore").write_text(dockerignore, encoding="utf-8")
        added.append(".dockerignore")
    _git(path, "add", *added)
    _git(path, "commit", "-m", "initial")
    return path


def _capture(repo: Path, context: Path | None = None) -> dict[str, object]:
    return provenance.capture(repo, context or repo, repo / "Dockerfile", "example-image")


def test_clean_tree_is_a_clean_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = _capture(repo)
    assert record["dirty"] is False
    assert record["tag"] == f"example-image:{record['revision']}"
    assert not str(record["tag"]).endswith("-dirty")
    assert record["tracked_diff_sha256"] == _EMPTY
    assert record["porcelain_sha256"] == _EMPTY
    assert record["base_image"] == "example-base:1"
    assert record["image_id"] is None
    assert "not a clean reproduction of HEAD" in str(record["limits"])
    assert "reconstructs untracked file contents" in str(record["limits"])
    finished = provenance.complete(repo, record, _IMAGE_ID)
    assert finished["image_id"] == _IMAGE_ID
    assert provenance.build_arg_flags(finished) == [
        "--build-arg",
        f"URUMI_SOURCE_REVISION={record['revision']}",
        "--build-arg",
        "URUMI_SOURCE_DIRTY=false",
    ]


def test_dirty_tree_is_not_labeled_as_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.txt").write_text("changed\n", encoding="utf-8")
    record = _capture(repo)
    assert record["dirty"] is True
    assert str(record["tag"]).endswith("-dirty")
    assert record["tracked_diff_sha256"] != _EMPTY
    assert "changed" not in json.dumps(record)
    flags = provenance.build_arg_flags(record)
    assert "URUMI_SOURCE_DIRTY=true" in flags
    assert "URUMI_SOURCE_DIRTY=false" not in flags


def test_untracked_contents_are_not_stored_and_block_completion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    secret = "untracked-secret-value"
    note = repo / "note.txt"
    note.write_text(secret + "\n", encoding="utf-8")
    record = _capture(repo)
    assert record["dirty"] is True
    assert record["tracked_diff_sha256"] == _EMPTY
    encoded = json.dumps(record)
    assert secret not in encoded
    assert "note.txt" not in encoded
    note.write_text(secret + "-edited\n", encoding="utf-8")
    with pytest.raises(provenance.ProvenanceChanged) as caught:
        provenance.complete(repo, record, _IMAGE_ID)
    assert caught.value.fields == ["context_sha256"]
    assert secret not in str(caught.value)
    assert record["image_id"] is None


def test_ignored_secret_file_is_outside_the_context_hash(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ".env\n.env.*\n!.env.example\n")
    env = repo / ".env"
    env.write_text("GATEWAY_UPSTREAM_KEY=super-secret\n", encoding="utf-8")
    example = repo / ".env.example"
    example.write_text("GATEWAY_UPSTREAM_KEY=\n", encoding="utf-8")
    _git(repo, "add", ".env.example")
    _git(repo, "commit", "-m", "example")
    first = _capture(repo)
    assert "super-secret" not in json.dumps(first)
    env.write_text("GATEWAY_UPSTREAM_KEY=rotated-secret\n", encoding="utf-8")
    second = _capture(repo)
    assert first["context_sha256"] == second["context_sha256"]
    example.write_text("GATEWAY_UPSTREAM_KEY=placeholder\n", encoding="utf-8")
    third = _capture(repo)
    assert third["context_sha256"] != first["context_sha256"]
    assert third["dirty"] is True


def test_context_outside_a_dirty_repo_still_records_the_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dashboard = repo / "dashboard"
    dashboard.mkdir()
    (dashboard / "Dockerfile").write_text("FROM nginxinc/nginx-unprivileged:1.27-alpine\n", encoding="utf-8")
    (dashboard / "screen.txt").write_text("stable\n", encoding="utf-8")
    _git(repo, "add", "dashboard")
    _git(repo, "commit", "-m", "dashboard")
    clean = provenance.capture(repo, dashboard, dashboard / "Dockerfile", "urumi-dashboard")
    (repo / "outside.txt").write_text("not-in-dashboard\n", encoding="utf-8")
    record = provenance.capture(repo, dashboard, dashboard / "Dockerfile", "urumi-dashboard")
    assert record["context"] == "dashboard"
    assert record["base_image"] == "nginxinc/nginx-unprivileged:1.27-alpine"
    assert record["dirty"] is True
    assert str(record["tag"]).endswith("-dirty")
    assert record["context_sha256"] == clean["context_sha256"]
    assert "not-in-dashboard" not in json.dumps(record)
    with pytest.raises(provenance.ProvenanceChanged) as caught:
        provenance.complete(repo, clean, _IMAGE_ID)
    assert "context_sha256" not in caught.value.fields
    assert "porcelain_sha256" in caught.value.fields
    assert "dirty" in caught.value.fields


def test_project_dockerfiles_keep_unknown_defaults_and_nginx_labels() -> None:
    gateway = (ROOT / "apps" / "gateway" / "Dockerfile").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard" / "Dockerfile").read_text(encoding="utf-8")
    for text in (gateway, dashboard):
        assert text.rfind("FROM ") < text.rfind("ARG URUMI_SOURCE_REVISION=unknown")
        assert "ARG URUMI_SOURCE_DIRTY=unknown" in text
        assert 'LABEL io.urumi.source.revision="${URUMI_SOURCE_REVISION}"' in text
        assert 'io.urumi.source.dirty="${URUMI_SOURCE_DIRTY}"' in text
    assert "org.opencontainers.image.revision" not in gateway
    assert "org.opencontainers.image.revision" not in dashboard
    assert provenance.final_base_image(gateway) == "python:3.10-slim"
    assert provenance.final_base_image(dashboard) == "nginxinc/nginx-unprivileged:1.27-alpine"
