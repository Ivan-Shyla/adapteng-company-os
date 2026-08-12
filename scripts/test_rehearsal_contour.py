#!/usr/bin/env python3
"""Unit tests for the disposable PostgreSQL backup and restore rehearsal.

These are the negative controls for the rehearsal contour. The workflow runs
the guard and the comparator against a live cluster, where a check that always
passes is indistinguishable from a check that works. Here each property is
given an input that must make it fail, so a regression that silently disables a
gate is caught on every pull request rather than during an outage.
"""

from __future__ import annotations

import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

try:
    from scripts import rehearsal_b2_capability_probe as b2probe
    from scripts import rehearsal_digest_compare as compare
    from scripts import rehearsal_isolation_guard as guard
except ImportError:  # pragma: no cover - direct execution from scripts/
    import rehearsal_b2_capability_probe as b2probe  # type: ignore[no-redef]
    import rehearsal_digest_compare as compare  # type: ignore[no-redef]
    import rehearsal_isolation_guard as guard  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_REPO_PATH = "/adapteng_ops"
SCOPE_TOKEN = "30000000000-1"
REHEARSAL_REPO_PATH = f"/restore-rehearsal/{SCOPE_TOKEN}"

VALID_DIGEST = "\n".join(
    (
        "rehearsal.id_allocation|25000|0123456789abcdef0123456789abcdef",
        "rehearsal.run_ledger|12000|fedcba9876543210fedcba9876543210",
    )
)


def named(check: guard.Check) -> tuple[str, bool]:
    return check.name, check.passed


def result_of(checks: list[guard.Check], name: str) -> bool:
    for check in checks:
        if check.name == name:
            return check.passed
    raise AssertionError(f"check {name} was not evaluated")


class RepositoryDisjointnessTests(unittest.TestCase):
    def disjoint(self, production: str, rehearsal: str) -> bool:
        return result_of(
            guard.check_repository_disjoint(production, rehearsal),
            "rehearsal_repo_path_disjoint_from_production",
        )

    def test_sibling_prefixes_are_disjoint(self) -> None:
        self.assertTrue(self.disjoint(PRODUCTION_REPO_PATH, REHEARSAL_REPO_PATH))

    def test_identical_prefixes_are_rejected(self) -> None:
        self.assertFalse(self.disjoint(PRODUCTION_REPO_PATH, PRODUCTION_REPO_PATH))

    def test_rehearsal_nested_under_production_is_rejected(self) -> None:
        self.assertFalse(
            self.disjoint(PRODUCTION_REPO_PATH, f"{PRODUCTION_REPO_PATH}/rehearsal/1")
        )

    def test_rehearsal_containing_production_is_rejected(self) -> None:
        self.assertFalse(self.disjoint("/adapteng_ops/repo", "/adapteng_ops"))

    def test_shared_name_prefix_is_not_shared_lineage(self) -> None:
        self.assertTrue(self.disjoint("/adapteng_ops", "/adapteng_ops_rehearsal"))

    def test_bucket_root_production_path_is_rejected(self) -> None:
        self.assertFalse(self.disjoint("/", REHEARSAL_REPO_PATH))

    def test_relative_rehearsal_path_is_rejected(self) -> None:
        self.assertFalse(self.disjoint(PRODUCTION_REPO_PATH, "restore-rehearsal/1"))


class ScopeTokenTests(unittest.TestCase):
    def test_whole_segment_token_is_accepted(self) -> None:
        self.assertTrue(guard.check_scope_token(REHEARSAL_REPO_PATH, SCOPE_TOKEN).passed)

    def test_partial_segment_token_is_rejected(self) -> None:
        self.assertFalse(
            guard.check_scope_token(REHEARSAL_REPO_PATH, "30000000000").passed
        )

    def test_absent_token_is_rejected(self) -> None:
        self.assertFalse(guard.check_scope_token("/restore-rehearsal", SCOPE_TOKEN).passed)

    def test_blank_token_is_rejected(self) -> None:
        self.assertFalse(guard.check_scope_token(REHEARSAL_REPO_PATH, "  ").passed)


