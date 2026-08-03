#!/usr/bin/env python3
"""Negative controls for the effective-repository checks in the isolation guard.

The rest of the guard compares the two repository paths it is handed on the
command line. That proves what the caller intended. pgBackRest, however, reads
``PGBACKREST_REPO1_PATH`` from the environment, so the repository a backup
actually lands in is decided there and nowhere else.

The failure these tests exist to catch is therefore a caller that declares a
harmless rehearsal prefix while exporting the production one. Every check below
is given an input that must make it fail; a check that cannot fail is not a
check.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from scripts import rehearsal_isolation_guard as guard
except ImportError:  # pragma: no cover - direct execution from scripts/
    import rehearsal_isolation_guard as guard  # type: ignore[no-redef]


PRODUCTION = "/adapteng_ops"
REHEARSAL = "/restore-rehearsal/12345-1"


def outcomes(checks: list[guard.Check]) -> dict[str, bool]:
    return {check.name: check.passed for check in checks}


class EffectiveRepositoryTests(unittest.TestCase):
    def test_environment_agreeing_with_the_declared_rehearsal_passes(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": REHEARSAL},
                REHEARSAL,
                PRODUCTION,
                required=True,
            )
        )
        self.assertTrue(result["effective_repo_path_declared"])
        self.assertTrue(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertTrue(result["effective_repo_path_disjoint_from_production"])
        self.assertTrue(result["no_additional_pgbackrest_repository_configured"])

    def test_exporting_production_while_declaring_a_rehearsal_path_is_refused(
        self,
    ) -> None:
        """The whole reason these checks exist.

        The caller passes a run-scoped rehearsal prefix, which satisfies every
        argument-only check, while the environment pgBackRest obeys names
        production.
        """

        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": PRODUCTION}, REHEARSAL, PRODUCTION
            )
        )
        self.assertFalse(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertFalse(result["effective_repo_path_disjoint_from_production"])

    def test_a_child_of_the_production_prefix_is_refused(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": f"{PRODUCTION}/rehearsal"},
                f"{PRODUCTION}/rehearsal",
                PRODUCTION,
            )
        )
        self.assertTrue(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertFalse(result["effective_repo_path_disjoint_from_production"])

    def test_unset_environment_variable_is_refused_when_required(self) -> None:
        result = outcomes(
            guard.check_effective_repository({}, REHEARSAL, PRODUCTION, required=True)
        )
        self.assertFalse(result["effective_repo_path_declared"])

    def test_unset_environment_variable_is_not_judged_when_not_required(self) -> None:
        """A fixture-evaluating caller is not asked about a variable it never set.

        This is the one concession to callers outside the workflow, and it is
        deliberately narrow: it withholds the demand that the variable exist. It
        does not withhold judgement on a variable that does exist -- see below.
        """

        result = outcomes(
            guard.check_effective_repository({}, REHEARSAL, PRODUCTION)
        )
        self.assertNotIn("effective_repo_path_declared", result)
        self.assertNotIn("effective_repo_path_matches_declared_rehearsal", result)

    def test_a_wrong_environment_is_caught_even_without_required(self) -> None:
        """Opting out of `required` must not buy a way past the real check."""

        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": PRODUCTION}, REHEARSAL, PRODUCTION
            )
        )
        self.assertFalse(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertFalse(result["effective_repo_path_disjoint_from_production"])

    def test_whitespace_only_environment_variable_is_refused(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": "   "},
                REHEARSAL,
                PRODUCTION,
                required=True,
            )
        )
        self.assertFalse(result["effective_repo_path_declared"])

    def test_a_relative_effective_path_is_refused(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": "restore-rehearsal/12345-1"},
                "restore-rehearsal/12345-1",
                PRODUCTION,
            )
        )
        self.assertFalse(result["effective_repo_path_disjoint_from_production"])

    def test_bucket_root_as_the_effective_path_is_refused(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": "/"}, "/", PRODUCTION
            )
        )
        self.assertFalse(result["effective_repo_path_disjoint_from_production"])

    def test_a_second_configured_repository_is_refused(self) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {
                    "PGBACKREST_REPO1_PATH": REHEARSAL,
                    "PGBACKREST_REPO2_PATH": PRODUCTION,
                },
                REHEARSAL,
                PRODUCTION,
            )
        )
        self.assertTrue(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertFalse(result["no_additional_pgbackrest_repository_configured"])

    def test_repo10_is_recognised_as_an_additional_repository(self) -> None:
        """A two-digit repository index must not read as repo1 with a suffix."""

        result = outcomes(
            guard.check_effective_repository(
                {"PGBACKREST_REPO1_PATH": REHEARSAL, "PGBACKREST_REPO10_PATH": "/x"},
                REHEARSAL,
                PRODUCTION,
            )
        )
        self.assertFalse(result["no_additional_pgbackrest_repository_configured"])

    def test_other_repo1_options_are_not_mistaken_for_a_second_repository(
        self,
    ) -> None:
        result = outcomes(
            guard.check_effective_repository(
                {
                    "PGBACKREST_REPO1_PATH": REHEARSAL,
                    "PGBACKREST_REPO1_S3_BUCKET": "adapteng-postgres-backups",
                    "PGBACKREST_REPO1_CIPHER_TYPE": "aes-256-cbc",
                },
                REHEARSAL,
                PRODUCTION,
            )
        )
        self.assertTrue(result["no_additional_pgbackrest_repository_configured"])


class GuardWiringTests(unittest.TestCase):
    """The checks above are only worth anything if `evaluate` actually runs them."""

    def _namespace(self, root: Path, rehearsal: str, *, require: bool = True):
        cluster = root / "source"
        cluster.mkdir(parents=True, exist_ok=True)
        argv = [
            "--production-repo-path",
            PRODUCTION,
            "--rehearsal-repo-path",
            rehearsal,
            "--scope-token",
            "12345-1",
            "--ephemeral-root",
            str(root),
            "--cluster",
            f"source={cluster}",
        ]
        if require:
            argv.append("--require-effective-repo-path")
        return guard.build_parser().parse_args(argv)

    def test_evaluate_refuses_a_declared_rehearsal_that_the_environment_contradicts(
        self,
    ) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.evaluate(
                self._namespace(root, REHEARSAL),
                {"PGBACKREST_REPO1_PATH": PRODUCTION},
            )
        result = outcomes(checks)
        self.assertIn("effective_repo_path_matches_declared_rehearsal", result)
        self.assertFalse(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertFalse(all(result.values()))

    def test_evaluate_refuses_a_contradiction_without_the_flag_too(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.evaluate(
                self._namespace(root, REHEARSAL, require=False),
                {"PGBACKREST_REPO1_PATH": PRODUCTION},
            )
        self.assertFalse(all(outcomes(checks).values()))

    def test_evaluate_refuses_an_unset_variable_when_the_flag_is_given(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.evaluate(self._namespace(root, REHEARSAL), {})
        result = outcomes(checks)
        self.assertFalse(result["effective_repo_path_declared"])

    def test_evaluate_accepts_the_honest_case(self) -> None:
        """Positive control: the contradiction above is what fails, not the fixture."""

        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.evaluate(
                self._namespace(root, REHEARSAL),
                {"PGBACKREST_REPO1_PATH": REHEARSAL},
            )
        result = outcomes(checks)
        self.assertTrue(result["effective_repo_path_matches_declared_rehearsal"])
        self.assertTrue(result["effective_repo_path_disjoint_from_production"])
        self.assertTrue(all(result.values()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
