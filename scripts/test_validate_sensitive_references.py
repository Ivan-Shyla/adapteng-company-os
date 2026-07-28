#!/usr/bin/env python3
"""Regression tests for tracked sensitive-reference validation."""

from __future__ import annotations

import unittest
import unicodedata

from scripts.validate_sensitive_references import (
    CONTINUATION_LINKER_CATALOG,
    CONTINUATION_WORDS,
    IDENTIFIER_JOIN_CONTROLS,
    MULTIWORD_CONTINUATION_STARTERS,
    ParserMetrics,
    classify_post_quote_context,
    credential_prose_literals,
    inspect_line,
    inspect_url,
    is_identifier_continuation,
    is_malformed_candidate_token,
    match_multiword_continuation,
    normalize_ascii_linker_word,
    read_context_word,
    scan_clauses,
)


class SensitiveReferenceValidatorTests(unittest.TestCase):
    QUOTE_PAIRS = (
        ("backtick", "`", "`"),
        ("ascii-double", '"', '"'),
        ("ascii-single", "'", "'"),
        ("typographic-double", "\u201c", "\u201d"),
        ("typographic-single", "\u2018", "\u2019"),
    )
    SIMPLE_WRAPPERS = (
        ("none", "", ""),
        ("parentheses", "(", ")"),
        ("brackets", "[", "]"),
        ("braces", "{", "}"),
    )
    EMPHASIS_WRAPPERS = (
        ("strong-asterisk", "**", "**"),
        ("strong-underscore", "__", "__"),
        ("emphasis-asterisk", "*", "*"),
        ("emphasis-underscore", "_", "_"),
    )
    NEXT_CONTEXT_WRAPPERS = SIMPLE_WRAPPERS + EMPHASIS_WRAPPERS
    REPRODUCED_BOUNDED_LINKERS = (
        "even when",
        "just as",
        "in order that",
        "on condition that",
        "whereby",
        "wherein",
    )
    LATEST_REPRODUCED_LINKERS = (
        "no matter whom",
        "no matter whose",
        "even as",
        "by the time",
        "in the event that",
        "just when",
        "just before",
        "just after",
    )
    REVIEWED_STANDARD_ADDITIONS = (
        "albeit",
        "whilst",
        "seeing that",
        "such that",
        "every time",
        "each time",
        "the moment",
        "the minute",
        "the instant",
        "the second",
    )
    REVIEWED_LINKER_FAMILIES = {
        "only-family": frozenset(
            {
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
            }
        ),
        "just-family": frozenset(
            {
                "just after",
                "just as",
                "just because",
                "just before",
                "just when",
                "just while",
            }
        ),
        "no-matter-interrogative": frozenset(
            {
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
            }
        ),
        "formal-concession": frozenset(
            {
                "notwithstanding",
                "notwithstanding that",
                "notwithstanding the fact that",
            }
        ),
    }
    EXACT_SINGLE_FALLBACK_CANDIDATES = {
        "as": "if",
        "except": "that",
        "provided": "that",
        "providing": "that",
        "so": "that",
        "whether": "or",
    }
    UNRELATED_UNICODE_GLUE = (
        ("combining-acute", "\u0301", "Mn"),
        ("variation-selector", "\ufe0f", "Mn"),
        ("devanagari-spacing-mark", "\u0903", "Mc"),
        ("combining-enclosing-circle", "\u20dd", "Me"),
        ("zero-width-non-joiner", "\u200c", "Cf"),
        ("zero-width-joiner", "\u200d", "Cf"),
        ("word-joiner", "\u2060", "Cf"),
        ("invisible-separator", "\u2063", "Cf"),
        ("underscore", "_", "Pc"),
        ("undertie", "\u203f", "Pc"),
        ("character-tie", "\u2040", "Pc"),
    )
    UNICODE_QUOTE_EDGE_CHARACTERS = (
        ("uppercase-letter", "A", "Lu"),
        ("lowercase-letter", "a", "Ll"),
        ("titlecase-letter", "\u01c5", "Lt"),
        ("other-letter", "\u05d0", "Lo"),
        ("decimal-number", "7", "Nd"),
        ("letter-number", "\u2160", "Nl"),
        ("other-number", "\u00b2", "No"),
        ("combining-acute", "\u0301", "Mn"),
        ("devanagari-spacing-mark", "\u0903", "Mc"),
        ("combining-enclosing-circle", "\u20dd", "Me"),
        ("connector", "\u203f", "Pc"),
        ("zero-width-joiner", "\u200d", "Cf"),
        ("zero-width-non-joiner", "\u200c", "Cf"),
        ("word-joiner", "\u2060", "Cf"),
    )
    UNICODE_IDENTIFIER_BOUNDARIES = (
        ("combining-acute", "\u0301", "Mn"),
        ("combining-enclosing-circle", "\u20dd", "Me"),
        ("devanagari-spacing-mark", "\u0903", "Mc"),
        ("zero-width-non-joiner", "\u200c", "Cf"),
        ("zero-width-joiner", "\u200d", "Cf"),
        ("word-joiner", "\u2060", "Cf"),
        ("underscore", "_", "Pc"),
        ("undertie", "\u203f", "Pc"),
        ("greek-letter", "\u03b1", "Ll"),
        ("arabic-indic-digit", "\u0661", "Nd"),
    )

    def assert_single_clause_violation(
        self,
        line: str,
        expected_literal: str,
    ) -> None:
        self.assertEqual(
            [(0, len(line))],
            [(clause.start, clause.end) for clause in scan_clauses(line)],
        )
        self.assertIn(
            expected_literal,
            list(credential_prose_literals(line)),
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def assert_boundary_after(
        self,
        line: str,
        prefix: str,
    ) -> None:
        clauses = scan_clauses(line)
        self.assertGreaterEqual(len(clauses), 2)
        self.assertEqual(len(prefix), clauses[0].end)
        self.assertEqual([], inspect_line(line))

    def assert_context_outcome(
        self,
        context: str,
        line: str,
        prefix: str,
        unsafe_value: str,
        *,
        expected_continuation: bool | None = None,
    ) -> None:
        first_end, first_word = read_context_word(context, 0)
        exact_single = (
            normalize_ascii_linker_word(first_word)
            in CONTINUATION_WORDS
        )
        if expected_continuation is None:
            expected_continuation = exact_single
        _index, category, context_value = classify_post_quote_context(
            context,
            0,
        )
        if expected_continuation:
            self.assertEqual("continuation-word", category)
            if exact_single:
                self.assertEqual(context[:first_end], context_value)
            self.assert_single_clause_violation(line, unsafe_value)
        else:
            self.assertEqual("word", category)
            self.assert_boundary_after(line, prefix)

    def test_rejects_indented_yaml_google_id(self) -> None:
        line = "  folder_" + "id: " + ("A" * 24)
        self.assertIn(
            "raw-google-resource-id",
            inspect_line(line, yaml_file=True),
        )

    def test_rejects_credential_name_with_quoted_literal(self) -> None:
        line = (
            "BASEROW_API_"
            + "TOKEN "
            + "`"
            + "synthetic-token-value-1234567890"
            + "`"
        )
        self.assertIn("credential-name-literal", inspect_line(line))

    def test_rejects_credential_name_with_bare_literal(self) -> None:
        line = (
            "BASEROW_API_"
            + "TOKEN "
            + "synthetic-token-value-1234567890"
        )
        self.assertIn("credential-name-literal", inspect_line(line))

    def test_allows_redacted_google_id_placeholder(self) -> None:
        prefix = "  folder_" + "id: "
        for value in (
            "<redacted>",
            "fingerprint-redacted-0000000000",
            "PLACEHOLDER_GOOGLE_RESOURCE_ID",
        ):
            with self.subTest(value=value):
                self.assertEqual([], inspect_line(prefix + value, yaml_file=True))

    def test_allows_safe_credential_placeholders(self) -> None:
        name = "BASEROW_API_" + "TOKEN "
        for value in (
            "<redacted>",
            "fingerprint-redacted",
            "${TOKEN_VALUE}",
            "{{environment.TOKEN_VALUE}}",
            "PLACEHOLDER_TOKEN",
        ):
            with self.subTest(value=value):
                self.assertEqual([], inspect_line(name + "`" + value + "`"))

    def test_allows_non_secret_credential_label(self) -> None:
        name = "BASEROW_API_" + "TOKEN "
        self.assertEqual([], inspect_line(name + "`host-only`"))

    def test_rejects_cleanup_token_in_normal_prose(self) -> None:
        literal = "synthetic-cleanup-token-12345"
        line = (
            "The cleanup "
            + "token `"
            + literal
            + "` must be rotated."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))
        self.assertEqual(
            [literal],
            list(credential_prose_literals(line)),
        )

    def test_rejects_long_same_clause_credential_prose(self) -> None:
        literal = "synthetic-long-clause-secret-1234567890"
        filler = (
            " remains documented through an intentionally extended governance "
            + "explanation with owner review and containment evidence before value "
        )
        line = "The cleanup " + "token" + filler + "`" + literal + "` must rotate."
        distance = line.index("`") - (line.lower().index("token") + len("token"))
        self.assertGreater(distance, 64)
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_rejects_typographic_credential_prose_literals(self) -> None:
        literal = "synthetic-typographic-secret-1234567890"
        for opening, closing in (
            ("\u201c", "\u201d"),
            ("\u2018", "\u2019"),
        ):
            with self.subTest(opening=opening):
                line = (
                    "The access "
                    + "token "
                    + opening
                    + literal
                    + closing
                    + " must rotate."
                )
                self.assertIn("credential-prose-literal", inspect_line(line))
                self.assertEqual(
                    [literal],
                    list(credential_prose_literals(line)),
                )

    def test_ascii_quote_escape_matrix(self) -> None:
        for name, quote in (
            ("backtick", "`"),
            ("ascii-double", '"'),
            ("ascii-single", "'"),
        ):
            for backslash_count in (1, 3):
                with self.subTest(name=name, backslashes=backslash_count):
                    escaped_quote = ("\\" * backslash_count) + quote
                    value = (
                        "short-"
                        + escaped_quote
                        + "continued-synthetic-secret-1234567890"
                    )
                    line = (
                        "The cleanup "
                        + "token "
                        + quote
                        + value
                        + quote
                        + " must rotate."
                    )
                    self.assertEqual([value], list(credential_prose_literals(line)))
                    self.assertIn("credential-prose-literal", inspect_line(line))

    def test_even_backslash_run_closes_ascii_quotes(self) -> None:
        for name, quote in (
            ("backtick", "`"),
            ("ascii-double", '"'),
            ("ascii-single", "'"),
        ):
            for backslash_count in (2, 4):
                with self.subTest(name=name, backslashes=backslash_count):
                    first_value = "short-" + ("\\" * backslash_count)
                    second_value = "synthetic-even-run-secret-1234567890"
                    line = (
                        "The cleanup "
                        + "token "
                        + quote
                        + first_value
                        + quote
                        + " and "
                        + quote
                        + second_value
                        + quote
                        + " must rotate."
                    )
                    self.assertEqual(
                        [first_value, second_value],
                        list(credential_prose_literals(line)),
                    )
                    self.assertIn("credential-prose-literal", inspect_line(line))

    def test_rejects_mixed_case_punctuated_credential_prose(self) -> None:
        line = (
            "During review, the ClEaNuP-"
            + "ToKeN, after owner approval, was `"
            + "synthetic-punctuated-secret-1234567890"
            + "` and must rotate."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_clause_scanner_ignores_punctuation_inside_paired_literal(self) -> None:
        line = (
            "The cleanup "
            + "token `"
            + "synthetic.secret.value.1234567890"
            + "` must rotate."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_independent_nested_span_cross_product(self) -> None:
        literal = "synthetic-nested-secret-1234567890"
        for outer_name, outer_opening, outer_closing in self.QUOTE_PAIRS:
            for inner_name, inner_opening, inner_closing in self.QUOTE_PAIRS:
                with self.subTest(outer=outer_name, inner=inner_name):
                    line = (
                        "The cleanup token "
                        + outer_opening
                        + "outer prose "
                        + inner_opening
                        + literal
                        + inner_closing
                        + " outer prose"
                        + outer_closing
                        + " must rotate."
                    )
                    self.assertIn("credential-prose-literal", inspect_line(line))
                    self.assertEqual(
                        [literal],
                        list(credential_prose_literals(line)),
                    )

    def test_keyword_and_literal_inside_typographic_outer_span(self) -> None:
        literal = "synthetic-nested-keyword-secret-1234567890"
        for outer_name, outer_opening, outer_closing in self.QUOTE_PAIRS[3:]:
            for inner_name, inner_opening, inner_closing in self.QUOTE_PAIRS:
                with self.subTest(outer=outer_name, inner=inner_name):
                    line = (
                        outer_opening
                        + "the cleanup token uses "
                        + inner_opening
                        + literal
                        + inner_closing
                        + " before rotation"
                        + outer_closing
                    )
                    self.assertEqual(
                        [literal],
                        list(credential_prose_literals(line)),
                    )

    def test_malformed_prefix_later_valid_span_cross_product(self) -> None:
        literal = "synthetic-later-valid-secret-1234567890"
        for malformed_name, malformed_opening, _ in self.QUOTE_PAIRS:
            for valid_name, valid_opening, valid_closing in self.QUOTE_PAIRS:
                with self.subTest(malformed=malformed_name, valid=valid_name):
                    line = (
                        "The cleanup token "
                        + malformed_opening
                        + "malformed prefix "
                        + valid_opening
                        + literal
                        + valid_closing
                        + " must rotate."
                    )
                    self.assertIn("credential-prose-literal", inspect_line(line))
                    self.assertEqual(
                        [literal],
                        list(credential_prose_literals(line)),
                    )

    def test_quote_terminal_sentence_boundary_matrix(self) -> None:
        audit_literal = "synthetic-unrelated-audit-label-1234567890"
        suffixes = (
            "",
            "   ",
            " The unrelated audit label `" + audit_literal + "` remains.",
        )
        for name, opening, closing in self.QUOTE_PAIRS:
            for suffix in suffixes:
                with self.subTest(name=name, suffix=bool(suffix)):
                    line = (
                        "The cleanup token was "
                        + opening
                        + "removed."
                        + closing
                        + suffix
                    )
                    self.assertEqual([], inspect_line(line))

    def test_quote_final_punctuation_continuation_matrix(self) -> None:
        audit_literal = "synthetic-same-sentence-audit-label-1234567890"
        continuations = (
            " and later the audit label `" + audit_literal + "` remains.",
            ", and later the audit label `" + audit_literal + "` remains.",
            ") and later the audit label `" + audit_literal + "` remains.",
            " (and later the audit label `" + audit_literal + "` remains.)",
            " with the later audit label `" + audit_literal + "` remaining.",
        )
        for name, opening, closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for continuation in continuations:
                    with self.subTest(
                        name=name,
                        punctuation=punctuation,
                        continuation=continuation[:2],
                    ):
                        line = (
                            "The cleanup token was "
                            + opening
                            + "removed"
                            + punctuation
                            + closing
                            + continuation
                        )
                        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_quote_final_punctuation_true_boundary_matrix(self) -> None:
        audit_literal = "synthetic-next-sentence-audit-label-1234567890"
        next_contexts = (
            " The unrelated audit label `" + audit_literal + "` remains.",
            ") The unrelated audit label `" + audit_literal + "` remains.",
            " (The unrelated audit label `" + audit_literal + "` remains.)",
            "\nThe unrelated audit label `" + audit_literal + "` remains.",
            "; The unrelated audit label `" + audit_literal + "` remains.",
        )
        for name, opening, closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_context in next_contexts:
                    with self.subTest(
                        name=name,
                        punctuation=punctuation,
                        context=repr(next_context[:2]),
                    ):
                        line = (
                            "The cleanup token was "
                            + opening
                            + "removed"
                            + punctuation
                            + closing
                            + next_context
                        )
                        self.assertEqual([], inspect_line(line))

    def test_quoted_next_sentence_opener_boundary_cross_product(self) -> None:
        next_sentence_values = (
            "Synthetic-next-sentence-label-1234567890",
            "7-synthetic-next-sentence-label-1234567890",
        )
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for spacing in ("", " ", "\t"):
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.SIMPLE_WRAPPERS:
                            for value in next_sentence_values:
                                with self.subTest(
                                    first=first_name,
                                    next=next_name,
                                    punctuation=punctuation,
                                    spacing=repr(spacing),
                                    wrapper=wrapper_name,
                                    value=value[0],
                                ):
                                    prefix = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                    )
                                    line = (
                                        prefix
                                        + spacing
                                        + wrapper_opening
                                        + next_opening
                                        + value
                                        + next_closing
                                        + wrapper_closing
                                        + " remains."
                                    )
                                    self.assert_boundary_after(line, prefix)

    def test_quoted_next_context_continuation_cross_product(self) -> None:
        continuation_values = (
            "synthetic-lowercase-continuation-1234567890",
            "and-synthetic-conjunction-continuation-1234567890",
            ",synthetic-comma-continuation-1234567890",
        )
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                for spacing in ("", " ", "\t"):
                    for value in continuation_values:
                        with self.subTest(
                            first=first_name,
                            next=next_name,
                            spacing=repr(spacing),
                            value=value[0],
                        ):
                            line = (
                                "The cleanup token "
                                + first_opening
                                + "[REDACTED]."
                                + first_closing
                                + spacing
                                + next_opening
                                + value
                                + next_closing
                                + " remains"
                            )
                            self.assertEqual(
                                [(0, len(line))],
                                [
                                    (clause.start, clause.end)
                                    for clause in scan_clauses(line)
                                ],
                            )
                            self.assertIn(
                                "credential-prose-literal",
                                inspect_line(line),
                            )

    def test_quoted_uppercase_next_literal_continuation_matrix(self) -> None:
        next_value = "Synthetic-uppercase-audit-label-1234567890"
        unsafe_value = "synthetic-later-unsafe-value-1234567890"
        tails = (
            ("comma", ", which later records "),
            ("colon", ": explanation later records "),
            ("lowercase", " which later records "),
            ("conjunction", " and later records "),
        )
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for spacing in ("", " ", "\t"):
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.SIMPLE_WRAPPERS:
                            for tail_name, tail in tails:
                                with self.subTest(
                                    first=first_name,
                                    punctuation=punctuation,
                                    next=next_name,
                                    spacing=repr(spacing),
                                    wrapper=wrapper_name,
                                    tail=tail_name,
                                ):
                                    line = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                        + spacing
                                        + wrapper_opening
                                        + next_opening
                                        + next_value
                                        + next_closing
                                        + wrapper_closing
                                        + tail
                                        + "`"
                                        + unsafe_value
                                        + "` remains"
                                    )
                                    self.assert_single_clause_violation(
                                        line,
                                        unsafe_value,
                                    )

    def test_continuation_catalog_is_canonical_and_family_complete(
        self,
    ) -> None:
        flattened = tuple(
            linker
            for category in CONTINUATION_LINKER_CATALOG
            for linker in category.linkers
        )
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertTrue(
            all(category.source for category in CONTINUATION_LINKER_CATALOG)
        )
        self.assertTrue(
            all(
                linker == linker.casefold()
                and linker
                and "\n" not in linker
                and "\r" not in linker
                for linker in flattened
            )
        )
        self.assertEqual(
            frozenset(linker for linker in flattened if " " not in linker),
            CONTINUATION_WORDS,
        )
        self.assertEqual(
            tuple(linker for linker in flattened if " " in linker),
            MULTIWORD_CONTINUATION_STARTERS,
        )
        by_category = {
            category.name: frozenset(category.linkers)
            for category in CONTINUATION_LINKER_CATALOG
        }
        self.assertEqual(
            frozenset(
                {
                    "even after",
                    "even as",
                    "even before",
                    "even if",
                    "even though",
                    "even when",
                    "even while",
                }
            ),
            by_category["even-family"],
        )
        self.assertEqual(
            frozenset(
                {
                    "just after",
                    "just as",
                    "just because",
                    "just before",
                    "just when",
                    "just while",
                }
            ),
            by_category["just-family"],
        )
        self.assertEqual(
            frozenset(
                {
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
                }
            ),
            by_category["no-matter-interrogative"],
        )
        self.assertTrue(
            {
                "by the time",
                "each time",
                "every time",
                "if only",
                "in case",
                "in order that",
                "in the event that",
                "now that",
                "on condition that",
                "on the condition that",
                "the instant",
                "the minute",
                "the moment",
                "the second",
            }.issubset(by_category["condition-time"])
        )
        self.assertTrue(
            {"albeit", "whilst"}.issubset(
                by_category["subordinating-single"]
            )
        )
        self.assertTrue(
            {"seeing that", "such that"}.issubset(
                by_category["cause-result"]
            )
        )
        for category, expected in self.REVIEWED_LINKER_FAMILIES.items():
            with self.subTest(category=category):
                self.assertEqual(expected, by_category[category])
        self.assertTrue(
            set(self.REVIEWED_STANDARD_ADDITIONS).issubset(flattened)
        )

    def test_standard_continuation_word_matrix(self) -> None:
        next_value = "Synthetic-standard-link-label-1234567890"
        unsafe_value = "synthetic-standard-link-unsafe-1234567890"
        variants = (
            lambda word: " " + word + " later records ",
            lambda word: "\t" + word.upper() + ", later records ",
            lambda word: "  " + word.title() + ": later records ",
        )
        cases = 0
        for word_index, word in enumerate(sorted(CONTINUATION_WORDS)):
            for first_index, (
                first_name,
                first_opening,
                first_closing,
            ) in enumerate(self.QUOTE_PAIRS):
                for punctuation_index, punctuation in enumerate(".?!"):
                    for next_index, (
                        next_name,
                        next_opening,
                        next_closing,
                    ) in enumerate(self.QUOTE_PAIRS):
                        for wrapper_index, (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in enumerate(self.NEXT_CONTEXT_WRAPPERS):
                            variant_index = (
                                word_index
                                + first_index
                                + punctuation_index
                                + next_index
                                + wrapper_index
                            ) % len(variants)
                            tail = variants[variant_index](word)
                            with self.subTest(
                                word=word,
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                wrapper=wrapper_name,
                                variant=variant_index,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + wrapper_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + wrapper_closing
                                    + tail
                                    + "`"
                                    + unsafe_value
                                    + "` remains"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )
                                cases += 1
        self.assertEqual(
            len(CONTINUATION_WORDS)
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS),
            cases,
        )

    def test_standard_multiword_continuation_starters(self) -> None:
        next_value = "Synthetic-multiword-link-label-1234567890"
        unsafe_value = "synthetic-multiword-link-unsafe-1234567890"
        variants = (
            lambda phrase: phrase,
            lambda phrase: phrase.upper().replace(" ", "\t"),
            lambda phrase: phrase.title().replace(" ", "  "),
        )
        for phrase_index, phrase in enumerate(
            MULTIWORD_CONTINUATION_STARTERS
        ):
            for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                for (
                    wrapper_name,
                    wrapper_opening,
                    wrapper_closing,
                ) in self.NEXT_CONTEXT_WRAPPERS:
                    variant_index = phrase_index % len(variants)
                    rendered_phrase = variants[variant_index](phrase)
                    with self.subTest(
                        phrase=phrase,
                        next=next_name,
                        wrapper=wrapper_name,
                        variant=variant_index,
                    ):
                        line = (
                            'The cleanup token "[REDACTED]." '
                            + wrapper_opening
                            + next_opening
                            + next_value
                            + next_closing
                            + wrapper_closing
                            + " "
                            + rendered_phrase
                            + " later records `"
                            + unsafe_value
                            + "`"
                        )
                        self.assert_single_clause_violation(
                            line,
                            unsafe_value,
                        )

    def test_reproduced_bounded_linker_full_matrix(self) -> None:
        next_value = "Synthetic-reproduced-linker-label-1234567890"
        unsafe_value = "synthetic-reproduced-linker-unsafe-1234567890"
        spacings = ("", " ", "\t")
        cases = 0
        for linker_index, linker in enumerate(
            self.REPRODUCED_BOUNDED_LINKERS
        ):
            for first_index, (
                first_name,
                first_opening,
                first_closing,
            ) in enumerate(self.QUOTE_PAIRS):
                for punctuation_index, punctuation in enumerate(".?!"):
                    for next_index, (
                        next_name,
                        next_opening,
                        next_closing,
                    ) in enumerate(self.QUOTE_PAIRS):
                        for wrapper_index, (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in enumerate(self.NEXT_CONTEXT_WRAPPERS):
                            for spacing_index, spacing in enumerate(spacings):
                                variant = (
                                    linker_index
                                    + first_index
                                    + punctuation_index
                                    + next_index
                                    + wrapper_index
                                    + spacing_index
                                ) % 3
                                rendered_linker = (
                                    linker
                                    if variant == 0
                                    else (
                                        linker.upper().replace(" ", "\t")
                                        if variant == 1
                                        else linker.title().replace(" ", "  ")
                                    )
                                )
                                tail_spacing = spacings[
                                    (spacing_index + 1) % len(spacings)
                                ]
                                with self.subTest(
                                    linker=linker,
                                    first=first_name,
                                    punctuation=punctuation,
                                    next=next_name,
                                    wrapper=wrapper_name,
                                    spacing=repr(spacing),
                                    variant=variant,
                                ):
                                    line = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                        + spacing
                                        + wrapper_opening
                                        + next_opening
                                        + next_value
                                        + next_closing
                                        + wrapper_closing
                                        + tail_spacing
                                        + rendered_linker
                                        + " later records `"
                                        + unsafe_value
                                        + "`"
                                    )
                                    self.assert_single_clause_violation(
                                        line,
                                        unsafe_value,
                                    )
                                    cases += 1
        self.assertEqual(
            len(self.REPRODUCED_BOUNDED_LINKERS)
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS)
            * len(spacings),
            cases,
        )

    def test_completed_linker_families_full_boundary_matrix(self) -> None:
        next_value = "Synthetic-completed-linker-subject-1234567890"
        unsafe_value = "synthetic-completed-linker-unsafe-1234567890"
        spacings = ("", " ", "\t")
        casing_variants = (
            lambda linker: linker,
            lambda linker: linker.upper().replace(" ", "\t"),
            lambda linker: linker.title().replace(" ", "  "),
        )
        cases = 0
        for linker in self.LATEST_REPRODUCED_LINKERS:
            self.assertIn(linker, MULTIWORD_CONTINUATION_STARTERS)
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.NEXT_CONTEXT_WRAPPERS:
                            for spacing in spacings:
                                for casing_index, render in enumerate(
                                    casing_variants
                                ):
                                    rendered_linker = render(linker)
                                    with self.subTest(
                                        linker=linker,
                                        first=first_name,
                                        punctuation=punctuation,
                                        next=next_name,
                                        wrapper=wrapper_name,
                                        spacing=repr(spacing),
                                        casing=casing_index,
                                    ):
                                        line = (
                                            "The cleanup token "
                                            + first_opening
                                            + "[REDACTED]"
                                            + punctuation
                                            + first_closing
                                            + spacing
                                            + wrapper_opening
                                            + next_opening
                                            + next_value
                                            + next_closing
                                            + wrapper_closing
                                            + spacing
                                            + rendered_linker
                                            + " later records `"
                                            + unsafe_value
                                            + "`"
                                        )
                                        self.assert_single_clause_violation(
                                            line,
                                            unsafe_value,
                                        )
                                        cases += 1
        self.assertEqual(
            len(self.LATEST_REPRODUCED_LINKERS)
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS)
            * len(spacings)
            * len(casing_variants),
            cases,
        )

    def test_reviewed_standard_additions_reproduce_full_matrix(self) -> None:
        next_value = "Synthetic-standard-addition-subject-1234567890"
        unsafe_value = "synthetic-standard-addition-unsafe-1234567890"
        spacings = ("", " ", "\t")
        catalog = {
            linker
            for category in CONTINUATION_LINKER_CATALOG
            for linker in category.linkers
        }
        cases = 0
        for linker in self.REVIEWED_STANDARD_ADDITIONS:
            self.assertIn(linker, catalog)
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.NEXT_CONTEXT_WRAPPERS:
                            for spacing in spacings:
                                with self.subTest(
                                    linker=linker,
                                    first=first_name,
                                    punctuation=punctuation,
                                    next=next_name,
                                    wrapper=wrapper_name,
                                    spacing=repr(spacing),
                                ):
                                    line = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                        + spacing
                                        + wrapper_opening
                                        + next_opening
                                        + next_value
                                        + next_closing
                                        + wrapper_closing
                                        + spacing
                                        + linker
                                        + " later records `"
                                        + unsafe_value
                                        + "`"
                                    )
                                    self.assert_single_clause_violation(
                                        line,
                                        unsafe_value,
                                    )
                                    cases += 1
        self.assertEqual(18_000, cases)

    def test_reviewed_linker_families_reproduce_full_matrix(self) -> None:
        next_value = "Synthetic-reviewed-family-subject-1234567890"
        unsafe_value = "synthetic-reviewed-family-unsafe-1234567890"
        spacings = ("", " ", "\t")
        categories = {
            category.name: category.linkers
            for category in CONTINUATION_LINKER_CATALOG
        }
        rendered_variants = (
            lambda linker: linker,
            lambda linker: linker.upper().replace(" ", "\t"),
            lambda linker: linker.title().replace(" ", "  "),
        )
        cases = 0
        for category_name in self.REVIEWED_LINKER_FAMILIES:
            for linker_index, linker in enumerate(categories[category_name]):
                for first_index, (
                    first_name,
                    first_opening,
                    first_closing,
                ) in enumerate(self.QUOTE_PAIRS):
                    for punctuation_index, punctuation in enumerate(".?!"):
                        for next_index, (
                            next_name,
                            next_opening,
                            next_closing,
                        ) in enumerate(self.QUOTE_PAIRS):
                            for wrapper_index, (
                                wrapper_name,
                                wrapper_opening,
                                wrapper_closing,
                            ) in enumerate(self.NEXT_CONTEXT_WRAPPERS):
                                for spacing_index, spacing in enumerate(spacings):
                                    variant = (
                                        linker_index
                                        + first_index
                                        + punctuation_index
                                        + next_index
                                        + wrapper_index
                                        + spacing_index
                                    ) % len(rendered_variants)
                                    rendered_linker = rendered_variants[variant](
                                        linker
                                    )
                                    with self.subTest(
                                        category=category_name,
                                        linker=linker,
                                        first=first_name,
                                        punctuation=punctuation,
                                        next=next_name,
                                        wrapper=wrapper_name,
                                        spacing=repr(spacing),
                                        variant=variant,
                                    ):
                                        line = (
                                            "The cleanup token "
                                            + first_opening
                                            + "[REDACTED]"
                                            + punctuation
                                            + first_closing
                                            + spacing
                                            + wrapper_opening
                                            + next_opening
                                            + next_value
                                            + next_closing
                                            + wrapper_closing
                                            + spacing
                                            + rendered_linker
                                            + " later records `"
                                            + unsafe_value
                                            + "`"
                                        )
                                        self.assert_single_clause_violation(
                                            line,
                                            unsafe_value,
                                        )
                                        cases += 1
        self.assertEqual(
            sum(
                len(categories[name])
                for name in self.REVIEWED_LINKER_FAMILIES
            )
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS)
            * len(spacings),
            cases,
        )

    def test_reproduced_linker_partial_forms_stay_standalone(self) -> None:
        malformed_by_linker = {
            "even when": ("even", "evenwhen", "even whenever", "xeven when"),
            "just as": ("just", "justas", "just asx", "xjust as"),
            "in order that": (
                "in",
                "in order",
                "inorderthat",
                "in order thatx",
                "xin order that",
            ),
            "on condition that": (
                "on",
                "on condition",
                "onconditionthat",
                "on condition thatx",
                "xon condition that",
            ),
            "whereby": ("xwhereby", "wherebyx", "whereby-extra"),
            "wherein": ("xwherein", "whereinx", "wherein-extra"),
        }
        next_value = "Synthetic-partial-linker-subject-1234567890"
        unsafe_value = "synthetic-partial-linker-label-1234567890"
        for linker, malformed_forms in malformed_by_linker.items():
            for malformed in malformed_forms:
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        wrapper_name,
                        wrapper_opening,
                        wrapper_closing,
                    ) in self.NEXT_CONTEXT_WRAPPERS:
                        with self.subTest(
                            linker=linker,
                            malformed=malformed,
                            next=next_name,
                            wrapper=wrapper_name,
                        ):
                            prefix = 'The cleanup token "[REDACTED]."'
                            line = (
                                prefix
                                + " "
                                + wrapper_opening
                                + next_opening
                                + next_value
                                + next_closing
                                + wrapper_closing
                                + " "
                                + malformed.title()
                                + " production applies. `"
                                + unsafe_value
                                + "`"
                            )
                            self.assert_boundary_after(line, prefix)

    def test_multiword_linkers_do_not_cross_newlines(self) -> None:
        next_value = "Synthetic-newline-linker-subject-1234567890"
        for linker in (
            "even when",
            "just as",
            "in order that",
            "on condition that",
        ):
            malformed = linker.replace(" ", "\n", 1)
            prefix = 'The cleanup token "[REDACTED]."'
            line = (
                prefix
                + ' "'
                + next_value
                + '" '
                + malformed
                + " production applies."
            )
            with self.subTest(linker=linker):
                self.assert_boundary_after(line, prefix)

    def test_multiword_first_words_require_the_complete_phrase(self) -> None:
        first_words = sorted(
            {
                phrase.partition(" ")[0]
                for phrase in MULTIWORD_CONTINUATION_STARTERS
            }
            - CONTINUATION_WORDS
        )
        self.assertTrue(first_words)
        next_value = "Synthetic-bare-first-word-subject-1234567890"
        for first_word in first_words:
            for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                for (
                    wrapper_name,
                    wrapper_opening,
                    wrapper_closing,
                ) in self.NEXT_CONTEXT_WRAPPERS:
                    with self.subTest(
                        first_word=first_word,
                        next=next_name,
                        wrapper=wrapper_name,
                    ):
                        prefix = 'The cleanup token "[REDACTED]."'
                        line = (
                            prefix
                            + " "
                            + wrapper_opening
                            + next_opening
                            + next_value
                            + next_closing
                            + wrapper_closing
                            + " "
                            + first_word.title()
                            + " production applies."
                        )
                        self.assert_boundary_after(line, prefix)

    def test_catalog_partial_prefixes_follow_exact_membership(self) -> None:
        all_linkers = CONTINUATION_WORDS | frozenset(
            MULTIWORD_CONTINUATION_STARTERS
        )
        next_value = "Synthetic-catalog-prefix-subject-1234567890"
        unsafe_value = "synthetic-catalog-prefix-unsafe-1234567890"
        for phrase in MULTIWORD_CONTINUATION_STARTERS:
            parts = phrase.split()
            for prefix_length in range(1, len(parts)):
                partial = " ".join(parts[:prefix_length])
                first_end, first_word = read_context_word(partial, 0)
                expected_continuation = (
                    partial in all_linkers
                    or normalize_ascii_linker_word(first_word)
                    in CONTINUATION_WORDS
                )
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        wrapper_name,
                        wrapper_opening,
                        wrapper_closing,
                    ) in self.NEXT_CONTEXT_WRAPPERS:
                        with self.subTest(
                            phrase=phrase,
                            partial=partial,
                            expected=expected_continuation,
                            next=next_name,
                            wrapper=wrapper_name,
                        ):
                            prefix = 'The cleanup token "[REDACTED]."'
                            line = (
                                prefix
                                + " "
                                + wrapper_opening
                                + next_opening
                                + next_value
                                + next_closing
                                + wrapper_closing
                                + " "
                                + partial.title()
                                + " production records `"
                                + unsafe_value
                                + "`"
                            )
                            self.assert_context_outcome(
                                partial,
                                line,
                                prefix,
                                unsafe_value,
                                expected_continuation=expected_continuation,
                            )

    def test_catalog_glued_prefixed_and_suffixed_forms_are_standalone(
        self,
    ) -> None:
        next_value = "Synthetic-catalog-malformed-subject-1234567890"
        unsafe_value = "synthetic-catalog-malformed-unsafe-1234567890"
        all_linkers = tuple(sorted(CONTINUATION_WORDS)) + (
            MULTIWORD_CONTINUATION_STARTERS
        )
        for linker in all_linkers:
            malformed_forms = {
                "x" + linker,
                linker + "x",
                linker + "-extra",
            }
            if " " in linker:
                malformed_forms.add(linker.replace(" ", ""))
            for malformed in sorted(malformed_forms):
                for (
                    wrapper_name,
                    wrapper_opening,
                    wrapper_closing,
                ) in self.NEXT_CONTEXT_WRAPPERS:
                    with self.subTest(
                        linker=linker,
                        malformed=malformed,
                        wrapper=wrapper_name,
                    ):
                        prefix = 'The cleanup token "[REDACTED]."'
                        line = (
                            prefix
                            + " "
                            + wrapper_opening
                            + '"'
                            + next_value
                            + '"'
                            + wrapper_closing
                            + " "
                            + malformed
                            + " production records `"
                            + unsafe_value
                            + "`"
                        )
                        self.assert_context_outcome(
                            malformed,
                            line,
                            prefix,
                            unsafe_value,
                        )

    def test_unicode_identifier_continuation_categories(self) -> None:
        self.assertTrue({"\u200c", "\u200d"}.issubset(
            IDENTIFIER_JOIN_CONTROLS
        ))
        for name, character, expected_category in (
            self.UNICODE_IDENTIFIER_BOUNDARIES
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    expected_category,
                    unicodedata.category(character),
                )
                self.assertTrue(is_identifier_continuation(character))
        for character in ("A", "\u03b1", "7", "\u0661", "-"):
            with self.subTest(character=character):
                self.assertTrue(is_identifier_continuation(character))
        for character in (" ", "\t", ".", ",", "\n"):
            with self.subTest(character=repr(character)):
                self.assertFalse(is_identifier_continuation(character))
        self.assertEqual("and", normalize_ascii_linker_word("AnD"))
        for word in ("\u017fo", "\u017fince", "a\u0301nd", "and\u200d"):
            with self.subTest(word=word):
                self.assertIsNone(normalize_ascii_linker_word(word))

    def test_unicode_casefold_lookalikes_do_not_become_linkers(self) -> None:
        next_value = "Synthetic-unicode-lookalike-subject-1234567890"
        unsafe_value = "synthetic-unicode-lookalike-unsafe-1234567890"
        all_linkers = tuple(sorted(CONTINUATION_WORDS)) + (
            MULTIWORD_CONTINUATION_STARTERS
        )
        substitutions = (
            ("s", "\u017f"),
            ("ss", "\u00df"),
            ("st", "\ufb06"),
        )
        cases: set[tuple[str, str]] = set()
        for linker in all_linkers:
            for source, replacement in substitutions:
                start = 0
                while True:
                    index = linker.find(source, start)
                    if index < 0:
                        break
                    malformed = (
                        linker[:index]
                        + replacement
                        + linker[index + len(source) :]
                    )
                    if malformed.casefold() == linker:
                        cases.add((linker, malformed))
                    start = index + 1
        self.assertTrue(cases)
        for linker, malformed in sorted(cases):
            with self.subTest(linker=linker, malformed=malformed):
                prefix = 'The cleanup token "[REDACTED]."'
                line = (
                    prefix
                    + ' "'
                    + next_value
                    + '" '
                    + malformed
                    + " production records `"
                    + unsafe_value
                    + "`"
                )
                self.assert_context_outcome(
                    malformed,
                    line,
                    prefix,
                    unsafe_value,
                )

    def test_single_linker_survives_unrelated_multiword_prefixes(
        self,
    ) -> None:
        next_value = "Synthetic-single-fallback-subject-1234567890"
        unsafe_value = "synthetic-single-fallback-unsafe-1234567890"
        cases: set[tuple[str, str]] = {("as", "so")}
        for phrase in MULTIWORD_CONTINUATION_STARTERS:
            parts = phrase.split()
            if parts[0] in CONTINUATION_WORDS:
                cases.add((parts[0], parts[1][0].upper()))
        self.assertTrue(cases)
        for linker, ordinary_word in sorted(cases):
            context = linker + " " + ordinary_word + " reviewer records"
            with self.subTest(linker=linker, ordinary=ordinary_word):
                _index, category, context_value = (
                    classify_post_quote_context(context, 0)
                )
                self.assertEqual("continuation-word", category)
                self.assertEqual(linker, context_value)
                line = (
                    'The cleanup token "[REDACTED]." "'
                    + next_value
                    + '" '
                    + context
                    + " `"
                    + unsafe_value
                    + "`"
                )
                self.assert_single_clause_violation(line, unsafe_value)

    def test_exact_single_fallback_ignores_unrelated_unicode_words(
        self,
    ) -> None:
        next_value = "Synthetic-unrelated-word-subject-1234567890"
        unsafe_value = "synthetic-unrelated-word-unsafe-1234567890"
        cases = 0
        for single, candidate in (
            self.EXACT_SINGLE_FALLBACK_CANDIDATES.items()
        ):
            self.assertIn(single, CONTINUATION_WORDS)
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.SIMPLE_WRAPPERS:
                            for glue_name, glue, _category in (
                                self.UNRELATED_UNICODE_GLUE
                            ):
                                unrelated_words = (
                                    candidate + glue + "tail",
                                    "head" + glue + candidate + glue + "tail",
                                    "head" + glue + candidate,
                                )
                                for position, unrelated in zip(
                                    ("start", "middle", "end"),
                                    unrelated_words,
                                ):
                                    with self.subTest(
                                        single=single,
                                        candidate=candidate,
                                        first=first_name,
                                        punctuation=punctuation,
                                        next=next_name,
                                        wrapper=wrapper_name,
                                        glue=glue_name,
                                        position=position,
                                    ):
                                        context = (
                                            single
                                            + " "
                                            + unrelated
                                            + " later records"
                                        )
                                        _index, category, context_value = (
                                            classify_post_quote_context(
                                                context,
                                                0,
                                            )
                                        )
                                        self.assertEqual(
                                            "continuation-word",
                                            category,
                                        )
                                        self.assertEqual(single, context_value)
                                        line = (
                                            "The cleanup token "
                                            + first_opening
                                            + "[REDACTED]"
                                            + punctuation
                                            + first_closing
                                            + " "
                                            + wrapper_opening
                                            + next_opening
                                            + next_value
                                            + next_closing
                                            + wrapper_closing
                                            + " "
                                            + context
                                            + " `"
                                            + unsafe_value
                                            + "`"
                                        )
                                        self.assert_single_clause_violation(
                                            line,
                                            unsafe_value,
                                        )
                                        cases += 1
        self.assertEqual(59_400, cases)

    def test_malformed_candidate_projection_requires_exact_equivalence(
        self,
    ) -> None:
        for candidate in set(
            self.EXACT_SINGLE_FALLBACK_CANDIDATES.values()
        ):
            for _name, glue, _category in self.UNRELATED_UNICODE_GLUE:
                genuine_glued = (
                    glue + candidate,
                    candidate + glue,
                    candidate[:1] + glue + candidate[1:],
                )
                unrelated = (
                    candidate + glue + "tail",
                    "head" + glue + candidate + glue + "tail",
                    "head" + glue + candidate,
                )
                for actual in genuine_glued:
                    with self.subTest(
                        candidate=candidate,
                        actual=actual,
                        expected="glued",
                    ):
                        self.assertTrue(
                            is_malformed_candidate_token(
                                actual,
                                candidate,
                            )
                        )
                for actual in unrelated:
                    with self.subTest(
                        candidate=candidate,
                        actual=actual,
                        expected="unrelated",
                    ):
                        self.assertFalse(
                            is_malformed_candidate_token(
                                actual,
                                candidate,
                            )
                        )

    def test_ascii_prefixes_on_multiword_final_tokens_use_single_fallback(
        self,
    ) -> None:
        next_value = "Synthetic-ascii-prefix-subject-1234567890"
        unsafe_value = "synthetic-ascii-prefix-unsafe-1234567890"
        for phrase in MULTIWORD_CONTINUATION_STARTERS:
            parts = phrase.split()
            malformed = " ".join(parts[:-1] + ["x" + parts[-1]])
            with self.subTest(phrase=phrase, malformed=malformed):
                prefix = 'The cleanup token "[REDACTED]."'
                line = (
                    prefix
                    + ' "'
                    + next_value
                    + '" '
                    + malformed
                    + " production records `"
                    + unsafe_value
                    + "`"
                )
                self.assertIsNone(
                    match_multiword_continuation(
                        malformed,
                        malformed.index(" "),
                        malformed.partition(" ")[0],
                    )
                )
                self.assert_context_outcome(
                    malformed,
                    line,
                    prefix,
                    unsafe_value,
                )

    def test_composed_unicode_and_ascii_glue_is_standalone(self) -> None:
        next_value = "Synthetic-composed-glue-subject-1234567890"
        unsafe_value = "synthetic-composed-glue-unsafe-1234567890"
        for phrase in MULTIWORD_CONTINUATION_STARTERS:
            parts = phrase.split()
            final_word = parts[-1]
            malformed_words = {
                "x\u200d" + final_word,
                final_word + "\u200dx",
                "x\u0301" + final_word,
                final_word + "\u203fx",
                "".join(
                    chr(ord(character) + 0xFEE0)
                    for character in final_word
                ),
            }
            if "s" in final_word:
                malformed_words.add(
                    final_word.replace("s", "\u017f", 1) + "x"
                )
            for malformed_word in sorted(malformed_words):
                malformed = " ".join(parts[:-1] + [malformed_word])
                with self.subTest(phrase=phrase, malformed=malformed):
                    prefix = 'The cleanup token "[REDACTED]."'
                    line = (
                        prefix
                        + ' "'
                        + next_value
                        + '" '
                        + malformed
                        + " production records `"
                        + unsafe_value
                        + "`"
                    )
                    self.assertIsNone(
                        match_multiword_continuation(
                            malformed,
                            malformed.index(" "),
                            malformed.partition(" ")[0],
                        )
                    )
                    self.assert_context_outcome(
                        malformed,
                        line,
                        prefix,
                        unsafe_value,
                    )

    def test_unicode_identifier_boundaries_reject_every_linker(
        self,
    ) -> None:
        all_linkers = tuple(sorted(CONTINUATION_WORDS)) + (
            MULTIWORD_CONTINUATION_STARTERS
        )
        next_value = "Synthetic-unicode-boundary-subject-1234567890"
        unsafe_value = "synthetic-unicode-boundary-unsafe-1234567890"
        for linker in all_linkers:
            first_word = linker.partition(" ")[0]
            final_word = linker.rpartition(" ")[2]
            for boundary_name, boundary, _category in (
                self.UNICODE_IDENTIFIER_BOUNDARIES
            ):
                for position in ("initial-prefix", "final-prefix", "suffix"):
                    if position == "initial-prefix":
                        malformed = boundary + linker
                        isolated_word = boundary + first_word
                    elif position == "final-prefix":
                        phrase_prefix, separator, _word = linker.rpartition(" ")
                        malformed = (
                            phrase_prefix
                            + separator
                            + boundary
                            + final_word
                        )
                        isolated_word = boundary + final_word
                    else:
                        malformed = linker + boundary
                        isolated_word = final_word + boundary
                    with self.subTest(
                        linker=linker,
                        boundary=boundary_name,
                        position=position,
                    ):
                        word_end, context_word = read_context_word(
                            isolated_word,
                            0,
                        )
                        self.assertEqual(len(isolated_word), word_end)
                        self.assertEqual(isolated_word, context_word)

                        if " " in linker:
                            malformed_first_end = malformed.find(" ")
                            malformed_first = malformed[:malformed_first_end]
                            self.assertIsNone(
                                match_multiword_continuation(
                                    malformed,
                                    malformed_first_end,
                                    malformed_first,
                                )
                            )

                        prefix = 'The cleanup token "[REDACTED]."'
                        line = (
                            prefix
                            + ' "'
                            + next_value
                            + '" '
                            + malformed
                            + " production records `"
                            + unsafe_value
                            + "`"
                        )
                        self.assert_context_outcome(
                            malformed,
                            line,
                            prefix,
                            unsafe_value,
                        )

    def test_unicode_glue_rejects_every_multiword_token(self) -> None:
        next_value = "Synthetic-every-token-glue-subject-1234567890"
        unsafe_value = "synthetic-every-token-glue-unsafe-1234567890"
        cases = 0
        for phrase in MULTIWORD_CONTINUATION_STARTERS:
            original_parts = phrase.split()
            for part_index, original_part in enumerate(original_parts):
                for boundary_name, boundary, _category in (
                    self.UNICODE_IDENTIFIER_BOUNDARIES
                ):
                    for position in ("prefix", "suffix"):
                        parts = list(original_parts)
                        parts[part_index] = (
                            boundary + original_part
                            if position == "prefix"
                            else original_part + boundary
                        )
                        malformed = " ".join(parts)
                        with self.subTest(
                            phrase=phrase,
                            part=part_index,
                            boundary=boundary_name,
                            position=position,
                        ):
                            prefix = 'The cleanup token "[REDACTED]."'
                            line = (
                                prefix
                                + ' "'
                                + next_value
                                + '" '
                                + malformed
                                + " production records `"
                                + unsafe_value
                                + "`"
                            )
                            self.assertIsNone(
                                match_multiword_continuation(
                                    malformed,
                                    malformed.index(" "),
                                    malformed.partition(" ")[0],
                                )
                            )
                            self.assert_context_outcome(
                                malformed,
                                line,
                                prefix,
                                unsafe_value,
                            )
                            cases += 1
        self.assertEqual(
            sum(
                len(phrase.split())
                for phrase in MULTIWORD_CONTINUATION_STARTERS
            )
            * len(self.UNICODE_IDENTIFIER_BOUNDARIES)
            * 2,
            cases,
        )

    def test_linker_token_boundaries_accept_only_valid_delimiters(
        self,
    ) -> None:
        all_linkers = tuple(sorted(CONTINUATION_WORDS)) + (
            MULTIWORD_CONTINUATION_STARTERS
        )
        for linker in all_linkers:
            for suffix in ("", " ", "\t", ",", ":", ".", "!", ";"):
                with self.subTest(linker=linker, suffix=repr(suffix)):
                    text = linker + suffix
                    _index, category, context_value = (
                        classify_post_quote_context(
                            text,
                            0,
                        )
                    )
                    self.assertEqual("continuation-word", category)
                    self.assertEqual(linker, context_value)
                    if " " in linker:
                        first_end = linker.index(" ")
                        self.assertEqual(
                            len(linker),
                            match_multiword_continuation(
                                text,
                                first_end,
                                linker[:first_end],
                            ),
                        )

    def test_multiword_catalog_never_matches_across_newline(self) -> None:
        for linker in MULTIWORD_CONTINUATION_STARTERS:
            first_end = linker.index(" ")
            for separator in ("\r", "\n", "\f", "\v"):
                malformed = (
                    linker[:first_end]
                    + separator
                    + linker[first_end + 1 :]
                )
                with self.subTest(linker=linker, separator=repr(separator)):
                    self.assertIsNone(
                        match_multiword_continuation(
                            malformed,
                            first_end,
                            malformed[:first_end],
                        )
                    )
                    line = (
                        'The cleanup token "[REDACTED]" '
                        + malformed
                        + " records `synthetic-line-split-1234567890`"
                    )
                    clauses = scan_clauses(line)
                    separator_index = line.index(separator)
                    self.assertEqual(separator_index, clauses[0].end)
                    self.assertEqual([], inspect_line(line))

    def test_generic_predicate_remains_a_standalone_control(self) -> None:
        next_value = "Synthetic-generic-subject-label-1234567890"
        for predicate in ("describes", "documents", "remains", "reports"):
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.NEXT_CONTEXT_WRAPPERS:
                            with self.subTest(
                                predicate=predicate,
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                wrapper=wrapper_name,
                            ):
                                prefix = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                )
                                line = (
                                    prefix
                                    + " "
                                    + wrapper_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + wrapper_closing
                                    + " "
                                    + predicate
                                    + "."
                                )
                                self.assert_boundary_after(line, prefix)

    def test_unicode_identifier_attached_next_quote_prefix_matrix(
        self,
    ) -> None:
        next_value = "Synthetic-attached-prefix-subject-1234567890"
        unsafe_value = "synthetic-attached-prefix-unsafe-1234567890"
        cases = 0
        for edge_name, edge, expected_category in (
            self.UNICODE_QUOTE_EDGE_CHARACTERS
        ):
            self.assertEqual(expected_category, unicodedata.category(edge))
            self.assertTrue(is_identifier_continuation(edge))
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.NEXT_CONTEXT_WRAPPERS:
                            with self.subTest(
                                edge=edge_name,
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                wrapper=wrapper_name,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + edge
                                    + wrapper_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + wrapper_closing
                                    + " remains and later `"
                                    + unsafe_value
                                    + "`"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )
                                cases += 1
        self.assertEqual(
            len(self.UNICODE_QUOTE_EDGE_CHARACTERS)
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS),
            cases,
        )

    def test_unicode_identifier_attached_next_quote_suffix_matrix(
        self,
    ) -> None:
        next_value = "Synthetic-attached-suffix-subject-1234567890"
        unsafe_value = "synthetic-attached-suffix-unsafe-1234567890"
        cases = 0
        for edge_name, edge, expected_category in (
            self.UNICODE_QUOTE_EDGE_CHARACTERS
        ):
            self.assertEqual(expected_category, unicodedata.category(edge))
            self.assertTrue(is_identifier_continuation(edge))
            for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
                for punctuation in ".?!":
                    for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                        for (
                            wrapper_name,
                            wrapper_opening,
                            wrapper_closing,
                        ) in self.NEXT_CONTEXT_WRAPPERS:
                            with self.subTest(
                                edge=edge_name,
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                wrapper=wrapper_name,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + wrapper_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + wrapper_closing
                                    + edge
                                    + " remains and later `"
                                    + unsafe_value
                                    + "`"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )
                                cases += 1
        self.assertEqual(
            len(self.UNICODE_QUOTE_EDGE_CHARACTERS)
            * len(self.QUOTE_PAIRS)
            * 3
            * len(self.QUOTE_PAIRS)
            * len(self.NEXT_CONTEXT_WRAPPERS),
            cases,
        )

    def test_next_quote_edges_remain_terminal_when_detached(self) -> None:
        next_value = "Synthetic-detached-edge-subject-1234567890"
        for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
            for (
                wrapper_name,
                wrapper_opening,
                wrapper_closing,
            ) in self.NEXT_CONTEXT_WRAPPERS:
                for prefix_spacing, suffix in (
                    (" ", " remains."),
                    ("\t", "\tremains."),
                    ("", "."),
                ):
                    with self.subTest(
                        next=next_name,
                        wrapper=wrapper_name,
                        prefix_spacing=repr(prefix_spacing),
                        suffix=repr(suffix),
                    ):
                        prefix = 'The cleanup token "[REDACTED]."'
                        line = (
                            prefix
                            + prefix_spacing
                            + wrapper_opening
                            + next_opening
                            + next_value
                            + next_closing
                            + wrapper_closing
                            + suffix
                        )
                        self.assert_boundary_after(line, prefix)

    def test_possessives_and_contractions_are_not_attached_quotes(
        self,
    ) -> None:
        audit_value = "synthetic-detached-reference-1234567890"
        for ordinary_context in (
            "Alice's report records ",
            "Alice's owners' report records ",
            "Alice\u0301's report records ",
            "The reviewer isn't recording ",
            "Owners' reports record ",
        ):
            for audit_name, audit_opening, audit_closing in (
                self.QUOTE_PAIRS
            ):
                with self.subTest(
                    context=ordinary_context,
                    audit=audit_name,
                ):
                    prefix = 'The cleanup token "[REDACTED]."'
                    line = (
                        prefix
                        + " "
                        + ordinary_context
                        + audit_opening
                        + audit_value
                        + audit_closing
                        + "."
                    )
                    self.assert_boundary_after(line, prefix)

        for ordinary_context in (
            "Alice's report. Owners' reports remain.",
            "The reviewer isn't done. It won't change.",
            "Alice\u0301's report. James' notes remain.",
        ):
            with self.subTest(context=ordinary_context):
                prefix = 'The cleanup token "[REDACTED]."'
                line = (
                    prefix
                    + " "
                    + ordinary_context
                    + " `synthetic-unscoped-audit-label-1234567890`"
                )
                self.assert_boundary_after(line, prefix)

        unsafe_value = "synthetic-unscoped-audit-label-1234567890"
        morphology = (
            "Alice's",
            "we'd",
            "we'll",
            "I'm",
            "we're",
            "we've",
            "isn't",
            "ma'am",
            "y'all",
            "o'clock",
            "'cause",
            "'em",
            "'tis",
            "'twas",
            "Owners'",
            "James'",
        )
        followers = (
            (".", ""),
            ("!", ""),
            ("?", ""),
            (";", ""),
            (",", " report."),
            (":", " report."),
            ("\u00a0", "ready."),
            ("\u2003", "ready."),
        )
        for apostrophe_form in morphology:
            for follower, tail in followers:
                ordinary_prefix = apostrophe_form + follower + tail
                with self.subTest(
                    form=apostrophe_form,
                    follower=repr(follower),
                ):
                    prefix = 'The cleanup token "[REDACTED]." '
                    line = (
                        prefix
                        + ordinary_prefix
                        + " A'Audit' and later `"
                        + unsafe_value
                        + "` remains."
                    )
                    expected_boundary = next(
                        prefix_index + 1
                        for prefix_index, character in enumerate(
                            prefix + ordinary_prefix
                        )
                        if (
                            prefix_index >= len(prefix)
                            and character in ".?!;"
                        )
                    )
                    self.assertIn(
                        expected_boundary,
                        [clause.end for clause in scan_clauses(line)],
                    )
                    self.assertEqual([], inspect_line(line))

        for left, right in (
            ("we're", "'cause"),
            ("Alice's", "Owners'"),
            ("ma'am", "y'all"),
            ("'tis", "'twas"),
            ("O'Brien", "D'Angelo"),
            ("O'Brien", "Owners'"),
        ):
            for punctuation in ".?!;":
                with self.subTest(
                    left=left,
                    right=right,
                    punctuation=punctuation,
                ):
                    prefix = 'The cleanup token "[REDACTED]."'
                    line = (
                        prefix
                        + " "
                        + left
                        + punctuation
                        + right
                        + " and later `"
                        + unsafe_value
                        + "` remains."
                    )
                    self.assertIn(
                        len(prefix) + 1 + len(left) + 1,
                        [clause.end for clause in scan_clauses(line)],
                    )
                    self.assertEqual([], inspect_line(line))

        for suffix in (
            "all",
            "am",
            "clock",
            "d",
            "ll",
            "m",
            "re",
            "s",
            "t",
            "ve",
        ):
            for ending in ("0", "s"):
                value = (
                    suffix
                    + ".synthetic-attached-morphology-value-123456789"
                    + ending
                )
                with self.subTest(
                    suffix=suffix,
                    ending=ending,
                    expected="evidence",
                ):
                    line = "The cleanup token x'" + value + "'A"
                    self.assertIn(
                        value,
                        list(credential_prose_literals(line)),
                    )
                    self.assertIn(
                        "credential-prose-literal",
                        inspect_line(line),
                    )

        punctuation_value = (
            ".synthetic-trailing-possessive-value-1234567890"
        )
        line = "The cleanup token owners'" + punctuation_value + "'"
        self.assertIn(
            punctuation_value,
            list(credential_prose_literals(line)),
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

        competing_value = (
            "s.synthetic-attached-morphology-value-1234567890="
        )
        line = (
            "The cleanup token x'"
            + competing_value
            + "'Other'"
        )
        self.assertIn(
            competing_value,
            list(credential_prose_literals(line)),
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

        overlapping_value = "actual.secret.1234567890"
        line = (
            "The cleanup token 'redacted-token-placeholder'"
            + overlapping_value
            + "'"
        )
        self.assertIn(
            overlapping_value,
            list(credential_prose_literals(line)),
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

        for value, attached in (
            ("." * 20, False),
            ("*" * 20, True),
            ("_" * 20, True),
        ):
            with self.subTest(symbol=value[0], attached=attached):
                quoted = "'" + value + "'"
                line = (
                    "The cleanup token "
                    + ("A" if attached else "")
                    + quoted
                )
                self.assertIn(
                    value,
                    list(credential_prose_literals(line)),
                )
                self.assertIn(
                    "credential-prose-literal",
                    inspect_line(line),
                )

        for leading in ("bout", "cause", "em", "round", "til", "tis", "twas"):
            value = (
                leading
                + ".synthetic-leading-contraction-value-1234567890"
            )
            for closer_suffix in ("", "A"):
                with self.subTest(
                    leading=leading,
                    closer_suffix=closer_suffix,
                    expected="evidence",
                ):
                    line = (
                        "The cleanup token '"
                        + value
                        + "'"
                        + closer_suffix
                    )
                    self.assertIn(
                        value,
                        list(credential_prose_literals(line)),
                    )
                    self.assertIn(
                        "credential-prose-literal",
                        inspect_line(line),
                    )

        nested_value = "abcdefghijkl`host-only.`Z1234567890"
        nested_line = (
            'The cleanup token "safe"'
            + nested_value
            + '"'
        )
        self.assertIn(
            nested_value,
            list(credential_prose_literals(nested_line)),
        )
        self.assertIn("credential-prose-literal", inspect_line(nested_line))

        for outer_opening, outer_closing in (
            ("\u201c", "\u201d"),
            ("\u2018", "\u2019"),
        ):
            outer_value = (
                "y`synthetic-nested-endpoint-secret-1234567890"
                + '"Safe."'
            )
            line = (
                "The cleanup token `x"
                + outer_opening
                + outer_value
                + outer_closing
            )
            with self.subTest(
                outer=outer_opening,
                boundary="content-end",
            ):
                self.assertIn(
                    outer_value,
                    list(credential_prose_literals(line)),
                )
                self.assertIn(
                    "credential-prose-literal",
                    inspect_line(line),
                )

        detached_s_value = "synthetic-secret-value-1234567890s"
        detached_s_line = (
            "The cleanup token '"
            + detached_s_value
            + "'"
        )
        self.assertIn(
            detached_s_value,
            list(credential_prose_literals(detached_s_line)),
        )
        self.assertIn(
            "credential-prose-literal",
            inspect_line(detached_s_line),
        )

        displaced_value = "+actual.secret.1234567890"
        displaced_line = (
            "The cleanup token '[REDACTED;safe]'"
            + displaced_value
            + "'A"
        )
        self.assertIn(
            displaced_value,
            list(credential_prose_literals(displaced_line)),
        )
        self.assertIn(
            "credential-prose-literal",
            inspect_line(displaced_line),
        )

    def test_attached_single_quote_segmentation_is_evidence_independent(
        self,
    ) -> None:
        identifier_edges = (
            ("uppercase", "A"),
            ("decimal-number", "7"),
            ("letter-number", "\u2160"),
            ("other-number", "\u00b2"),
        )
        unsafe_value = "synthetic-attached-segmentation-unsafe-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for edge_name, edge in identifier_edges:
                    for attachment in ("prefix", "suffix"):
                        for label in ("Audit", "Audit label"):
                            for spacing in ("", " ", "\t"):
                                quoted_label = "'" + label + "'"
                                attached_label = (
                                    edge + quoted_label
                                    if attachment == "prefix"
                                    else quoted_label + edge
                                )
                                with self.subTest(
                                    first=first_name,
                                    punctuation=punctuation,
                                    edge=edge_name,
                                    attachment=attachment,
                                    label=label,
                                    spacing=repr(spacing),
                                ):
                                    line = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                        + spacing
                                        + attached_label
                                        + " and later `"
                                        + unsafe_value
                                        + "` remains"
                                    )
                                    self.assert_single_clause_violation(
                                        line,
                                        unsafe_value,
                                    )

    def test_attached_single_quote_escape_parity_matrix(self) -> None:
        base_value = "SyntheticLiteralValue2468"
        split_positions = (9, 12, 15)
        for attachment in ("prefix", "suffix"):
            for internal_delimiter in ("'", '"', "`"):
                for position in split_positions:
                    for slash_count in (1, 3, 5):
                        content = (
                            base_value[:position]
                            + ("\\" * slash_count)
                            + internal_delimiter
                            + base_value[position:]
                        )
                        attached = (
                            "A'" + content + "'"
                            if attachment == "prefix"
                            else "'" + content + "'A"
                        )
                        with self.subTest(
                            attachment=attachment,
                            delimiter=internal_delimiter,
                            position=position,
                            slash_count=slash_count,
                            parity="odd",
                        ):
                            line = "The cleanup token " + attached
                            self.assertIn(
                                content,
                                list(credential_prose_literals(line)),
                            )
                            self.assertIn(
                                "credential-prose-literal",
                                inspect_line(line),
                            )

                    for slash_count in (0, 2, 4):
                        content = (
                            base_value[:position]
                            + ("\\" * slash_count)
                            + internal_delimiter
                            + base_value[position:]
                        )
                        attached = (
                            "A'" + content + "'"
                            if attachment == "prefix"
                            else "'" + content + "'A"
                        )
                        with self.subTest(
                            attachment=attachment,
                            delimiter=internal_delimiter,
                            position=position,
                            slash_count=slash_count,
                            parity="even",
                        ):
                            line = "The cleanup token " + attached
                            self.assertNotIn(
                                content,
                                list(credential_prose_literals(line)),
                            )
                            self.assertEqual([], inspect_line(line))

    def test_shared_unescaped_apostrophe_cannot_bridge_primary_spans(
        self,
    ) -> None:
        unsafe_value = "synthetic-shared-apostrophe-unsafe-1234567890"
        for punctuation in ".?!;":
            for spacing in ("", " ", "\t"):
                for slash_count in (0, 2, 4):
                    left_value = (
                        "[REDACTED]"
                        + ("\\" * slash_count)
                    )
                    prefix = (
                        'The cleanup token "[REDACTED]." A\''
                        + left_value
                        + "'"
                    )
                    line = (
                        prefix
                        + punctuation
                        + spacing
                        + "'"
                        + unsafe_value
                        + "' remains"
                    )
                    with self.subTest(
                        punctuation=punctuation,
                        spacing=repr(spacing),
                        slash_count=slash_count,
                    ):
                        clauses = scan_clauses(line)
                        self.assertIn(
                            len(prefix) + 1,
                            [clause.end for clause in clauses],
                        )
                        self.assertEqual([], inspect_line(line))

    def test_identifier_attached_literals_are_direct_evidence(self) -> None:
        for edge_name, edge, _category in (
            self.UNICODE_QUOTE_EDGE_CHARACTERS
        ):
            for quote_name, opening, closing in self.QUOTE_PAIRS:
                for attachment in ("prefix", "suffix"):
                    literal = (
                        "synthetic-attached-"
                        + attachment
                        + "-value-1234567890"
                    )
                    quoted = opening + literal + closing
                    attached = (
                        edge + quoted
                        if attachment == "prefix"
                        else quoted + edge
                    )
                    with self.subTest(
                        edge=edge_name,
                        quote=quote_name,
                        attachment=attachment,
                    ):
                        line = "The cleanup token " + attached
                        self.assertEqual(
                            [(0, len(line))],
                            [
                                (clause.start, clause.end)
                                for clause in scan_clauses(line)
                            ],
                        )
                        self.assertIn(
                            literal,
                            list(credential_prose_literals(line)),
                        )
                        self.assertIn(
                            "credential-prose-literal",
                            inspect_line(line),
                        )

    def test_possessive_before_attached_literal_does_not_shadow_it(
        self,
    ) -> None:
        for literal in (
            "synthetic-attached-dotted.value.1234567890",
            "+actual.secret.1234567890",
        ):
            with self.subTest(literal=literal):
                line = "The cleanup token O'Brien'" + literal + "'"
                self.assertEqual(
                    [(0, len(line))],
                    [
                        (clause.start, clause.end)
                        for clause in scan_clauses(line)
                    ],
                )
                self.assertIn(
                    literal,
                    list(credential_prose_literals(line)),
                )
                self.assertIn(
                    "credential-prose-literal",
                    inspect_line(line),
                )

    def test_apostrophe_bridge_does_not_shadow_wrapped_secret(
        self,
    ) -> None:
        literal = "+actual.secret.12345678901234567890"
        for wrapper_name, wrapper_opening, wrapper_closing in (
            self.SIMPLE_WRAPPERS[1:]
        ):
            with self.subTest(wrapper=wrapper_name):
                line = (
                    "The cleanup token Alice's"
                    + wrapper_opening
                    + "'"
                    + literal
                    + "'"
                    + wrapper_closing
                )
                self.assertEqual(
                    [(0, len(line))],
                    [
                        (clause.start, clause.end)
                        for clause in scan_clauses(line)
                    ],
                )
                self.assertIn(
                    literal,
                    list(credential_prose_literals(line)),
                )
                self.assertIn(
                    "credential-prose-literal",
                    inspect_line(line),
                )

    def test_attached_recovery_preserves_existing_primary_coverage(
        self,
    ) -> None:
        cases = (
            (
                "The cleanup token 'prefix'\""
                + "synthetic'.secret-1234567890"
                + "\"",
                "synthetic'.secret-1234567890",
                True,
            ),
            (
                "The cleanup token 'Alice's report'"
                + "+actual.secret.1234567890'",
                "+actual.secret.1234567890",
                True,
            ),
            (
                "The cleanup token '[REDACTED]'.'"
                + "synthetic-lowercase-next-label-1234567890"
                + "' remains",
                "",
                False,
            ),
        )
        for line, literal, should_reject in cases:
            with self.subTest(line=line):
                if should_reject:
                    self.assertIn(
                        literal,
                        list(credential_prose_literals(line)),
                    )
                    self.assertIn(
                        "credential-prose-literal",
                        inspect_line(line),
                    )
                else:
                    clauses = scan_clauses(line)
                    self.assertGreaterEqual(len(clauses), 2)
                    self.assertEqual([], inspect_line(line))

    def test_attached_recovery_rejects_cross_syntax_bridges(self) -> None:
        cross_delimiter_literal = "def'ghi.secret.1234567890"
        cross_delimiter = (
            "The cleanup token x'placeholder-xxxxxxxx\""
            + cross_delimiter_literal
            + "\""
        )
        self.assertIn(
            cross_delimiter_literal,
            list(credential_prose_literals(cross_delimiter)),
        )
        self.assertIn(
            "credential-prose-literal",
            inspect_line(cross_delimiter),
        )

        punctuation_bridge = (
            "The cleanup token '[REDACTED]'"
            + ("." * 20)
            + "'synthetic-unrelated-secret-1234567890'"
        )
        self.assertGreaterEqual(
            len(scan_clauses(punctuation_bridge)),
            2,
        )
        self.assertEqual([], inspect_line(punctuation_bridge))

        wrapper_bridge = (
            "The cleanup token "
            + ("(" * 20)
            + "'safe'"
            + (")" * 20)
            + "'next'"
        )
        self.assertEqual([], inspect_line(wrapper_bridge))

        for delimiter in ('"', "`"):
            punctuation_bridge = (
                "The cleanup token "
                + delimiter
                + "[REDACTED]"
                + delimiter
                + ("." * 20)
                + delimiter
                + "synthetic-unrelated-secret-1234567890"
                + delimiter
            )
            with self.subTest(
                delimiter=delimiter,
                bridge="close-to-open",
            ):
                self.assertGreaterEqual(
                    len(scan_clauses(punctuation_bridge)),
                    2,
                )
                self.assertEqual([], inspect_line(punctuation_bridge))

        unmatched_bridges = (
            ("'", "`", "`"),
            ('"', "`", "`"),
            ("`", '"', '"'),
        )
        for delimiter, next_opening, next_closing in unmatched_bridges:
            punctuation_bridge = (
                "The cleanup token "
                + delimiter
                + "[REDACTED]"
                + delimiter
                + ("." * 20)
                + delimiter
                + next_opening
                + "synthetic-unrelated-secret-1234567890"
                + next_closing
            )
            with self.subTest(
                delimiter=delimiter,
                bridge="unmatched-opener",
            ):
                self.assertGreaterEqual(
                    len(scan_clauses(punctuation_bridge)),
                    2,
                )
                self.assertEqual([], inspect_line(punctuation_bridge))

        unsafe_value = "synthetic-cross-syntax-unsafe-1234567890"
        for delimiter in ('"', "`"):
            for slash_count in (0, 2, 4):
                escaped_delimiter = ("\\" * slash_count) + delimiter
                prefix = (
                    "The cleanup token 'abc"
                    + escaped_delimiter
                    + "def"
                    + escaped_delimiter
                    + "z'A."
                )
                line = (
                    prefix
                    + " 'Other' and `"
                    + unsafe_value
                    + "`"
                )
                with self.subTest(
                    delimiter=delimiter,
                    slash_count=slash_count,
                ):
                    self.assertIn(
                        len(prefix),
                        [clause.end for clause in scan_clauses(line)],
                    )
                    self.assertEqual([], inspect_line(line))

    def test_balanced_emphasis_next_sentence_matrix(self) -> None:
        next_value = "Synthetic-emphasized-next-sentence-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in self.EMPHASIS_WRAPPERS:
                        with self.subTest(
                            first=first_name,
                            punctuation=punctuation,
                            next=next_name,
                            emphasis=emphasis_name,
                        ):
                            prefix = (
                                "The cleanup token "
                                + first_opening
                                + "[REDACTED]"
                                + punctuation
                                + first_closing
                            )
                            line = (
                                prefix
                                + " "
                                + emphasis_opening
                                + next_opening
                                + next_value
                                + next_closing
                                + emphasis_closing
                                + " remains."
                            )
                            self.assert_boundary_after(line, prefix)

    def test_balanced_emphasis_continuation_matrix(self) -> None:
        next_value = "Synthetic-emphasized-audit-label-1234567890"
        unsafe_value = "synthetic-emphasized-unsafe-value-1234567890"
        tails = (
            ("comma", ", which later records "),
            ("colon", ": explanation later records "),
            ("lowercase", " which later records "),
            ("conjunction", " and later records "),
        )
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in self.EMPHASIS_WRAPPERS:
                        for tail_name, tail in tails:
                            with self.subTest(
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                emphasis=emphasis_name,
                                tail=tail_name,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + emphasis_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + emphasis_closing
                                    + tail
                                    + "`"
                                    + unsafe_value
                                    + "` remains"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )

    def test_unbalanced_and_mismatched_emphasis_are_not_skipped(self) -> None:
        next_value = "Synthetic-unbalanced-audit-label-1234567890"
        unsafe_value = "synthetic-unbalanced-unsafe-value-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for index, (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in enumerate(self.EMPHASIS_WRAPPERS):
                        mismatched_closing = self.EMPHASIS_WRAPPERS[
                            (index + 1) % len(self.EMPHASIS_WRAPPERS)
                        ][2]
                        overrun_closing = (
                            emphasis_closing + emphasis_closing[-1]
                        )
                        for kind, closing in (
                            ("unbalanced", ""),
                            ("mismatched", mismatched_closing),
                            ("overrun", overrun_closing),
                        ):
                            with self.subTest(
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                emphasis=emphasis_name,
                                kind=kind,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + emphasis_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + closing
                                    + " remains and later `"
                                    + unsafe_value
                                    + "` is recorded"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )

    def test_markdown_emphasis_identifier_suffix_is_conservative(self) -> None:
        next_value = "Synthetic-intraword-suffix-label-1234567890"
        unsafe_value = "synthetic-intraword-suffix-unsafe-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in self.EMPHASIS_WRAPPERS:
                        for suffix in ("7tail", "_tail", "tail"):
                            with self.subTest(
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                emphasis=emphasis_name,
                                suffix=suffix,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + emphasis_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + emphasis_closing
                                    + suffix
                                    + " and later `"
                                    + unsafe_value
                                    + "` remains"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )

    def test_markdown_emphasis_identifier_prefix_is_conservative(self) -> None:
        next_value = "Synthetic-intraword-prefix-label-1234567890"
        unsafe_value = "synthetic-intraword-prefix-unsafe-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in self.EMPHASIS_WRAPPERS:
                        for identifier_prefix in ("7", "_", "x"):
                            with self.subTest(
                                first=first_name,
                                punctuation=punctuation,
                                next=next_name,
                                emphasis=emphasis_name,
                                prefix=identifier_prefix,
                            ):
                                line = (
                                    "The cleanup token "
                                    + first_opening
                                    + "[REDACTED]"
                                    + punctuation
                                    + first_closing
                                    + " "
                                    + identifier_prefix
                                    + emphasis_opening
                                    + next_opening
                                    + next_value
                                    + next_closing
                                    + emphasis_closing
                                    + " remains and later `"
                                    + unsafe_value
                                    + "` is recorded"
                                )
                                self.assert_single_clause_violation(
                                    line,
                                    unsafe_value,
                                )

    def test_markdown_emphasis_valid_flanking_matrix(self) -> None:
        next_value = "Synthetic-valid-flanking-label-1234567890"
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for (
                        emphasis_name,
                        emphasis_opening,
                        emphasis_closing,
                    ) in self.EMPHASIS_WRAPPERS:
                        for spacing in ("", " ", "\t"):
                            for suffix_name, suffix in (
                                ("end", ""),
                                ("punctuation", "."),
                                ("whitespace", " remains."),
                            ):
                                with self.subTest(
                                    first=first_name,
                                    punctuation=punctuation,
                                    next=next_name,
                                    emphasis=emphasis_name,
                                    spacing=repr(spacing),
                                    suffix=suffix_name,
                                ):
                                    prefix = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                    )
                                    line = (
                                        prefix
                                        + spacing
                                        + emphasis_opening
                                        + next_opening
                                        + next_value
                                        + next_closing
                                        + emphasis_closing
                                        + suffix
                                    )
                                    self.assert_boundary_after(line, prefix)

    def test_overlong_markdown_marker_runs_are_conservative(self) -> None:
        next_value = "Synthetic-overlong-marker-label-1234567890"
        unsafe_value = "synthetic-overlong-marker-unsafe-1234567890"
        flanks = (
            ("detached", "", ""),
            ("identifier-prefix", "x", ""),
            ("identifier-suffix", "", "tail"),
        )
        cases = 0
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for marker in ("*", "_"):
                        for run_length in (3, 4, 5, 7):
                            marker_run = marker * run_length
                            for flank_name, prefix_flank, suffix_flank in flanks:
                                with self.subTest(
                                    first=first_name,
                                    punctuation=punctuation,
                                    next=next_name,
                                    marker=marker,
                                    run_length=run_length,
                                    flank=flank_name,
                                ):
                                    line = (
                                        "The cleanup token "
                                        + first_opening
                                        + "[REDACTED]"
                                        + punctuation
                                        + first_closing
                                        + " "
                                        + prefix_flank
                                        + marker_run
                                        + next_opening
                                        + next_value
                                        + next_closing
                                        + marker_run
                                        + suffix_flank
                                        + " and later `"
                                        + unsafe_value
                                        + "` remains"
                                    )
                                    self.assert_single_clause_violation(
                                        line,
                                        unsafe_value,
                                    )
                                    cases += 1
        self.assertEqual(5 * 3 * 5 * 2 * 4 * len(flanks), cases)

    def test_distinct_markdown_marker_runs_can_nest(self) -> None:
        next_value = "Synthetic-distinct-marker-label-1234567890"
        distinct_pairs = tuple(
            (outer, inner)
            for outer in self.EMPHASIS_WRAPPERS
            for inner in self.EMPHASIS_WRAPPERS
            if outer[1][0] != inner[1][0]
        )
        self.assertEqual(8, len(distinct_pairs))
        for first_name, first_opening, first_closing in self.QUOTE_PAIRS:
            for punctuation in ".?!":
                for next_name, next_opening, next_closing in self.QUOTE_PAIRS:
                    for outer, inner in distinct_pairs:
                        with self.subTest(
                            first=first_name,
                            punctuation=punctuation,
                            next=next_name,
                            outer=outer[0],
                            inner=inner[0],
                        ):
                            prefix = (
                                "The cleanup token "
                                + first_opening
                                + "[REDACTED]"
                                + punctuation
                                + first_closing
                            )
                            line = (
                                prefix
                                + " "
                                + outer[1]
                                + inner[1]
                                + next_opening
                                + next_value
                                + next_closing
                                + inner[2]
                                + outer[2]
                                + " remains."
                            )
                            self.assert_boundary_after(line, prefix)

    def test_nested_simple_and_emphasis_wrapper_contexts(self) -> None:
        next_value = "Synthetic-nested-wrapper-label-1234567890"
        unsafe_value = "synthetic-nested-wrapper-unsafe-1234567890"
        for (
            simple_name,
            simple_opening,
            simple_closing,
        ) in self.SIMPLE_WRAPPERS[1:]:
            for (
                emphasis_name,
                emphasis_opening,
                emphasis_closing,
            ) in self.EMPHASIS_WRAPPERS:
                wrappers = (
                    (
                        "simple-outer",
                        simple_opening + emphasis_opening,
                        emphasis_closing + simple_closing,
                    ),
                    (
                        "emphasis-outer",
                        emphasis_opening + simple_opening,
                        simple_closing + emphasis_closing,
                    ),
                )
                for order, opening, closing in wrappers:
                    with self.subTest(
                        simple=simple_name,
                        emphasis=emphasis_name,
                        order=order,
                        kind="standalone",
                    ):
                        prefix = 'The cleanup token "[REDACTED]."'
                        line = (
                            prefix
                            + " "
                            + opening
                            + '"'
                            + next_value
                            + '"'
                            + closing
                            + " remains."
                        )
                        self.assert_boundary_after(line, prefix)
                    with self.subTest(
                        simple=simple_name,
                        emphasis=emphasis_name,
                        order=order,
                        kind="continuation",
                    ):
                        line = (
                            'The cleanup token "[REDACTED]." '
                            + opening
                            + '"'
                            + next_value
                            + '"'
                            + closing
                            + ", which later records `"
                            + unsafe_value
                            + "`"
                        )
                        self.assert_single_clause_violation(
                            line,
                            unsafe_value,
                        )

    def test_recovery_spans_do_not_mask_outside_terminators(self) -> None:
        value = "synthetic-lowercase-next-label-1234567890"
        for name, opening, closing in self.QUOTE_PAIRS:
            for punctuation in ".?!;":
                for separator in (
                    punctuation,
                    punctuation + " ",
                    " " + punctuation + " ",
                    "\t" + punctuation + "\t",
                ):
                    with self.subTest(
                        name=name,
                        punctuation=punctuation,
                        separator=repr(separator),
                    ):
                        prefix = (
                            "The cleanup token "
                            + opening
                            + "[REDACTED]"
                            + closing
                        )
                        line = (
                            prefix
                            + separator
                            + opening
                            + value
                            + closing
                            + " remains"
                        )
                        punctuation_end = (
                            len(prefix) + separator.index(punctuation) + 1
                        )
                        clauses = scan_clauses(line)
                        self.assertEqual(punctuation_end, clauses[0].end)
                        self.assertEqual([], inspect_line(line))

    def test_primary_quote_coverage_preserves_internal_terminators(self) -> None:
        for name, opening, closing in self.QUOTE_PAIRS:
            for punctuation in ".?!;":
                with self.subTest(name=name, punctuation=punctuation):
                    value = (
                        "synthetic"
                        + punctuation
                        + "internal-primary-value-1234567890"
                    )
                    line = (
                        "The cleanup token "
                        + opening
                        + value
                        + closing
                        + " remains"
                    )
                    self.assertEqual(
                        [(0, len(line))],
                        [
                            (clause.start, clause.end)
                            for clause in scan_clauses(line)
                        ],
                    )
                    self.assertIn("credential-prose-literal", inspect_line(line))

    def test_quote_terminal_segment_end_matrix(self) -> None:
        suffixes = ("", "   ", " The next sentence starts here.")
        for name, opening, closing in self.QUOTE_PAIRS:
            for suffix in suffixes:
                with self.subTest(name=name, suffix=repr(suffix)):
                    prefix = "The cleanup token was " + opening + "removed." + closing
                    clauses = scan_clauses(prefix + suffix)
                    self.assertEqual(len(prefix), clauses[0].end)

    def test_nonterminal_quote_keeps_following_literal_in_clause(self) -> None:
        audit_literal = "synthetic-same-clause-audit-label-1234567890"
        for name, opening, closing in self.QUOTE_PAIRS:
            with self.subTest(name=name):
                line = (
                    "The cleanup token was "
                    + opening
                    + "removed"
                    + closing
                    + " and the audit label `"
                    + audit_literal
                    + "` remains."
                )
                self.assertIn("credential-prose-literal", inspect_line(line))

    def test_escaped_terminal_punctuation_is_not_a_boundary(self) -> None:
        line = (
            'The cleanup token was "removed\\." and the audit label `'
            + "synthetic-same-clause-audit-label-1234567890"
            + "` remains."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_even_escape_run_leaves_terminal_punctuation_unescaped(self) -> None:
        line = (
            'The cleanup token was "removed\\\\."'
            + " The unrelated audit label `"
            + "synthetic-unrelated-audit-label-1234567890"
            + "` remains."
        )
        self.assertEqual([], inspect_line(line))

    def test_unicode_credential_dash_matrix(self) -> None:
        for dash in (
            "\u2010",
            "\u2011",
            "\u2012",
            "\u2013",
            "\u2014",
            "\u2015",
        ):
            with self.subTest(codepoint=f"U+{ord(dash):04X}"):
                line = (
                    "The API"
                    + dash
                    + "key `"
                    + "synthetic-unicode-dash-secret-1234567890"
                    + "` must rotate."
                )
                self.assertIn("credential-prose-literal", inspect_line(line))

    def test_rejects_related_credential_names_in_prose(self) -> None:
        literal = "synthetic-prose-secret-1234567890"
        for credential_name in (
            "token",
            "API token",
            "API key",
            "secret",
            "password",
            "cleanup token",
            "access token",
            "bearer token",
            "credential",
        ):
            for quote in ("`", '"', "'"):
                with self.subTest(credential_name=credential_name, quote=quote):
                    line = (
                        "The "
                        + credential_name
                        + " was recorded for owner follow-up as "
                        + quote
                        + literal
                        + quote
                        + " before rotation."
                    )
                    self.assertIn("credential-prose-literal", inspect_line(line))

    def test_credential_prose_detector_stays_within_clause(self) -> None:
        for separator in (". ", "; ", "! ", "? ", "\n"):
            with self.subTest(separator=separator):
                line = (
                    "The cleanup token reference was removed"
                    + separator
                    + "The unrelated audit label `"
                    + "synthetic-prose-secret-1234567890"
                    + "` remains."
                )
                self.assertEqual([], inspect_line(line))

    def test_credential_prose_detector_requires_paired_quotes(self) -> None:
        line = (
            "The cleanup "
            + "token \u201c"
            + "synthetic-mismatched-secret-1234567890"
            + "\u2019 must rotate."
        )
        self.assertEqual([], inspect_line(line))

    def test_unclosed_and_mismatched_quote_matrix(self) -> None:
        literal = "synthetic-unclosed-secret-1234567890"
        mismatched = (
            ('"', "'"),
            ("'", '"'),
            ("\u201c", "\u2019"),
            ("\u2018", "\u201d"),
            ("`", "\u201d"),
        )
        for name, opening, _ in self.QUOTE_PAIRS:
            with self.subTest(name=name, kind="unclosed"):
                line = "The cleanup " + "token " + opening + literal
                self.assertEqual([], inspect_line(line))
        for opening, closing in mismatched:
            with self.subTest(opening=opening, closing=closing, kind="mismatched"):
                line = "The cleanup " + "token " + opening + literal + closing
                self.assertEqual([], inspect_line(line))

    def test_newline_terminates_an_open_quoted_context(self) -> None:
        line = (
            'The cleanup token "reference'
            + "\n"
            + '" The unrelated audit label `'
            + "synthetic-prose-secret-1234567890"
            + "` remains."
        )
        self.assertEqual([], inspect_line(line))

    def test_rejects_generic_secret_prefixed_prose_literal(self) -> None:
        line = (
            "The cleanup "
            + "token `secret:"
            + "synthetic-prose-secret-1234567890"
            + "` requires rotation."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_allows_safe_credential_prose_references(self) -> None:
        safe_values = (
            "[REDACTED]",
            "<from-secret-manager>",
            "[REDACTED-CREDENTIAL-VALUE]",
            "redacted-token-placeholder",
            "sha256:fingerprint-redacted",
            "sha256:" + ("a" * 64),
            "PLACEHOLDER_CREDENTIAL_VALUE",
            "${CLEANUP_TOKEN}",
            "{{secret_manager.cleanup_token}}",
            "env:COMPANY_BASEROW_CLEANUP_TOKEN",
            "secret-manager:cleanup-token",
            "stored-in-secret-manager",
            "owner-managed-host-only",
            "AE-SYS-baserow-adapter",
            "rejectUnauthorized=false",
        )
        for _, opening, closing in self.QUOTE_PAIRS:
            for value in safe_values:
                with self.subTest(value=value, opening=opening):
                    line = (
                        "The cleanup "
                        + "token "
                        + opening
                        + value
                        + closing
                        + " is a non-secret reference."
                    )
                    self.assertEqual([], inspect_line(line))

    def test_safe_span_does_not_hide_later_unsafe_span(self) -> None:
        line = (
            "The cleanup token `[REDACTED]` and later `"
            + "synthetic-later-secret-1234567890"
            + "` must rotate."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_terminal_safe_span_does_not_taint_next_clause(self) -> None:
        line = (
            "The cleanup token `[REDACTED].` "
            + "The unrelated audit label `"
            + "synthetic-unrelated-audit-label-1234567890"
            + "` remains."
        )
        self.assertEqual([], inspect_line(line))

    def test_safe_punctuated_span_with_continuation_keeps_clause(self) -> None:
        line = (
            "The cleanup token `[REDACTED].` and later `"
            + "synthetic-same-sentence-audit-label-1234567890"
            + "` must rotate."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

    def test_keyword_span_association_order(self) -> None:
        before = "synthetic-before-keyword-label-1234567890"
        after_first = "synthetic-after-first-secret-1234567890"
        after_second = "synthetic-after-second-secret-1234567890"
        line = (
            "`"
            + before
            + "` appears first, then the cleanup token uses `"
            + after_first
            + "`, and the password later uses `"
            + after_second
            + "` before rotation."
        )
        self.assertEqual(
            [after_first, after_second],
            list(credential_prose_literals(line)),
        )

    def test_keyword_inside_span_does_not_associate_later_span(self) -> None:
        line = (
            '"cleanup token" is only a quoted label, while `'
            + "synthetic-unrelated-audit-label-1234567890"
            + "` remains unrelated."
        )
        self.assertEqual([], inspect_line(line))

    def test_parser_scaling_is_linear(self) -> None:
        operation_counts = []
        for size in (64, 128, 256, 512):
            line = " ".join(
                "cleanup token `synthetic-scale-secret-"
                + f"{index:08d}"
                + "`"
                for index in range(size)
            )
            metrics = ParserMetrics()
            values = list(credential_prose_literals(line, metrics))
            self.assertEqual(size, len(values))
            self.assertLessEqual(metrics.association_steps, (6 * size) + 8)
            self.assertLessEqual(
                metrics.total_operations,
                (9 * len(line)) + (10 * size),
            )
            operation_counts.append(metrics.total_operations)

        for previous, current in zip(operation_counts, operation_counts[1:]):
            self.assertLessEqual(current, (2.1 * previous) + 32)

    def test_structured_next_context_scaling_is_linear(self) -> None:
        operation_counts = []
        for size in (32, 64, 128, 256):
            line = " ".join(
                'cleanup token "[REDACTED]." '
                + '"Synthetic-context-label-'
                + f"{index:08d}"
                + '", which later records `synthetic-context-secret-'
                + f"{index:08d}"
                + "`"
                for index in range(size)
            )
            metrics = ParserMetrics()
            values = list(credential_prose_literals(line, metrics))
            self.assertEqual(3 * size, len(values))
            self.assertEqual(
                size,
                sum(
                    value.startswith("synthetic-context-secret-")
                    for value in values
                ),
            )
            self.assertLessEqual(metrics.context_steps, (160 * size) + 16)
            self.assertLessEqual(
                metrics.total_operations,
                (10 * len(line)) + (180 * size),
            )
            operation_counts.append(metrics.total_operations)

        for previous, current in zip(operation_counts, operation_counts[1:]):
            self.assertLessEqual(current, (2.1 * previous) + 64)

    def test_rejects_literal_secret_assignment(self) -> None:
        line = "api_" + "key = " + "synthetic-secret-value-1234567890"
        self.assertIn("literal-secret-assignment", inspect_line(line))

    def test_allows_redacted_secret_assignment(self) -> None:
        line = "api_" + "key: <redacted>"
        self.assertEqual([], inspect_line(line))

    def test_rejects_exact_google_resource_url(self) -> None:
        url = (
            "https://drive.google.com/"
            + "drive/folders/"
            + ("A" * 24)
        )
        self.assertIn("exact-google-resource-url", inspect_url(url))

    def test_rejects_credential_bearing_url(self) -> None:
        url = (
            "https://service.invalid/resource?"
            + "access_"
            + "token="
            + "synthetic-secret-value-1234567890"
        )
        self.assertIn("credential-bearing-url", inspect_url(url))

    def test_allows_google_documentation_url(self) -> None:
        self.assertEqual(
            [],
            inspect_url("https://docs.google.com/documentation/help"),
        )


if __name__ == "__main__":
    unittest.main()
