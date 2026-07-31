#!/usr/bin/env python3
"""Build or verify the restore procedure seal from immutable Git blobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


MEMBERS = (
    "scripts/postgres_restore_c_final_assert.py",
    "scripts/postgres_restore_c_final_assert.sql",
    "scripts/postgres_restore_generation.sh",
    "scripts/postgres_restore_generation.py",
    "scripts/postgres_restore_guard.py",
    "scripts/postgres_restore_host_inventory.py",
    "scripts/postgres_restore_image_identity.py",
    "scripts/postgres_restore_inventory_exporter.py",
    "scripts/postgres_restore_inventory_exporter_manifest.json",
    "scripts/postgres_restore_isolation_gate.py",
    "scripts/postgres_restore_provider_inventory.py",
    "scripts/postgres_restore_provider_manifest.json",
    "scripts/postgres_restore_retention.py",
    "scripts/postgres_restore_runner.py",
    "scripts/postgres_restore_runner_manifest.json",
    "scripts/postgres_restore_status_gate.sh",
    "scripts/postgres_restore_status_gate.py",
    "scripts/postgres_restore_transaction_probe.sh",
    "scripts/postgres_restore_transaction_probe.py",
    "scripts/postgres_restore_transaction_probe.sql",
)
MANIFEST = "scripts/postgres_restore_procedure_manifest.json"
REF = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/-]*$")
OID = re.compile(r"^[0-9a-f]{40}$")


class SealError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def git(*args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        return subprocess.run(
            ["git", *args],
            input=input_bytes,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SealError(f"Git object operation failed: {' '.join(args)}") from exc


def validate_member(path: str, mode: str, oid: str, payload: bytes) -> None:
    if path not in MEMBERS or mode not in {"100644", "100755"} or not OID.fullmatch(oid):
        raise SealError(f"{path} is not an allowed regular Git blob")
    if not payload or b"\0" in payload or b"\r" in payload or not payload.endswith(b"\n"):
        raise SealError(f"{path} is not exact LF text")
    expected_oid = hashlib.sha1(
        f"blob {len(payload)}\0".encode("ascii") + payload
    ).hexdigest()
    if oid != expected_oid:
        raise SealError(f"{path} object ID does not match its raw bytes")


def index_member(path: str) -> tuple[str, str, bytes]:
    entry = git("ls-files", "--stage", "-z", "--", path)
    records = [item for item in entry.split(b"\0") if item]
    if len(records) != 1:
        raise SealError(f"{path} has a missing or duplicate index entry")
    metadata, actual = records[0].split(b"\t", 1)
    fields = metadata.decode("ascii").split()
    if len(fields) != 3 or fields[2] != "0" or actual.decode("utf-8") != path:
        raise SealError(f"{path} index entry is not exact stage zero")
    mode, oid = fields[:2]
    payload = git("cat-file", "blob", oid)
    validate_member(path, mode, oid, payload)
    return mode, oid, payload


def resolve_source(ref: str) -> tuple[str, str]:
    if not REF.fullmatch(ref) or ".." in ref or "@{" in ref:
        raise SealError("Git reference is ambiguous")
    for kind in ("commit", "tree"):
        try:
            oid = git("rev-parse", "--verify", f"{ref}^{{{kind}}}").decode("ascii").strip()
        except SealError:
            continue
        if OID.fullmatch(oid):
            tree = (
                git("rev-parse", "--verify", f"{oid}^{{tree}}").decode("ascii").strip()
                if kind == "commit"
                else oid
            )
            return oid, tree
    raise SealError("Git reference does not resolve to one commit or tree")


def ref_member(tree: str, path: str) -> tuple[str, str, bytes]:
    entry = git("ls-tree", "-z", tree, "--", path)
    records = [item for item in entry.split(b"\0") if item]
    if len(records) != 1:
        raise SealError(f"{path} has a missing or duplicate tree entry")
    metadata, actual = records[0].split(b"\t", 1)
    mode, kind, oid = metadata.decode("ascii").split()
    if kind != "blob" or actual.decode("utf-8") != path:
        raise SealError(f"{path} is a symlink, submodule, tree, or ambiguous path")
    payload = git("cat-file", "blob", oid)
    validate_member(path, mode, oid, payload)
    return mode, oid, payload


def build(entries: dict[str, tuple[str, str, bytes]]) -> dict[str, Any]:
    if set(entries) != set(MEMBERS) or len(entries) != len(MEMBERS):
        raise SealError("procedure member set is missing, duplicated, or expanded")
    artifacts = {
        path: hashlib.sha256(entries[path][2]).hexdigest() for path in MEMBERS
    }
    blobs = {path: entries[path][1] for path in MEMBERS}
    modes = {path: entries[path][0] for path in MEMBERS}
    return {
        "schema_version": 2,
        "docker_inspect_schema_version": 1,
        "member_tree_sha256": hashlib.sha256(
            canonical_json({"git_blobs": blobs, "git_modes": modes})
        ).hexdigest(),
        "artifacts": artifacts,
        "git_blobs": blobs,
        "git_modes": modes,
    }


def build_index() -> bytes:
    if subprocess.run(
        ["git", "diff", "--quiet", "--", *MEMBERS],
        check=False,
    ).returncode != 0:
        raise SealError("sealed members have unstaged worktree substitutions")
    entries = {path: index_member(path) for path in MEMBERS}
    return canonical_json(build(entries))


def verify_ref(ref: str) -> bytes:
    source, tree = resolve_source(ref)
    entries = {path: ref_member(tree, path) for path in MEMBERS}
    expected = canonical_json(build(entries))
    manifest_entry = git("ls-tree", "-z", tree, "--", MANIFEST)
    records = [item for item in manifest_entry.split(b"\0") if item]
    if len(records) != 1:
        raise SealError("procedure manifest is absent or ambiguous in the Git tree")
    metadata, actual = records[0].split(b"\t", 1)
    mode, kind, oid = metadata.decode("ascii").split()
    if mode != "100644" or kind != "blob" or actual.decode("utf-8") != MANIFEST:
        raise SealError("procedure manifest is not one regular tracked blob")
    manifest = git("cat-file", "blob", oid)
    if manifest != expected:
        raise SealError("procedure manifest differs from immutable member blobs")
    return canonical_json(
        {
            "source_object": source,
            "tree": tree,
            "manifest_blob": oid,
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "member_tree_sha256": json.loads(manifest)["member_tree_sha256"],
            "probe_sha256": json.loads(manifest)["artifacts"][
                "scripts/postgres_restore_transaction_probe.sql"
            ],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("build-index")
    verify = modes.add_parser("verify-ref")
    verify.add_argument("ref")
    args = parser.parse_args()
    try:
        sys.stdout.buffer.write(
            build_index() if args.mode == "build-index" else verify_ref(args.ref)
        )
        return 0
    except (UnicodeError, ValueError, json.JSONDecodeError, SealError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
