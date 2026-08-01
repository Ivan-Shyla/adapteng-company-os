#!/usr/bin/env python3
"""Real-host regression guard for the scheduler surfaces the exporter measures.

This module is POSIX-only by subject and carries no skip marker. It exercises
the production call that main() makes at
scripts/postgres_restore_inventory_exporter.py, with only systemctl stubbed, so
the filesystem walk over the exporter's hardcoded absolute host roots is real.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.postgres_restore_inventory_exporter as inventory_exporter


class RealHostSchedulerSurfaceTests(unittest.TestCase):
    def test_scheduler_records_completes_against_the_real_host_roots(self) -> None:
        with patch.object(inventory_exporter, "command_bytes", lambda arguments: b""):
            records = inventory_exporter.scheduler_records(set())
        self.assertNotEqual(records, [])
        for record in records:
            self.assertRegex(record["path_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
