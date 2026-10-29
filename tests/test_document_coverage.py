from __future__ import annotations
import base64
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import _support
from _support import PROJECT
from jobops.document_coverage import require_complete_page_text
from jobops.document_qa import extract_pdf_text
from jobops.errors import JobOpsError
from jobops.local_document_parser import read_region_pdf

CORPUS = PROJECT / 'tests/fixtures/complete-materials-v1'


class DocumentCoverageTests(unittest.TestCase):
    def test_blank_and_numbered_missing_pages_block_real_default_text_extraction(self):
        for name, reason in [('challenge-en-blank_middle.pdf', 'PAGE_TEXT_MISSING'),
                             ('challenge-en-missing_middle.pdf', 'PRINTED_PAGINATION_INCOMPLETE'),
                             ('en-hybrid_text_scan.pdf', 'PAGE_TEXT_MISSING')]:
            for layout in (False, True):
                with self.subTest(name=name, layout=layout), self.assertRaises(JobOpsError) as caught:
                    extract_pdf_text(CORPUS / name, layout=layout, require_complete_pages=True)
                self.assertEqual(caught.exception.code, 'ONBOARDING_DOCUMENT_QUALITY_FAILED')
                self.assertIn(reason, caught.exception.details['document_quality']['reason_codes'])
                self.assertNotIn('candidate', json.dumps(caught.exception.details).casefold())

    def test_complete_and_unlabelled_sources_do_not_claim_semantic_completeness(self):
        require_complete_page_text(['Example record\nPage 1 of 2', 'Another record\nPage 2 of 2'])
        require_complete_page_text(['Unlabelled excerpt', 'Another unlabelled excerpt'])
        require_complete_page_text(['报告内容\n第1页，共2页', '另页内容\n第2页，共2页'])
        text, count = extract_pdf_text(CORPUS / 'en-chronological.pdf', require_complete_pages=True)
        self.assertEqual(count, 3)
        self.assertIn('no spending authority', text)

    def test_number_like_body_text_is_not_a_page_edge_label(self):
        require_complete_page_text(['Report discusses Page 3 of 9 in a cited source.', 'Appendix text'])
        # One isolated label alone is insufficient to assert a pagination scheme.
        require_complete_page_text(['Page 3 of 9', 'Appendix text'])

    def test_complete_numbered_section_with_unnumbered_cover_or_addendum_is_valid(self):
        require_complete_page_text(['Cover', 'Record\nPage 1 of 2', 'Details\nPage 2 of 2', 'Addendum'])
        require_complete_page_text(['Page 1 of 2', 'Page 2 of 2', 'Page 1 of 2', 'Page 2 of 2'])
        for parts in (['Page 1 of 3', 'Page 3 of 3', 'Addendum'], ['第1页，共3页', '第3页，共3页']):
            with self.assertRaises(JobOpsError):
                require_complete_page_text(parts)

    def test_geometry_route_rejects_known_missing_source_page(self):
        with self.assertRaises(JobOpsError) as caught:
            read_region_pdf((CORPUS / 'challenge-en-missing_middle.pdf').read_bytes())
        self.assertEqual(caught.exception.code, 'LOCAL_PARSER_INCOMPLETE_PAGINATION')

    def test_wrapped_column_development_regression_retains_all_facts(self):
        manifest = json.loads((CORPUS / 'manifest.json').read_text(encoding='utf-8'))
        case = next(c for c in manifest['cases'] if c['id'] == 'en-paired_columns')
        text, meta = read_region_pdf((CORPUS / case['file']).read_bytes())
        compact = lambda value: ''.join(value.split()).casefold()
        for fact in case['facts']:
            self.assertIn(compact(fact['text']), compact(text), fact['id'])
        self.assertEqual(meta['local_parser_version'], 'pdf-regions-3')
        self.assertTrue(meta['visual_verification_required'])

    def test_fallback_helper_exposes_the_actual_empty_page_instead_of_aggregate_only(self):
        helper = PROJECT / '.agents/skills/job-application-operator/scripts/extract-pdf-text.py'
        result = subprocess.run([sys.executable, str(helper), str(CORPUS / 'challenge-en-blank_middle.pdf'), '--page-parts'], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        value = json.loads(result.stdout)
        parts = [base64.b64decode(v).decode() for v in value['page_parts_base64']]
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[1].strip(), '')
        with self.assertRaises(JobOpsError):
            require_complete_page_text(parts)


if __name__ == '__main__':
    unittest.main()
