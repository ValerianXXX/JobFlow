"""Frozen synthetic identity attribution and encrypted source lifecycle contracts."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from _support import project_temp
import test_onboarding_center as support
from jobops.ai_runtime import AI_QUALITY_CONTRACT
from jobops.onboarding_center import OnboardingCenterService
from jobops.resume_onboarding import parse_resume
from jobops.util import sha256_bytes


class ProfileSourceHintsTests(unittest.TestCase):
    def prepare(self, root, text, *, service=None, segmented=False):
        if service is None:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            self.addCleanup(service.close)
        summary = {"analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "quality_contract": AI_QUALITY_CONTRACT,
                   "ai_chunks": 1, "ai_input_characters": len(text), "ai_covered_characters": len(text),
                   "ai_input_truncated": False, "segmented_oversize_lines": int(segmented)}
        with mock.patch.object(service.ai_engine, "analyze_document", return_value=([], summary)):
            receipt = service.preview_source("resume", ".txt", text.encode())
        _, state = service.ensure_state()
        pending = next(p for p in state["pending_sources"] if p["source_id"] == receipt["source_id"])
        return service, receipt, pending

    def test_explicit_multilingual_display_names_survive_without_invented_parts(self):
        for text, expected in (
            ("PROFILE\nName: Synthetic Candidate 001\nEmail: candidate001@example.test\nEXPERIENCE\nDocumented reporting work.", "Synthetic Candidate 001"),
            ("个人信息\n姓名：林岚\n邮箱：linlan@example.test\n工作经历\n维护有来源的业务记录。", "林岚"),
            ("PROFILE\n姓名：陈岚 / Synthetic 003\nEmail: mixed003@example.test\nPROJECTS\nSource-grounded synthetic portfolio.", "陈岚 / Synthetic 003"),
        ):
            with self.subTest(expected=expected), project_temp() as root:
                service, receipt, pending = self.prepare(root, text)
                self.assertEqual(pending["profile_hints"].get("candidate_display_name"), expected)
                self.assertNotIn("first_name", pending["profile_hints"])
                self.assertNotIn("last_name", pending["profile_hints"])
                service.commit_source(receipt["source_id"], [])
                _, state = service.ensure_state()
                self.assertEqual(state["answers"]["candidate_display_name"]["value"], expected)
                profile = service._build_profile(state, "secure-ref:SYNTHETIC_PROFILE_01")
                self.assertEqual(profile["candidate_display_name"], expected)
                self.assertNotIn("first_name", profile)
                self.assertNotIn("last_name", profile)

    def test_header_preserves_middle_name_and_scopes_contacts(self):
        text = ("Jordan Alex Lee\njordan.lee@example.test | +1 555 010 0200\n"
                "https://github.com/jordan-lee\nProfessional Summary\nSource-grounded analysis work.\n"
                "References\nReferee: Morgan Example\nreferee@example.test\nhttps://employer.example.test")
        with project_temp() as root:
            service, receipt, pending = self.prepare(root, text)
            hints = pending["profile_hints"]
            self.assertEqual(hints.get("candidate_display_name"), "Jordan Alex Lee")
            self.assertEqual(hints.get("email"), "jordan.lee@example.test")
            self.assertEqual(hints.get("first_name"), "Jordan")
            self.assertNotIn("website_url", hints)
            service.commit_source(receipt["source_id"], [])
            _, state = service.ensure_state()
            self.assertEqual(service._build_profile(state, "secure-ref:SYNTHETIC_PROFILE_01")["candidate_display_name"], "Jordan Alex Lee")
            # Broad privacy detection must still recognize third-party contacts.
            self.assertIn("referee@example.test", parse_resume(text)["contact_values"]["email"])

    def test_job_title_and_reference_only_contact_are_not_applicant_identity(self):
        for text in (
            "Senior Operations Analyst\nProfessional Summary\nBuilt an evidence review process.\nReferences\nEmail: referee@example.test",
            "工作经历\n分析师，示例公司\n推荐人\n姓名：王明\n邮箱：referee@example.test",
            "EXPERIENCE\nSupport analyst\nEmployer contact: office@example.test\nName: Employer Representative",
        ):
            with self.subTest(text=text), project_temp() as root:
                _, _, pending = self.prepare(root, text)
                self.assertEqual(pending["profile_hints"], {})

    def test_ambiguous_header_values_remain_unresolved(self):
        for body in (
            "Name: Jordan Lee\nName: Morgan Chen\nEmail: first@example.test\nEmail: second@example.test",
            "姓名：林岚\n姓名：王明\n邮箱：first@example.test / second@example.test",
        ):
            with self.subTest(body=body), project_temp() as root:
                _, _, pending = self.prepare(root, "PROFILE\n" + body + "\nEXPERIENCE\nDocumented reporting work.")
                hints = pending["profile_hints"]
                self.assertNotIn("candidate_display_name", hints)
                self.assertNotIn("first_name", hints)
                self.assertNotIn("email", hints)

    def test_provenance_is_bound_and_survives_reopen_without_bootstrap_transcript(self):
        text = "PROFILE\nName: Synthetic Candidate 001\nEmail: candidate001@example.test\nEXPERIENCE\nDocumented work."
        with project_temp() as root:
            service, receipt, pending = self.prepare(root, text)
            before = service.bootstrap()
            self.assertEqual(before["answers"]["email"]["status"], "UNKNOWN")
            self.assertNotIn("profile_hints", before["pending_sources"][0])
            self.assertNotIn("_profile_hint_evidence", before["pending_sources"][0]["metadata"])
            service.commit_source(receipt["source_id"], [])
            service.close()
            reopened = OnboardingCenterService(service.project, service.database, service.onboarding, ai_engine=service.ai_engine)
            self.addCleanup(reopened.close)
            public = reopened.bootstrap()
            origin = public["profile_hint_sources"]["email"]
            self.assertEqual(origin["status"], "AVAILABLE")
            self.assertEqual(origin["source_id"], receipt["source_id"])
            self.assertEqual(origin["source_sha256"], sha256_bytes(text.encode()))
            self.assertEqual((origin["line_start"], origin["line_end"]), (3, 3))
            self.assertNotIn("value_sha256", origin)
            view = reopened.source_review_text({"source_id": origin["source_id"], "expected_source_sha256": origin["source_sha256"],
                "expected_text_sha256": origin["text_sha256"], "line_start": origin["line_start"]})
            self.assertEqual(view["lines"][0]["text"], text.splitlines()[2])
            self.assertNotIn(text, json.dumps(public, ensure_ascii=False))
            self.assertEqual(public["profile_review"], "PENDING")
            self.assertFalse(public["external_claim_approval"]["current"])
            self.assertEqual(public["claims"], before["claims"])
            self.assertEqual(public["answers"]["work_authorization"]["status"], "UNKNOWN")

    def test_manual_answer_is_not_overwritten_or_misattributed(self):
        with project_temp() as root:
            service, _, _ = self.prepare(root, "PROFILE\nName: Jordan Lee\nEmail: source@example.test\nEXPERIENCE\nRecorded work.")
            service.save_answers({"answers": {"email": {"value": "manual@example.test", "status": "CONFIRMED", "use_policy": "reuse"}}})
            service.commit_source(service.bootstrap()["pending_sources"][0]["source_id"], [])
            public = service.bootstrap()
            self.assertEqual(public["answers"]["email"]["value"], "manual@example.test")
            self.assertNotIn("email", public.get("profile_hint_sources", {}))
            service.save_answers({"answers": {"candidate_display_name": {"value": "Applicant Chosen Name", "status": "CONFIRMED", "use_policy": "reuse"}}})
            public = service.bootstrap()
            self.assertNotIn("candidate_display_name", public.get("profile_hint_sources", {}))
            _, state = service.ensure_state()
            self.assertEqual(service._build_profile(state, "secure-ref:SYNTHETIC_PROFILE_01")["candidate_display_name"], "Applicant Chosen Name")

    def test_source_deletion_revokes_only_unreviewed_imported_values(self):
        with project_temp() as root:
            service, receipt, _ = self.prepare(root, "PROFILE\n姓名：林岚\nEmail: applicant@example.test\nEXPERIENCE\nRecorded work.")
            service.commit_source(receipt["source_id"], [])
            service.save_answers({"answers": {"email": {"value": "applicant@example.test", "status": "CONFIRMED", "use_policy": "reuse"}}})
            service.delete_source(receipt["source_id"], user_confirmed=True)
            public = service.bootstrap()
            self.assertEqual(public["answers"]["candidate_display_name"]["status"], "UNKNOWN")
            self.assertEqual(public["answers"]["email"]["value"], "applicant@example.test")
            self.assertEqual(public["answers"]["email"]["source"], "APPLICANT_CONFIRMED")
            self.assertEqual(public.get("profile_hint_sources", {}), {})

    def test_reanalysis_invalidates_coordinate_links_until_discard_or_commit(self):
        text = "PROFILE\nName: Jordan Lee\nEmail: applicant@example.test\nEXPERIENCE\nRecorded work."
        changed = "PROFILE\nName: Jordan Lee\n\nEmail: applicant@example.test\nEXPERIENCE\nRecorded work."
        with project_temp() as root:
            service, receipt, pending = self.prepare(root, text)
            service.commit_source(receipt["source_id"], [])
            summary = pending["extraction_summary"]
            with mock.patch.object(service, "_extract_text", return_value=(changed, 0, {})), mock.patch.object(service.ai_engine, "analyze_document", return_value=([], summary)):
                service.reprocess_source(receipt["source_id"])
            self.assertEqual(service.bootstrap()["profile_hint_sources"]["email"]["status"], "SOURCE_CHANGED")
            service.discard_source_preview(receipt["source_id"])
            self.assertEqual(service.bootstrap()["profile_hint_sources"]["email"]["status"], "AVAILABLE")

    def test_unstable_analysis_coordinates_do_not_seed_untraceable_hints(self):
        with project_temp() as root:
            _, _, pending = self.prepare(root, "PROFILE\nName: Jordan Lee\nEmail: applicant@example.test\nEXPERIENCE\nRecorded work.", segmented=True)
            self.assertEqual(pending["profile_hints"], {})


if __name__ == "__main__":
    unittest.main()
