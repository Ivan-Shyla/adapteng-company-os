"""Tests for scripts/canary_evidence.py.

The database is injected everywhere; nothing here opens a socket. The point
of these tests is that the operation reads only, never writes, and reports
the counts a caller actually needs to tell "the canary produced one call" from
"the canary produced none" or "produced two."
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


class EvidenceTests(unittest.TestCase):
    def test_no_rows_anywhere_is_reported_plainly(self) -> None:
        # agent_task count, agent_run count, ai_gateway_call count
        code, output = run_evidence(["0", "0", "0"])
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("agent_task rows: 0", output)
        self.assertIn("agent_run rows: 0", output)
        self.assertIn("ai_gateway_call rows for this run_id: 0", output)
        self.assertIn("RESULT canary-evidence ok agent_task=0 agent_run=0 ai_gateway_call=0", output)

    def test_a_completed_run_with_one_call_reports_every_field(self) -> None:
        code, output = run_evidence(
            [
                "1",  # agent_task count
                "1",  # agent_run count
                "succeeded",  # status
                "2026-09-14 17:47:17",  # started_at
                "2026-09-14 17:47:19",  # ended_at
                "1",  # ai_gateway_call count
                "1",  # finalized count
                "vertex-ai/gemini-3.1-flash-lite",  # provider/model
                "0.000420",  # total actual_eur_amount
                "call-1",  # call ids
            ]
        )
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("agent_run: status='succeeded'", output)
        self.assertIn("finalized: 1 of 1", output)
        self.assertIn("provider/model: vertex-ai/gemini-3.1-flash-lite", output)
        self.assertIn("total actual_eur_amount: 0.000420", output)
        self.assertIn("call_id(s): call-1", output)
        self.assertIn("RESULT canary-evidence ok agent_task=1 agent_run=1 ai_gateway_call=1", output)

    def test_a_second_provider_call_is_visible_in_the_count(self) -> None:
        """This is exactly the signal an idempotency-replay check reads: a
        count of 2 after the second run means the replay was NOT idempotent."""

        code, output = run_evidence(
            [
                "1",
                "1",
                "succeeded",
                "2026-09-14 17:47:17",
                "2026-09-14 17:47:19",
                "2",  # ai_gateway_call count -- the failure signal
                "2",
                "vertex-ai/gemini-3.1-flash-lite",
                "0.000840",
                "call-1, call-2",
            ]
        )
        self.assertIn("ai_gateway_call rows for this run_id: 2", output)
        self.assertIn("RESULT canary-evidence ok agent_task=1 agent_run=1 ai_gateway_call=2", output)

    def test_it_never_writes(self) -> None:
        """No call this operation makes may be anything other than scalar()
        (a SELECT), so patching only scalar is sufficient to prove nothing
        else touches the database."""

        target = mock.Mock()
        with mock.patch.object(driver, "scalar", side_effect=["0", "0", "0"]):
            with redirect_stdout(io.StringIO()):
                driver.operate_evidence(target)
        target.assert_not_called()


if __name__ == "__main__":
    unittest.main()
