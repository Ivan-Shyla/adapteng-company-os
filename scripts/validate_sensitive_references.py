#!/usr/bin/env python3
"""Reject sensitive resource and credential references in tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from heapq import heappop, heappush
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
POST_QUOTE_SKIPPABLE = frozenset(" \t\f\v)]}\u201d\u2019")
OPENING_WRAPPERS = frozenset("([{")
CONTINUATION_PUNCTUATION = frozenset(",:-\u2010\u2011\u2012\u2013\u2014\u2015")
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
class DelimiterSpec:
    name: str
    opener: str
    closer: str
    escape_aware: bool
    priority: int

    @property
    def symmetric(self) -> bool:
        return self.opener == self.closer


DELIMITER_SPECS = (
    DelimiterSpec("backtick", "`", "`", True, 0),
    DelimiterSpec("ascii-double", '"', '"', True, 1),
    DelimiterSpec("ascii-single", "'", "'", True, 2),
    DelimiterSpec("typographic-double", "\u201c", "\u201d", False, 3),
    DelimiterSpec("typographic-single", "\u2018", "\u2019", False, 4),
)
NEXT_CONTEXT_SKIPPABLE = (
    POST_QUOTE_SKIPPABLE
    | OPENING_WRAPPERS
    | frozenset(spec.opener for spec in DELIMITER_SPECS)
)


@dataclass(frozen=True)
class QuotedSpan:
    start: int
    end: int
    content_start: int
    content_end: int
    delimiter: str
    priority: int


@dataclass(frozen=True)
class QuoteViews:
    primary_spans: tuple[QuotedSpan, ...]
    evidence_spans: tuple[QuotedSpan, ...]


@dataclass(frozen=True)
class ClauseSegment:
    start: int
    end: int
    quoted_spans: tuple[QuotedSpan, ...]


@dataclass
class ParserMetrics:
    characters_scanned: int = 0
    boundary_checks: int = 0
    keyword_matches: int = 0
    association_steps: int = 0

    @property
    def total_operations(self) -> int:
        return (
            self.characters_scanned
            + self.boundary_checks
            + self.keyword_matches
            + self.association_steps
        )


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


def build_escape_flags(text: str, metrics: ParserMetrics | None) -> list[bool]:
    escaped = [False] * len(text)
    backslash_run = 0
    for index, character in enumerate(text):
        escaped[index] = backslash_run % 2 == 1
        backslash_run = backslash_run + 1 if character == "\\" else 0
        if metrics is not None:
            metrics.characters_scanned += 1
    return escaped


def has_newline(prefix: list[int], start: int, end: int) -> bool:
    return prefix[end] != prefix[start]


def can_open_delimiter(text: str, index: int, spec: DelimiterSpec) -> bool:
    if index + 1 >= len(text) or text[index + 1].isspace():
        return False
    if spec.opener == "'" and index > 0 and text[index - 1].isalnum():
        return False
    return True


def can_close_delimiter(text: str, index: int, spec: DelimiterSpec) -> bool:
    if index == 0 or text[index - 1].isspace():
        return False
    if (
        spec.closer == "'"
        and index + 1 < len(text)
        and text[index - 1].isalnum()
        and text[index + 1].isalnum()
    ):
        return False
    return True


def make_quoted_span(
    start: int,
    end: int,
    spec: DelimiterSpec,
) -> QuotedSpan:
    return QuotedSpan(
        start=start,
        end=end + 1,
        content_start=start + 1,
        content_end=end,
        delimiter=spec.name,
        priority=spec.priority,
    )


def deduplicate_quote_spans(
    candidates: list[QuotedSpan],
) -> tuple[QuotedSpan, ...]:
    by_bounds: dict[tuple[int, int], QuotedSpan] = {}
    for span in candidates:
        key = (span.start, span.end)
        current = by_bounds.get(key)
        if current is None or span.priority < current.priority:
            by_bounds[key] = span
    return tuple(
        sorted(
            by_bounds.values(),
            key=lambda span: (
                span.start,
                span.end - span.start,
                span.priority,
            ),
        )
    )


def select_primary_quote_spans(
    candidates: list[QuotedSpan],
) -> tuple[QuotedSpan, ...]:
    primary: list[QuotedSpan] = []
    for span in sorted(
        deduplicate_quote_spans(candidates),
        key=lambda candidate: (
            candidate.start,
            -candidate.end,
            candidate.priority,
        ),
    ):
        if primary and span.start < primary[-1].end:
            continue
        primary.append(span)
    return tuple(primary)


def collect_quote_views(
    text: str,
    escaped: list[bool],
    metrics: ParserMetrics | None = None,
) -> QuoteViews:
    """Collect primary segmentation spans and independent evidence spans."""
    newline_prefix = [0] * (len(text) + 1)
    for index, character in enumerate(text):
        newline_prefix[index + 1] = newline_prefix[index] + int(
            character in "\r\n"
        )

    evidence_candidates: list[QuotedSpan] = []
    primary_candidates: list[QuotedSpan] = []
    for spec in DELIMITER_SPECS:
        if spec.symmetric:
            previous: int | None = None
            primary_opener: int | None = None
            for index, character in enumerate(text):
                if metrics is not None:
                    metrics.characters_scanned += 1
                if character in "\r\n":
                    previous = None
                    primary_opener = None
                    continue
                if character != spec.opener:
                    continue
                if spec.escape_aware and escaped[index]:
                    continue
                if (
                    spec.opener == "'"
                    and index > 0
                    and index + 1 < len(text)
                    and text[index - 1].isalnum()
                    and text[index + 1].isalnum()
                ):
                    continue
                if (
                    previous is not None
                    and can_open_delimiter(text, previous, spec)
                    and can_close_delimiter(text, index, spec)
                    and not has_newline(newline_prefix, previous, index + 1)
                ):
                    evidence_candidates.append(
                        make_quoted_span(previous, index, spec)
                    )
                previous = index
                if primary_opener is None:
                    if can_open_delimiter(text, index, spec):
                        primary_opener = index
                elif can_close_delimiter(text, index, spec):
                    primary_candidates.append(
                        make_quoted_span(primary_opener, index, spec)
                    )
                    primary_opener = None
            continue

        openers: list[int] = []
        for index, character in enumerate(text):
            if metrics is not None:
                metrics.characters_scanned += 1
            if character in "\r\n":
                openers = []
                continue
            if character == spec.opener and can_open_delimiter(text, index, spec):
                openers.append(index)
                continue
            if (
                character == spec.closer
                and can_close_delimiter(text, index, spec)
                and openers
            ):
                start = openers.pop()
                if not has_newline(newline_prefix, start, index + 1):
                    span = make_quoted_span(start, index, spec)
                    evidence_candidates.append(span)
                    primary_candidates.append(span)

    return QuoteViews(
        primary_spans=select_primary_quote_spans(primary_candidates),
        evidence_spans=deduplicate_quote_spans(evidence_candidates),
    )


def merge_intervals(
    intervals: Iterator[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def build_next_context(text: str) -> list[int]:
    next_context = [len(text)] * (len(text) + 1)
    next_index = len(text)
    for index in range(len(text) - 1, -1, -1):
        if text[index] in NEXT_CONTEXT_SKIPPABLE:
            next_context[index] = next_index
        else:
            next_index = index
            next_context[index] = index
    return next_context


def is_terminal_quote_context(
    text: str,
    span: QuotedSpan,
    next_context: list[int],
) -> bool:
    context_index = next_context[span.end]
    if context_index >= len(text):
        return True
    character = text[context_index]
    if character in "\r\n;" or character in SENTENCE_FINAL_PUNCTUATION:
        return True
    if character in CONTINUATION_PUNCTUATION:
        return False
    return character.isupper() or character.isdigit()


def build_clause_bounds(
    text: str,
    primary_spans: tuple[QuotedSpan, ...],
    escaped: list[bool],
    metrics: ParserMetrics | None,
) -> tuple[tuple[int, int], ...]:
    """Classify boundaries only from deterministic primary quote coverage."""
    covered = merge_intervals(
        (span.content_start, span.content_end)
        for span in primary_spans
    )
    boundaries: set[tuple[int, int]] = set()
    covered_index = 0
    index = 0
    while index < len(text):
        if metrics is not None:
            metrics.characters_scanned += 1
        character = text[index]
        if character in "\r\n":
            next_index = index + 1
            if (
                character == "\r"
                and next_index < len(text)
                and text[next_index] == "\n"
            ):
                next_index += 1
            boundaries.add((index, next_index))
            index = next_index
            continue
        while (
            covered_index < len(covered)
            and covered[covered_index][1] <= index
        ):
            covered_index += 1
            if metrics is not None:
                metrics.boundary_checks += 1
        inside_quote = (
            covered_index < len(covered)
            and covered[covered_index][0] <= index < covered[covered_index][1]
        )
        if character in CLAUSE_TERMINATORS and not inside_quote:
            boundaries.add((index + 1, index + 1))
        index += 1

    next_context = build_next_context(text)
    for span in primary_spans:
        if metrics is not None:
            metrics.boundary_checks += 1
        punctuation_index = span.content_end - 1
        if (
            punctuation_index >= span.content_start
            and text[punctuation_index] in SENTENCE_FINAL_PUNCTUATION
            and not escaped[punctuation_index]
            and is_terminal_quote_context(text, span, next_context)
        ):
            boundaries.add((span.end, span.end))

    clause_bounds: list[tuple[int, int]] = []
    clause_start = 0
    for clause_end, next_start in sorted(
        boundaries,
        key=lambda boundary: (boundary[1], boundary[0]),
    ):
        if next_start <= clause_start:
            continue
        if clause_start < clause_end:
            clause_bounds.append((clause_start, clause_end))
        clause_start = next_start
    if clause_start < len(text):
        clause_bounds.append((clause_start, len(text)))
    return tuple(clause_bounds)


def assign_spans_to_clauses(
    clause_bounds: tuple[tuple[int, int], ...],
    spans: tuple[QuotedSpan, ...],
) -> tuple[ClauseSegment, ...]:
    span_groups: list[list[QuotedSpan]] = [
        [] for _ in clause_bounds
    ]
    clause_index = 0
    for span in spans:
        while (
            clause_index < len(clause_bounds)
            and span.start >= clause_bounds[clause_index][1]
        ):
            clause_index += 1
        if clause_index >= len(clause_bounds):
            break
        clause_start, clause_end = clause_bounds[clause_index]
        if clause_start <= span.start and span.end <= clause_end:
            span_groups[clause_index].append(span)
    return tuple(
        ClauseSegment(
            start=start,
            end=end,
            quoted_spans=tuple(span_groups[index]),
        )
        for index, (start, end) in enumerate(clause_bounds)
    )


def scan_clauses(
    text: str,
    metrics: ParserMetrics | None = None,
) -> tuple[ClauseSegment, ...]:
    """Collect spans, classify boundaries, and assign spans to clauses."""
    escaped = build_escape_flags(text, metrics)
    quote_views = collect_quote_views(text, escaped, metrics)
    clause_bounds = build_clause_bounds(
        text,
        quote_views.primary_spans,
        escaped,
        metrics,
    )
    return assign_spans_to_clauses(
        clause_bounds,
        quote_views.evidence_spans,
    )


def credential_prose_literals(
    line: str,
    metrics: ParserMetrics | None = None,
) -> Iterator[str]:
    """Associate sorted keywords and spans without opposite-list rescans."""
    for clause in scan_clauses(line, metrics):
        clause_text = line[clause.start : clause.end]
        normalized_clause = clause_text.translate(CREDENTIAL_DASH_TRANSLATION)
        credential_scopes: list[tuple[int, int]] = []
        containing_scope_heap: list[int] = []
        containing_span_index = 0
        for credential in CREDENTIAL_PROSE_NAME.finditer(normalized_clause):
            if metrics is not None:
                metrics.keyword_matches += 1
            credential_start = clause.start + credential.start()
            credential_end = clause.start + credential.end()
            while (
                containing_span_index < len(clause.quoted_spans)
                and clause.quoted_spans[containing_span_index].start
                < credential_start
            ):
                heappush(
                    containing_scope_heap,
                    -clause.quoted_spans[containing_span_index].end,
                )
                containing_span_index += 1
                if metrics is not None:
                    metrics.association_steps += 1
            if (
                containing_scope_heap
                and -containing_scope_heap[0] < credential_end
            ):
                containing_scope_heap = []
            scope_end = (
                -containing_scope_heap[0]
                if containing_scope_heap
                else clause.end
            )
            credential_scopes.append((credential_end, scope_end))

        credential_index = 0
        active_scope_ends: list[int] = []
        emitted_content_bounds: set[tuple[int, int]] = set()
        for span in clause.quoted_spans:
            if metrics is not None:
                metrics.association_steps += 1
            while (
                credential_index < len(credential_scopes)
                and credential_scopes[credential_index][0] <= span.start
            ):
                heappush(
                    active_scope_ends,
                    credential_scopes[credential_index][1],
                )
                credential_index += 1
                if metrics is not None:
                    metrics.association_steps += 1
            while active_scope_ends and active_scope_ends[0] <= span.start:
                heappop(active_scope_ends)
                if metrics is not None:
                    metrics.association_steps += 1
            if not active_scope_ends:
                continue
            content_bounds = (span.content_start, span.content_end)
            if content_bounds in emitted_content_bounds:
                continue
            emitted_content_bounds.add(content_bounds)
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
