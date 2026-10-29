from __future__ import annotations
import unittest
from unittest.mock import patch
import _support
from workbench_fixtures import material_cases
from jobops._docling_worker import parse_native_pdf
from jobops.local_document_parser import read_local_pdf, validate_native_result
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService


class LocalDocumentTests(unittest.TestCase):
    def test_actual_optional_native_parser_and_subprocess(self):
        case = material_cases()[0]
        text, meta = read_local_pdf(case["raw"])
        self.assertIn("120", text)
        self.assertTrue(meta["visual_verification_required"])
        self.assertFalse(meta["automatic_claim_selection"])
        self.assertEqual(meta["model_calls"], 0)
        self.assertEqual(meta["local_parser_version"], "2.144.0")
        self.assertTrue(meta["visual_regions"])

    def test_scan_and_missing_page_never_become_successful_empty_text(self):
        cases = material_cases()
        scan = next(c for c in cases if c["kind"] == "scanned_pdf")
        with self.assertRaises(ValueError):
            parse_native_pdf(scan["raw"])
        case = cases[0]
        result = parse_native_pdf(case["raw"])
        result["pages"] = []
        with self.assertRaises(JobOpsError):
            validate_native_result(case["raw"], result)
        result = parse_native_pdf(case["raw"])
        result["pages"][0]["blocks"][0]["bbox"] = [0, 0, float("nan"), 1]
        with self.assertRaises(JobOpsError):
            validate_native_result(case["raw"], result)

    def test_opt_in_preserves_existing_default_and_does_not_fallback_to_cloud(self):
        service = object.__new__(OnboardingCenterService)
        with (
            patch.dict(
                "os.environ",
                {
                    "JOBFLOW_DOCUMENT_PARSER": "docling_native",
                    "JOBFLOW_VISUAL_ENABLE": "true",
                },
            ),
            patch(
                "jobops.local_document_parser.read_local_pdf",
                side_effect=JobOpsError("LOCAL_PARSER_NEEDS_OCR", "Synthetic"),
            ),
            patch("jobops.multimodal.read_visual") as cloud,
        ):
            with self.assertRaises(JobOpsError):
                service._extract_text(b"synthetic", ".pdf", "resume")
            cloud.assert_not_called()
