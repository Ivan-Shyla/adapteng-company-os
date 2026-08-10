#!/usr/bin/env python3
"""The documented pre-PR commands must run exactly what CI runs.

README.md tells a contributor which commands to run before opening a pull
request and states that CI runs the same ones. That sentence was false once
already: CI gained ``test_rehearsal_contour`` and
``test_rehearsal_effective_repository`` while the README kept listing three
modules, so anyone following the README ran a weaker check than the one that
decides whether their pull request is mergeable, and found out only after
pushing.

A promise that a document matches a workflow is worth no more than the thing
that checks it, so this module checks it. Both files are parsed for the set of
``scripts.test_*`` modules they name and the two sets must be equal.

Parsing is deliberately literal. ci.yml is read as text rather than as YAML
because the repository has no third-party dependencies and stdlib has no YAML
parser; the pattern anchors on the ``python -m unittest`` invocation so a
comment mentioning a module name cannot satisfy the test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
README = REPOSITORY_ROOT / "README.md"

MODULE_PATTERN = re.compile(r"scripts\.test_[a-z0-9_]+")


def _modules_in_unittest_invocations(text: str) -> set[str]:
    """Return every ``scripts.test_*`` module named in a unittest invocation.

    A run of the form ``python -m unittest a b c`` may be spread over several
    lines, by YAML folding in the workflow or simply by length in the README.
    The invocation is therefore taken to continue until a line that neither
    continues the command nor names another module.
    """
    modules: set[str] = set()
    collecting = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        stripped = line.lstrip("#").strip()
        if stripped.startswith("```") or not line:
            collecting = False
            continue
        # A comment line never starts or extends an invocation; this is what
        # stops ci.yml's explanatory paragraph about the POSIX-only suite from
        # being read as if it were running that suite.
        if line.startswith("#"):
            collecting = False
            continue
        if "python -m unittest" in line:
            collecting = True
            head = line.split("python -m unittest", 1)[1]
            modules.update(MODULE_PATTERN.findall(head))
            continue
        if collecting:
            found = MODULE_PATTERN.findall(line)
            if not found:
                collecting = False
                continue
            modules.update(found)
    return modules


class PrePrCommandsMatchCiTests(unittest.TestCase):
    def test_both_files_are_present(self) -> None:
        self.assertTrue(CI_WORKFLOW.is_file(), f"missing {CI_WORKFLOW}")
        self.assertTrue(README.is_file(), f"missing {README}")

    def test_the_readme_runs_every_module_ci_runs(self) -> None:
        ci_modules = _modules_in_unittest_invocations(
            CI_WORKFLOW.read_text(encoding="utf-8")
        )
        readme_modules = _modules_in_unittest_invocations(
            README.read_text(encoding="utf-8")
        )

        self.assertTrue(ci_modules, "parsed no modules out of ci.yml")

        missing_from_readme = sorted(ci_modules - readme_modules)
        self.assertEqual(
            [],
            missing_from_readme,
            "CI runs these suites but the README does not tell anyone to: "
            f"{missing_from_readme}. A contributor following the README would "
            "pass locally and fail in CI. Add them to the pre-PR command.",
        )

        missing_from_ci = sorted(readme_modules - ci_modules)
        self.assertEqual(
            [],
            missing_from_ci,
            "the README asks for suites CI never runs: "
            f"{missing_from_ci}. Either add them to ci.yml or stop asking.",
        )

    def test_this_suite_is_itself_registered(self) -> None:
        """A check that no one runs cannot fail, so it must be in both lists."""
        ci_modules = _modules_in_unittest_invocations(
            CI_WORKFLOW.read_text(encoding="utf-8")
        )
        self.assertIn(
            "scripts.test_pre_pr_commands_match_ci",
            ci_modules,
            "this suite must be named in ci.yml or it enforces nothing",
        )

    def test_the_parser_ignores_module_names_inside_comments(self) -> None:
        sample = (
            "      # test_postgres_restore_scheduler_surface is POSIX-only\n"
            "      # and scripts.test_not_really_run is discussed here\n"
            "      - name: Run unittest suites\n"
            "        run: >-\n"
            "          python -m unittest\n"
            "          scripts.test_alpha\n"
            "          scripts.test_beta\n"
        )
        self.assertEqual(
            {"scripts.test_alpha", "scripts.test_beta"},
            _modules_in_unittest_invocations(sample),
        )

    def test_the_parser_reads_a_single_line_invocation(self) -> None:
        sample = "python -m unittest scripts.test_alpha scripts.test_beta\n"
        self.assertEqual(
            {"scripts.test_alpha", "scripts.test_beta"},
            _modules_in_unittest_invocations(sample),
        )

    def test_the_parser_stops_at_a_fence_and_ignores_later_prose(self) -> None:
        sample = (
            "```bash\n"
            "python -m unittest scripts.test_alpha\n"
            "```\n"
            "Prose mentioning scripts.test_gamma must not count.\n"
        )
        self.assertEqual(
            {"scripts.test_alpha"},
            _modules_in_unittest_invocations(sample),
        )

    def test_the_parser_collects_a_trailing_comment_marker(self) -> None:
        """The README marks the POSIX-only line with a trailing comment."""
        sample = (
            "python -m unittest scripts.test_surface  # POSIX only\n"
        )
        self.assertEqual(
            {"scripts.test_surface"},
            _modules_in_unittest_invocations(sample),
        )


if __name__ == "__main__":
    unittest.main()
