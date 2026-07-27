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
GOOGLE_SCOPED_ID_FIELD = re.compile(
    r"(?:^\s*|[,{]\s*)(?:id|drive_id|folder_id|document_id|sheet_id)\s*:\s*"
    r"[\"']?(?P<value>[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)
GOOGLE_NAMED_ID_FIELD = re.compile(
    r"^\s*(?:drive_id|folder_id|document_id|sheet_id)\s*:\s*"
    r"[\"']?(?P<value>[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_ -]?key|token|secret|password|private[_ -]?key)\b"
    r"\s*(?:=|:)\s*[\"'`]?(?P<value>[^\s#,\"'`}\]]+)",
    re.IGNORECASE,
)
CREDENTIAL_NAME_LITERAL = re.compile(
    r"\b(?P<name>[A-Z][A-Z0-9_-]*"
    r"(?:TOKEN|API[_-]?KEY|SECRET|PASSWORD|PRIVATE[_-]?KEY)"
    r"[A-Z0-9_-]*)\b"
    r"\s+(?:(?:value|credential)\s+)?(?:is\s+)?"
    r"(?:"
    r"(?P<quote>[`\"'])(?P<quoted_value>[^`\"'\s]{20,})(?P=quote)"
    r"|(?P<bare_value>[A-Za-z0-9_./+=-]{20,})"
    r")"
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


def allowed_resource_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized.startswith(("<", "$", "{{"))
        or "example" in normalized
        or "fingerprint" in normalized
        or "placeholder" in normalized
        or "redacted" in normalized
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


def inspect_line(
    line: str,
    *,
    yaml_file: bool = False,
    in_google_drive: bool = False,
) -> list[str]:
    violations: list[str] = []

    if yaml_file:
        named_id = GOOGLE_NAMED_ID_FIELD.search(line)
        if named_id and not allowed_resource_placeholder(named_id.group("value")):
            violations.append("raw-google-resource-id")

    if in_google_drive:
        scoped_id = GOOGLE_SCOPED_ID_FIELD.search(line)
        if scoped_id and not allowed_resource_placeholder(scoped_id.group("value")):
            violations.append("raw-google-resource-id")

    for match in URL_PATTERN.finditer(line):
        violations.extend(inspect_url(match.group(0)))

    for assignment in SECRET_ASSIGNMENT.finditer(line):
        if not allowed_placeholder(assignment.group("value")):
            violations.append("literal-secret-assignment")

    for credential in CREDENTIAL_NAME_LITERAL.finditer(line):
        value = credential.group("quoted_value") or credential.group("bare_value")
        if not allowed_placeholder(value):
            violations.append("credential-name-literal")

    if LEAKED_TOKEN_LITERAL.search(line):
        violations.append("leaked-token-literal")

    return list(dict.fromkeys(violations))


def main() -> int:
    violations: list[tuple[Path, int, str]] = []
    for path in tracked_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        relative = path.relative_to(ROOT)
        in_google_drive = False
        yaml_file = path.suffix.lower() in {".yaml", ".yml"}
        for number, line in enumerate(lines, 1):
            if relative.as_posix() == "registry/data-stores.yaml":
                if line == "google_drive:":
                    in_google_drive = True
                elif in_google_drive and line and not line[0].isspace():
                    in_google_drive = False

            for rule in inspect_line(
                line,
                yaml_file=yaml_file,
                in_google_drive=in_google_drive,
            ):
                violations.append((relative, number, rule))

    if violations:
        for path, number, rule in violations:
            print(f"{path}:{number}: {rule}")
        return 1

    print("Sensitive-reference regression check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
