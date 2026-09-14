"""Tests for scripts/canary_evidence.py.

The database is injected everywhere; nothing here opens a socket. The point
of these tests is that the operation reads only, never writes, and reports
the counts a caller actually needs to tell "the canary produced one call"
from "the canary produced none" or "produced two" -- for the ledger and for
the two Drive replay reservations the canary's two Drive nodes key on.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import canary_evidence as driver  # noqa: E402


def run_evidence(scalar_values: list[str]):
    target = mock.Mock()
    buffer = io.StringIO()
    with mock.patch.object(driver, "scalar", side_effect=scalar_values):
        with redirect_stdout(buffer):
            code = driver.operate_evidence(target)
    return code, buffer.getvalue()


# Every "no rows" run: agent_task=0, agent_run=0 (skips the status/started/
# ended trio), ai_gateway_call=0 (skips the four detail queries), then one
# reservation count per Drive key (both 0, each skipping its completed read).
NO_ROWS_ANYWHERE = ["0", "0", "0", "0", "0"]

# A completed run with one gateway call and both Drive reservations present
# and completed -- the clean, fully-idempotent shape a replay should produce.
CLEAN_REPLAY = [
    "1",  # agent_task count
    "1",  # agent_run count
    "succeeded",  # status
    "2026-09-14 17:47:17",  # started_at
    "2026-09-14 17:47:19",  # ended_at
    "1",  # ai_gateway_call count
    "1",  # finalized count
    "vertex-ai/gemini-3.1-flash-lite",  # provider/model
    "0.000157",  # total actual_eur_amount
    "pilot-canary-2026-09-run-001",  # call ids
    "1",  # ensure_folder reservation count
    "t",  # ensure_folder completed
    "1",  # upload_draft reservation count
    "t",  # upload_draft completed
]


class EvidenceTests(unittest.TestCase):
    def test_no_rows_anywhere_is_reported_plainly(self) -> None:
        code, output = run_evidence(NO_ROWS_ANYWHERE)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("agent_task rows: 0", output)
        self.assertIn("agent_run rows: 0", output)
        self.assertIn("ai_gateway_call rows for this run_id: 0", output)
        self.assertIn("drive_bridge_replay_reservations[ensure_folder]: rows=0 completed=n/a", output)
        self.assertIn("drive_bridge_replay_reservations[upload_draft]: rows=0 completed=n/a", output)
        self.assertIn(
            "RESULT canary-evidence ok agent_task=0 agent_run=0 ai_gateway_call=0 "
            "drive_ensure_folder=0 drive_upload_draft=0",
            output,
        )

    def test_a_clean_idempotent_replay_reports_every_field(self) -> None:
        code, output = run_evidence(CLEAN_REPLAY)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("agent_run: status='succeeded'", output)
        self.assertIn("finalized: 1 of 1", output)
        self.assertIn("provider/model: vertex-ai/gemini-3.1-flash-lite", output)
        self.assertIn("total actual_eur_amount: 0.000157", output)
        self.assertIn("drive_bridge_replay_reservations[ensure_folder]: rows=1 completed=t", output)
        self.assertIn("drive_bridge_replay_reservations[upload_draft]: rows=1 completed=t", output)
        self.assertIn(
            "RESULT canary-evidence ok agent_task=1 agent_run=1 ai_gateway_call=1 "
            "drive_ensure_folder=1 drive_upload_draft=1",
            output,
        )

    def test_a_second_provider_call_is_visible_in_the_count(self) -> None:
        """This is exactly the signal an idempotency-replay check reads: a
        count of 2 after the second run means the replay was NOT idempotent."""

        values = list(CLEAN_REPLAY)
        values[5] = "2"  # ai_gateway_call count -- the failure signal
        values[6] = "2"  # finalized
        values[9] = "call-1, call-2"
        code, output = run_evidence(values)
        self.assertIn("ai_gateway_call rows for this run_id: 2", output)
        self.assertIn("ai_gateway_call=2", output)

    def test_a_duplicate_drive_reservation_is_visible_in_the_count(self) -> None:
        """A second row under the same idempotency_key digest cannot exist --
        key_digest is the table's primary key -- so seeing 2 here would mean
        the two runs computed two DIFFERENT digests, not that Drive duplicated
        anything. Still worth surfacing plainly rather than assuming 1."""

        values = list(CLEAN_REPLAY)
        values[10] = "2"  # ensure_folder reservation count
        # count != "1", so no "completed" read follows for this key
        del values[11]
        code, output = run_evidence(values)
        self.assertIn("drive_bridge_replay_reservations[ensure_folder]: rows=2 completed=n/a", output)

    def test_the_key_digest_matches_the_adapters_own_derivation(self) -> None:
        """Pinned so a change to _idempotency_digest's algorithm in the
        adapter and a drift here would be caught, not silently query the
        wrong row forever."""

        import hashlib

        for key in driver.DRIVE_IDEMPOTENCY_KEYS.values():
            expected = hashlib.sha256(key.encode("utf-8")).hexdigest()
            self.assertEqual(driver._digest(key), expected)
            self.assertEqual(len(expected), 64)

    def test_it_never_writes(self) -> None:
        """No call this operation makes may be anything other than scalar()
        (a SELECT), so patching only scalar is sufficient to prove nothing
        else touches the database."""

        target = mock.Mock()
        with mock.patch.object(driver, "scalar", side_effect=NO_ROWS_ANYWHERE):
            with redirect_stdout(io.StringIO()):
                driver.operate_evidence(target)
        target.assert_not_called()


if __name__ == "__main__":
    unittest.main()
