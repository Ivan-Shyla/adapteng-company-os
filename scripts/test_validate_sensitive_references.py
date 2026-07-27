#!/usr/bin/env python3
"""Regression tests for tracked sensitive-reference validation."""

from __future__ import annotations

import unittest

from scripts.validate_sensitive_references import (
    credential_prose_literals,
    inspect_line,
    inspect_url,
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
        line = (
            "The cleanup "
            + "token `"
            + "synthetic-cleanup-token-12345"
            + "` must be rotated."
        )
        self.assertIn("credential-prose-literal", inspect_line(line))

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

    def test_ascii_quote_escape_matrix(self) -> None:
        for name, quote in (("ascii-double", '"'), ("ascii-single", "'")):
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
        for name, quote in (("ascii-double", '"'), ("ascii-single", "'")):
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
