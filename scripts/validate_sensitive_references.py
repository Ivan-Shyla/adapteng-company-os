#!/usr/bin/env python3
"""Reject sensitive resource and credential references in tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import chain
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
HORIZONTAL_SPACE = frozenset(" \t")
LINE_TERMINATORS = frozenset("\r\n\f\v")
POST_QUOTE_SKIPPABLE = HORIZONTAL_SPACE | frozenset(")]}\u201d\u2019")
OPENING_WRAPPERS = frozenset("([{")
CONTINUATION_PUNCTUATION = frozenset(",:-\u2010\u2011\u2012\u2013\u2014\u2015")
IDENTIFIER_JOIN_CONTROLS = frozenset({"\u200c", "\u200d"})
IDENTIFIER_CONTINUATION_CATEGORY_PREFIXES = frozenset({"L", "M", "N"})
IDENTIFIER_CONTINUATION_CATEGORIES = frozenset({"Cf", "Pc"})
MIN_SENSITIVE_LITERAL_LENGTH = 20
APOSTROPHE_CONTRACTION_SUFFIXES = frozenset(
    {"all", "am", "clock", "d", "ll", "m", "re", "s", "t", "ve"}
)
LEADING_APOSTROPHE_CONTRACTIONS = frozenset(
    {"bout", "cause", "em", "round", "til", "till", "tis", "twas"}
)


@dataclass(frozen=True)
class ContinuationLinkerCategory:
    name: str
    source: str
    linkers: tuple[str, ...]


# This reviewed finite catalog is the supported inventory of common modern
# English clause linkers. It is intentionally explicit rather than claiming
# linguistic completeness: treating every lowercase predicate as a linker
# would merge otherwise independent sentences. The table is the sole source
# for matcher and positive-test generation.
CONTINUATION_LINKER_CATALOG = (
    ContinuationLinkerCategory(
        "coordinating",
        "standard coordinating conjunctions",
        ("and", "but", "for", "nor", "or", "so", "yet"),
    ),
    ContinuationLinkerCategory(
        "subordinating-single",
        "common single-token subordinating conjunctions",
        (
            "after",
            "albeit",
            "although",
            "as",
            "because",
            "before",
            "except",
            "if",
            "lest",
            "once",
            "provided",
            "providing",
            "since",
            "than",
            "though",
            "till",
            "unless",
            "until",
            "when",
            "whenever",
            "whereas",
            "whether",
            "while",
            "whilst",
        ),
    ),
    ContinuationLinkerCategory(
        "relative",
        "common relative and connective forms",
        (
            "that",
            "what",
            "whatever",
            "where",
            "whereby",
            "wherein",
            "whereupon",
            "wherever",
            "which",
            "whichever",
            "who",
            "whoever",
            "whom",
            "whomever",
            "whose",
        ),
    ),
    ContinuationLinkerCategory(
        "additional-linking",
        "bounded additive and sequential linkers",
        ("plus", "then", "with"),
    ),
    ContinuationLinkerCategory(
        "as-family",
        "common as-led subordinators",
        (
            "as far as",
            "as if",
            "as long as",
            "as much as",
            "as often as",
            "as soon as",
            "as though",
        ),
    ),
    ContinuationLinkerCategory(
        "participial-condition",
        "common participial condition linkers",
        (
            "assuming that",
            "considering that",
            "given that",
            "granted that",
            "granting that",
            "presuming that",
            "supposing that",
        ),
    ),
    ContinuationLinkerCategory(
        "even-family",
        "supported common even-led temporal and concessive family",
        (
            "even after",
            "even as",
            "even before",
            "even if",
            "even though",
            "even when",
            "even while",
        ),
    ),
    ContinuationLinkerCategory(
        "exception-condition",
        "bounded exception and condition phrases",
        (
            "except if",
            "except that",
            "except when",
            "except where",
            "provided that",
            "providing that",
            "save that",
        ),
    ),
    ContinuationLinkerCategory(
        "condition-time",
        "common condition and time multiword subordinators",
        (
            "any time",
            "by the time",
            "each time",
            "every time",
            "if and when",
            "if only",
            "in case",
            "in order that",
            "in the event that",
            "now that",
            "on the assumption that",
            "on condition that",
            "on the condition that",
            "on the understanding that",
            "the first time",
            "the instant",
            "the last time",
            "the minute",
            "the moment",
            "the next time",
            "the second",
        ),
    ),
    ContinuationLinkerCategory(
        "only-family",
        "reviewed finite restrictive cause, condition, and time family",
        (
            "only after",
            "only as long as",
            "only because",
            "only before",
            "only if",
            "only in case",
            "only once",
            "only provided that",
            "only providing that",
            "only since",
            "only so long as",
            "only until",
            "only when",
            "only whenever",
            "only where",
            "only while",
        ),
    ),
    ContinuationLinkerCategory(
        "cause-result",
        "common finite cause, content, concession, and result linkers",
        ("for all that", "in that", "seeing that", "such that"),
    ),
    ContinuationLinkerCategory(
        "formal-causal",
        "bounded formal causal linkers",
        ("inasmuch as", "insofar as"),
    ),
    ContinuationLinkerCategory(
        "just-family",
        "reviewed finite just-led cause and temporal family",
        (
            "just after",
            "just as",
            "just because",
            "just before",
            "just when",
            "just while",
        ),
    ),
    ContinuationLinkerCategory(
        "no-matter-interrogative",
        "complete no-matter interrogative family",
        (
            "no matter how",
            "no matter if",
            "no matter what",
            "no matter when",
            "no matter where",
            "no matter whether",
            "no matter which",
            "no matter who",
            "no matter whom",
            "no matter whose",
            "no matter why",
        ),
    ),
    ContinuationLinkerCategory(
        "formal-concession",
        "reviewed finite notwithstanding concessive family",
        (
            "notwithstanding",
            "notwithstanding that",
            "notwithstanding the fact that",
        ),
    ),
    ContinuationLinkerCategory(
        "comparison-purpose",
        "bounded comparison and purpose linkers",
        ("rather than", "so that"),
    ),
    ContinuationLinkerCategory(
        "whether-alternative",
        "bounded whether alternative phrase",
        ("whether or not",),
    ),
)
CONTINUATION_LINKERS = tuple(
    linker
    for category in CONTINUATION_LINKER_CATALOG
    for linker in category.linkers
)
if len(CONTINUATION_LINKERS) != len(set(CONTINUATION_LINKERS)):
    raise ValueError("continuation linker catalog contains duplicates")
CONTINUATION_WORDS = frozenset(
    linker for linker in CONTINUATION_LINKERS if " " not in linker
)
MULTIWORD_CONTINUATION_STARTERS = tuple(
    linker for linker in CONTINUATION_LINKERS if " " in linker
)
MULTIWORD_CONTINUATION_PARTS = tuple(
    tuple(phrase.split()) for phrase in MULTIWORD_CONTINUATION_STARTERS
)
_multiword_by_first: dict[str, list[tuple[str, ...]]] = {}
for _parts in MULTIWORD_CONTINUATION_PARTS:
    _multiword_by_first.setdefault(_parts[0], []).append(_parts)
MULTIWORD_CONTINUATION_BY_FIRST = {
    first: tuple(parts)
    for first, parts in _multiword_by_first.items()
}
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


@dataclass(frozen=True)
class WrapperSpec:
    name: str
    opener: str
    closer: str
    markdown_emphasis: bool = False


DELIMITER_SPECS = (
    DelimiterSpec("backtick", "`", "`", True, 0),
    DelimiterSpec("ascii-double", '"', '"', True, 1),
    DelimiterSpec("ascii-single", "'", "'", True, 2),
    DelimiterSpec("typographic-double", "\u201c", "\u201d", False, 3),
    DelimiterSpec("typographic-single", "\u2018", "\u2019", False, 4),
)
NEXT_QUOTE_WRAPPERS = (
    WrapperSpec("strong-asterisk", "**", "**", True),
    WrapperSpec("strong-underscore", "__", "__", True),
    WrapperSpec("parentheses", "(", ")"),
    WrapperSpec("brackets", "[", "]"),
    WrapperSpec("braces", "{", "}"),
    WrapperSpec("emphasis-asterisk", "*", "*", True),
    WrapperSpec("emphasis-underscore", "_", "_", True),
)
WRAPPER_MARKERS = frozenset("*_()[]{}")
OPENING_WRAPPER_MARKERS = frozenset("*_([{")


@dataclass(frozen=True)
class QuotedSpan:
    start: int
    end: int
    content_start: int
    content_end: int
    delimiter: str
    priority: int


@dataclass(frozen=True)
class NextQuotedContext:
    opening_wrappers: tuple[str, ...]
    quoted_span: QuotedSpan
    content_leading: str
    closing_wrappers: tuple[str, ...]
    closing_end: int
    post_index: int
    post_category: str
    post_token: str
    remaining_tail_start: int


@dataclass(frozen=True)
class MultiwordContinuationMatch:
    end: int | None
    examined_end: int
    malformed_prefix: bool


@dataclass(frozen=True)
class QuoteViews:
    primary_spans: tuple[QuotedSpan, ...]
    primary_coverage: tuple[tuple[int, int], ...]
    evidence_spans: tuple[QuotedSpan, ...]
    evidence_coverage: tuple[tuple[int, int], ...]


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
    context_steps: int = 0

    @property
    def total_operations(self) -> int:
        return (
            self.characters_scanned
            + self.boundary_checks
            + self.keyword_matches
            + self.association_steps
            + self.context_steps
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


def is_apostrophe_identifier_edge(character: str) -> bool:
    return (
        character not in "*_"
        and is_identifier_continuation(character)
    )


def is_intraword_apostrophe(text: str, index: int) -> bool:
    return (
        index > 0
        and index + 1 < len(text)
        and is_apostrophe_identifier_edge(text[index - 1])
        and is_apostrophe_identifier_edge(text[index + 1])
    )


def is_apostrophe_morphology_opener(text: str, index: int) -> bool:
    """Recognize finite common morphology only when it could bridge a clause."""
    suffix_end = index + 1
    while (
        suffix_end < len(text)
        and is_identifier_continuation(text[suffix_end])
    ):
        suffix_end += 1
    suffix = normalize_ascii_linker_word(text[index + 1 : suffix_end])
    if is_intraword_apostrophe(text, index):
        return suffix in APOSTROPHE_CONTRACTION_SUFFIXES
    preceding = text[index - 1] if index else ""
    return (
        (not preceding or not is_identifier_continuation(preceding))
        and suffix in LEADING_APOSTROPHE_CONTRACTIONS
    )


def is_trailing_possessive_apostrophe(text: str, index: int) -> bool:
    if (
        index < 2
        or text[index - 1].casefold() != "s"
        or not is_apostrophe_identifier_edge(text[index - 2])
    ):
        return False
    following = text[index + 1] if index + 1 < len(text) else ""
    return (
        not following
        or following.isspace()
        or following in CLAUSE_TERMINATORS
        or following in CONTINUATION_PUNCTUATION
        or following in ")]}"
    )


def can_open_delimiter(text: str, index: int, spec: DelimiterSpec) -> bool:
    if index + 1 >= len(text) or text[index + 1].isspace():
        return False
    if (
        spec.opener == "'"
        and index > 0
        and is_apostrophe_identifier_edge(text[index - 1])
    ):
        return False
    return True


def can_close_delimiter(text: str, index: int, spec: DelimiterSpec) -> bool:
    if index == 0 or text[index - 1].isspace():
        return False
    if (
        spec.closer == "'"
        and index + 1 < len(text)
        and is_apostrophe_identifier_edge(text[index - 1])
        and is_apostrophe_identifier_edge(text[index + 1])
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


def is_punctuation_bridge_span(text: str, span: QuotedSpan) -> bool:
    bridge_characters = (
        CLAUSE_TERMINATORS
        | CONTINUATION_PUNCTUATION
        | HORIZONTAL_SPACE
    )
    content = text[span.content_start : span.content_end]
    return bool(content) and all(
        character in bridge_characters
        for character in content
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


def select_single_quote_primary_spans(
    ordinary: list[QuotedSpan],
    attached: list[QuotedSpan],
    attached_evidence_bounds: set[tuple[int, int]],
) -> tuple[QuotedSpan, ...]:
    """Give each unescaped apostrophe one deterministic primary role."""
    candidates = sorted(
        deduplicate_quote_spans([*ordinary, *attached]),
        key=lambda span: (span.end, span.start),
    )
    if not candidates:
        return ()
    ordinary_bounds = {(span.start, span.end) for span in ordinary}
    ends = [span.end for span in candidates]
    predecessors = [
        bisect_right(ends, span.start, 0, index) - 1
        for index, span in enumerate(candidates)
    ]
    scores: list[tuple[int, int, int]] = [(0, 0, 0)]
    take: list[bool] = [False] * len(candidates)
    for index, span in enumerate(candidates):
        bounds = (span.start, span.end)
        previous_score = scores[predecessors[index] + 1]
        include = (
            previous_score[0]
            + int(bounds in attached_evidence_bounds),
            previous_score[1] + int(bounds in ordinary_bounds),
            previous_score[2] + span.content_end - span.content_start,
        )
        exclude = scores[index]
        choose = include > exclude
        take[index] = choose
        scores.append(include if choose else exclude)

    selected: list[QuotedSpan] = []
    index = len(candidates) - 1
    while index >= 0:
        if take[index] and scores[index + 1] != scores[index]:
            selected.append(candidates[index])
            index = predecessors[index]
        else:
            index -= 1
    return tuple(sorted(selected, key=lambda span: (span.start, span.end)))


def collect_quote_views(
    text: str,
    escaped: list[bool],
    metrics: ParserMetrics | None = None,
) -> QuoteViews:
    """Collect primary segmentation spans and independent evidence spans."""
    delimiter_characters = frozenset(
        character
        for spec in DELIMITER_SPECS
        for character in (spec.opener, spec.closer)
    )
    escape_aware_delimiter_characters = frozenset(
        character
        for spec in DELIMITER_SPECS
        if spec.escape_aware
        for character in (spec.opener, spec.closer)
    )
    newline_prefix = [0] * (len(text) + 1)
    whitespace_prefix = [0] * (len(text) + 1)
    terminator_prefix = [0] * (len(text) + 1)
    invalid_single_delimiter_prefix = [0] * (len(text) + 1)
    single_quote_positions: list[int] = []
    for index, character in enumerate(text):
        newline_prefix[index + 1] = newline_prefix[index] + int(
            character in LINE_TERMINATORS
        )
        whitespace_prefix[index + 1] = whitespace_prefix[index] + int(
            character.isspace()
        )
        terminator_prefix[index + 1] = terminator_prefix[index] + int(
            character in CLAUSE_TERMINATORS
        )
        invalid_single_delimiter_prefix[index + 1] = (
            invalid_single_delimiter_prefix[index]
            + int(
                character in delimiter_characters
                and not (
                    character in escape_aware_delimiter_characters
                    and escaped[index]
                )
            )
        )
        if character == "'" and not escaped[index]:
            single_quote_positions.append(index)

    evidence_candidates: list[QuotedSpan] = []
    primary_candidates: list[QuotedSpan] = []
    symmetric_primary_openers: set[tuple[str, int]] = set()
    symmetric_primary_closers: set[tuple[str, int]] = set()
    for spec in DELIMITER_SPECS:
        if spec.symmetric:
            previous: int | None = None
            primary_opener: int | None = None
            for index, character in enumerate(text):
                if metrics is not None:
                    metrics.characters_scanned += 1
                if character in LINE_TERMINATORS:
                    previous = None
                    primary_opener = None
                    continue
                if character != spec.opener:
                    continue
                if spec.escape_aware and escaped[index]:
                    continue
                if (
                    spec.opener == "'"
                    and (
                        is_intraword_apostrophe(text, index)
                        or is_trailing_possessive_apostrophe(text, index)
                        or is_apostrophe_morphology_opener(text, index)
                    )
                ):
                    continue
                if (
                    previous is not None
                    and can_open_delimiter(text, previous, spec)
                    and can_close_delimiter(text, index, spec)
                    and not has_newline(newline_prefix, previous, index + 1)
                    and (
                        spec.opener != "'"
                        or invalid_single_delimiter_prefix[index]
                        == invalid_single_delimiter_prefix[previous + 1]
                    )
                ):
                    evidence_candidates.append(
                        make_quoted_span(previous, index, spec)
                    )
                previous = index
                if primary_opener is None:
                    if can_open_delimiter(text, index, spec):
                        primary_opener = index
                        symmetric_primary_openers.add((spec.name, index))
                elif can_close_delimiter(text, index, spec):
                    valid_primary = (
                        spec.opener != "'"
                        or invalid_single_delimiter_prefix[index]
                        == invalid_single_delimiter_prefix[
                            primary_opener + 1
                        ]
                    )
                    if valid_primary:
                        symmetric_primary_closers.add((spec.name, index))
                        primary_candidates.append(
                            make_quoted_span(primary_opener, index, spec)
                        )
                        primary_opener = None
                    else:
                        primary_opener = (
                            index
                            if can_open_delimiter(text, index, spec)
                            else None
                        )
                        if primary_opener is not None:
                            symmetric_primary_openers.add(
                                (spec.name, primary_opener)
                            )
            continue

        openers: list[int] = []
        for index, character in enumerate(text):
            if metrics is not None:
                metrics.characters_scanned += 1
            if character in LINE_TERMINATORS:
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

    ordinary_primary_candidates = tuple(primary_candidates)
    symmetric_delimiters = frozenset(
        spec.name for spec in DELIMITER_SPECS if spec.symmetric
    )
    ordinary_openers = symmetric_primary_openers
    ordinary_closers = symmetric_primary_closers
    evidence_candidates = [
        span
        for span in evidence_candidates
        if not (
            span.delimiter in symmetric_delimiters
            and (span.delimiter, span.start) in ordinary_closers
            and (
                span.delimiter,
                span.end - 1,
            )
            in ordinary_openers
        )
    ]

    single_quote_spec = DELIMITER_SPECS[2]
    ordinary_single_openers = {
        index
        for delimiter, index in ordinary_openers
        if delimiter == single_quote_spec.name
    }
    ordinary_single_closers = {
        index
        for delimiter, index in ordinary_closers
        if delimiter == single_quote_spec.name
    }
    independent_single_primary = [
        span
        for span in evidence_candidates
        if (
            span.delimiter == single_quote_spec.name
            and not is_punctuation_bridge_span(text, span)
        )
    ]
    attached_primary_candidates: list[QuotedSpan] = []
    attached_evidence_bounds: set[tuple[int, int]] = set()
    for opening, closing in zip(
        single_quote_positions,
        single_quote_positions[1:],
    ):
        if closing <= opening + 1:
            continue
        opening_attached = (
            opening > 0
            and is_apostrophe_identifier_edge(text[opening - 1])
            and opening + 1 < closing
            and not text[opening + 1].isspace()
        )
        closing_attached = (
            closing + 1 < len(text)
            and is_apostrophe_identifier_edge(text[closing + 1])
            and closing > opening + 1
            and not text[closing - 1].isspace()
        )
        opening_morphology = is_apostrophe_morphology_opener(
            text,
            opening,
        )
        content = text[opening + 1 : closing]
        if (
            opening in ordinary_single_closers
            and closing in ordinary_single_openers
        ):
            continue
        if (
            invalid_single_delimiter_prefix[closing]
            != invalid_single_delimiter_prefix[opening + 1]
        ):
            continue
        morphology_bridge = (
            is_trailing_possessive_apostrophe(text, opening)
            or opening_morphology
            or (
                (
                    is_intraword_apostrophe(text, opening)
                    or is_trailing_possessive_apostrophe(text, opening)
                )
                and (
                    is_intraword_apostrophe(text, closing)
                    or is_trailing_possessive_apostrophe(text, closing)
                )
                and terminator_prefix[closing]
                != terminator_prefix[opening + 1]
            )
        )
        if not (
            opening_attached
            or can_open_delimiter(text, opening, single_quote_spec)
        ):
            continue
        if not (
            closing_attached
            or can_close_delimiter(text, closing, single_quote_spec)
        ):
            continue
        span = make_quoted_span(opening, closing, single_quote_spec)
        evidence_eligible = (
            whitespace_prefix[closing] == whitespace_prefix[opening + 1]
            and len(content) >= MIN_SENSITIVE_LITERAL_LENGTH
        )
        # Pairing controls clause segmentation even when the content is too
        # short or contains whitespace to qualify as literal evidence.
        if not morphology_bridge or evidence_eligible:
            attached_primary_candidates.append(span)
        if not evidence_eligible:
            continue
        evidence_candidates.append(span)
        attached_evidence_bounds.add((span.start, span.end))

    ordinary_single_primary = [
        span
        for span in ordinary_primary_candidates
        if (
            span.delimiter == single_quote_spec.name
            and not is_punctuation_bridge_span(text, span)
        )
    ]
    selected_single_primary = select_single_quote_primary_spans(
        [*ordinary_single_primary, *independent_single_primary],
        attached_primary_candidates,
        attached_evidence_bounds,
    )
    primary_candidates = [
        span
        for span in ordinary_primary_candidates
        if span.delimiter != single_quote_spec.name
    ]
    primary_candidates.extend(selected_single_primary)

    evidence_spans = deduplicate_quote_spans(evidence_candidates)
    return QuoteViews(
        primary_spans=select_primary_quote_spans(primary_candidates),
        primary_coverage=merge_intervals(
            (span.content_start, span.content_end)
            for span in chain(
                (
                    span
                    for span in ordinary_primary_candidates
                    if not (
                        span.delimiter == single_quote_spec.name
                        and is_punctuation_bridge_span(text, span)
                    )
                ),
                selected_single_primary,
            )
        ),
        evidence_spans=evidence_spans,
        evidence_coverage=merge_intervals(
            (span.start, span.end)
            for span in evidence_spans
            if (
                span.content_end - span.content_start
                >= MIN_SENSITIVE_LITERAL_LENGTH
                and whitespace_prefix[span.content_end]
                == whitespace_prefix[span.content_start]
            )
        ),
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


def skip_characters(text: str, index: int, characters: frozenset[str]) -> int:
    while index < len(text) and text[index] in characters:
        index += 1
    return index


def wrapper_at(text: str, index: int) -> WrapperSpec | None:
    if index < len(text) and text[index] in "*_":
        run_length = markdown_marker_run_length(text, index)
        if run_length not in {1, 2}:
            return None
        marker = text[index]
        expected = marker * run_length
        return next(
            (
                wrapper
                for wrapper in NEXT_QUOTE_WRAPPERS
                if wrapper.markdown_emphasis
                and wrapper.opener == expected
            ),
            None,
        )
    for wrapper in NEXT_QUOTE_WRAPPERS:
        if (
            not wrapper.markdown_emphasis
            and text.startswith(wrapper.opener, index)
        ):
            return wrapper
    return None


def markdown_marker_run_length(text: str, index: int) -> int:
    marker = text[index]
    if marker not in "*_" or (
        index > 0 and text[index - 1] == marker
    ):
        return 0
    end = index + 1
    while end < len(text) and text[end] == marker:
        end += 1
    return end - index


def is_identifier_continuation(character: str) -> bool:
    if not character:
        return False
    category = unicodedata.category(character)
    # Keep invisible controls, marks, and connectors attached to a token so a
    # glued Unicode form cannot be truncated into an approved ASCII linker.
    return (
        character == "-"
        or character in IDENTIFIER_JOIN_CONTROLS
        or category[0] in IDENTIFIER_CONTINUATION_CATEGORY_PREFIXES
        or category in IDENTIFIER_CONTINUATION_CATEGORIES
    )


def normalize_ascii_linker_word(word: str) -> str | None:
    if not word or any(
        not ("A" <= character <= "Z" or "a" <= character <= "z")
        for character in word
    ):
        return None
    return word.lower()


def is_identifier_glue(character: str) -> bool:
    if not is_identifier_continuation(character):
        return False
    category = unicodedata.category(character)
    return (
        character == "-"
        or character in IDENTIFIER_JOIN_CONTROLS
        or category[0] == "M"
        or category in IDENTIFIER_CONTINUATION_CATEGORIES
    )


def candidate_token_projection(
    actual: str,
    *,
    compatibility: bool,
) -> str:
    value = (
        unicodedata.normalize("NFKC", actual)
        if compatibility
        else actual
    )
    return "".join(
        character
        for character in value.casefold()
        if not is_identifier_glue(character)
    )


def is_malformed_candidate_token(actual: str, expected: str) -> bool:
    if normalize_ascii_linker_word(actual) == expected:
        return False
    return expected in {
        candidate_token_projection(actual, compatibility=False),
        candidate_token_projection(actual, compatibility=True),
    }


def next_quote_opening_has_identifier_attachment(
    text: str,
    current_span: QuotedSpan,
    next_span: QuotedSpan | None,
) -> bool:
    if next_span is None:
        return False
    preceding = next_span.start - 1
    if (
        preceding >= current_span.end
        and is_identifier_continuation(text[preceding])
    ):
        return True
    while (
        preceding >= current_span.end
        and text[preceding] in OPENING_WRAPPER_MARKERS
    ):
        preceding -= 1
    return (
        preceding >= current_span.end
        and is_identifier_continuation(text[preceding])
    )


# Markdown markers justify a sentence boundary only when they are detached from
# identifier text. Ambiguous intraword markers keep the credential clause open.
def wrapper_has_valid_opening_flank(
    text: str,
    index: int,
    wrapper: WrapperSpec,
) -> bool:
    if not wrapper.markdown_emphasis:
        return True
    if markdown_marker_run_length(text, index) != len(wrapper.closer):
        return False
    previous = text[index - 1] if index else ""
    following_index = index + len(wrapper.opener)
    return (
        (
            not previous
            or previous in "*_"
            or not is_identifier_continuation(previous)
        )
        and following_index < len(text)
        and not text[following_index].isspace()
    )


def wrapper_has_valid_closing_flank(
    text: str,
    index: int,
    wrapper: WrapperSpec,
) -> bool:
    if not wrapper.markdown_emphasis:
        return True
    previous = text[index - 1] if index else ""
    following_index = index + len(wrapper.closer)
    return (
        bool(previous)
        and not previous.isspace()
        and (
            following_index >= len(text)
            or text[following_index] in "*_"
            or not is_identifier_continuation(text[following_index])
        )
    )


def classify_content_leading(text: str, span: QuotedSpan) -> str:
    index = skip_characters(text, span.content_start, HORIZONTAL_SPACE)
    if index >= span.content_end:
        return "empty"
    character = text[index]
    if character.isupper() or character.isdigit():
        return "uppercase-or-digit"
    if character.islower():
        end = index + 1
        while (
            end < span.content_end
            and is_identifier_continuation(text[end])
        ):
            end += 1
        word = normalize_ascii_linker_word(text[index:end])
        return "conjunction" if word in CONTINUATION_WORDS else "lowercase"
    if character in CONTINUATION_PUNCTUATION:
        return "continuation-punctuation"
    return "other"


def classify_post_quote_context(
    text: str,
    index: int,
) -> tuple[int, str, str]:
    index = skip_characters(text, index, HORIZONTAL_SPACE)
    if index >= len(text):
        return index, "end", ""

    character = text[index]
    if character in LINE_TERMINATORS:
        return index, "newline", character
    if character in CLAUSE_TERMINATORS:
        return index, "terminal-punctuation", character
    if character in CONTINUATION_PUNCTUATION:
        return index, "continuation-punctuation", character
    if is_identifier_continuation(character):
        end, context_word = read_context_word(text, index)
        phrase_match = scan_multiword_continuation(
            text,
            end,
            context_word,
        )
        normalized_word = normalize_ascii_linker_word(context_word)
        category = (
            "continuation-word"
            if (
                phrase_match.end is not None
                or normalized_word in CONTINUATION_WORDS
            )
            else "word"
        )
        token_end = (
            phrase_match.end
            or (
                end
                if normalized_word in CONTINUATION_WORDS
                else (
                    phrase_match.examined_end
                    if phrase_match.malformed_prefix
                    else end
                )
            )
        )
        return index, category, text[index:token_end]
    if character in WRAPPER_MARKERS:
        return index, "unbalanced-wrapper", character
    return index, "other", character


def read_context_word(text: str, index: int) -> tuple[int, str]:
    end = index + 1
    while end < len(text) and is_identifier_continuation(text[end]):
        end += 1
    return end, text[index:end]


def scan_multiword_continuation(
    text: str,
    first_end: int,
    first_word: str,
) -> MultiwordContinuationMatch:
    first_normalized = normalize_ascii_linker_word(first_word)
    candidates = MULTIWORD_CONTINUATION_BY_FIRST.get(
        first_normalized,
        (),
    )
    examined_end = first_end
    malformed_prefix = False
    for parts in candidates:
        cursor = first_end
        matched_parts = 1
        for expected in parts[1:]:
            if cursor >= len(text) or text[cursor] not in HORIZONTAL_SPACE:
                if matched_parts > 1:
                    malformed_prefix = True
                break
            cursor = skip_characters(text, cursor, HORIZONTAL_SPACE)
            if (
                cursor >= len(text)
                or not is_identifier_continuation(text[cursor])
            ):
                if matched_parts > 1:
                    malformed_prefix = True
                break
            cursor, actual = read_context_word(text, cursor)
            examined_end = max(examined_end, cursor)
            actual_normalized = normalize_ascii_linker_word(actual)
            if actual_normalized != expected:
                if is_malformed_candidate_token(actual, expected):
                    malformed_prefix = True
                break
            matched_parts += 1
        else:
            return MultiwordContinuationMatch(
                end=cursor,
                examined_end=cursor,
                malformed_prefix=False,
            )
    return MultiwordContinuationMatch(
        end=None,
        examined_end=examined_end,
        malformed_prefix=malformed_prefix,
    )


def match_multiword_continuation(
    text: str,
    first_end: int,
    first_word: str,
) -> int | None:
    return scan_multiword_continuation(
        text,
        first_end,
        first_word,
    ).end


def parse_next_quoted_context(
    text: str,
    current_span: QuotedSpan,
    next_span: QuotedSpan | None,
    metrics: ParserMetrics | None,
) -> NextQuotedContext | None:
    if next_span is None:
        return None

    if metrics is not None:
        metrics.context_steps += next_span.end - current_span.end
    index = skip_characters(text, current_span.end, HORIZONTAL_SPACE)
    wrappers: list[WrapperSpec] = []
    while index < next_span.start:
        wrapper = wrapper_at(text, index)
        if (
            wrapper is None
            or not wrapper_has_valid_opening_flank(text, index, wrapper)
        ):
            return None
        wrappers.append(wrapper)
        index += len(wrapper.opener)
    if index != next_span.start:
        return None

    closing_end = next_span.end
    closing_wrappers: list[str] = []
    for wrapper in reversed(wrappers):
        if (
            not text.startswith(wrapper.closer, closing_end)
            or not wrapper_has_valid_closing_flank(
                text,
                closing_end,
                wrapper,
            )
        ):
            return None
        closing_wrappers.append(wrapper.name)
        closing_end += len(wrapper.closer)
    if closing_end < len(text) and text[closing_end] in WRAPPER_MARKERS:
        return None

    if (
        closing_end < len(text)
        and is_identifier_continuation(text[closing_end])
    ):
        post_index = closing_end
        _post_end, post_token = read_context_word(text, closing_end)
        post_category = "attached-identifier"
    else:
        post_index, post_category, post_token = (
            classify_post_quote_context(
                text,
                closing_end,
            )
        )
    if metrics is not None:
        metrics.context_steps += (
            post_index - closing_end + max(1, len(post_token))
        )
    return NextQuotedContext(
        opening_wrappers=tuple(wrapper.name for wrapper in wrappers),
        quoted_span=next_span,
        content_leading=classify_content_leading(text, next_span),
        closing_wrappers=tuple(closing_wrappers),
        closing_end=closing_end,
        post_index=post_index,
        post_category=post_category,
        post_token=post_token,
        remaining_tail_start=post_index,
    )


def direct_quote_context_is_terminal(text: str, span: QuotedSpan) -> bool:
    context_index = skip_characters(text, span.end, POST_QUOTE_SKIPPABLE)
    while (
        context_index < len(text)
        and text[context_index] in OPENING_WRAPPERS
    ):
        context_index = skip_characters(
            text,
            context_index + 1,
            HORIZONTAL_SPACE,
        )
    if context_index >= len(text):
        return True
    character = text[context_index]
    if character in LINE_TERMINATORS or character == ";" or (
        character in SENTENCE_FINAL_PUNCTUATION
    ):
        return True
    if character in CONTINUATION_PUNCTUATION:
        return False
    return character.isupper() or character.isdigit()


def is_terminal_quote_context(
    text: str,
    span: QuotedSpan,
    next_span: QuotedSpan | None,
    metrics: ParserMetrics | None,
) -> bool:
    next_context = parse_next_quoted_context(
        text,
        span,
        next_span,
        metrics,
    )
    if next_context is None:
        if next_quote_opening_has_identifier_attachment(
            text,
            span,
            next_span,
        ):
            return False
        # A wrapper-like run that failed exact parsing/flanking is ambiguous.
        # Keeping the clause open is the security-conservative outcome.
        if (
            next_span is not None
            and next_span.start > span.end
            and text[next_span.start - 1] in OPENING_WRAPPER_MARKERS
        ):
            return False
        return direct_quote_context_is_terminal(text, span)
    if next_context.content_leading != "uppercase-or-digit":
        return False
    return next_context.post_category in {
        "end",
        "newline",
        "terminal-punctuation",
        "word",
    }


def build_clause_bounds(
    text: str,
    primary_spans: tuple[QuotedSpan, ...],
    primary_coverage: tuple[tuple[int, int], ...],
    evidence_coverage: tuple[tuple[int, int], ...],
    escaped: list[bool],
    metrics: ParserMetrics | None,
) -> tuple[tuple[int, int], ...]:
    """Classify boundaries from quote roles plus qualifying evidence coverage."""
    covered = merge_intervals(
        chain(
            primary_coverage,
            evidence_coverage,
        )
    )
    covered_starts = [start for start, _end in covered]
    boundaries: set[tuple[int, int]] = set()
    covered_index = 0
    index = 0
    while index < len(text):
        if metrics is not None:
            metrics.characters_scanned += 1
        character = text[index]
        if character in LINE_TERMINATORS:
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

    for span_index, span in enumerate(primary_spans):
        if metrics is not None:
            metrics.boundary_checks += 1
        punctuation_index = span.content_end - 1
        next_span = (
            primary_spans[span_index + 1]
            if span_index + 1 < len(primary_spans)
            else None
        )
        coverage_index = bisect_right(covered_starts, span.end) - 1
        bisects_evidence = (
            coverage_index >= 0
            and covered[coverage_index][0] < span.end
            < covered[coverage_index][1]
        )
        if (
            punctuation_index >= span.content_start
            and text[punctuation_index] in SENTENCE_FINAL_PUNCTUATION
            and not escaped[punctuation_index]
            and is_terminal_quote_context(text, span, next_span, metrics)
            and not bisects_evidence
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
        quote_views.primary_coverage,
        quote_views.evidence_coverage,
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
        if (
            len(value) >= MIN_SENSITIVE_LITERAL_LENGTH
            and not allowed_placeholder(value)
        ):
            violations.append("credential-prose-literal")

    if LEAKED_TOKEN_LITERAL.search(line):
        violations.append("leaked-token-literal")

    return list(dict.fromkeys(violations))


def lf_delimited_lines(text: str) -> list[str]:
    """Split into LF-delimited records, matching editor and ``git diff`` numbering.

    ``str.splitlines()`` breaks on eleven separators — CR, VT, FF, FS, GS, RS,
    NEL, LS and PS as well as LF — so a tracked file containing any of them
    would make this validator report a line number higher than the one the
    reader sees when they open the file at ``path:number:``. Detection is
    unaffected either way; the reported position is not.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def main() -> int:
    violations: list[tuple[Path, int, str]] = []
    for path in tracked_files():
        try:
            lines = lf_delimited_lines(path.read_text(encoding="utf-8"))
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
