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
import os
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from scripts import rehearsal_digest_compare as compare
    from scripts import rehearsal_isolation_guard as guard
except ImportError:  # pragma: no cover - direct execution from scripts/
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


def workflow_step_script(workflow: str, step_name: str) -> str:
    """Return the shell body of one workflow step.

    The parsing is deliberately literal rather than YAML-aware: CI installs no
    dependencies, so every import here has to resolve to the standard library.
    """
    path = ROOT.joinpath(".github", "workflows", workflow)
    lines = path.read_text(encoding="utf-8").splitlines()

    start = None
    for index, line in enumerate(lines):
        if line.strip() == f"- name: {step_name}":
            start = index
            break
    if start is None:
        raise AssertionError(f"{workflow} has no step named {step_name!r}")

    run_at = None
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == "- name:":
            break
        if lines[index].lstrip().startswith("- name: "):
            break
        if lines[index].strip() == "run: |":
            run_at = index
            break
    if run_at is None:
        raise AssertionError(f"step {step_name!r} in {workflow} has no literal run block")

    body: list[str] = []
    body_indent = None
    for index in range(run_at + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            body.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is None:
            body_indent = indent
        if indent < body_indent:
            break
        body.append(line[body_indent:])
    return "\n".join(body).rstrip("\n") + "\n"


def working_bash() -> str | None:
    """Locate a bash that actually runs.

    On Windows `bash` on PATH is frequently the WSL launcher, which exits
    non-zero when no distribution is installed, so each candidate is proved
    before it is used.
    """
    candidates = []
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(discovered)
    candidates.extend(
        (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        )
    )
    for candidate in candidates:
        if not Path(candidate).exists():
            continue
        try:
            proof = subprocess.run(
                [candidate, "-c", "echo ok"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError:  # pragma: no cover - candidate is not executable here
            continue
        if proof.returncode == 0 and proof.stdout.strip() == "ok":
            return candidate
    return None


BASH = working_bash()

CAP_MESSAGE = (
    "An error occurred (AccessDenied) when calling the GetObject operation: "
    "Cannot download file, download bandwidth or transaction (Class B) cap "
    "exceeded. See the Caps & Alerts page to increase your cap."
)

# A stand-in for the object store. SCENARIO chooses which transaction classes it
# is willing to serve, which is the distinction the probes exist to draw.
AWS_STUB = """#!/usr/bin/env bash
op="$2"
key=""
prev=""
for argument in "$@"; do
  if [ "$prev" = "--key" ]; then key="$argument"; fi
  prev="$argument"
done
case "$op" in
  put-object|delete-object)
    echo '{"ETag":"\\"probe\\""}'
    exit 0
    ;;
  list-objects-v2)
    echo '{"KeyCount":1}'
    exit 0
    ;;
esac
absent=no
case "$key" in
  *absent*|*archive.info) absent=yes ;;
esac
case "$SCENARIO" in
  healthy)
    if [ "$absent" = yes ]; then
      echo "An error occurred (404) when calling the ${op} operation: Not Found" >&2
      exit 254
    fi
    echo '{"ContentLength":42}'
    exit 0
    ;;
  cap)
    if [ "$op" = head-object ]; then
      echo "An error occurred (403) when calling the HeadObject operation: Forbidden" >&2
    else
      echo "__CAP__" >&2
    fi
    exit 254
    ;;
  refused)
    if [ "$op" = head-object ]; then
      echo "An error occurred (403) when calling the HeadObject operation: Forbidden" >&2
    else
      echo "An error occurred (AccessDenied) when calling the GetObject operation: Access Denied" >&2
    fi
    exit 254
    ;;
esac
exit 9
"""

WRAPPER = """#!/usr/bin/env bash
here="$(cd "$(dirname "$0")" && pwd)"
PATH="$here/bin:$PATH"
export PATH
exec bash "$here/step.sh"
"""


@unittest.skipUnless(BASH, "no working bash interpreter is available")
class ObjectStoreRefusalProbeTests(unittest.TestCase):
    """Negative controls for the probes that read a refusal from the store.

    These branches only execute when the object store misbehaves, which is
    precisely when they cannot be exercised on demand. Six rehearsals failed on
    an unexplained 403 that was read as a credential fault; the probes exist to
    make that reading impossible, so they are given each refusal here and their
    verdict is asserted.
    """

    def run_step(self, script: str, scenario: str, extra_env: dict[str, str]) -> tuple[int, str]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            stub = binaries / "aws"
            stub.write_text(AWS_STUB.replace("__CAP__", CAP_MESSAGE), encoding="utf-8", newline="\n")
            os.chmod(stub, 0o755)
            (root / "step.sh").write_text(script, encoding="utf-8", newline="\n")
            wrapper = root / "wrapper.sh"
            wrapper.write_text(WRAPPER, encoding="utf-8", newline="\n")
            os.chmod(wrapper, 0o755)

            environment = dict(os.environ)
            environment["SCENARIO"] = scenario
            environment["RUNNER_TEMP"] = str(root).replace("\\", "/")
            environment["GITHUB_STEP_SUMMARY"] = str(root / "summary.md").replace("\\", "/")
            environment.update(extra_env)

            completed = subprocess.run(
                [str(BASH), str(wrapper).replace("\\", "/")],
                capture_output=True,
                text=True,
                env=environment,
                timeout=120,
            )
            return completed.returncode, completed.stdout + completed.stderr

    def preflight(self, scenario: str) -> tuple[int, str]:
        return self.run_step(
            workflow_step_script(
                "postgres-backup-rehearsal.yml",
                "Prove the object store still answers download-class requests",
            ),
            scenario,
            {
                "PGBACKREST_REPO1_PATH": REHEARSAL_REPO_PATH,
                "REHEARSAL_STANZA": "rehearsal",
                "PGBACKREST_REPO1_S3_ENDPOINT": "endpoint.invalid",
                "PGBACKREST_REPO1_S3_BUCKET": "bucket",
            },
        )

    def classifier(self, scenario: str) -> tuple[int, str]:
        return self.run_step(
            workflow_step_script(
                "verify-b2-connectivity.yml",
                "Classify which transaction classes the object store still serves",
            ),
            scenario,
            {
                "ENDPOINT": "endpoint.invalid",
                "BUCKET": "bucket",
                "CLASS_PROBE_KEY": "connectivity-check/1-1-class-probe.txt",
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
            },
        )

    def test_absent_archive_info_is_the_healthy_answer(self) -> None:
        # 404 means the stanza has not been created yet, which is exactly the
        # state the rehearsal starts from. The preflight must not object to it.
        code, output = self.preflight("healthy")
        self.assertEqual(code, 0, output)
        self.assertIn("serves download-class requests", output)
        self.assertNotIn("::error", output)

    def test_preflight_names_a_cap_rather_than_the_credentials(self) -> None:
        code, output = self.preflight("cap")
        self.assertEqual(code, 1, output)
        self.assertIn("because a cap is reached", output)
        self.assertIn("not a credential, permission or URI-style fault", output)

    def test_preflight_does_not_blame_a_cap_for_an_unexplained_refusal(self) -> None:
        # The opposite failure matters just as much: a refusal that says nothing
        # about a cap must not be reported as one.
        code, output = self.preflight("refused")
        self.assertEqual(code, 1, output)
        self.assertIn("refused a repository read", output)
        self.assertNotIn("cap is reached", output)

    def test_classifier_passes_only_when_downloads_are_served(self) -> None:
        code, output = self.classifier("healthy")
        self.assertEqual(code, 0, output)
        self.assertIn("All three transaction classes are being served", output)
        self.assertNotIn("::error", output)

    def test_classifier_reports_the_cap_and_the_classes_that_still_work(self) -> None:
        code, output = self.classifier("cap")
        self.assertEqual(code, 1, output)
        self.assertIn("because a cap is reached", output)
        # The neighbouring classes are the whole point: they are what makes the
        # refusal look like a credential fault when they are not reported.
        self.assertIn("Class A is served and class C is served", output)

    def test_classifier_rules_out_key_prefix_and_uri_style(self) -> None:
        code, output = self.classifier("refused")
        self.assertEqual(code, 1, output)
        self.assertIn("refuses download-class requests", output)
        self.assertIn("not about a missing key", output)
        self.assertNotIn("cap is reached", output)


class TrackedRehearsalFileTests(unittest.TestCase):
    """The contour is authored on Windows and consumed by bash and psql."""

    def rehearsal_files(self) -> list[Path]:
        scripts = sorted(ROOT.joinpath("scripts").glob("rehearsal_*"))
        workflows = sorted(ROOT.joinpath(".github", "workflows").glob("*.yml"))
        self.assertTrue(scripts, "no rehearsal scripts were found")
        self.assertTrue(workflows, "no workflows were found")
        return scripts + workflows

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
