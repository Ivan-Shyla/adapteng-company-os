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
