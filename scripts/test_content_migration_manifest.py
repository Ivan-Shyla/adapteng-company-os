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

    def test_recurring_writer_must_stay_accounted_for(self) -> None:
        broken = copy.deepcopy(self.manifest)
        del broken["recurring_writer"]
        self.assertTrue(any("recurring_writer" in p for p in manifest_module.validate(broken)))

    def test_protected_fields_cannot_be_dropped(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["recurring_writer"]["protected_fields"] = ["status"]
        self.assertTrue(any("protected_fields" in p for p in manifest_module.validate(broken)))

    def test_writer_cannot_be_called_proven_with_open_confirmations(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["recurring_writer"]["status"] = "proven"
        self.assertTrue(any("proven" in p for p in manifest_module.validate(broken)))

    def test_plan_cannot_claim_completion_while_gates_are_open(self) -> None:
        self.assertTrue(any(not g["satisfied"] for g in self.manifest["completion_gates"]))
        broken = copy.deepcopy(self.manifest)
        broken["status"] = "complete"
        self.assertTrue(any("completion gate" in p for p in manifest_module.validate(broken)))

    def test_mm20_is_in_the_workflow_map(self) -> None:
        names = [w["workflow"] for w in self.manifest["workflow_map"]]
        self.assertIn("MM-20 Publication Scout", names)

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


class ProcessedStateTests(unittest.TestCase):
    discovered = {
        "dedupe_key": "k1",
        "pub_id": "CROSSREF_10_1_x",
        "status": "candidate_for_fetch",
        "drive_file_id": "",
        "analysis_id": "",
        "relevance_score": 90,
    }

    def test_new_publication_is_inserted(self) -> None:
        self.assertEqual(manifest_module.rediscovery_row(None, self.discovered), self.discovered)

    def test_rediscovery_never_writes_an_existing_row(self) -> None:
        processed = {**self.discovered, "status": "approved_for_analysis", "drive_file_id": "f1", "analysis_id": "a1"}
        self.assertIsNone(manifest_module.rediscovery_row(processed, self.discovered))

    def test_current_upsert_mapping_is_flagged(self) -> None:
        columns = {name: "={{ $json." + name + " }}" for name in self.discovered}
        self.assertEqual(
            manifest_module.protected_field_mappings(columns),
            ["status", "drive_file_id", "analysis_id"],
        )

    def test_reviewed_mapping_is_clean(self) -> None:
        columns = {"dedupe_key": "x", "pub_id": "y", "relevance_score": "z"}
        self.assertEqual(manifest_module.protected_field_mappings(columns), [])

    def test_source_package_is_written_once(self) -> None:
        row = {"pub_id": "CROSSREF_10_1_x", "drive_file_id": ""}
        self.assertTrue(manifest_module.should_write_source_package(row, set()))
        self.assertFalse(manifest_module.should_write_source_package({**row, "drive_file_id": "f1"}, set()))
        self.assertFalse(
            manifest_module.should_write_source_package(row, {"CROSSREF_10_1_x__title.txt"})
        )

    def test_fan_out_candidates_are_reduced_to_one_per_publication(self) -> None:
        rows = [{"pub_id": "A"}, {"pub_id": "B"}, {"pub_id": "A"}, {"pub_id": "B"}, {"pub_id": ""}, {}]
        self.assertEqual(
            manifest_module.dedupe_candidates(rows), [{"pub_id": "A"}, {"pub_id": "B"}]
        )

    def test_row_without_pub_id_is_never_written(self) -> None:
        self.assertFalse(manifest_module.should_write_source_package({"drive_file_id": ""}, set()))


if __name__ == "__main__":
    unittest.main()
