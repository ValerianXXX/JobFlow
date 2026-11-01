"""Read-only material contracts. Synthetic store; real PDF page rendering."""
from __future__ import annotations

import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from _support import PROJECT, project_temp
from test_onboarding_center import MemorySecureStore
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.util import canonical_json, sha256_bytes


class ApplicationMaterialReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(project_temp())
        self.database = JobOpsDB(self.root / 'review.db')
        self.database.initialize()
        self.store = MemorySecureStore(Path(self.enterContext(tempfile.TemporaryDirectory(prefix='jobflow-review-contract-'))))
        self.onboarding = PrivateOnboarding(self.database, self.store)
        writer = PdfWriter()
        page = PdfReader(PROJECT / 'tests/fixtures/native-citation-wrap-v1/cover-letter.pdf').pages[0]
        writer.add_page(page)
        writer.add_page(page)
        stream = io.BytesIO()
        writer.write(stream)
        self.pdf = stream.getvalue()
        self.enterContext(patch('jobops.orchestrator.export_docx_to_pdf',
                                side_effect=lambda _docx, pdf, _script: pdf.write_bytes(self.pdf)))
        self.orchestrator = JobOpsOrchestrator(PROJECT, self.database, self.onboarding)
        refs = self.orchestrator.secure_onboard_synthetic()
        fixtures = PROJECT / 'tests/fixtures'
        result = self.orchestrator.run_to_awaiting(
            fixtures / 'synthetic-forward-jd.txt', profile_ref=refs['profile_ref'],
            master_resume_ref=refs['master_resume_ref'], answer_bank_ref=refs['answer_bank_ref'],
            route_fixture=fixtures / 'synthetic-forward-route.json',
            form_fixture=fixtures / 'synthetic-material-form.html',
            research_fixture=fixtures / 'synthetic-research.html', synthetic=True,
        )
        self.service = OnboardingCenterService(PROJECT, self.database, self.onboarding)
        self.application_id = result['application_id']
        self.displayed = self.service.review_packet(self.application_id)
        self.request = {'application_id': self.application_id,
                        'expected_packet_hash': self.displayed['packet']['content_hash']}
        self.resume_ref = self.displayed['packet']['material_plan']['resume']['pdf_secure_ref']

    def snapshot(self):
        with self.database.connect() as connection:
            tables = ('applications', 'review_packets', 'private_refs', 'materials',
                      'approvals', 'external_action_attempts', 'events')
            rows = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                    for table in tables}
        return rows, copy.deepcopy(self.store.values)

    def expect_block(self, operation):
        before = self.snapshot()
        with self.assertRaises(JobOpsError) as caught:
            operation()
        self.assertNotIn('data:image', str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        return caught.exception.code

    def test_list_and_download_are_bound_and_do_not_grant_approval(self):
        before = self.snapshot()
        result = self.service.application_materials(self.request)
        items = {item['material_id']: item for item in result['materials']}
        self.assertTrue({'resume.pdf', 'resume.docx', 'cover_letter.pdf', 'cover_letter.docx'} <= items.keys())
        self.assertNotIn('secure-ref:', json.dumps(result))
        for material_id in ('resume.pdf', 'resume.docx', 'cover_letter.pdf', 'cover_letter.docx'):
            with self.subTest(material_id=material_id):
                raw, descriptor = self.service.application_material_file({**self.request, 'material_id': material_id})
                self.assertEqual(sha256_bytes(bytes(raw)), items[material_id]['sha256'])
                self.assertEqual(descriptor['sha256'], items[material_id]['sha256'])
                self.assertNotIn('/', descriptor['filename'])
                self.assertNotIn('\\', descriptor['filename'])
                raw[:] = b'\0' * len(raw)
        self.assertEqual(self.snapshot(), before)

    def test_preview_uses_real_second_pdf_page_without_writes(self):
        before = self.snapshot()
        result = self.service.application_material_page({**self.request, 'material_id': 'resume.pdf', 'page': 2})
        self.assertEqual(result['page']['page_count'], 2)
        self.assertEqual(result['page']['page'], 2)
        self.assertTrue(result['page']['image_data_url'].startswith('data:image/png;base64,'))
        self.assertEqual(result['page']['source_sha256'], sha256_bytes(self.pdf))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_page_and_nonpreview_format_fail_without_writes(self):
        for page in (0, -1, True, '1', 3, 61):
            with self.subTest(page=page):
                self.expect_block(lambda: self.service.application_material_page(
                    {**self.request, 'material_id': 'resume.pdf', 'page': page}))
        self.expect_block(lambda: self.service.application_material_page(
            {**self.request, 'material_id': 'resume.docx', 'page': 1}))

    def test_stale_hash_and_arbitrary_selector_never_decrypt_material(self):
        original = self.onboarding.read_bytes
        reads = []
        def track(reference):
            reads.append(reference)
            return original(reference)
        with patch.object(self.onboarding, 'read_bytes', side_effect=track):
            for material_id in ('../resume.pdf', self.resume_ref, 'master.docx', 'profile', 'resume.pdf?x=1'):
                self.expect_block(lambda: self.service.application_material_file(
                    {**self.request, 'material_id': material_id}))
            self.expect_block(lambda: self.service.application_material_file(
                {**self.request, 'expected_packet_hash': 'sha256:' + '0' * 64, 'material_id': 'resume.pdf'}))
        self.assertNotIn(self.resume_ref, reads)

    def test_packet_content_cannot_change_while_retaining_its_old_hash(self):
        with self.database.connect() as connection:
            packet_ref = connection.execute('SELECT relative_path FROM review_packets WHERE application_id=?',
                                            (self.application_id,)).fetchone()[0]
        packet = copy.deepcopy(self.displayed['packet'])
        packet['resume_bullets'][0]['text'] = 'Changed synthetic statement.'
        self.onboarding.rotate(packet_ref, canonical_json(packet))
        self.expect_block(lambda: self.service.application_material_file({**self.request, 'material_id': 'resume.pdf'}))

    def test_material_must_belong_to_this_application(self):
        with self.database.connect() as connection:
            connection.execute("DELETE FROM materials WHERE application_id=? AND kind='resume_pdf'", (self.application_id,))
        result = self.service.application_materials(self.request)
        resume = next(item for item in result['materials'] if item['material_id'] == 'resume.pdf')
        self.assertFalse(resume['available'])
        self.expect_block(lambda: self.service.application_material_file({**self.request, 'material_id': 'resume.pdf'}))
        raw, _ = self.service.application_material_file({**self.request, 'material_id': 'cover_letter.pdf'})
        self.assertEqual(bytes(raw), self.pdf)

    def test_revoked_wrong_kind_or_changed_hash_is_unavailable(self):
        for column, value in (('status', 'REVOKED'), ('kind', 'candidate_profile'), ('content_sha256', 'sha256:' + '0' * 64)):
            with self.subTest(column=column):
                with self.database.connect() as connection:
                    original = connection.execute(f'SELECT {column} FROM private_refs WHERE secure_ref=?', (self.resume_ref,)).fetchone()[0]
                    connection.execute(f'UPDATE private_refs SET {column}=? WHERE secure_ref=?', (value, self.resume_ref))
                self.expect_block(lambda: self.service.application_material_file({**self.request, 'material_id': 'resume.pdf'}))
                with self.database.connect() as connection:
                    connection.execute(f'UPDATE private_refs SET {column}=? WHERE secure_ref=?', (original, self.resume_ref))

    def test_corrupt_ciphertext_is_not_returned(self):
        self.store.values[self.resume_ref] = b'corrupt synthetic bytes'
        self.expect_block(lambda: self.service.application_material_file({**self.request, 'material_id': 'resume.pdf'}))

    def test_revocation_during_read_is_rechecked(self):
        original = self.onboarding.read_bytes
        def revoke_after_read(reference):
            raw = original(reference)
            if reference == self.resume_ref:
                with self.database.connect() as connection:
                    connection.execute("UPDATE private_refs SET status='REVOKED' WHERE secure_ref=?", (reference,))
            return raw
        with patch.object(self.onboarding, 'read_bytes', side_effect=revoke_after_read):
            with self.assertRaises(JobOpsError):
                self.service.application_material_file({**self.request, 'material_id': 'resume.pdf'})

    def test_packet_change_during_render_discards_the_image(self):
        from jobops.source_review import source_page
        def changed(*args, **kwargs):
            page = source_page(*args, **kwargs)
            with self.database.connect() as connection:
                connection.execute("UPDATE review_packets SET content_hash=? WHERE application_id=?",
                                   ('sha256:' + '0' * 64, self.application_id))
            return page
        with patch('jobops.source_review.source_page', side_effect=changed):
            with self.assertRaises(JobOpsError):
                self.service.application_material_page({**self.request, 'material_id': 'resume.pdf', 'page': 1})

    def test_http_requires_local_session_and_origin_and_returns_no_store_bytes(self):
        server = create_server(self.service, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        origin = f'http://{host}:{port}'
        path = '/api/application-material-file'
        body = json.dumps({**self.request, 'material_id': 'resume.pdf'})
        for session, request_origin, expected in ((None, origin, 403),
                                                  (server.session_token, 'https://attacker.example.test', 403),
                                                  (server.session_token, origin, 200)):
            with self.subTest(session=bool(session), origin=request_origin):
                client = http.client.HTTPConnection(host, port, timeout=15)
                try:
                    headers = {'Content-Type': 'application/json', 'Origin': request_origin}
                    if session:
                        headers['X-JobOps-Session'] = session
                    client.request('POST', path, body, headers)
                    response = client.getresponse()
                    payload = response.read()
                    self.assertEqual(response.status, expected)
                    self.assertIn('no-store', response.getheader('Cache-Control'))
                    if expected == 200:
                        self.assertEqual(payload, self.pdf)
                        self.assertEqual(response.getheader('Content-Type'), 'application/pdf')
                        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                finally:
                    client.close()


if __name__ == '__main__':
    unittest.main()
