from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import _support
from _support import PROJECT
from jobops.document_qa import extract_pdf_text
from jobops.errors import JobOpsError
from jobops.local_document_parser import read_region_pdf, validate_native_result
from jobops._pdf_region_worker import parse_region_pdf
from jobops.onboarding_center import OnboardingCenterService
from jobops.util import sha256_bytes
from pypdf import PdfReader, PdfWriter

CORPUS = PROJECT / "tests/fixtures/material-layout-probes-v1"


def compact(value):
    return "".join(value.split())


class PdfRegionTests(unittest.TestCase):
    def test_real_worker_preserves_column_entities_and_binds_original(self):
        case = next(c for c in json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))["cases"] if c["id"] == "en-columns")
        raw = (CORPUS / case["file"]).read_bytes()
        baseline, _ = extract_pdf_text(CORPUS / case["file"])
        text, meta = read_region_pdf(raw)
        for entity in case["entities"]:
            self.assertNotIn(compact("\n".join(entity["lines"])), compact(baseline))
            self.assertIn(compact("\n".join(entity["lines"])), compact(text))
        self.assertEqual(meta["local_parser_column_pages"], 1)
        self.assertEqual(meta["visual_source_sha256"], sha256_bytes(raw))
        self.assertTrue(meta["visual_verification_required"])
        self.assertFalse(meta["automatic_claim_selection"])
        self.assertEqual(len(text.splitlines()), len(meta["visual_regions"]))
        for number, region in enumerate(meta["visual_regions"], 1):
            self.assertEqual((region["line_start"], region["line_end"]), (number, number))

    def test_staggered_chinese_columns_do_not_require_matching_baselines(self):
        raw = (CORPUS / "zh-staggered_columns.pdf").read_bytes()
        text, metadata = validate_native_result(raw, parse_region_pdf(raw))
        self.assertLess(text.index("申请人编写"), text.index("合成海港"))
        self.assertTrue(metadata["visual_regions"])

    def test_ruled_table_keeps_rows_and_does_not_claim_column_inference(self):
        result = parse_region_pdf((CORPUS / "en-ruled_table.pdf").read_bytes())
        page = result["pages"][0]
        self.assertTrue(page["table_order_retained"])
        self.assertFalse(page["column_reordered"])
        self.assertTrue(any("Cedar" in b["text"] and "Harbor" in b["text"] for b in page["blocks"]))

    def test_mixed_text_scan_rejects_whole_source_without_dropping_page(self):
        writer = PdfWriter()
        for name in ("en-single.pdf", "en-scan.pdf"):
            writer.add_page(PdfReader(CORPUS / name).pages[0])
        out = io.BytesIO()
        writer.write(out)
        with self.assertRaises(JobOpsError) as caught:
            read_region_pdf(out.getvalue())
        self.assertEqual(caught.exception.code, "LOCAL_PARSER_NEEDS_OCR")

    def test_pages_and_normalized_source_bounds_remain_complete(self):
        raw = (CORPUS / "zh-two_pages.pdf").read_bytes()
        text, meta = validate_native_result(raw, parse_region_pdf(raw))
        self.assertEqual({r["page"] for r in meta["visual_regions"]}, {1, 2})
        self.assertIn("合成青杉", text)
        self.assertIn("合成海港", text)
        for region in meta["visual_regions"]:
            left, top, right, bottom = region["bbox"]
            self.assertTrue(0 <= left < right <= 1 and 0 <= top < bottom <= 1)

    def test_opt_in_failure_never_falls_back_to_visual_provider(self):
        service = object.__new__(OnboardingCenterService)
        for choice, code in (("pdf_regions", "LOCAL_PARSER_NEEDS_OCR"), ("typo", "LOCAL_PARSER_SELECTION_INVALID")):
            with self.subTest(choice=choice), patch.dict("os.environ", {"JOBFLOW_DOCUMENT_PARSER": choice, "JOBFLOW_VISUAL_ENABLE": "true"}), patch("jobops.multimodal.read_visual") as visual:
                with self.assertRaises(JobOpsError) as caught:
                    service._extract_text((CORPUS / "en-scan.pdf").read_bytes(), ".pdf", "resume")
                self.assertEqual(caught.exception.code, code)
                visual.assert_not_called()

    def test_page_budget_does_not_return_partial_text(self):
        writer = PdfWriter()
        for _ in range(7):
            writer.add_page(PdfReader(CORPUS / "en-single.pdf").pages[0])
        out = io.BytesIO()
        writer.write(out)
        with self.assertRaises(JobOpsError) as caught:
            read_region_pdf(out.getvalue())
        self.assertEqual(caught.exception.code, "LOCAL_PARSER_LIMIT")

    def test_corrupt_input_has_only_safe_diagnostics(self):
        with self.assertRaises(JobOpsError) as caught:
            read_region_pdf(b"private source phrase - not a PDF")
        self.assertNotIn("private source phrase", json.dumps(caught.exception.as_dict()))
        self.assertEqual(caught.exception.code, "LOCAL_PARSER_FAILED")


if __name__ == "__main__":
    unittest.main()
