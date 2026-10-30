from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest import mock

from _support import project_temp
import test_onboarding_center as fixtures
from test_source_context_persistence import TEXT, response, selector
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService


class ContextDecisionTests(unittest.TestCase):
    def service(self, root):
        service, onboarding, store, _, _ = fixtures.OnboardingCenterTests().make_service(root)
        self.addCleanup(service.close)
        return service, onboarding, store

    def source(self, service, text=TEXT):
        notes = [selector(), selector(2, 'work_authorization'), selector(3, '', 'evidence_gap')]
        with mock.patch.object(service.ai_engine, 'execute_structured_task', return_value=response(notes)):
            identifier = service.preview_source('project_case', '.txt', text.encode())['source_id']
        service.commit_source(identifier, [])
        return identifier

    def item(self, service, index=0):
        state = service.bootstrap()
        note = state['context_notes'][index]
        return note, state['context_review']['notes'][note['note_id']]

    def request(self, service, index=0, decision='PROFILE_REVIEWED', reason=''):
        note, view = self.item(service, index)
        return {'note_id': note['note_id'], 'expected_binding_sha256': view['binding_sha256'],
                'expected_review_id': view['review_id'], 'expected_answer_sha256': view['answer_sha256'],
                'decision': decision, 'reason': reason, 'user_confirmed': True}

    def answer(self, service, value, status='CONFIRMED'):
        service.save_answers({'answers': {'remote_preference': {'value': value.casefold(), 'status': status, 'use_policy': 'reuse'}}})

    def test_review_is_private_persistent_and_never_changes_answers_or_approval(self):
        with project_temp() as root:
            service, onboarding, _ = self.service(root)
            self.source(service)
            self.answer(service, 'Remote')
            before = service.bootstrap()
            result = service.review_source_context(self.request(service))
            self.assertEqual(result['status'], 'SOURCE_CONTEXT_REVIEW_RECORDED')
            self.assertNotIn('Remote', json.dumps(result))
            reopened = OnboardingCenterService(root / 'project', service.database, onboarding, ai_engine=service.ai_engine)
            self.addCleanup(reopened.close)
            after = reopened.bootstrap()
            self.assertEqual(self.item(reopened)[1]['status'], 'PROFILE_REVIEWED')
            for key in ('answers', 'claims', 'profile_review', 'external_claim_approval'):
                self.assertEqual(after[key], before[key], key)
            self.assertFalse(self.item(reopened)[1]['approved_for_external'])
            for path in (root / 'project' / 'state').glob('*'):
                if path.is_file():
                    self.assertNotIn(TEXT.splitlines()[0].encode(), path.read_bytes())

    def test_unknown_is_explicitly_reviewed_as_unknown_not_an_answer_or_eligibility(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            before = service.bootstrap()['answers']['work_authorization']
            service.review_source_context(self.request(service, 1))
            self.assertEqual(self.item(service, 1)[1]['status'], 'PROFILE_UNKNOWN_REVIEWED')
            self.assertEqual(service.bootstrap()['answers']['work_authorization'], before)
            self.assertEqual(before['status'], 'UNKNOWN')

    def test_changed_then_restored_answer_never_resurrects_old_review_but_noop_save_does(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            self.answer(service, 'Remote')
            service.review_source_context(self.request(service))
            self.answer(service, 'Remote')
            self.assertEqual(self.item(service)[1]['status'], 'PROFILE_REVIEWED')
            self.answer(service, 'Hybrid')
            self.assertEqual(self.item(service)[1]['status'], 'ANSWER_CHANGED')
            self.answer(service, 'Remote')
            self.assertEqual(self.item(service)[1]['status'], 'ANSWER_CHANGED')
            service.review_source_context(self.request(service))
            self.assertEqual(self.item(service)[1]['status'], 'PROFILE_REVIEWED')

    def test_reanalysis_requires_confirm_or_discard_and_confirm_invalidates_even_same_quote(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            source_id = self.source(service)
            service.review_source_context(self.request(service))
            original = self.item(service)[1]
            with mock.patch.object(service.ai_engine, 'execute_structured_task', return_value=response([selector()])):
                service.reprocess_source(source_id)
            self.assertFalse(self.item(service)[1]['available'])
            with self.assertRaises(JobOpsError):
                service.review_source_context(self.request(service))
            service.discard_source_preview(source_id)
            self.assertEqual(self.item(service)[1], original)
            with mock.patch.object(service.ai_engine, 'execute_structured_task', return_value=response([selector()])):
                service.reprocess_source(source_id)
            service.commit_source(source_id, [])
            self.assertEqual(self.item(service)[1]['status'], 'SOURCE_CHANGED')
            _, saved = service.ensure_state()
            self.assertEqual(len(saved['source_context_reviews']), 1)

    def test_stale_requests_and_unconfirmed_or_extra_fields_cannot_write_review(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            original = self.request(service)
            for update in ({'expected_binding_sha256': 'sha256:' + '0' * 64},
                           {'expected_answer_sha256': 'sha256:' + '0' * 64},
                           {'expected_review_id': 'CTXREV-forged'}, {'user_confirmed': False},
                           {'approved_for_external': True}, {'decision': 'APPROVED'}):
                with self.subTest(update=update), self.assertRaises(JobOpsError):
                    service.review_source_context({**original, **update})
            _, saved = service.ensure_state()
            self.assertEqual(saved.get('source_context_reviews', []), [])

    def test_duplicate_click_is_idempotent_and_different_stale_decision_is_rejected(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            request = self.request(service)
            first = service.review_source_context(request)
            _, stored = service.ensure_state()
            service.review_source_context(request)
            self.assertEqual(service.ensure_state()[1], stored)
            self.assertEqual(self.item(service)[1]['review_id'], first['review_id'])
            with self.assertRaises(JobOpsError):
                service.review_source_context({**request, 'expected_review_id': 'CTXREV-unrelated'})
            with self.assertRaises(JobOpsError):
                service.review_source_context({**request, 'decision': 'FOLLOW_UP', 'reason': 'NEEDS_EVIDENCE'})

    def test_corrupt_review_history_fails_closed_with_a_recovery_error(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            service.review_source_context(self.request(service))
            reference, original = service.ensure_state()
            for field, value in [('decision', []), ('reason', {}), ('note_id', []),
                                 ('answer_sha256', []), ('review_id', 'CTXREV-forged'),
                                 ('approved_for_external', True)]:
                saved = deepcopy(original)
                saved['source_context_reviews'][0][field] = value
                service._save_state(reference, saved)
                with self.subTest(field=field), self.assertRaises(JobOpsError) as error:
                    service.bootstrap()
                self.assertEqual(error.exception.code, 'SOURCE_CONTEXT_HISTORY_INVALID')

    def test_review_rejects_changed_source_bytes_and_forged_but_rehashed_note(self):
        from jobops.util import sha256_bytes
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            reference, original = service.ensure_state()
            forged = deepcopy(original)
            forged['source_context_notes'][0]['quote'] = 'A forged preference.'
            forged['source_context_notes'][0]['quote_sha256'] = sha256_bytes(b'A forged preference.')
            service._save_state(reference, forged)
            with self.assertRaises(JobOpsError) as error:
                service.review_source_context(self.request(service))
            self.assertEqual(error.exception.code, 'SOURCE_CONTEXT_REVIEW_MISMATCH')
            service._save_state(reference, original)
            request = self.request(service)
            original_read = service.onboarding.read_bytes
            raw_ref = original['sources'][0]['secure_ref']
            with mock.patch.object(service.onboarding, 'read_bytes', side_effect=lambda ref:
                                   b'Changed retained bytes' if ref == raw_ref else original_read(ref)):
                with self.assertRaises(JobOpsError):
                    service.review_source_context(request)
            self.assertEqual(service.ensure_state()[1].get('source_context_reviews', []), [])

    def test_gap_followup_dismissal_and_reopen_are_reversible_without_claim_creation(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            with self.assertRaises(JobOpsError):
                service.review_source_context(self.request(service, 2))
            service.review_source_context(self.request(service, 2, 'FOLLOW_UP', 'NEEDS_EVIDENCE'))
            self.assertEqual(self.item(service, 2)[1]['status'], 'FOLLOW_UP')
            with self.assertRaises(JobOpsError):
                service.review_source_context(self.request(service, 2, 'DISMISSED'))
            service.review_source_context(self.request(service, 2, 'DISMISSED', 'NOT_APPLICABLE'))
            self.assertEqual(self.item(service, 2)[1]['status'], 'DISMISSED')
            service.review_source_context(self.request(service, 2, 'REOPEN'))
            self.assertEqual(self.item(service, 2)[1]['status'], 'PENDING')
            _, saved = service.ensure_state()
            self.assertEqual([event['decision'] for event in saved['source_context_reviews']], ['FOLLOW_UP', 'DISMISSED', 'REOPEN'])
            self.assertEqual(saved['material_claims'], [])

    def test_source_deletion_removes_only_its_private_review_history(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            first = self.source(service)
            service.review_source_context(self.request(service))
            second = self.source(service, TEXT.replace('Remote work only.', 'Remote work preferred.'))
            service.review_source_context(self.request(service, 3))
            service.delete_source(first, user_confirmed=True)
            _, saved = service.ensure_state()
            self.assertEqual(len(saved['source_context_reviews']), 1)
            self.assertEqual(saved['source_context_reviews'][0]['source_id'], second)

    def test_completed_onboarding_is_immutable(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            request = self.request(service)
            reference, saved = service.ensure_state()
            saved['status'] = 'ONBOARDING_COMPLETE'
            service._save_state(reference, saved)
            with self.assertRaises(JobOpsError) as error:
                service.review_source_context(request)
            self.assertEqual(error.exception.code, 'ONBOARDING_REVISION_REQUIRED')

    def test_failed_save_and_tampered_transcript_cannot_promote_review(self):
        with project_temp() as root:
            service, _, _ = self.service(root)
            self.source(service)
            request = self.request(service)
            with mock.patch.object(service, '_save_state', side_effect=RuntimeError('synthetic save failure')):
                with self.assertRaises(RuntimeError):
                    service.review_source_context(request)
            self.assertEqual(self.item(service)[1]['status'], 'PENDING')
            reference, saved = service.ensure_state()
            saved['sources'][0]['_analysis_transcript']['text'] = 'Forged source analysis'
            service._save_state(reference, saved)
            with self.assertRaises(JobOpsError):
                service.review_source_context(self.request(service))


if __name__ == '__main__':
    unittest.main()
