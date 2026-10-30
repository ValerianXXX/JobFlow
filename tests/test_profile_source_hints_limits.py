"""Additional source-hint limits and explicit commit/edit transitions."""
from __future__ import annotations

import unittest
from unittest import mock
from _support import project_temp
import test_profile_source_hints as fixture
from jobops.analysis_text_review import capture
from jobops.profile_source_hints import collect
from jobops.util import sha256_bytes


class ProfileSourceHintLimitsTests(unittest.TestCase):
    def hints(self, text):
        _, descriptor = capture(text, source_id="SRC-SYNTHETIC", source_hash=sha256_bytes(text.encode()), retained=True, segmented=False)
        return collect(text, descriptor, "SRC-SYNTHETIC")

    def test_repeated_value_is_one_hint_but_competing_urls_are_ambiguous(self):
        hints, _, summary = self.hints("PROFILE\nName: Jordan Lee\nEmail: candidate@example.test\nEmail: candidate@example.test\nhttps://one.example.test\nhttps://two.example.test\nEXPERIENCE\nRecorded work.")
        self.assertEqual(hints["email"], "candidate@example.test")
        self.assertNotIn("website_url", hints)
        self.assertIn("website_url", summary["ambiguous_fields"])

    def test_body_contact_labels_do_not_reopen_header_scope(self):
        hints, _, _ = self.hints("PROFILE\nName: Jordan Lee\nEXPERIENCE\nSupport analyst\nContact information\nEmail: employer@example.test")
        self.assertEqual(hints["candidate_display_name"], "Jordan Lee")
        self.assertNotIn("email", hints)

    def test_year_range_is_not_phone_and_unlabeled_single_name_is_unresolved(self):
        hints, _, _ = self.hints("林岚\n2021-2023\nEmail: candidate@example.test\nEXPERIENCE\nRecorded work.")
        self.assertNotIn("phone", hints)
        self.assertNotIn("candidate_display_name", hints)
        self.assertEqual(hints["email"], "candidate@example.test")

    def test_header_limit_cannot_scan_entire_document_for_identity(self):
        text = "\n" * 80 + "Name: Jordan Lee\nEmail: too-late@example.test"
        self.assertEqual(self.hints(text)[0], {})

    def test_section_words_inside_contact_values_are_not_headings(self):
        hints, _, _ = self.hints("Name: Jordan Lee\nEmail: projects@example.test\nhttps://skills.example.test\nEXPERIENCE\nRecorded work.")
        self.assertEqual(hints["email"], "projects@example.test")
        self.assertEqual(hints["website_url"], "https://skills.example.test")

    def test_commit_reanalysis_rebinds_same_value_but_not_new_value(self):
        text = "PROFILE\nName: Jordan Lee\nEmail: candidate@example.test\nEXPERIENCE\nRecorded work."
        changed = "PROFILE\n\nName: Jordan Lee\nEmail: candidate@example.test\nEXPERIENCE\nRecorded work."
        with project_temp() as root:
            harness = fixture.ProfileSourceHintsTests()
            self.addCleanup(harness.doCleanups)
            service, receipt, pending = harness.prepare(root, text)
            service.commit_source(receipt["source_id"], [])
            for value in (changed, changed.replace("candidate@example.test", "different@example.test")):
                with mock.patch.object(service, "_extract_text", return_value=(value, 0, {})), mock.patch.object(service.ai_engine, "analyze_document", return_value=([], pending["extraction_summary"])):
                    service.reprocess_source(receipt["source_id"])
                service.commit_source(receipt["source_id"], [])
                public = service.bootstrap()
                self.assertEqual(public["answers"]["email"]["value"], "candidate@example.test")
                expected = "AVAILABLE" if value == changed else "SOURCE_CHANGED"
                self.assertEqual(public["profile_hint_sources"]["email"]["status"], expected)
                self.assertEqual(public["profile_review"], "PENDING")

    def test_changed_answer_invalidates_profile_review_and_source_badge(self):
        with project_temp() as root:
            harness = fixture.ProfileSourceHintsTests()
            self.addCleanup(harness.doCleanups)
            service, receipt, _ = harness.prepare(root, "PROFILE\n姓名：林岚\nEmail: original@example.test\nEXPERIENCE\nRecorded work.")
            service.commit_source(receipt["source_id"], [])
            service.save_review({"profile_review": "CONFIRMED"})
            service.save_answers({"answers": {"email": {"value": "corrected@example.test", "status": "CONFIRMED", "use_policy": "reuse"}}})
            public = service.bootstrap()
            self.assertEqual(public["profile_review"], "PENDING")
            self.assertNotIn("email", public["profile_hint_sources"])

    def test_legacy_preview_without_attribution_does_not_fill_answers(self):
        with project_temp() as root:
            harness = fixture.ProfileSourceHintsTests()
            self.addCleanup(harness.doCleanups)
            service, receipt, _ = harness.prepare(root, "PROFILE\nName: Jordan Lee\nEmail: original@example.test\nEXPERIENCE\nRecorded work.")
            reference, state = service.ensure_state()
            state["pending_sources"][0]["metadata"].pop("_profile_hint_evidence")
            service._save_state(reference, state)
            result = service.commit_source(receipt["source_id"], [])
            self.assertEqual(result["profile_hints_applied"], 0)
            self.assertEqual(service.bootstrap()["answers"]["email"]["status"], "UNKNOWN")
            self.assertEqual(len(service.bootstrap()["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
