#!/usr/bin/env python3
"""Adversarial tests for the scheduler surfaces the inventory exporter measures.

These tests are POSIX-only by subject, not by choice, and they carry no skip
marker. ``scheduler_file_record`` opens with ``os.O_NOFOLLOW``, which does not
exist on Windows, and the fixtures need ``os.symlink``, which Windows refuses
without SeCreateSymbolicLinkPrivilege. So this file cannot execute on Windows
at all, which is also why ``scheduler_file_record`` had never been executed by
any test on any platform before it was written. Rather than hide that behind
``skipUnless`` - an invisible control - the POSIX-only cases live in their own
named module that ``.github/workflows/ci.yml`` runs unconditionally on
ubuntu-latest, the platform the exporter actually runs on.
"""

from __future__ import annotations

import inspect
import os
import pwd
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.postgres_restore_inventory_exporter as inventory_exporter
from scripts.postgres_restore_inventory_exporter import (
    SCHEDULER_ROOTS,
    ExporterError,
    record_sha256,
    scheduler_candidates,
    scheduler_file_record,
    scheduler_records,
    sha256_bytes,
    user_unit_roots,
    validate_capability_inventory,
)

UNIT = "[Service]\nExecStart=/usr/bin/pgbackrest\n"


def path_digest(path: Path) -> str:
    return sha256_bytes(path.as_posix().encode("utf-8"))


class SchedulerSurfaceRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sandbox = Path(directory.name).resolve()
        self.root = self.sandbox / "systemd-user"
        self.root.mkdir()
        self.outside = self.sandbox / "outside"
        self.outside.mkdir()

    def write(self, path: Path, payload: str = UNIT, mode: int = 0o644) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        path.chmod(mode)
        return path

    def walk(self, *roots: Path) -> list[dict[str, object]]:
        with patch.object(inventory_exporter, "command_bytes", lambda arguments: b""):
            return scheduler_records(
                set(),
                account_homes=set(),
                scheduler_roots=roots or (self.root,),
                run_user=self.sandbox / "absent-run-user",
            )

    def test_a_direct_regular_file_keeps_the_historical_record_shape(self) -> None:
        path = self.write(self.root / "approved.service")
        self.assertEqual(
            scheduler_file_record(path),
            {
                "source_type": "scheduler-file",
                "path_sha256": path_digest(path),
                "owner_uid": os.getuid(),
                "mode": 0o644,
                "content_sha256": sha256_bytes(UNIT.encode("utf-8")),
            },
        )

    def test_a_symlink_into_the_same_root_is_recorded_not_rejected(self) -> None:
        target = self.write(self.root / "real.service")
        link = self.root / "sockets.target.wants" / "real.service"
        link.parent.mkdir()
        link.symlink_to(target)
        self.assertEqual(
            scheduler_file_record(link),
            {
                "source_type": "scheduler-link",
                "path_sha256": path_digest(link),
                "redirect_kind": "symlink",
                "link_text_sha256": sha256_bytes(target.as_posix().encode("utf-8")),
                "resolved_path_sha256": path_digest(target),
                "resolved_state": "regular-file",
                "owner_uid": os.getuid(),
                "mode": 0o644,
                "content_sha256": sha256_bytes(UNIT.encode("utf-8")),
            },
        )

    def test_a_symlink_escaping_the_root_is_recorded_with_the_escape_visible(
        self,
    ) -> None:
        inside = self.write(self.root / "real.service")
        outside = self.write(self.outside / "real.service", "[Service]\nExecStart=/x\n")
        link = self.root / "enabled.service"
        link.symlink_to(inside)
        contained = scheduler_file_record(link)
        link.unlink()
        link.symlink_to(outside)
        escaped = scheduler_file_record(link)
        self.assertEqual(contained["path_sha256"], escaped["path_sha256"])
        self.assertEqual(escaped["resolved_path_sha256"], path_digest(outside))
        self.assertEqual(
            escaped["content_sha256"],
            sha256_bytes(b"[Service]\nExecStart=/x\n"),
        )
        self.assertNotEqual(record_sha256(contained), record_sha256(escaped))

    def test_a_relative_symlink_records_its_literal_text_and_resolved_target(
        self,
    ) -> None:
        target = self.write(self.outside / "relative.service")
        link = self.root / "relative.service"
        link.symlink_to(Path("../outside/relative.service"))
        record = scheduler_file_record(link)
        self.assertEqual(
            record["link_text_sha256"],
            sha256_bytes(b"../outside/relative.service"),
        )
        self.assertEqual(record["resolved_path_sha256"], path_digest(target))
        self.assertEqual(record["resolved_state"], "regular-file")

    def test_a_symlink_chain_resolves_to_and_records_its_final_target(self) -> None:
        target = self.write(self.outside / "final.service")
        middle = self.outside / "middle.service"
        middle.symlink_to(target)
        link = self.root / "first.service"
        link.symlink_to(middle)
        record = scheduler_file_record(link)
        self.assertEqual(
            record["link_text_sha256"],
            sha256_bytes(middle.as_posix().encode("utf-8")),
        )
        self.assertEqual(record["resolved_path_sha256"], path_digest(target))
        self.assertEqual(record["content_sha256"], sha256_bytes(UNIT.encode("utf-8")))

    def test_a_broken_symlink_is_recorded_as_an_absent_target(self) -> None:
        link = self.root / "broken.service"
        link.symlink_to(self.outside / "never-created.service")
        record = scheduler_file_record(link)
        self.assertEqual(record["resolved_state"], "absent")
        self.assertNotIn("content_sha256", record)
        self.assertEqual(
            record["resolved_path_sha256"],
            path_digest(self.outside / "never-created.service"),
        )

    def test_a_masked_unit_and_a_directory_link_are_recorded_as_non_regular(
        self,
    ) -> None:
        masked = self.root / "masked.service"
        masked.symlink_to(Path("/dev/null"))
        directory = self.root / "drop-in.d"
        directory.symlink_to(self.outside)
        for link in (masked, directory):
            record = scheduler_file_record(link)
            self.assertEqual(record["resolved_state"], "not-a-regular-file")
            self.assertNotIn("content_sha256", record)

    def test_a_symlink_loop_fails_closed(self) -> None:
        first = self.root / "loop-a.service"
        second = self.root / "loop-b.service"
        first.symlink_to(second)
        second.symlink_to(first)
        with self.assertRaises(ExporterError):
            scheduler_file_record(first)

    def test_a_group_or_world_writable_scheduler_file_still_fails_closed(self) -> None:
        direct = self.write(self.root / "writable.service", mode=0o666)
        target = self.write(self.outside / "writable.service", mode=0o664)
        link = self.root / "writable-link.service"
        link.symlink_to(target)
        for path in (direct, link):
            with self.assertRaises(ExporterError):
                scheduler_file_record(path)

    def test_a_parent_directory_redirect_is_recorded_with_the_resolved_path(
        self,
    ) -> None:
        target = self.write(self.outside / "hidden.service")
        redirected_root = self.sandbox / "cron.d"
        redirected_root.symlink_to(self.outside)
        records = self.walk(redirected_root)
        by_path = {record["path_sha256"]: record for record in records}
        self.assertEqual(
            by_path[path_digest(redirected_root)]["resolved_state"],
            "not-a-regular-file",
        )
        child = by_path[path_digest(redirected_root / "hidden.service")]
        self.assertEqual(child["redirect_kind"], "parent-symlink")
        self.assertEqual(child["link_text_sha256"], sha256_bytes(b""))
        self.assertEqual(child["resolved_path_sha256"], path_digest(target))
        self.assertEqual(child["resolved_state"], "regular-file")

    def test_the_walk_records_every_file_and_symlink_under_an_injected_root(
        self,
    ) -> None:
        plain = self.write(self.root / "plain.service")
        nested = self.write(self.root / "nested" / "deep.service")
        link = self.root / "enabled.service"
        link.symlink_to(plain)
        broken = self.root / "broken.service"
        broken.symlink_to(self.outside / "absent.service")
        records = self.walk()
        self.assertEqual(
            sorted(record["path_sha256"] for record in records),
            sorted(path_digest(path) for path in (broken, link, nested, plain)),
        )

    def test_an_unapproved_redirect_is_rejected_by_the_capability_inventory(
        self,
    ) -> None:
        approved_target = self.write(self.root / "real.service")
        link = self.root / "enabled.service"
        link.symlink_to(approved_target)
        approved = self.walk()
        capability = {
            "allowed_scheduler_sources": sorted(
                record_sha256(record) for record in approved
            ),
            "allowed_containers": [],
            "allowed_writer_processes": [],
        }
        self.assertEqual(
            validate_capability_inventory(
                scheduler_records=approved,
                container_records=[],
                writer_process_records=[],
                capability=capability,
            )["unclassified_capability_surfaces"],
            0,
        )
        backdoor = self.write(self.outside / "backdoor.service", "[Service]\nX=1\n")
        link.unlink()
        link.symlink_to(backdoor)
        with self.assertRaises(ExporterError):
            validate_capability_inventory(
                scheduler_records=self.walk(),
                container_records=[],
                writer_process_records=[],
                capability=capability,
            )

    def test_the_default_scheduler_roots_stay_the_absolute_host_roots(self) -> None:
        self.assertIs(
            inspect.signature(scheduler_records).parameters["scheduler_roots"].default,
            SCHEDULER_ROOTS,
        )
        self.assertEqual(len(SCHEDULER_ROOTS), 32)
        self.assertTrue(all(root.is_absolute() for root in SCHEDULER_ROOTS))
        for root in (
            Path("/etc/crontab"),
            Path("/etc/cron.d"),
            Path("/var/spool/cron/crontabs"),
            Path("/etc/systemd/user"),
            Path("/usr/lib/systemd/user"),
            Path("/run/systemd/user-generators"),
        ):
            self.assertIn(root, SCHEDULER_ROOTS)


