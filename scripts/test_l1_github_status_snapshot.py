"""Tests for the sanitized, read-only GitHub L1 status snapshot."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import l1_github_status_snapshot as snapshot


class FakeTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, endpoint: str) -> Any:
        self.calls.append(endpoint)
        response = self.responses.get(endpoint)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise snapshot.GitHubMetadataError("ignored")
        return response


def complete_responses(*, include_unexpected: bool = False) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for index, repository in enumerate(snapshot.REPOSITORIES):
        branch = "main"
        metadata: dict[str, Any] = {"default_branch": branch}
        if include_unexpected:
            metadata["owner"] = {"login": "unexpected-owner"}
            metadata["token"] = "unexpected-secret"
        responses[f"repos/{repository}"] = metadata
        responses[f"repos/{repository}/git/ref/heads/{branch}"] = {
            "object": {"sha": f"sha-{index}", "url": "unexpected-url"},
            "node_id": "unexpected-node",
        }
        responses[f"repos/{repository}/pulls?state=open&per_page=100"] = [
            {
                "number": index + 10,
                "state": "open",
                "draft": bool(index % 2),
                "head": {"sha": f"pr-sha-{index}", "ref": "unexpected-ref"},
                "body": "unexpected secret-like body",
            },
            {"number": 999, "state": "closed", "draft": False, "head": {"sha": "skip"}},
        ]
        responses[f"repos/{repository}/actions/runs?branch={branch}&per_page=100"] = {
            "workflow_runs": [
                {"updated_at": "2026-08-20T00:00:00Z", "conclusion": "failure"},
                {
                    "updated_at": "2026-08-21T00:00:00Z",
                    "conclusion": "success",
                    "logs_url": "unexpected-url",
                },
            ],
            "total_count": 2,
        }
    return responses


class L1GitHubStatusSnapshotTests(unittest.TestCase):
    def test_all_five_repositories_have_deterministic_allowlisted_shape(self) -> None:
        result, succeeded = snapshot.collect_snapshot(
            FakeTransport(complete_responses()), observed_at="2026-08-21T01:02:03Z"
        )

        self.assertTrue(succeeded)
        self.assertEqual(
            {"schema_version", "timestamp", "repositories"}, set(result)
        )
        self.assertEqual(snapshot.SCHEMA_VERSION, result["schema_version"])
        self.assertEqual(list(snapshot.REPOSITORIES), [item["repository"] for item in result["repositories"]])
        for index, item in enumerate(result["repositories"]):
            self.assertEqual(
                {"repository", "default_branch", "default_branch_sha", "open_pull_requests", "latest_ci"},
                set(item),
            )
            self.assertEqual("main", item["default_branch"])
            self.assertEqual(f"sha-{index}", item["default_branch_sha"])
            self.assertEqual(
                [{"number": index + 10, "state": "open", "draft": bool(index % 2), "head_sha": f"pr-sha-{index}"}],
                item["open_pull_requests"],
            )
            self.assertEqual(
                {"conclusion": "success", "timestamp": "2026-08-21T00:00:00Z"},
                item["latest_ci"],
            )

    def test_unexpected_and_secret_like_fields_are_discarded(self) -> None:
        result, succeeded = snapshot.collect_snapshot(
            FakeTransport(complete_responses(include_unexpected=True)),
            observed_at="2026-08-21T01:02:03Z",
        )

        self.assertTrue(succeeded)
        rendered = json.dumps(result)
        self.assertNotIn("unexpected", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("token", rendered)

    def test_existing_output_is_not_overwritten_without_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "snapshot.json"
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaises(snapshot.SnapshotError):
                snapshot.write_snapshot(destination, {"safe": True}, overwrite=False)
            self.assertEqual("existing", destination.read_text(encoding="utf-8"))
            snapshot.write_snapshot(destination, {"safe": True}, overwrite=True)
            self.assertEqual({"safe": True}, json.loads(destination.read_text(encoding="utf-8")))

    def test_partial_failures_have_no_error_or_transport_details(self) -> None:
        responses = complete_responses()
        repository = snapshot.REPOSITORIES[0]
        responses[f"repos/{repository}/git/ref/heads/main"] = snapshot.GitHubMetadataError(
            "raw stderr must not escape"
        )
        result, succeeded = snapshot.collect_snapshot(
            FakeTransport(responses), observed_at="2026-08-21T01:02:03Z"
        )

        self.assertFalse(succeeded)
        first = result["repositories"][0]
        self.assertIsNone(first["default_branch_sha"])
        self.assertEqual(
            [{"number": 10, "state": "open", "draft": False, "head_sha": "pr-sha-0"}],
            first["open_pull_requests"],
        )
        self.assertEqual(
            {"conclusion": "success", "timestamp": "2026-08-21T00:00:00Z"},
            first["latest_ci"],
        )
        self.assertNotIn("error", json.dumps(result).lower())
        self.assertNotIn("stderr", json.dumps(result).lower())

    def test_total_failures_are_sanitized_and_keep_the_full_repository_set(self) -> None:
        result, succeeded = snapshot.collect_snapshot(
            FakeTransport({}), observed_at="2026-08-21T01:02:03Z"
        )

        self.assertFalse(succeeded)
        self.assertEqual(5, len(result["repositories"]))
        for item in result["repositories"]:
            self.assertIsNone(item["default_branch"])
            self.assertIsNone(item["default_branch_sha"])
            self.assertEqual([], item["open_pull_requests"])
            self.assertEqual({"conclusion": None, "timestamp": None}, item["latest_ci"])

    def test_repository_output_paths_are_rejected(self) -> None:
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.output_path(snapshot.REPOSITORY_ROOT / "snapshot.json")

    def test_gh_transport_uses_only_api_and_discards_stderr(self) -> None:
        completed = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": b"", "stderr": b"sensitive stderr"},
        )()
        with patch(
            "scripts.l1_github_status_snapshot.subprocess.run",
            return_value=completed,
        ) as run:
            with self.assertRaises(snapshot.GitHubMetadataError) as raised:
                snapshot.GhTransport().get_json("repos/example")

        self.assertEqual("GitHub metadata request failed", str(raised.exception))
        self.assertEqual(
            ["gh", "api", "repos/example"],
            run.call_args.args[0],
        )
        self.assertNotIn("auth", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
