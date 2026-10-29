from __future__ import annotations
import base64
from copy import deepcopy
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import _support
from workbench_fixtures import material_cases
from jobops.local_document_parser import read_local_pdf, validate_native_result
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService


class LocalDocumentTests(unittest.TestCase):
    def native_result(self, raw):
        # Native extension failures must fail this test with an exit code, not
        # terminate the entire Python matrix before unittest can report it.
        result = subprocess.run(
            [sys.executable, "-m", "jobops._docling_worker"],
            input=json.dumps({"pdf_base64": base64.b64encode(raw).decode("ascii")}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, f"native worker exit: {result.returncode}")
        return json.loads(result.stdout)

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
        response = self.native_result(scan["raw"])
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "LOCAL_PARSER_NEEDS_OCR")
        case = cases[0]
        response = self.native_result(case["raw"])
        self.assertTrue(response["ok"])
        valid_result = response["result"]
        validate_native_result(case["raw"], valid_result)
        result = deepcopy(valid_result)
        result["pages"] = []
        with self.assertRaises(JobOpsError):
            validate_native_result(case["raw"], result)
        result = deepcopy(valid_result)
        result["pages"][0]["blocks"][0]["bbox"] = [0, 0, float("nan"), 1]
        with self.assertRaises(JobOpsError):
            validate_native_result(case["raw"], result)

    def test_worker_failure_diagnostics_contain_no_document_or_stderr(self):
        cases = [
            (subprocess.CompletedProcess([], 42, b"private document", b"private path"), "LOCAL_PARSER_FAILED", "WORKER_EXIT"),
            (subprocess.CompletedProcess([], 0, b"not json", b"private path"), "LOCAL_PARSER_FAILED", "INVALID_RESULT"),
            (subprocess.CompletedProcess([], 0, b"[]", b"private path"), "LOCAL_PARSER_FAILED", "INVALID_RESULT"),
            (subprocess.TimeoutExpired([], 60, output=b"private document"), "LOCAL_PARSER_TIMEOUT", "WORKER_TIMEOUT"),
        ]
        for outcome, code, stage in cases:
            with self.subTest(stage=stage), patch("jobops.local_document_parser.subprocess.run") as run:
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.return_value = outcome
                with self.assertRaises(JobOpsError) as caught:
                    read_local_pdf(b"synthetic document")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.details["failure_stage"], stage)
                self.assertNotIn("private", json.dumps(caught.exception.as_dict()))

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
