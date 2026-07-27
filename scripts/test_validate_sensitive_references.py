#!/usr/bin/env python3
"""Regression tests for tracked sensitive-reference validation."""

from __future__ import annotations

import unittest

from scripts.validate_sensitive_references import inspect_line, inspect_url


class SensitiveReferenceValidatorTests(unittest.TestCase):
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
        quote_pairs = (("`", "`"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))
        for opening, closing in quote_pairs:
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
