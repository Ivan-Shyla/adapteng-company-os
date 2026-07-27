#!/usr/bin/env python3
"""Reject sensitive resource and credential references in tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
URL_PATTERN = re.compile(r"https?://[^\s<>()`]+", re.IGNORECASE)
GOOGLE_RESOURCE_PATH = re.compile(
    r"^/(?:drive/(?:u/\d+/)?folders|file/d|document/d|spreadsheets/d|"
    r"presentation/d|forms/d)/[^/]+",
    re.IGNORECASE,
)
GOOGLE_ID_FIELD = re.compile(
    r"(?:^|[,{]\s*)(?:id|folder_id|document_id|sheet_id)\s*:\s*"
    r"[\"']?[A-Za-z0-9_-]{20,}",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_ -]?key|token|secret|password|private[_ -]?key)\b"
    r"\s*(?:=|:)\s*[\"'`]?(?P<value>[^\s#,\"'`}\]]+)",
    re.IGNORECASE,
)
LEAKED_TOKEN_LITERAL = re.compile(
    r"\b(?:leaked|compromised)\b[^\n`]{0,80}\btoken\b[^\n`]{0,20}`[^`]+`",
    re.IGNORECASE,
)
SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "key",
    "password",
    "secret",
    "token",
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def allowed_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized in {"false", "none", "null", "true"}
        or normalized.startswith(("<", "$", "{{"))
        or "example" in normalized
        or "fingerprint" in normalized
        or "placeholder" in normalized
        or "redacted" in normalized
        or bool(re.fullmatch(r"[A-Z][A-Z0-9_]+", value))
    )


def inspect_url(raw: str) -> list[str]:
    parsed = urlsplit(raw.rstrip(".,;"))
    violations: list[str] = []
    host = (parsed.hostname or "").lower()
    if host in {"drive.google.com", "docs.google.com"}:
        if GOOGLE_RESOURCE_PATH.match(parsed.path):
            violations.append("exact-google-resource-url")
        if host == "drive.google.com" and parsed.path == "/open":
            if any(key.lower() == "id" for key, _ in parse_qsl(parsed.query)):
                violations.append("exact-google-resource-url")
    if parsed.username or parsed.password:
        violations.append("credential-bearing-url")
    if any(
        key.lower() in SECRET_QUERY_KEYS
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        violations.append("credential-bearing-url")
    return violations


def main() -> int:
    violations: list[tuple[Path, int, str]] = []
    for path in tracked_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        relative = path.relative_to(ROOT)
        in_google_drive = False
        for number, line in enumerate(lines, 1):
            if relative.as_posix() == "registry/data-stores.yaml":
                if line == "google_drive:":
                    in_google_drive = True
                elif in_google_drive and line and not line[0].isspace():
                    in_google_drive = False
                if in_google_drive and GOOGLE_ID_FIELD.search(line):
                    violations.append((relative, number, "raw-google-resource-id"))

            for match in URL_PATTERN.finditer(line):
                for rule in inspect_url(match.group(0)):
                    violations.append((relative, number, rule))

            assignment = SECRET_ASSIGNMENT.search(line)
            if assignment and not allowed_placeholder(assignment.group("value")):
                violations.append((relative, number, "literal-secret-assignment"))

            if LEAKED_TOKEN_LITERAL.search(line):
                violations.append((relative, number, "leaked-token-literal"))

    if violations:
        for path, number, rule in violations:
            print(f"{path}:{number}: {rule}")
        return 1

    print("Sensitive-reference regression check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
