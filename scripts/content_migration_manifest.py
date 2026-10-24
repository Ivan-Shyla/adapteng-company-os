#!/usr/bin/env python3
"""Validate the content Drive migration manifest and guard copies against duplicates.

The manifest in registry/content-drive-migration.json is the reviewed plan for
copying article and marketing content from a personal Drive into the company
Shared Drive. It records no Drive IDs; the copy step reads those at run time.
This module checks the manifest's invariants and provides the duplicate guard
a copy run must apply before it writes anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPOSITORY_ROOT / "registry" / "content-drive-migration.json"

SCHEMA = "adapteng.content-drive-migration/v1"
ALLOWED_ACTIONS = ("copy", "hold", "exclude")
AE_CGR_ID = re.compile(r"^AE-CGR-\d{4}$")
DRIVE_ID_LIKE = re.compile(r"[A-Za-z0-9_-]{28,}")
DRIVE_URL = re.compile(r"drive\.google\.com|docs\.google\.com", re.IGNORECASE)
FORBIDDEN_DESTINATION_PARTS = ("01_Inbox",)
PROTECTED_RADAR_FIELDS = ("status", "drive_file_id", "analysis_id")


class ManifestError(ValueError):
    """The manifest breaks an invariant a copy run depends on."""


def load(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _action_word(value: str) -> str:
    return value.split(":", 1)[0].strip().lower()


def validate(manifest: dict) -> list[str]:
    """Return every violated invariant; an empty list means the manifest is usable."""
    problems: list[str] = []

    if manifest.get("schema") != SCHEMA:
        problems.append("schema is not " + SCHEMA)
    if manifest.get("status") != "prepared-not-executed":
        problems.append("status must stay prepared-not-executed until a governed run records evidence")

    text = json.dumps(manifest)
    if DRIVE_URL.search(text):
        problems.append("manifest contains a Drive or Docs URL")
    for word in re.findall(r'"([^"]+)"', text):
        if DRIVE_ID_LIKE.fullmatch(word) and re.search(r"[A-Z]", word) and re.search(r"[a-z]", word) and re.search(r"\d", word):
            problems.append("manifest contains a value shaped like a Drive ID: " + word[:6] + "...")

    destination = manifest.get("destination", {})
    if destination.get("folders_created_so_far"):
        problems.append("folders_created_so_far must be empty until a governed folder operation records them")

    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for source_class in manifest.get("source_classes", []):
        label = source_class.get("class", "?")
        action_text = source_class.get("action")
        items = source_class.get("items")
        if items is None and not isinstance(action_text, str):
            problems.append(label + ": class has no action")
        if isinstance(action_text, str) and _action_word(action_text) not in ALLOWED_ACTIONS:
            problems.append(label + ": action must start with copy, hold or exclude")
        for item in items or []:
            key = item.get("legacy_key")
            if not key:
                problems.append(label + ": item without legacy_key")
            elif key in seen_keys:
                problems.append(label + ": duplicate legacy_key " + key)
            else:
                seen_keys.add(key)
            ae_id = item.get("ae_cgr_id")
            if ae_id is not None:
                if not AE_CGR_ID.match(ae_id):
                    problems.append(label + ": malformed ae_cgr_id " + str(ae_id))
                elif ae_id in seen_ids:
                    problems.append(label + ": duplicate ae_cgr_id " + ae_id)
                else:
                    seen_ids.add(ae_id)
            if _action_word(item.get("action", "")) not in ALLOWED_ACTIONS:
                problems.append(label + ": item action must be copy, hold or exclude")
            target = item.get("destination", "")
            if any(part in target for part in FORBIDDEN_DESTINATION_PARTS):
                problems.append(label + ": destination is a watched intake folder")
            if _action_word(item.get("action", "")) == "copy" and not target.startswith("40_Content/"):
                problems.append(label + ": copied article items belong under 40_Content")

    for entry in manifest.get("workflow_map", []):
        if _action_word(str(entry.get("action", "hold"))) == "activate":
            problems.append(str(entry.get("workflow")) + ": the manifest must not activate a workflow")

    repeats = [c for c in manifest.get("source_classes", []) if c.get("repeats")]
    for source_class in repeats:
        if sum(source_class["repeats"].values()) != source_class.get("count"):
            problems.append(source_class["class"] + ": repeats do not add up to count")

    writer = manifest.get("recurring_writer")
    if not writer:
        problems.append("recurring_writer is missing: the path that writes recurring source packages is unaccounted for")
    else:
        if set(writer.get("protected_fields", [])) != set(PROTECTED_RADAR_FIELDS):
            problems.append("recurring_writer.protected_fields must be exactly " + ", ".join(PROTECTED_RADAR_FIELDS))
        if writer.get("status") == "proven" and writer.get("confirm_with"):
            problems.append("recurring_writer is marked proven while confirm_with is still listed")
    gates = manifest.get("completion_gates", [])
    if not gates:
        problems.append("completion_gates is missing")
    if manifest.get("status") in ("complete", "completed") and not all(g.get("satisfied") for g in gates):
        problems.append("status claims completion while a completion gate is unsatisfied")

    return problems


def plan_copy(candidates: list[dict], already_copied: set[tuple[str, str]] | None = None) -> dict:
    """Split candidate source files into those to copy and those to skip.

    Each candidate carries ``source_file_id``, ``legacy_key`` and ``sha256``. A
    candidate is skipped when its source file ID or its (legacy_key, sha256)
    pair was already copied, or repeats an earlier candidate in the same run.
    The function only plans; it writes nothing.
    """
    copied_pairs = set(already_copied or set())
    seen_file_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    to_copy: list[dict] = []
    skipped: list[dict] = []
    for candidate in candidates:
        file_id = candidate["source_file_id"]
        pair = (candidate["legacy_key"], candidate["sha256"])
        if file_id in seen_file_ids:
            skipped.append({**candidate, "reason": "repeated source file id"})
        elif pair in copied_pairs or pair in seen_pairs:
            skipped.append({**candidate, "reason": "same article and content already copied"})
        else:
            to_copy.append(candidate)
            seen_pairs.add(pair)
        seen_file_ids.add(file_id)
    return {"copy": to_copy, "skip": skipped}


def rediscovery_row(existing: dict | None, discovered: dict) -> dict | None:
    """Return the row discovery may write, or None when it must write nothing.

    A publication found again must not touch the row that already tracks it:
    the upsert in MM-20 maps status, drive_file_id and analysis_id from the new
    item, and the new item carries empty values for the last two.
    """
    if existing is not None:
        return None
    return dict(discovered)


def protected_field_mappings(columns: dict) -> list[str]:
    """Return protected radar fields that a discovery upsert maps from the item.

    ``columns`` is the ``columns.value`` mapping of the data table upsert node.
    A reviewed rewiring of MM-20 must return an empty list.
    """
    return [field for field in PROTECTED_RADAR_FIELDS if field in columns]


def dedupe_candidates(rows: list[dict]) -> list[dict]:
    """Keep the first row per pub_id, in order, and drop rows without one.

    MM-21 reads its candidates once per incoming item, so two approved items
    returned the same two rows twice and produced four files. Applying this
    before the Drive write makes the result independent of the fan-out.
    """
    seen: set[str] = set()
    kept: list[dict] = []
    for row in rows:
        pub_id = str(row.get("pub_id") or "").strip()
        if not pub_id or pub_id in seen:
            continue
        seen.add(pub_id)
        kept.append(row)
    return kept


def should_write_source_package(row: dict, existing_names: set[str]) -> bool:
    """Decide whether MM-21 may create a source package for a radar row.

    It writes only when the row has no Drive file recorded and the destination
    holds no file whose name starts with the row's pub_id.
    """
    if str(row.get("drive_file_id") or "").strip():
        return False
    pub_id = str(row.get("pub_id") or "").strip()
    if not pub_id:
        return False
    return not any(name.startswith(pub_id) for name in existing_names)


if __name__ == "__main__":
    found = validate(load())
    for problem in found:
        print("PROBLEM:", problem)
    raise SystemExit(1 if found else 0)
