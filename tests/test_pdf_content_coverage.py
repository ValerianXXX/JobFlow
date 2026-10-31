from __future__ import annotations
import base64
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import _support
from _support import PROJECT
from jobops.document_qa import extract_pdf_text
from jobops.errors import JobOpsError
from jobops.local_document_parser import read_region_pdf, read_local_pdf
from jobops.onboarding_center import OnboardingCenterService

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


if __name__ == '__main__':
    unittest.main()
