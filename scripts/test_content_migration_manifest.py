#!/usr/bin/env python3
"""The content migration manifest must stay safe to act on."""

from __future__ import annotations

import copy
import unittest

from scripts import content_migration_manifest as manifest_module


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = manifest_module.load()

    def test_committed_manifest_is_valid(self) -> None:
        self.assertEqual(manifest_module.validate(self.manifest), [])

    def test_no_folder_is_recorded_as_created(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["destination"]["folders_created_so_far"] = ["AE-CGR-0001_x"]
        self.assertTrue(manifest_module.validate(broken))

    def test_drive_url_is_rejected(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["rules"]["note"] = "https://drive." + "google.com/folder-link"
        self.assertTrue(manifest_module.validate(broken))

    def test_duplicate_legacy_key_is_rejected(self) -> None:
        broken = copy.deepcopy(self.manifest)
        items = broken["source_classes"][0]["items"]
        items[1]["legacy_key"] = items[0]["legacy_key"]
        self.assertTrue(any("duplicate legacy_key" in p for p in manifest_module.validate(broken)))

    def test_hand_chosen_article_id_must_be_unique_and_well_formed(self) -> None:
        broken = copy.deepcopy(self.manifest)
        items = broken["source_classes"][0]["items"]
        items[0]["ae_cgr_id"] = "AE-CGR-1"
        self.assertTrue(any("malformed" in p for p in manifest_module.validate(broken)))
        items[0]["ae_cgr_id"] = items[1]["ae_cgr_id"] = "AE-CGR-0007"
        self.assertTrue(any("duplicate ae_cgr_id" in p for p in manifest_module.validate(broken)))

    def test_copy_into_watched_inbox_is_rejected(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["source_classes"][0]["items"][0]["destination"] = "40_Content/01_Inbox"
        self.assertTrue(any("watched" in p for p in manifest_module.validate(broken)))

    def test_copy_outside_content_root_is_rejected(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["source_classes"][0]["items"][0]["destination"] = "30_Projects_Cases/x"
        self.assertTrue(any("40_Content" in p for p in manifest_module.validate(broken)))

    def test_unknown_action_is_rejected(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["source_classes"][0]["items"][0]["action"] = "move"
        self.assertTrue(manifest_module.validate(broken))

    def test_duplicate_repeat_counts_add_up(self) -> None:
        broken = copy.deepcopy(self.manifest)
        for source_class in broken["source_classes"]:
            if source_class.get("repeats"):
                source_class["count"] += 1
        self.assertTrue(any("repeats" in p for p in manifest_module.validate(broken)))

    def test_status_cannot_claim_execution(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["status"] = "executed"
        self.assertTrue(manifest_module.validate(broken))

    def test_workflows_are_never_activated_by_the_manifest(self) -> None:
        self.assertTrue(self.manifest["workflow_map"])
        broken = copy.deepcopy(self.manifest)
        broken["workflow_map"][0]["action"] = "activate"
        self.assertTrue(manifest_module.validate(broken))


class PlanCopyTests(unittest.TestCase):
    def candidate(self, file_id: str, key: str, sha: str) -> dict:
        return {"source_file_id": file_id, "legacy_key": key, "sha256": sha}

    def test_first_copy_is_planned(self) -> None:
        plan = manifest_module.plan_copy([self.candidate("f1", "a", "h1")])
        self.assertEqual(len(plan["copy"]), 1)
        self.assertEqual(plan["skip"], [])

    def test_same_content_for_same_article_is_copied_once(self) -> None:
        plan = manifest_module.plan_copy(
            [self.candidate("f1", "a", "h1"), self.candidate("f2", "a", "h1")]
        )
        self.assertEqual([c["source_file_id"] for c in plan["copy"]], ["f1"])
        self.assertEqual(plan["skip"][0]["reason"], "same article and content already copied")

    def test_repeated_source_file_id_is_skipped(self) -> None:
        plan = manifest_module.plan_copy(
            [self.candidate("f1", "a", "h1"), self.candidate("f1", "b", "h2")]
        )
        self.assertEqual(len(plan["copy"]), 1)
        self.assertEqual(plan["skip"][0]["reason"], "repeated source file id")

    def test_replay_after_a_completed_copy_plans_nothing(self) -> None:
        candidates = [self.candidate("f1", "a", "h1"), self.candidate("f2", "b", "h2")]
        first = manifest_module.plan_copy(candidates)
        done = {(c["legacy_key"], c["sha256"]) for c in first["copy"]}
        second = manifest_module.plan_copy(candidates, already_copied=done)
        self.assertEqual(second["copy"], [])
        self.assertEqual(len(second["skip"]), 2)

    def test_changed_content_for_same_article_is_a_new_copy(self) -> None:
        plan = manifest_module.plan_copy(
            [self.candidate("f1", "a", "h2")], already_copied={("a", "h1")}
        )
        self.assertEqual(len(plan["copy"]), 1)


if __name__ == "__main__":
    unittest.main()
