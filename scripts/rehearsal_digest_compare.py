#!/usr/bin/env python3
"""Compare deterministic PostgreSQL content digests between two clusters.

A restore that exits zero has proven nothing. This compares what the restored
cluster actually contains against what was backed up: the exact set of tables,
the row count of each, and a content checksum of each table's rows.

The parser is strict on purpose. A digest file that is empty, truncated,
duplicated or malformed would otherwise compare equal to another equally broken
file and report success, so every one of those cases is an error rather than an
input. For the same reason the floors exist: comparing zero tables or zero rows
succeeds trivially, so a comparison that has nothing to compare is rejected.

``--expect different`` is the negative control. The same comparison that proves
a restore matches its backup must be able to prove that two genuinely different
database states do not match, otherwise it is not measuring anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENTRY = re.compile(r"^(?P<table>[^|\s]+)\|(?P<rows>\d+)\|(?P<digest>[0-9a-f]{32})$")


class DigestError(RuntimeError):
    """A digest file could not be trusted."""


@dataclass(frozen=True)
class TableDigest:
    rows: int
    digest: str


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def parse_digest(text: str, *, label: str) -> dict[str, TableDigest]:
    entries: dict[str, TableDigest] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = ENTRY.fullmatch(line.strip())
        if match is None:
            raise DigestError(f"{label} line {number} is not a digest entry")
        table = match.group("table")
        if table in entries:
            raise DigestError(f"{label} line {number} repeats table {table}")
        entries[table] = TableDigest(int(match.group("rows")), match.group("digest"))
    if not entries:
        raise DigestError(f"{label} contains no digest entries")
    return entries


def load_digest(path: Path, *, label: str) -> dict[str, TableDigest]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DigestError(f"{label} cannot be read from {path}") from exc
    return parse_digest(text, label=label)


def set_sha256(entries: dict[str, TableDigest]) -> str:
    return hashlib.sha256(
        canonical_json(
            {table: [value.rows, value.digest] for table, value in entries.items()}
        )
    ).hexdigest()


def total_rows(entries: dict[str, TableDigest]) -> int:
    return sum(value.rows for value in entries.values())


def differences(
    left: dict[str, TableDigest], right: dict[str, TableDigest]
) -> list[str]:
    found: list[str] = []
    for table in sorted(set(left) - set(right)):
        found.append(f"{table}: present on the left only")
    for table in sorted(set(right) - set(left)):
        found.append(f"{table}: present on the right only")
    for table in sorted(set(left) & set(right)):
        if left[table].rows != right[table].rows:
            found.append(
                f"{table}: row count {left[table].rows} != {right[table].rows}"
            )
        if left[table].digest != right[table].digest:
            found.append(f"{table}: content checksum differs")
    return found


def enforce_floors(
    entries: dict[str, TableDigest], *, label: str, min_tables: int, min_rows: int
) -> None:
    if len(entries) < min_tables:
        raise DigestError(
            f"{label} has {len(entries)} tables, fewer than the required {min_tables}"
        )
    rows = total_rows(entries)
    if rows < min_rows:
        raise DigestError(
            f"{label} has {rows} rows, fewer than the required {min_rows}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--left", required=True, type=Path)
    parser.add_argument("--right", required=True, type=Path)
    parser.add_argument("--left-label", default="left")
    parser.add_argument("--right-label", default="right")
    parser.add_argument("--expect", required=True, choices=("equal", "different"))
    parser.add_argument("--min-tables", type=int, default=1)
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        left = load_digest(args.left, label=args.left_label)
        right = load_digest(args.right, label=args.right_label)
        enforce_floors(
            left,
            label=args.left_label,
            min_tables=args.min_tables,
            min_rows=args.min_rows,
        )
        enforce_floors(
            right,
            label=args.right_label,
            min_tables=args.min_tables,
            min_rows=args.min_rows,
        )
    except DigestError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    found = differences(left, right)
    satisfied = not found if args.expect == "equal" else bool(found)
    payload = {
        "expectation": args.expect,
        "difference_count": len(found),
        args.left_label: {
            "tables": len(left),
            "rows": total_rows(left),
            "set_sha256": set_sha256(left),
        },
        args.right_label: {
            "tables": len(right),
            "rows": total_rows(right),
            "set_sha256": set_sha256(right),
        },
        "satisfied": satisfied,
    }
    if args.output is not None:
        args.output.write_bytes(canonical_json(payload))

    print(
        f"{args.left_label}: {len(left)} tables, {total_rows(left)} rows, "
        f"set_sha256={set_sha256(left)}"
    )
    print(
        f"{args.right_label}: {len(right)} tables, {total_rows(right)} rows, "
        f"set_sha256={set_sha256(right)}"
    )
    for difference in found:
        print(f"difference {difference}")

    if not satisfied:
        print(
            f"STOP: expected the two states to be {args.expect}, found "
            f"{len(found)} differences",
            file=sys.stderr,
        )
        return 2
    if args.expect == "equal":
        print(
            f"{args.left_label} and {args.right_label} are identical across "
            f"{len(left)} tables and {total_rows(left)} rows."
        )
    else:
        print(
            f"{args.left_label} and {args.right_label} differ in {len(found)} ways, "
            "as required."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
