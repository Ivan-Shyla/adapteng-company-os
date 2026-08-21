"""Tests for sanitized, online-ready L1 GitHub status reports."""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import l1_github_status_snapshot as snapshot


class FakeTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    def get_json(self, endpoint: str) -> Any:
        response = self.responses.get(endpoint)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise snapshot.GitHubMetadataError("ignored")
        return response


def responses(*, conclusion: str | None = "success", open_pr: bool = False, extras: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, repository in enumerate(snapshot.REPOSITORIES):
        result[f"repos/{repository}"] = {"default_branch": "main", "token": "discard-me"} if extras else {"default_branch": "main"}
        result[f"repos/{repository}/git/ref/heads/main"] = {"object": {"sha": f"sha-{index}", "url": "discard-me"}}
        result[f"repos/{repository}/pulls?state=open&per_page=100"] = ([{"number": index + 1, "state": "open", "draft": True, "head": {"sha": f"pr-{index}"}, "body": "discard-me"}] if open_pr else [])
        result[f"repos/{repository}/actions/runs?branch=main&per_page=100"] = {"workflow_runs": [{"updated_at": "2026-08-21T00:00:00Z", "conclusion": conclusion, "logs_url": "discard-me"}]}
    return result


class L1GitHubStatusSnapshotTests(unittest.TestCase):
    def collect(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        return snapshot.collect_snapshot(FakeTransport(responses(**kwargs)), observed_at="2026-08-21T01:02:03Z")

    def test_json_and_markdown_are_allowlisted_and_describe_the_same_status(self) -> None:
        result, succeeded = self.collect(open_pr=True, extras=True)
        report = snapshot.markdown_report(result)

        self.assertTrue(succeeded)
        self.assertEqual({"schema_version", "timestamp", "repositories", "overall_status"}, set(result))
        self.assertEqual("YELLOW", result["overall_status"])
        rendered_json = json.dumps(result)
        self.assertNotIn("discard-me", rendered_json + report)
        self.assertNotIn("token", rendered_json + report)
        for index, repository in enumerate(snapshot.REPOSITORIES):
            self.assertIn(repository, report)
            self.assertIn(f"sha-{index}", report)
            self.assertIn(f"pr-{index}", report)
        self.assertIn("YELLOW", report)

    def test_green_yellow_and_red_classification(self) -> None:
        green, green_succeeded = self.collect()
        yellow, yellow_succeeded = self.collect(open_pr=True)
        red, red_succeeded = self.collect(conclusion="failure")

        self.assertTrue(green_succeeded)
        self.assertEqual("GREEN", green["overall_status"])
        self.assertTrue(yellow_succeeded)
        self.assertEqual("YELLOW", yellow["overall_status"])
        self.assertTrue(red_succeeded)
        self.assertEqual("RED", red["overall_status"])

    def test_api_failure_is_generic_and_red_without_transport_details(self) -> None:
        result, succeeded = snapshot.collect_snapshot(FakeTransport({}), observed_at="2026-08-21T01:02:03Z")

        self.assertFalse(succeeded)
        self.assertEqual("RED", result["overall_status"])
        self.assertNotIn("error", json.dumps(result).lower())
        self.assertNotIn("stderr", snapshot.markdown_report(result).lower())
        self.assertEqual(5, len(result["repositories"]))

    def test_cli_api_failure_has_a_generic_error_and_sanitized_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_output = Path(directory) / "status.json"
            markdown_output = Path(directory) / "status.md"
            stderr = io.StringIO()
            with patch.object(snapshot, "GhTransport", return_value=FakeTransport({})):
                with redirect_stderr(stderr):
                    result = snapshot.main(
                        [
                            "--output",
                            str(json_output),
                            "--markdown-output",
                            str(markdown_output),
                        ]
                    )

            self.assertEqual(1, result)
            self.assertEqual("GitHub metadata collection was incomplete.\n", stderr.getvalue())
            self.assertNotIn("ignored", stderr.getvalue())
            self.assertEqual("RED", json.loads(json_output.read_text(encoding="utf-8"))["overall_status"])
            self.assertIn("**Overall:** RED", markdown_output.read_text(encoding="utf-8"))

    def test_existing_outputs_are_not_overwritten_without_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "snapshot.json"
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaises(snapshot.SnapshotError):
                snapshot.write_output(destination, "new", overwrite=False)
            self.assertEqual("existing", destination.read_text(encoding="utf-8"))
            snapshot.write_output(destination, "new", overwrite=True)
            self.assertEqual("new", destination.read_text(encoding="utf-8"))

    def test_repository_output_paths_are_rejected(self) -> None:
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.output_path(snapshot.REPOSITORY_ROOT / "status.json")

    def test_gh_transport_uses_only_api_and_discards_stderr(self) -> None:
        completed = type("Completed", (), {"returncode": 1, "stdout": b"", "stderr": b"secret stderr"})()
        with patch("scripts.l1_github_status_snapshot.subprocess.run", return_value=completed) as run:
            with self.assertRaises(snapshot.GitHubMetadataError) as raised:
                snapshot.GhTransport().get_json("repos/example")

        self.assertEqual("GitHub metadata request failed", str(raised.exception))
        self.assertEqual(["gh", "api", "repos/example"], run.call_args.args[0])
        self.assertNotIn("auth", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