class RealHostSchedulerSurfaceTests(unittest.TestCase):
    def host_walk(self) -> list[Path]:
        homes = {
            Path(account.pw_dir)
            for account in pwd.getpwall()
            if account.pw_dir.startswith("/")
        }
        return [
            path
            for root in (*SCHEDULER_ROOTS, *user_unit_roots(homes))
            for path in scheduler_candidates(root)
        ]

    def test_no_real_scheduler_surface_is_writable_by_a_non_owner(self) -> None:
        # The precondition the exporter enforces, asserted separately so that a
        # host which violates it names the offending path instead of stopping
        # the walk with an opaque error. Reported as a list, not first-failure,
        # so one run shows every offender.
        offenders = []
        for path in self.host_walk():
            try:
                info = os.stat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mode & 0o022:
                offenders.append(
                    f"{path} -> {path.resolve()} "
                    f"uid={info.st_uid} gid={info.st_gid} "
                    f"mode={stat.S_IMODE(info.st_mode):04o}"
                )
        self.assertEqual(sorted(offenders), [])

    def test_scheduler_records_completes_against_the_real_host_roots(self) -> None:
        # The regression guard for the whole class: no injected roots, no
        # sandbox, the production call from main() with only systemctl stubbed.
        # It fails with "scheduler source contains a symlink chain" on any
        # ordinary Linux host before the fix, because enabled units are
        # symlinks. It runs as the unprivileged CI user, so directories only
        # root can read - /var/spool/cron/crontabs - are silently skipped by
        # rglob and stay unexercised here.
        with patch.object(inventory_exporter, "command_bytes", lambda arguments: b""):
            records = scheduler_records(set())
        walked = self.host_walk()
        self.assertNotEqual(walked, [])
        self.assertEqual(
            sorted(record["path_sha256"] for record in records),
            sorted(path_digest(path) for path in walked),
        )
        self.assertNotEqual([path for path in walked if path.is_symlink()], [])
        self.assertEqual(
            sorted(
                record["path_sha256"]
                for record in records
                if record["source_type"] == "scheduler-link"
            ),
            sorted(
                path_digest(path)
                for path in walked
                if path.is_symlink() or path.resolve() != path
            ),
        )
        for record in records:
            self.assertRegex(record["path_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(
                record["source_type"], {"scheduler-file", "scheduler-link"}
            )


if __name__ == "__main__":
    unittest.main()
