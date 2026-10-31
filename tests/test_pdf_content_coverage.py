from __future__ import annotations
import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import _support
from _support import PROJECT
from jobops.document_qa import extract_pdf_text
from jobops.errors import JobOpsError
from jobops.local_document_parser import read_region_pdf, read_local_pdf
from jobops.onboarding_center import OnboardingCenterService
from jobops.pdf_content_coverage import inspect_pdf_page_content, require_pdf_content_reports, local_content_failure_code

CORPUS = PROJECT / 'tests/fixtures/pdf-content-probes-v1'
MANIFEST = json.loads((CORPUS / 'manifest.json').read_text())


class PdfContentCoverageTests(unittest.TestCase):
    def test_frozen_synthetic_files_have_not_changed(self):
        self.assertTrue(MANIFEST['synthetic_only'])
        for case in MANIFEST['cases']:
            self.assertEqual(hashlib.sha256((CORPUS / case['file']).read_bytes()).hexdigest(), case['sha256'])

    def test_logical_and_layout_routes_reject_unread_raster_or_only_pagination(self):
        for case in MANIFEST['cases']:
            if not case['must_block_text_only']:
                continue
            for layout in (False, True):
                with self.subTest(case=case['id'], layout=layout):
                    with self.assertRaises(JobOpsError) as caught:
                        extract_pdf_text(CORPUS / case['file'], layout=layout, require_complete_pages=True)
                    self.assertEqual(caught.exception.code, 'ONBOARDING_DOCUMENT_QUALITY_FAILED')
                    quality = caught.exception.details['document_quality']
                    self.assertEqual(quality['status'], 'FAIL')
                    self.assertIn('OCR_REQUIRED', quality['reason_codes'])
                    self.assertFalse(quality['contains_document_text'])
                    self.assertNotIn('Cedar', json.dumps(caught.exception.as_dict()))

    def test_text_small_decorations_and_searchable_raster_keep_review_boundary(self):
        for case in MANIFEST['cases']:
            if case['must_block_text_only']:
                continue
            with self.subTest(case=case['id']):
                text, count = extract_pdf_text(CORPUS / case['file'], require_complete_pages=True)
                self.assertEqual(count, 1)
                self.assertIn('no spending authority', text)
                self.assertIn('120 hours', text)

    def test_geometry_worker_rejects_incomplete_page_content(self):
        for name in ('scan-footer', 'scan-header', 'pagination-only', 'tiled-scan', 'mixed-pages'):
            with self.subTest(case=name):
                with self.assertRaises(JobOpsError) as caught:
                    read_region_pdf((CORPUS / (name + '.pdf')).read_bytes())
                self.assertEqual(caught.exception.code, 'LOCAL_PARSER_NEEDS_OCR')

    def test_optional_native_worker_rejects_incomplete_page_content(self):
        for name in ('scan-footer', 'scan-header', 'pagination-only', 'mixed-pages'):
            with self.subTest(case=name):
                with self.assertRaises(JobOpsError) as caught:
                    read_local_pdf((CORPUS / (name + '.pdf')).read_bytes())
                self.assertEqual(caught.exception.code, 'LOCAL_PARSER_NEEDS_OCR')

    def test_fallback_runtime_cannot_bypass_content_guard(self):
        with patch.dict(sys.modules, {'pdfplumber': None}), self.assertRaises(JobOpsError) as caught:
            extract_pdf_text(CORPUS / 'scan-footer.pdf', require_complete_pages=True)
        self.assertEqual(caught.exception.code, 'ONBOARDING_DOCUMENT_QUALITY_FAILED')

    def test_opt_in_worker_failure_never_invokes_configured_visual_provider(self):
        service = object.__new__(OnboardingCenterService)
        for parser in ('pdf_regions', 'docling_native'):
            with self.subTest(parser=parser), patch.dict('os.environ', {'JOBFLOW_DOCUMENT_PARSER': parser, 'JOBFLOW_VISUAL_ENABLE': 'true'}), patch('jobops.multimodal.read_visual') as visual:
                with self.assertRaises(JobOpsError):
                    service._extract_text((CORPUS / 'scan-header.pdf').read_bytes(), '.pdf', 'resume')
                visual.assert_not_called()

    def test_non_strict_diagnostic_still_exposes_actual_partial_text(self):
        text, count = extract_pdf_text(CORPUS / 'scan-footer.pdf')
        self.assertEqual(count, 1)
        self.assertEqual(text.strip(), 'Page 1 of 1')
        self.assertNotIn('120 hours', text)

    def test_missing_invalid_or_zero_page_helper_evidence_fails_closed(self):
        for reports, count in ((None, 1), ([], 1), ([['unknown']], 1), ([True], 1), ([[{}]], 1), ([], 0), ([[]], True)):
            with self.subTest(reports=reports, count=count):
                with self.assertRaises(JobOpsError) as caught:
                    require_pdf_content_reports(reports, count)
                self.assertEqual(caught.exception.code, 'PDF_PAGE_COVERAGE_UNAVAILABLE')

    def test_helper_missing_content_report_is_not_accepted_as_complete(self):
        text = 'Synthetic content'
        response = {'page_count': 1, 'text_base64': base64.b64encode(text.encode()).decode(),
                    'page_parts_base64': [base64.b64encode(text.encode()).decode()]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response), '')
        with patch.dict(sys.modules, {'pdfplumber': None}), patch('jobops.document_qa.subprocess.run', return_value=completed), self.assertRaises(JobOpsError) as caught:
            extract_pdf_text(CORPUS / 'text-only.pdf', require_complete_pages=True)
        self.assertEqual(caught.exception.code, 'PDF_PAGE_COVERAGE_UNAVAILABLE')

    def test_limits_and_nonfinite_geometry_do_not_emit_text(self):
        marker = 'Synthetic private-looking body, do not echo'
        page = SimpleNamespace(bbox=(0, 0, 612, 792), chars=[], images=[{}] * 257)
        self.assertEqual(inspect_pdf_page_content(page, marker), ['PAGE_CONTENT_INSPECTION_LIMIT'])
        page.images = [{'x0': 0, 'top': 0, 'x1': float('nan'), 'bottom': 700}]
        self.assertEqual(inspect_pdf_page_content(page, marker), ['PAGE_CONTENT_GEOMETRY_UNAVAILABLE'])
        with self.assertRaises(JobOpsError) as caught:
            require_pdf_content_reports([inspect_pdf_page_content(page, marker)], 1)
        self.assertNotIn(marker, json.dumps(caught.exception.as_dict()))
        self.assertEqual(local_content_failure_code(caught.exception), 'LOCAL_PARSER_PROVENANCE_INVALID')

    def test_many_image_objects_keep_resource_limit_distinct_from_ocr(self):
        spec = importlib.util.spec_from_file_location('content_probes', PROJECT / 'scripts/build-pdf-content-probes.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        raw = builder.make_pdf([(48, 740, 'Synthetic document')], [(460, 100, 100, 100)] * 257, decoration=True)
        for reader in (read_region_pdf, read_local_pdf):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(JobOpsError) as caught:
                    reader(raw)
                self.assertEqual(caught.exception.code, 'LOCAL_PARSER_LIMIT')

    def test_native_route_also_keeps_existing_numbered_page_gap_boundary(self):
        with self.assertRaises(JobOpsError) as caught:
            read_local_pdf((PROJECT / 'tests/fixtures/complete-materials-v1/challenge-en-missing_middle.pdf').read_bytes())
        self.assertEqual(caught.exception.code, 'LOCAL_PARSER_INCOMPLETE_PAGINATION')


if __name__ == '__main__':
    unittest.main()
