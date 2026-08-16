from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobops.resume_onboarding import (
    ResumeCandidate,
    ResumeOnboardingManager,
    _answer_bank,
    _metric_signatures,
    _numeric_conflict,
    _profile_draft,
    discover_resume_candidates,
    parse_resume,
    select_resume,
    validate_claim_candidate_evidence,
)
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import iso_utc, sha256_bytes, sha256_file


class ResumeOnboardingTests(unittest.TestCase):
    def test_discovery_is_bounded_filtered_deduplicated_and_pdf_only_continues(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = b"%PDF-1.4\nsynthetic fixture\n%%EOF\n"
            chosen = root / "Resume 2026 Aug.pdf"
            chosen.write_bytes(payload)
            (root / "Resume 2026 August copy.pdf").write_bytes(payload)
            (root / "Cover Letter 2026 Aug.pdf").write_bytes(b"excluded")
            (root / "Resume 2026 Jul.pdf").write_bytes(b"old")
            deep = root / "one" / "two" / "three"
            deep.mkdir(parents=True)
            (deep / "Resume 2026 Aug.pdf").write_bytes(b"too deep")

            candidates = discover_resume_candidates(root, max_depth=2)
            selected, paired, ambiguous = select_resume(candidates)

            self.assertEqual(len(candidates), 1)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.source_type, "pdf")
            self.assertIs(selected, paired)
            self.assertEqual(ambiguous, [])

    def test_docx_is_preferred_even_when_pdf_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            docx = root / "Resume 2026 Aug.docx"
            pdf = root / "Resume 2026 Aug.pdf"
            docx.write_bytes(b"not-a-real-docx-but-selection-is-safe")
            pdf.write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n")
            os.utime(docx, (1_700_000_000, 1_700_000_000))
            os.utime(pdf, (1_800_000_000, 1_800_000_000))

            selected, paired, ambiguous = select_resume(discover_resume_candidates(root))

            self.assertTrue(selected.path.samefile(docx))
            self.assertTrue(paired.path.samefile(pdf))
            self.assertEqual(ambiguous, [])

    def test_resume_facts_are_unconfirmed_and_missing_answers_remain_unknown(self) -> None:
        resume = parse_resume(
            "Synthetic Candidate\nsynthetic@example.test\nProfessional Summary\n"
            "Built a synthetic local fixture.\nSkills\nPython, SQL\n"
        )
        self.assertTrue(resume["facts"])
        self.assertTrue(all(item["status"] == "APPLICANT_PROVIDED_UNCONFIRMED" for item in resume["facts"]))

        created_at = iso_utc()
        profile = _profile_draft(resume, "secure-ref:SYNTHETIC01", created_at)
        answers = _answer_bank(created_at)
        self.assertTrue(all(value["status"] == "UNKNOWN" for value in profile["hard_conditions"].values()))
        self.assertTrue(all(value["status"] == "UNKNOWN" for group in answers["groups"].values() for value in group.values()))

    def test_single_word_header_is_not_a_name_and_layout_hint_is_used(self) -> None:
        without_hint = parse_resume("ANALYTICS\nProfessional Summary\nSynthetic fixture text")
        with_hint = parse_resume(
            "ANALYTICS\nProfessional Summary\nSynthetic fixture text",
            candidate_name_hint="Synthetic Candidate",
        )
        self.assertIsNone(without_hint["candidate_display_name"])
        self.assertEqual(with_hint["candidate_display_name"], "Synthetic Candidate")

    def test_complete_us_address_is_kept_as_one_resume_contact_value(self) -> None:
        resume = parse_resume(
            "Synthetic Candidate\nsynthetic@example.test\n"
            "100 Example Avenue, New York, NY 10001, United States\n"
            "Professional Summary\nSynthetic fixture text\n"
        )

        self.assertEqual(
            resume["contact_values"]["address"],
            ["100 Example Avenue, New York, NY 10001, United States"],
        )

    def test_claim_candidate_evidence_is_hash_and_approval_gated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence = Path(raw) / "evidence.md"
            evidence.write_text("# Evidence\nSynthetic completed case.", encoding="utf-8")

            class Gateway:
                def safe_path(self, source_id, relative):
                    self.assert_source = source_id
                    return evidence

                def read_text(self, source_id, relative):
                    return evidence.read_text(encoding="utf-8")

            excerpt = "Synthetic completed case."
            bundle = {
                "claims": [{
                    "approved_for_external": False, "approval_required": True,
                    "supporting_evidence": [{
                        "source_id": "personal_redacted", "relative_path": "evidence.md",
                        "heading": "Evidence", "excerpt": excerpt,
                        "excerpt_fingerprint": sha256_bytes(excerpt.encode("utf-8")),
                        "fingerprint": sha256_file(evidence),
                    }],
                }]
            }
            self.assertEqual(validate_claim_candidate_evidence(bundle, Gateway())["verified_anchors"], 1)
            bundle["claims"][0]["approved_for_external"] = True
            with self.assertRaises(JobOpsError):
                validate_claim_candidate_evidence(bundle, Gateway())

    def test_metric_units_do_not_consume_the_first_letter_of_words(self) -> None:
        metrics = _metric_signatures("Built a 4,000 merchant database")
        self.assertEqual(metrics, {"merchant": {"4000"}})
        self.assertFalse(_numeric_conflict(
            "Built a 4,000 merchant database",
            "Implemented 27 methods and 37 routes in an unrelated API project.",
        ))

    def test_prepare_failure_cleans_staging_and_new_private_references(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            selected_path = root / "Resume 2026 Aug.pdf"
            selected_path.write_bytes(b"not a valid PDF")
            candidate = ResumeCandidate(
                path=selected_path,
                source_type="pdf",
                sha256=sha256_file(selected_path),
                size_bytes=selected_path.stat().st_size,
                modified_at="2026-08-13T00:00:00Z",
                filename_score=10,
                completeness_score=1,
                page_hint=1,
                date_count=1,
                structure_count=1,
            )
            database = JobOpsDB(root / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            onboarding = PrivateOnboarding(
                database,
                WindowsDPAPIStore(script, local_app_data=root / "localappdata"),
            )
            manager = ResumeOnboardingManager(project, database, onboarding)
            with patch("jobops.resume_onboarding.discover_resume_candidates", return_value=[candidate]), patch(
                "jobops.resume_onboarding.select_resume", return_value=(candidate, candidate, []),
            ):
                with self.assertRaises(JobOpsError):
                    manager.prepare()
            with database.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE'").fetchone()[0],
                    0,
                )
            staging = onboarding.store.private_root / "staging"
            self.assertTrue(staging.is_dir())
            self.assertEqual(list(staging.iterdir()), [])
            self.assertEqual(list(onboarding.store.private_root.glob("*.dpapi")), [])


if __name__ == "__main__":
    unittest.main()
