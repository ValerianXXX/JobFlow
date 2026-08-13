from __future__ import annotations

import unittest
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from _support import project_temp
from jobops.document_builder import _save_document_atomic, build_cover_letter, build_resume, export_docx_to_pdf, render_pdf_to_pngs
from jobops.errors import JobOpsError
from jobops.evidence import map_evidence
from jobops.materials import approved_wordings, master_diff
from jobops.util import iso_utc


def approved_claim(claim_id: str, wording: str, fact: str):
    now = datetime.now(timezone.utc)
    return {
        "claim_id": claim_id,
        "raw_fact": fact,
        "allowed_wording": [wording],
        "forbidden_wording": ["unverified outcome"],
        "responsibility_boundary": {"candidate": "performed synthetic fixture action", "team": "reviewed", "ai": "generated fixture inputs"},
        "evidence": [{"kind": "fixture", "value": 1}],
        "source_refs": [{"source_id": "personal_redacted", "relative_path": "synthetic/case.md", "fingerprint": "sha256:" + "a" * 64}],
        "approved_for_external": True,
        "sensitivity": "personal-redacted",
        "last_verified_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(days=30)),
    }


class MaterialTests(unittest.TestCase):
    def test_pdf_render_requires_fresh_pages_from_the_current_invocation(self) -> None:
        with project_temp() as temp:
            pdf = temp / "resume.pdf"
            renders = temp / "renders"
            renders.mkdir()
            pdf.write_bytes(b"synthetic pdf")
            stale = renders / "resume-1.png"
            stale.write_bytes(b"stale page")
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch("jobops.document_builder.subprocess.run", return_value=completed):
                with self.assertRaises(JobOpsError) as blocked:
                    render_pdf_to_pngs(pdf, renders, "pdftoppm")
            self.assertEqual(blocked.exception.code, "PDF_RENDER_FAILED")
            self.assertEqual(stale.read_bytes(), b"stale page")

            def successful_render(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(str(command[-1]) + "-1.png").write_bytes(b"fresh page")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("jobops.document_builder.subprocess.run", side_effect=successful_render):
                pages = render_pdf_to_pngs(pdf, renders, "pdftoppm")
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0].read_bytes(), b"fresh page")
            self.assertNotEqual(pages[0], stale)

    def test_pdf_export_never_accepts_or_overwrites_a_stale_target(self) -> None:
        with project_temp() as temp:
            docx = temp / "resume.docx"
            pdf = temp / "resume.pdf"
            docx.write_bytes(b"synthetic docx input")
            pdf.write_bytes(b"stable old pdf")
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch("jobops.document_builder.subprocess.run", return_value=completed):
                with self.assertRaises(JobOpsError) as blocked:
                    export_docx_to_pdf(docx, pdf, temp / "export.ps1")
            self.assertEqual(blocked.exception.code, "DOCX_PDF_EXPORT_FAILED")
            self.assertEqual(pdf.read_bytes(), b"stable old pdf")
            self.assertEqual(list(temp.glob(".resume.jobflow-*.tmp.pdf")), [])

            def successful_export(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"fresh pdf")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("jobops.document_builder.subprocess.run", side_effect=successful_export):
                export_docx_to_pdf(docx, pdf, temp / "export.ps1")
            self.assertEqual(pdf.read_bytes(), b"fresh pdf")

    def test_document_save_failure_preserves_existing_output(self) -> None:
        class InterruptedDocument:
            @staticmethod
            def save(path: str) -> None:
                from pathlib import Path
                Path(path).write_bytes(b"partial private output")
                raise OSError("synthetic interrupted save")

        with project_temp() as temp:
            output = temp / "resume.docx"
            output.write_bytes(b"stable existing output")
            with self.assertRaises(JobOpsError) as blocked:
                _save_document_atomic(InterruptedDocument(), output)
            self.assertEqual(blocked.exception.code, "DOCUMENT_OUTPUT_BUILD_FAILED")
            self.assertEqual(output.read_bytes(), b"stable existing output")
            self.assertEqual(list(temp.glob(".resume.docx.jobflow-*.tmp")), [])

    def test_evidence_mapping_uses_only_approved_claims(self) -> None:
        claim = approved_claim("CLM-SYNTHETIC01", "Analyzed synthetic product evidence", "synthetic product analysis evidence")
        mapping = map_evidence(["product analysis"], [claim])[0]
        self.assertEqual(mapping.claim_id, "CLM-SYNTHETIC01")
        self.assertIsNone(mapping.gap)
        claim["approved_for_external"] = False
        blocked = map_evidence(["product analysis"], [claim])[0]
        self.assertEqual(blocked.gap, "NO_APPROVED_EVIDENCE")

    def test_material_builder_rejects_unapproved_summary_and_unsourced_company_reason(self) -> None:
        claim = approved_claim("CLM-SYNTHETIC01", "Analyzed synthetic product evidence", "synthetic product analysis evidence")
        with project_temp() as temp:
            with self.assertRaises(JobOpsError) as caught:
                build_resume(temp / "resume.docx", candidate_display_name="Synthetic Candidate", target_role="Strategy Analyst", summary="Invented summary", claims=[claim], skills=["analysis"], education="Synthetic education fixture")
            self.assertEqual(caught.exception.code, "SUMMARY_NOT_CLAIM_GATED")
            with self.assertRaises(JobOpsError) as caught:
                build_cover_letter(temp / "letter.docx", candidate_display_name="Synthetic Candidate", company="Example", target_role="Strategy Analyst", why_company="No source", why_role="Analyzed synthetic product evidence", claims=[claim])
            self.assertEqual(caught.exception.code, "WHY_COMPANY_SOURCE_MISSING")

    def test_docx_materials_contain_exact_approved_wording(self) -> None:
        claim = approved_claim("CLM-SYNTHETIC01", "Analyzed synthetic product evidence", "synthetic product analysis evidence")
        skill = approved_claim("CLM-SYNTHETIC02", "Synthetic analysis skill", "synthetic analysis skill")
        education = approved_claim("CLM-SYNTHETIC03", "Synthetic education fixture", "synthetic education fixture")
        with project_temp() as temp:
            resume = build_resume(temp / "resume.docx", candidate_display_name="Synthetic Candidate", target_role="Strategy Analyst", summary=claim["allowed_wording"][0], claims=[claim, skill, education], skills=[skill["allowed_wording"][0]], education=education["allowed_wording"][0], bullet_claim_ids=[claim["claim_id"]])
            letter = build_cover_letter(temp / "letter.docx", candidate_display_name="Synthetic Candidate", company="Example", target_role="Strategy Analyst", why_company="Example posted a synthetic role (https://example.com/news, accessed 2026-08-12).", why_role=claim["allowed_wording"][0], claims=[claim])
            self.assertEqual(resume["claim_ids"], ["CLM-SYNTHETIC01", "CLM-SYNTHETIC02", "CLM-SYNTHETIC03"])
            self.assertEqual(letter["claim_ids"], ["CLM-SYNTHETIC01"])
            self.assertTrue((temp / "resume.docx").is_file())
            self.assertTrue((temp / "letter.docx").is_file())

    def test_master_diff_is_complete(self) -> None:
        diff = master_diff("one\ntwo\n", "one\nthree\n")
        self.assertIn("-two", diff)
        self.assertIn("+three", diff)


if __name__ == "__main__":
    unittest.main()