class EphemeralPathTests(unittest.TestCase):
    def test_directories_inside_the_root_are_accepted(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.check_ephemeral(
                root, {"source": root / "source", "restore": root / "restore"}
            )
            self.assertTrue(all(passed for _, passed in map(named, checks)))

    def test_directory_outside_the_root_is_rejected(self) -> None:
        with TemporaryDirectory() as raw, TemporaryDirectory() as elsewhere:
            root = Path(raw)
            checks = guard.check_ephemeral(
                root, {"source": root / "source", "escaped": Path(elsewhere) / "data"}
            )
            self.assertFalse(result_of(checks, "cluster_path_ephemeral[escaped]"))

    def test_the_root_itself_is_not_a_cluster_directory(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.check_ephemeral(root, {"source": root})
            self.assertFalse(result_of(checks, "cluster_path_ephemeral[source]"))

    def test_duplicate_directories_are_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            checks = guard.check_ephemeral(
                root, {"a": root / "same", "b": root / "same"}
            )
            self.assertFalse(result_of(checks, "cluster_paths_distinct"))

    def test_no_declared_directory_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            checks = guard.check_ephemeral(Path(raw), {})
            self.assertFalse(result_of(checks, "cluster_paths_distinct"))


class RestoreTargetEmptinessTests(unittest.TestCase):
    def test_absent_target_is_empty(self) -> None:
        with TemporaryDirectory() as raw:
            checks = guard.check_empty({"restore": Path(raw) / "absent"})
            self.assertTrue(result_of(checks, "restore_target_empty[restore]"))

    def test_populated_target_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            target = Path(raw) / "restore"
            target.mkdir()
            (target / "PG_VERSION").write_text("16\n", encoding="utf-8")
            checks = guard.check_empty({"restore": target})
            self.assertFalse(result_of(checks, "restore_target_empty[restore]"))

    def test_file_in_place_of_target_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            target = Path(raw) / "restore"
            target.write_text("not a directory\n", encoding="utf-8")
            checks = guard.check_empty({"restore": target})
            self.assertFalse(result_of(checks, "restore_target_empty[restore]"))


class ListenAddressTests(unittest.TestCase):
    def config(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_empty_listen_addresses_is_accepted(self) -> None:
        with TemporaryDirectory() as raw:
            path = self.config(Path(raw), "postgresql.conf", "listen_addresses = ''\n")
            checks = guard.check_listen_addresses([("source", path)])
            self.assertTrue(result_of(checks, "cluster_has_no_listener[source]"))

    def test_published_listener_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            path = self.config(Path(raw), "postgresql.conf", "listen_addresses = '*'\n")
            checks = guard.check_listen_addresses([("source", path)])
            self.assertFalse(result_of(checks, "cluster_has_no_listener[source]"))

    def test_loopback_listener_is_still_a_listener(self) -> None:
        with TemporaryDirectory() as raw:
            path = self.config(
                Path(raw), "postgresql.conf", "listen_addresses = '127.0.0.1'\n"
            )
            checks = guard.check_listen_addresses([("source", path)])
            self.assertFalse(result_of(checks, "cluster_has_no_listener[source]"))

    def test_undeclared_setting_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            path = self.config(
                Path(raw), "postgresql.conf", "#listen_addresses = ''\nport = 55432\n"
            )
            checks = guard.check_listen_addresses([("source", path)])
            self.assertFalse(result_of(checks, "cluster_has_no_listener[source]"))

    def test_a_later_override_file_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            base = self.config(directory, "postgresql.conf", "listen_addresses = ''\n")
            auto = self.config(
                directory, "postgresql.auto.conf", "listen_addresses = '0.0.0.0'\n"
            )
            checks = guard.check_listen_addresses([("restore", base), ("restore", auto)])
            self.assertFalse(result_of(checks, "cluster_has_no_listener[restore]"))

    def test_unreadable_configuration_stops(self) -> None:
        with TemporaryDirectory() as raw:
            missing = Path(raw) / "postgresql.conf"
            with self.assertRaises(guard.GuardError):
                guard.check_listen_addresses([("source", missing)])


class EnvironmentTests(unittest.TestCase):
    def test_clean_environment_is_accepted(self) -> None:
        checks = guard.check_environment({"PATH": "/usr/bin", "RUNNER_TEMP": "/tmp"})
        self.assertTrue(all(passed for _, passed in map(named, checks)))

    def test_forbidden_connection_variable_is_rejected(self) -> None:
        checks = guard.check_environment({"DATABASE_URL": "postgres://127.0.0.1/x"})
        self.assertFalse(result_of(checks, "no_production_connection_variables"))

    def test_blank_forbidden_variable_is_tolerated(self) -> None:
        checks = guard.check_environment({"PGHOST": "   "})
        self.assertTrue(result_of(checks, "no_production_connection_variables"))

    def test_remote_uri_in_any_variable_is_rejected(self) -> None:
        checks = guard.check_environment(
            {"SOME_UNRELATED_SETTING": "postgresql://db.internal.invalid:5432/adapteng_ops"}
        )
        self.assertFalse(result_of(checks, "no_remote_connection_uri_in_environment"))

    def test_credential_bearing_remote_uri_is_rejected(self) -> None:
        checks = guard.check_environment(
            {"ANYTHING": "postgres://user:pw@db.internal.invalid:5432/adapteng_ops"}
        )
        self.assertFalse(result_of(checks, "no_remote_connection_uri_in_environment"))

    def test_loopback_uri_is_accepted(self) -> None:
        checks = guard.check_environment({"ANYTHING": "postgres://127.0.0.1:55432/x"})
        self.assertTrue(result_of(checks, "no_remote_connection_uri_in_environment"))


class PgBackRestConfigurationTests(unittest.TestCase):
    def write(self, directory: Path, body: str) -> Path:
        path = directory / "pgbackrest.conf"
        path.write_text(body, encoding="utf-8")
        return path

    def test_local_data_directory_is_accepted(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            data = directory / "source"
            data.mkdir()
            path = self.write(directory, f"[rehearsal]\npg1-path={data}\npg1-port=55432\n")
            checks = guard.check_pgbackrest_config(path, {data.resolve()})
            self.assertTrue(all(passed for _, passed in map(named, checks)))

    def test_remote_postgresql_host_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            data = directory / "source"
            data.mkdir()
            path = self.write(
                directory,
                f"[rehearsal]\npg1-host=db.internal.invalid\npg1-path={data}\n",
            )
            checks = guard.check_pgbackrest_config(path, {data.resolve()})
            self.assertFalse(
                result_of(checks, "pgbackrest_targets_no_remote_postgresql")
            )

    def test_remote_host_user_option_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            data = directory / "source"
            data.mkdir()
            path = self.write(
                directory, f"[rehearsal]\npg1-host-user=postgres\npg1-path={data}\n"
            )
            checks = guard.check_pgbackrest_config(path, {data.resolve()})
            self.assertFalse(
                result_of(checks, "pgbackrest_targets_no_remote_postgresql")
            )

    def test_undeclared_data_directory_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            declared = directory / "source"
            declared.mkdir()
            path = self.write(directory, "[rehearsal]\npg1-path=/var/lib/postgresql/16/main\n")
            checks = guard.check_pgbackrest_config(path, {declared.resolve()})
            self.assertFalse(
                result_of(checks, "pgbackrest_data_directory_is_declared_ephemeral")
            )

    def test_absent_data_directory_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            path = self.write(directory, "[global]\nrepo1-type=s3\n")
            checks = guard.check_pgbackrest_config(path, set())
            self.assertFalse(
                result_of(checks, "pgbackrest_data_directory_is_declared_ephemeral")
            )

    def test_separator_truncated_path_is_refused_not_accepted(self) -> None:
        """A form feed used to end the value early and win the gate's approval."""
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            data = directory / "source"
            data.mkdir()
            body = f"[rehearsal]\npg1-path={data}\f/../../etc\n"
            path = self.write(directory, body)
            # The mechanism, asserted rather than described: splitlines() ends
            # the value at the form feed, leaving exactly the allowed prefix,
            # while the value an LF-delimited reader sees escapes the directory.
            self.assertEqual(body.splitlines()[1], f"pg1-path={data}")
            self.assertNotIn("\f", body.splitlines()[1])
            with self.assertRaises(guard.GuardError):
                guard.check_pgbackrest_config(path, {data.resolve()})

    def test_every_ambiguous_separator_is_refused(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            data = directory / "source"
            data.mkdir()
            for character, label in guard.AMBIGUOUS_LINE_SEPARATORS.items():
                with self.subTest(separator=label):
                    path = self.write(
                        directory, f"[rehearsal]\npg1-path={data}{character}/../../etc\n"
                    )
                    with self.assertRaises(guard.GuardError):
                        guard.check_pgbackrest_config(path, {data.resolve()})


class ConfigLineModelTests(unittest.TestCase):
    def test_matches_splitlines_when_no_ambiguous_separator_is_present(self) -> None:
        for text in (
            "",
            "a=1",
            "a=1\n",
            "a=1\nb=2\n",
            "a=1\n\nb=2\n",
            "[section]\n# comment\nkey = value\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    guard.config_lines(text, Path("pgbackrest.conf"), "gate"),
                    text.splitlines(),
                )

    def test_each_separator_is_named_in_the_failure(self) -> None:
        for character, label in guard.AMBIGUOUS_LINE_SEPARATORS.items():
            with self.subTest(separator=label):
                with self.assertRaises(guard.GuardError) as caught:
                    guard.config_lines(
                        f"a=1{character}b=2\n", Path("pgbackrest.conf"), "gate"
                    )
                self.assertIn(label, str(caught.exception))

    def test_carriage_return_is_normalised_before_the_gate_sees_it(self) -> None:
        """CR is absent from the set because it cannot arrive, not because it is safe."""
        with TemporaryDirectory() as raw:
            path = Path(raw) / "pgbackrest.conf"
            path.write_bytes(b"a=1\rb=2\r\nc=3\n")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\r", text)
            self.assertEqual(
                guard.config_lines(text, path, "gate"), ["a=1", "b=2", "c=3"]
            )


class GuardCommandTests(unittest.TestCase):
    def arrange(self, directory: Path) -> list[str]:
        source = directory / "source"
        source.mkdir()
        (source / "postgresql.conf").write_text(
            "listen_addresses = ''\nport = 55432\n", encoding="utf-8"
        )
        config = directory / "pgbackrest.conf"
        config.write_text(f"[rehearsal]\npg1-path={source}\n", encoding="utf-8")
        return [
            "--production-repo-path",
            PRODUCTION_REPO_PATH,
            "--rehearsal-repo-path",
            REHEARSAL_REPO_PATH,
            "--scope-token",
            SCOPE_TOKEN,
            "--ephemeral-root",
            str(directory),
            "--cluster",
            f"source={source}",
            "--restore-target",
            f"restore={directory / 'restore'}",
            "--cluster-config",
            f"source={source / 'postgresql.conf'}",
            "--pgbackrest-config",
            str(config),
        ]

    def test_isolated_rehearsal_passes_and_writes_evidence(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            evidence = directory / "evidence.json"
            argv = self.arrange(directory) + ["--output", str(evidence)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(guard.main(argv), 0)
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertTrue(payload["isolated"])
            self.assertGreaterEqual(payload["checks_evaluated"], 8)
            self.assertTrue(all(payload["checks"].values()))

    def test_production_repository_path_fails_the_command(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.arrange(directory)
            argv[argv.index("--rehearsal-repo-path") + 1] = PRODUCTION_REPO_PATH
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(guard.main(argv), 2)

    def test_malformed_named_path_fails_the_command(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.arrange(directory) + ["--cluster", "broken"]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(guard.main(argv), 2)


class DigestParsingTests(unittest.TestCase):
    def test_valid_digest_is_parsed(self) -> None:
        entries = compare.parse_digest(VALID_DIGEST, label="left")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries["rehearsal.run_ledger"].rows, 12000)

    def test_empty_digest_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest("\n\n", label="left")

    def test_malformed_line_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest("rehearsal.t|12|not-a-checksum", label="left")

    def test_truncated_checksum_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest("rehearsal.t|12|0123456789abcdef", label="left")

    def test_non_numeric_row_count_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest(
                "rehearsal.t|many|0123456789abcdef0123456789abcdef", label="left"
            )

    def test_psql_status_noise_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest(f"SET\n{VALID_DIGEST}", label="left")

    def test_duplicate_table_is_rejected(self) -> None:
        with self.assertRaises(compare.DigestError):
            compare.parse_digest(
                "rehearsal.t|1|0123456789abcdef0123456789abcdef\n"
                "rehearsal.t|1|0123456789abcdef0123456789abcdef",
                label="left",
            )

    def test_a_non_lf_separator_cannot_terminate_a_record(self) -> None:
        """A digest whose records are joined by a non-LF separator is malformed.

        This is the one case where LF records are stricter than ``splitlines()``
        rather than merely numbered differently. Under ``splitlines()`` the text
        below breaks into two well-formed entries and was accepted as a valid
        two-table digest; under LF records it is a single malformed record and
        raises.

        That matters because this parser exists so a malformed digest cannot
        compare equal to another equally malformed digest and report a restore as
        verified. The separators are exercised individually rather than as one
        representative so that a partial regression cannot hide behind a passing
        sibling.
        """
        for separator in ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            text = (
                "rehearsal.a|1|0123456789abcdef0123456789abcdef"
                f"{separator}"
                "rehearsal.b|2|fedcba9876543210fedcba9876543210"
            )
            with self.subTest(separator=repr(separator)):
                self.assertEqual(len(text.splitlines()), 2, "splitlines would accept two entries")
                with self.assertRaises(compare.DigestError):
                    compare.parse_digest(text, label="left")

    def test_a_separator_inside_a_table_name_is_rejected_and_numbered_from_lf(self) -> None:
        """The other case: rejected either way, but at the number the file shows."""
        for separator in ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            text = (
                "rehearsal.a|1|0123456789abcdef0123456789abcdef\n"
                f"rehearsal{separator}b|2|fedcba9876543210fedcba9876543210\n"
                "rehearsal.c|3|0123456789abcdef0123456789abcdef\n"
            )
            with self.subTest(separator=repr(separator)):
                with self.assertRaises(compare.DigestError) as caught:
                    compare.parse_digest(text, label="left")
                self.assertIn("line 2", str(caught.exception))

    def test_the_generator_filter_cannot_emit_the_stricter_case(self) -> None:
        """Bound the claim in the helper docstring instead of asserting it.

        ``rehearsal_capture_digest.sh`` filters psql output through an
        LF-oriented ``grep -E``. A record joined by a non-LF separator carries
        five pipe-separated fields and is dropped there, so that input cannot
        arrive from this repository's own generator — it is reachable only from a
        digest file this program did not produce, which ``load_digest`` accepts.
        The filter is read from the script rather than restated here, so the
        bound fails if the generator's filter is ever loosened.
        """
        script = (ROOT / "scripts" / "rehearsal_capture_digest.sh").read_text(encoding="utf-8")
        match = re.search(r"grep -E '([^']+)'", script)
        self.assertIsNotNone(match, "the generator's record filter was not found")
        record_filter = re.compile(match.group(1))

        joined = (
            "rehearsal.a|1|0123456789abcdef0123456789abcdef"
            "\v"
            "rehearsal.b|2|fedcba9876543210fedcba9876543210"
        )
        self.assertIsNone(record_filter.fullmatch(joined), "generator would have emitted it")
        for line in VALID_DIGEST.split("\n"):
            self.assertIsNotNone(record_filter.fullmatch(line), "generator would drop a good line")


class DigestComparisonTests(unittest.TestCase):
    def parsed(self, text: str) -> dict[str, compare.TableDigest]:
        return compare.parse_digest(text, label="x")

    def test_identical_digests_have_no_differences(self) -> None:
        self.assertEqual(
            compare.differences(self.parsed(VALID_DIGEST), self.parsed(VALID_DIGEST)), []
        )

    def test_row_count_difference_is_detected(self) -> None:
        other = VALID_DIGEST.replace("|25000|", "|24999|")
        self.assertEqual(
            len(compare.differences(self.parsed(VALID_DIGEST), self.parsed(other))), 1
        )

    def test_content_difference_at_equal_row_count_is_detected(self) -> None:
        other = VALID_DIGEST.replace(
            "0123456789abcdef0123456789abcdef", "00000000000000000000000000000000"
        )
        found = compare.differences(self.parsed(VALID_DIGEST), self.parsed(other))
        self.assertEqual(found, ["rehearsal.id_allocation: content checksum differs"])

    def test_extra_table_is_detected(self) -> None:
        other = f"{VALID_DIGEST}\nrehearsal.extra|1|00000000000000000000000000000000"
        found = compare.differences(self.parsed(VALID_DIGEST), self.parsed(other))
        self.assertEqual(found, ["rehearsal.extra: present on the right only"])

    def test_set_checksum_changes_with_content(self) -> None:
        other = VALID_DIGEST.replace("|12000|", "|11999|")
        self.assertNotEqual(
            compare.set_sha256(self.parsed(VALID_DIGEST)),
            compare.set_sha256(self.parsed(other)),
        )

    def test_floor_rejects_a_vacuous_comparison(self) -> None:
        entries = self.parsed("rehearsal.t|0|d41d8cd98f00b204e9800998ecf8427e")
        with self.assertRaises(compare.DigestError):
            compare.enforce_floors(entries, label="x", min_tables=1, min_rows=1)
        with self.assertRaises(compare.DigestError):
            compare.enforce_floors(entries, label="x", min_tables=2, min_rows=0)


class CompareCommandTests(unittest.TestCase):
    def files(self, directory: Path, left: str, right: str) -> list[str]:
        left_path = directory / "left.txt"
        right_path = directory / "right.txt"
        left_path.write_text(left, encoding="utf-8")
        right_path.write_text(right, encoding="utf-8")
        return ["--left", str(left_path), "--right", str(right_path)]

    def run_main(self, argv: list[str]) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return compare.main(argv)

    def test_equal_expectation_on_identical_input_succeeds(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.files(directory, VALID_DIGEST, VALID_DIGEST) + [
                "--expect",
                "equal",
                "--min-tables",
                "2",
                "--min-rows",
                "1000",
                "--output",
                str(directory / "evidence.json"),
            ]
            self.assertEqual(self.run_main(argv), 0)
            payload = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["satisfied"])
            self.assertEqual(payload["difference_count"], 0)

    def test_equal_expectation_on_different_input_fails(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.files(
                directory, VALID_DIGEST, VALID_DIGEST.replace("|25000|", "|25001|")
            ) + ["--expect", "equal"]
            self.assertEqual(self.run_main(argv), 2)

    def test_different_expectation_on_identical_input_fails(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.files(directory, VALID_DIGEST, VALID_DIGEST) + [
                "--expect",
                "different",
            ]
            self.assertEqual(self.run_main(argv), 2)

    def test_different_expectation_on_different_input_succeeds(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.files(
                directory, VALID_DIGEST, f"{VALID_DIGEST}\nrehearsal.x|5|{'a' * 32}"
            ) + ["--expect", "different"]
            self.assertEqual(self.run_main(argv), 0)

    def test_row_floor_fails_the_command(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = self.files(directory, VALID_DIGEST, VALID_DIGEST) + [
                "--expect",
                "equal",
                "--min-rows",
                "10000000",
            ]
            self.assertEqual(self.run_main(argv), 2)

    def test_missing_file_fails_the_command(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            argv = [
                "--left",
                str(directory / "absent.txt"),
                "--right",
                str(directory / "absent.txt"),
                "--expect",
                "equal",
            ]
            self.assertEqual(self.run_main(argv), 2)


class ObjectStoreRefusalTests(unittest.TestCase):
    """Backblaze reports an exhausted account cap as 403 AccessDenied.

    That single fact is what made the rehearsal's 403 unreadable: the cap
    message and a genuine permission refusal share a status code and an error
    code, and only the sentence tells them apart. Reading it as a permission
    problem cost six dispatches and produced an owner action item aimed at the
    wrong console page, so the classification is pinned here rather than left to
    be rediscovered.

    Note what the CAP string below actually says: `download bandwidth OR
    transaction (Class B) cap exceeded`. It names two meters. This constant sat
    in this file, verbatim and correct, while the report built on it resolved
    the disjunction to the second half and told the owner to raise a Class B cap
    that does not exist on his plan. Having the evidence is not the same as
    reading it, so the tests below pin that the disjunction is passed through
    rather than collapsed.
    """

    CAP = (
        "An error occurred (AccessDenied) when calling the GetObject operation: "
        "Cannot download file, download bandwidth or transaction (Class B) cap "
        "exceeded. See the Caps & Alerts page to increase your cap."
    )
    DENIED = (
        "An error occurred (AccessDenied) when calling the HeadObject operation: "
        "Forbidden"
    )
    ABSENT = (
        "An error occurred (404) when calling the HeadObject operation: Not Found"
    )
    UNSIGNABLE = (
        "An error occurred (SignatureDoesNotMatch) when calling the "
        "ListObjectsV2 operation: The request signature we calculated does not "
        "match the signature you provided."
    )

    def test_a_cap_refusal_is_not_read_as_a_permission_refusal(self) -> None:
        self.assertEqual(b2probe.classify(254, self.CAP), b2probe.CAP_EXCEEDED)

    def test_a_permission_refusal_is_not_read_as_a_cap(self) -> None:
        self.assertEqual(b2probe.classify(254, self.DENIED), b2probe.ACCESS_DENIED)

    def test_an_absent_object_is_recognised(self) -> None:
        self.assertEqual(b2probe.classify(254, self.ABSENT), b2probe.NOT_FOUND)

    def test_an_unusable_secret_is_recognised(self) -> None:
        self.assertEqual(b2probe.classify(254, self.UNSIGNABLE), b2probe.BAD_CREDENTIALS)

    def test_success_is_never_classified_from_text(self) -> None:
        self.assertEqual(b2probe.classify(0, self.CAP), b2probe.SUCCEEDED)

    def test_an_unrecognised_refusal_is_not_guessed(self) -> None:
        self.assertEqual(b2probe.classify(254, "connection reset"), b2probe.UNKNOWN)

    def test_the_witness_that_explains_most_is_the_one_believed(self) -> None:
        # Measured, not imagined: in run 30799068761 head-object answered
        # `(403) Forbidden` and get-object answered the cap sentence, for the
        # same key in the same second. Believing whichever answered first is
        # what turned a metering refusal back into a permission refusal.
        head = b2probe.Probe(
            name="head", transaction_class="B", operation="head-object",
            reason=b2probe.ACCESS_DENIED, returncode=254, message=self.DENIED,
        )
        get = b2probe.Probe(
            name="get", transaction_class="B", operation="get-object",
            reason=b2probe.CAP_EXCEEDED, returncode=254, message=self.CAP,
        )
        self.assertIs(b2probe.strongest_refusal([head, get]), get)
        self.assertIs(b2probe.strongest_refusal([get, head]), get)

    def test_a_bodiless_verb_loses_to_one_that_can_explain_itself(self) -> None:
        # Same reason, different verb. HEAD cannot carry a body at all, so its
        # message is empty and quoting it would say nothing.
        head = b2probe.Probe(
            name="head", transaction_class="B", operation="head-object",
            reason=b2probe.ACCESS_DENIED, returncode=254, message="",
        )
        get = b2probe.Probe(
            name="get", transaction_class="B", operation="get-object",
            reason=b2probe.ACCESS_DENIED, returncode=254, message=self.DENIED,
        )
        self.assertIs(b2probe.strongest_refusal([head, get]), get)

    def test_a_run_with_nothing_refused_has_no_witness(self) -> None:
        succeeded = b2probe.Probe(
            name="get", transaction_class="B", operation="get-object",
            reason=b2probe.SUCCEEDED, returncode=0,
        )
        self.assertIsNone(b2probe.strongest_refusal([succeeded]))
        self.assertIsNone(b2probe.strongest_refusal([]))


class ScriptedObjectStore:
    """Stands in for the AWS CLI so the probe's decision tree can be driven.

    The probe's value is in what it concludes from a combination of answers, not
    in any single call, so the combinations are scripted here instead of waiting
    for Backblaze to be in the right state.
    """

    OK = (0, "")

    def __init__(self, listing_output: str = "", **outcomes: tuple[int, str]) -> None:
        self.outcomes = outcomes
        self.listing_output = listing_output
        self.body: Path | None = None
        self.calls: list[tuple[str, str]] = []

    def outcome(self, name: str) -> tuple[int, str]:
        return self.outcomes.get(name, self.OK)

    def __call__(self, operation: str, *arguments: str) -> tuple[int, str, str]:
        args = list(arguments)
        key = args[args.index("--key") + 1] if "--key" in args else ""
        prefix = args[args.index("--prefix") + 1] if "--prefix" in args else ""
        self.calls.append((operation, key))

        if operation == "list-objects-v2":
            returncode, stderr = self.outcome("list")
            # The real CLI echoes the requested --prefix back inside its
            # response body, so this double does too. A fixture politer than
            # the tool it stands for hides precisely the defects that matter:
            # while this returned "", a substring check for the key in the raw
            # output looked like it distinguished present from absent, and in
            # production it matched the echo every time.
            stdout = json.dumps({"Prefix": prefix, "MaxKeys": 1, "KeyCount": 0})
            if prefix.endswith(".txt") and self.listing_output:
                stdout = self.listing_output
            return returncode, stdout, stderr
        if operation == "put-object":
            self.body = Path(args[args.index("--body") + 1])
            name = "put"
        elif operation == "delete-object":
            name = "delete"
        else:
            # archive.info is the key pgBackRest probes before it has created
            # anything; the .txt key is the object the probe just wrote.
            subject = "present" if key.endswith(".txt") else "missing"
            # HEAD and GET are scriptable apart because B2 answers them apart:
            # HTTP forbids a response body on HEAD, so only the GET can carry
            # the sentence that names the cause.
            verb = "head" if operation == "head-object" else "get"
            name = subject
            if f"{subject}_{verb}" in self.outcomes:
                name = f"{subject}_{verb}"

        returncode, stderr = self.outcome(name)
        if returncode == 0 and operation == "get-object" and name == "present":
            assert self.body is not None
            Path(args[-1]).write_bytes(self.body.read_bytes())
        return returncode, "", stderr


class CapabilityProbeTests(unittest.TestCase):
    ABSENT = ObjectStoreRefusalTests.ABSENT
    CAP = ObjectStoreRefusalTests.CAP
    DENIED = ObjectStoreRefusalTests.DENIED
    UNSIGNABLE = ObjectStoreRefusalTests.UNSIGNABLE

    def probe(self, store: ScriptedObjectStore, output: Path | None = None) -> tuple[int, str]:
        argv = [
            "--bucket",
            "a-bucket",
            "--endpoint",
            "object-store.invalid",
            "--prefix",
            REHEARSAL_REPO_PATH,
            "--stanza",
            "rehearsal",
            "--scope",
            SCOPE_TOKEN,
        ]
        if output is not None:
            argv += ["--output", str(output)]
        captured = io.StringIO()
        # `store` is an instance rather than a function, so it does not bind as
        # a method: Aws.run(...) reaches __call__ without a self argument.
        with mock.patch.object(b2probe.Aws, "run", store), mock.patch.object(
            b2probe.shutil, "which", return_value="/usr/bin/aws"
        ):
            with redirect_stdout(captured), redirect_stderr(io.StringIO()):
                status = b2probe.main(argv)
        return status, captured.getvalue()

    def probe_key(self) -> str:
        return (
            f"{REHEARSAL_REPO_PATH.strip('/')}/capability-probe/{SCOPE_TOKEN}.txt"
        )

    def verdict(self, output: str) -> str:
        for line in output.splitlines():
            if line.startswith("probe-verdict: "):
                return line.split(": ", 1)[1]
        raise AssertionError("the probe printed no machine-readable verdict")

    def test_a_healthy_object_store_is_accepted(self) -> None:
        with TemporaryDirectory() as raw:
            evidence = Path(raw) / "b2-capability.json"
            status, output = self.probe(
                ScriptedObjectStore(missing=(254, self.ABSENT)), evidence
            )
            self.assertEqual(status, 0)
            self.assertEqual(self.verdict(output), b2probe.VERDICT_OK)
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["verdict"], b2probe.VERDICT_OK)
            self.assertTrue(
                payload["missing_key"].endswith("/archive/rehearsal/archive.info")
            )

    def test_an_exhausted_cap_is_named_as_a_cap(self) -> None:
        status, output = self.probe(
            ScriptedObjectStore(missing=(254, self.CAP), present=(254, self.CAP))
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "read_refused_by_an_account_cap")
        self.assertIn("Caps & Alerts", output)

    def test_the_meter_is_not_guessed_when_b2_names_two(self) -> None:
        # B2 says "download bandwidth OR transaction (Class B) cap exceeded".
        # Both halves refuse downloads while leaving uploads and listings
        # working, so this log cannot discriminate and must not pretend to.
        # Resolving it to the Class B half is what produced an owner action item
        # pointing at a control that does not exist on a plan where Class A/B/C
        # transactions are free.
        _, output = self.probe(
            ScriptedObjectStore(missing=(254, self.CAP), present=(254, self.CAP))
        )
        annotation = next(
            line for line in output.splitlines() if line.startswith("::error")
        )
        # Both candidate meters are named ...
        self.assertIn("download bandwidth", annotation)
        self.assertIn("Class B", annotation)
        # ... the console is named as the thing that decides between them ...
        self.assertIn("Caps & Alerts page is the discriminator", annotation)
        # ... and the reader is never told a specific cap has been exhausted.
        for claim in (
            "daily cap is exhausted",
            "Class B cap is exhausted",
            "resets at 00:00",
        ):
            self.assertNotIn(claim, annotation)

    def test_the_owner_is_told_the_rehearsal_is_cheap_not_expensive(self) -> None:
        # A refusal on a meter invites the reader to assume the workload is
        # costly and throttle it. One rehearsal moves about 20 MB, so the true
        # reading is a low ceiling, and the annotation has to say so or the
        # wrong lesson gets learned.
        _, output = self.probe(
            ScriptedObjectStore(missing=(254, self.CAP), present=(254, self.CAP))
        )
        self.assertIn("20 MB", output)

    def test_a_class_b_refusal_that_is_not_a_cap_is_reported_separately(self) -> None:
        status, output = self.probe(
            ScriptedObjectStore(missing=(254, self.DENIED), present=(254, self.DENIED))
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "class_b_read_refused")

    def test_a_capped_head_beside_an_explaining_get_is_still_a_cap(self) -> None:
        # The shape B2 actually produced in run 30799068761: every HEAD said
        # `(403) Forbidden` and every GET said the cap sentence. An earlier
        # version of this probe read the HEAD first and reported a permission
        # refusal, which is the original misdiagnosis reproduced inside the
        # instrument built to prevent it.
        store = ScriptedObjectStore(
            missing_head=(254, self.DENIED),
            missing_get=(254, self.CAP),
            present_head=(254, self.DENIED),
            present_get=(254, self.CAP),
        )
        status, output = self.probe(store)
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "read_refused_by_an_account_cap")
        self.assertIn("Caps & Alerts", output)
        # Named from the missing-key pair alone, before spending a write.
        self.assertNotIn("put-object", [operation for operation, _ in store.calls])

    def test_a_signature_refusal_on_a_readable_bucket_names_the_credentials(self) -> None:
        # Listing works, so the key exists and is scoped to the prefix, but the
        # reads are rejected as unsignable. That is neither a cap nor a scope
        # problem and saying so is the whole point of quoting B2.
        status, output = self.probe(
            ScriptedObjectStore(
                missing_head=(254, self.DENIED), missing_get=(254, self.UNSIGNABLE)
            )
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "credentials_rejected")

    def test_a_key_that_cannot_probe_an_absent_object_is_caught(self) -> None:
        # Reads of a present object succeed, so the store is not capped; only
        # the not-yet-created key is refused, which is the request
        # stanza-create makes before it creates anything.
        status, output = self.probe(ScriptedObjectStore(missing=(254, self.DENIED)))
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "missing_key_probe_refused")

    def test_unusable_credentials_stop_at_the_listing(self) -> None:
        store = ScriptedObjectStore(list=(254, self.UNSIGNABLE))
        status, output = self.probe(store)
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "class_c_list_refused")
        # Nothing is written to the object store once it has refused to talk.
        self.assertNotIn("put-object", [operation for operation, _ in store.calls])

    def test_a_refused_write_is_reported_as_a_write(self) -> None:
        status, output = self.probe(
            ScriptedObjectStore(missing=(254, self.ABSENT), put=(254, self.DENIED))
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "class_a_write_refused")

    def test_the_probe_object_is_always_deleted_again(self) -> None:
        # The reads fail, so the probe exits early; the object it wrote to make
        # those reads possible must still be removed on the way out.
        store = ScriptedObjectStore(
            missing=(254, self.ABSENT), present=(254, self.DENIED)
        )
        self.probe(store)
        deletes = [key for operation, key in store.calls if operation == "delete-object"]
        self.assertEqual(len(deletes), 1)
        self.assertTrue(deletes[0].startswith(REHEARSAL_REPO_PATH.strip("/")))

    def test_nothing_outside_the_rehearsal_prefix_is_touched(self) -> None:
        store = ScriptedObjectStore(missing=(254, self.ABSENT))
        self.probe(store)
        for operation, key in store.calls:
            if not key:
                continue
            with self.subTest(operation=operation, key=key):
                self.assertTrue(key.startswith(REHEARSAL_REPO_PATH.strip("/") + "/"))
                self.assertFalse(key.startswith(PRODUCTION_REPO_PATH.strip("/")))

    def test_an_echoed_prefix_is_not_mistaken_for_a_surviving_object(self) -> None:
        # list-objects-v2 returns the prefix it was asked about, so the probe
        # key appears in the response text of a listing that enumerated nothing
        # at all. Asking whether the key occurs anywhere in that text is true
        # whenever the call succeeds: a check that cannot pass, which is exactly
        # as useless as the check that could not fail this branch set out to
        # close, and worse because it blocks the rehearsal on a healthy store.
        store = ScriptedObjectStore(missing=(254, self.ABSENT))
        _, listing, _ = store("list-objects-v2", "--prefix", self.probe_key())
        self.assertIn(self.probe_key(), listing)
        self.assertEqual(b2probe.listed_keys(listing), [])

        status, output = self.probe(store)
        self.assertEqual(status, 0)
        self.assertEqual(self.verdict(output), "ok")

    def test_an_unreadable_listing_is_not_read_as_absence(self) -> None:
        # Absence has to be established, not inferred from a listing that could
        # not be understood.
        self.assertNotEqual(b2probe.listed_keys("<html>gateway timeout</html>"), [])
        self.assertEqual(b2probe.listed_keys(""), [])

    def test_a_delete_that_did_not_delete_is_caught(self) -> None:
        # Cleanup is confirmed with a listing rather than head-object on
        # purpose, so that a capped account cannot mistake litter it is unable
        # to read for litter that is not there.
        store = ScriptedObjectStore(
            missing=(254, self.ABSENT),
            listing_output=(
                '{"Contents": [{"Key": "restore-rehearsal/'
                f'{SCOPE_TOKEN}/capability-probe/{SCOPE_TOKEN}.txt"' + "}]}"
            ),
        )
        status, output = self.probe(store)
        self.assertEqual(status, 1)
        self.assertEqual(self.verdict(output), "probe_object_survived_delete")

    def test_an_empty_prefix_is_refused_outright(self) -> None:
        with self.assertRaises(SystemExit):
            b2probe.run_probe(
                bucket="a-bucket",
                endpoint="https://object-store.invalid",
                prefix="/",
                stanza="rehearsal",
                scope=SCOPE_TOKEN,
            )


class TrackedRehearsalFileTests(unittest.TestCase):
    """The contour is authored on Windows and consumed by bash and psql."""

    def rehearsal_files(self) -> list[Path]:
        scripts = sorted(ROOT.joinpath("scripts").glob("rehearsal_*"))
        workflows = sorted(ROOT.joinpath(".github", "workflows").glob("*.yml"))
        self.assertTrue(scripts, "no rehearsal scripts were found")
        self.assertTrue(workflows, "no workflows were found")
        return scripts + workflows

    def probe_step(self) -> str:
        """Return the body of the rehearsal's capability-probe step."""

        text = ROOT.joinpath(
            ".github", "workflows", "postgres-backup-rehearsal.yml"
        ).read_text(encoding="utf-8")
        start = text.index("- name: Probe what Backblaze B2 will actually serve")
        end = text.index("\n      - name: ", start + 1)
        step = text[start:end]
        self.assertIn("rehearsal_b2_capability_probe.py", step)
        return step

    def test_the_negative_control_runs_before_the_probe_it_qualifies(self) -> None:
        # Ordering is the whole of this control's value. Placed after the real
        # probe it is skipped by `set -e` on precisely the runs where the probe
        # failed, so it can only ever run when its verdict is least needed --
        # and a control that is absent whenever the thing it qualifies goes
        # wrong is not a control. Pinned because nothing else would notice the
        # two blocks being reordered.
        step = self.probe_step()
        control = step.index("--scope \"$REHEARSAL_SCOPE-control\"")
        real = step.index("--scope \"$REHEARSAL_SCOPE\"")
        self.assertLess(control, real)
        self.assertIn("AWS_SECRET_ACCESS_KEY=wrong-on-purpose", step[:real])

    def test_the_negative_control_is_not_guarded_by_the_probe_succeeding(self) -> None:
        step = self.probe_step()
        control = step.index("AWS_SECRET_ACCESS_KEY=wrong-on-purpose")
        # A positive control on the assertion above: prove the slice examined is
        # really the head of the step and not the whole of it.
        self.assertGreater(len(step), control)
        self.assertNotIn("if [ \"$probe_status\"", step[:control])

    def test_files_are_lf_only_and_free_of_a_byte_order_mark(self) -> None:
        for path in self.rehearsal_files():
            with self.subTest(path=path.name):
                payload = path.read_bytes()
                self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\r", payload)
                self.assertTrue(payload.endswith(b"\n"))

    def test_the_digest_query_pins_its_representation(self) -> None:
        sql = ROOT.joinpath("scripts", "rehearsal_content_digest.sql").read_text(
            encoding="utf-8"
        )
        for setting in ("timezone", "datestyle", "extra_float_digits", "bytea_output"):
            with self.subTest(setting=setting):
                self.assertIn(f"SET {setting} =", sql)


if __name__ == "__main__":
    unittest.main()
