#!/usr/bin/env python3
"""Reject sensitive resource and credential references in tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
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
CREDENTIAL_PROSE_NAME = re.compile(
    r"\b(?P<name>"
    r"api[ \t_-]+(?:token|key)"
    r"|(?:cleanup|access|bearer)[ \t_-]+token"
    r"|token|secret|password|credential"
    r")\b",
    re.IGNORECASE,
)
CLAUSE_TERMINATORS = frozenset(".!?;")
SENTENCE_FINAL_PUNCTUATION = frozenset(".!?")
QUOTE_PAIRS = {
    "`": "`",
    '"': '"',
    "'": "'",
    "\u201c": "\u201d",
    "\u2018": "\u2019",
}
ASCII_ESCAPE_AWARE_QUOTES = frozenset({'"', "'"})
CREDENTIAL_DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\ufe58": "-",
        "\ufe63": "-",
        "\uff0d": "-",
    }
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
SAFE_SECRET_REFERENCE = re.compile(
    r"(?:env(?:ironment)?|secret[-_]?manager|vault|key[-_]?vault)"
    r"(?::|://|/)[A-Za-z0-9_./${}:-]+",
    re.IGNORECASE,
)
SAFE_HASH_REFERENCE = re.compile(
    r"(?:md5|sha(?:1|224|256|384|512)|hash|fingerprint)"
    r"[:=_-][A-Fa-f0-9]{8,128}",
    re.IGNORECASE,
)
SAFE_GOVERNED_IDENTIFIER = re.compile(
    r"AE-[A-Z][A-Z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
)
SAFE_BOOLEAN_SETTING = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]*=(?:false|true|null|none)",
    re.IGNORECASE,
)
SAFE_LITERAL_LABELS = {
    "host-only",
    "owner-managed-host-only",
    "owner-only",
    "stored-in-secret-manager",
}


@dataclass(frozen=True)
class QuotedSpan:
    start: int
    end: int
    content_start: int
    content_end: int


@dataclass(frozen=True)
class ClauseSegment:
    start: int
    end: int
    quoted_spans: tuple[QuotedSpan, ...]


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
        or normalized in SAFE_LITERAL_LABELS
        or normalized in {"[placeholder]", "[redacted]"}
        or normalized.startswith(("<", "$", "{{"))
        or "example" in normalized
        or "fingerprint" in normalized
        or "hash-redacted" in normalized
        or "placeholder" in normalized
        or "redacted" in normalized
        or bool(SAFE_SECRET_REFERENCE.fullmatch(value))
        or bool(SAFE_HASH_REFERENCE.fullmatch(value))
        or bool(SAFE_GOVERNED_IDENTIFIER.fullmatch(value))
        or bool(SAFE_BOOLEAN_SETTING.fullmatch(value))
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


def is_escaped(text: str, index: int) -> bool:
    """Return whether the character at index has an odd backslash prefix."""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def is_quote_opener(text: str, index: int) -> bool:
    """Recognize a supported opener without treating apostrophes as quotes."""
    character = text[index]
    if character not in QUOTE_PAIRS:
        return False
    if character in ASCII_ESCAPE_AWARE_QUOTES and is_escaped(text, index):
        return False
    if index + 1 >= len(text) or text[index + 1].isspace():
        return False
    if character == "'":
        if index > 0 and text[index - 1].isalnum():
            return False
    return True


def scan_clauses(text: str) -> tuple[ClauseSegment, ...]:
    """Segment clauses and paired literals in one escape-aware pass."""
    clauses: list[ClauseSegment] = []
    quoted_spans: list[QuotedSpan] = []
    clause_start = 0
    quote_start: int | None = None
    quote_opener: str | None = None
    quote_closer: str | None = None

    def finish_clause(end: int, next_start: int) -> None:
        nonlocal clause_start, quoted_spans
        if clause_start < end:
            clauses.append(
                ClauseSegment(
                    start=clause_start,
                    end=end,
                    quoted_spans=tuple(quoted_spans),
                )
            )
        clause_start = next_start
        quoted_spans = []

    index = 0
    while index < len(text):
        character = text[index]

        if character in "\r\n":
            next_index = index + 1
            if (
                character == "\r"
                and next_index < len(text)
                and text[next_index] == "\n"
            ):
                next_index += 1
            finish_clause(index, next_index)
            quote_start = None
            quote_opener = None
            quote_closer = None
            index = next_index
            continue

        if quote_start is not None:
            closes_quote = character == quote_closer
            if (
                closes_quote
                and quote_opener in ASCII_ESCAPE_AWARE_QUOTES
                and is_escaped(text, index)
            ):
                closes_quote = False
            if closes_quote:
                quoted_spans.append(
                    QuotedSpan(
                        start=quote_start,
                        end=index + 1,
                        content_start=quote_start + 1,
                        content_end=index,
                    )
                )
                terminal_quote = (
                    index > quote_start + 1
                    and text[index - 1] in SENTENCE_FINAL_PUNCTUATION
                    and not is_escaped(text, index - 1)
                )
                quote_start = None
                quote_opener = None
                quote_closer = None
                index += 1
                if terminal_quote:
                    finish_clause(index, index)
                continue
            index += 1
            continue

        if is_quote_opener(text, index):
            quote_start = index
            quote_opener = character
            quote_closer = QUOTE_PAIRS[character]
            index += 1
            continue

        if character in CLAUSE_TERMINATORS:
            index += 1
            finish_clause(index, index)
            continue

        index += 1

    finish_clause(len(text), len(text))
    return tuple(clauses)


def credential_prose_literals(line: str) -> Iterator[str]:
    for clause in scan_clauses(line):
        clause_text = line[clause.start : clause.end]
        normalized_clause = clause_text.translate(CREDENTIAL_DASH_TRANSLATION)
        credential_ends: list[int] = []
        for credential in CREDENTIAL_PROSE_NAME.finditer(normalized_clause):
            credential_start = clause.start + credential.start()
            credential_end = clause.start + credential.end()
            if any(
                span.start <= credential_start
                and credential_end <= span.end
                for span in clause.quoted_spans
            ):
                continue
            credential_ends.append(credential_end)

        for span in clause.quoted_spans:
            if not any(end <= span.start for end in credential_ends):
                continue
            value = line[span.content_start : span.content_end]
            if value and not any(character.isspace() for character in value):
                yield value


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

    for value in credential_prose_literals(line):
        if len(value) >= 20 and not allowed_placeholder(value):
            violations.append("credential-prose-literal")

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
