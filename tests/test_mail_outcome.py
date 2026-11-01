"""Synthetic human review contracts; no mailbox, model, or application action."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import _support
from _support import project_temp
import test_onboarding_center as support
from test_mail_review_contract import MemoryPrivate
from test_workbench import eml, seed_application
from mail_review_corpus import APPLICATIONS, A
from jobops.errors import JobOpsError
from jobops.mail_tracking import ApplicationStatusInbox, StatusMessage, SyntheticStatusAdapter
from jobops.mail_sync import SyntheticPagedStatusAdapter
from jobops.util import canonical_json, sha256_bytes


BODY = "Please choose a time to speak with our hiring panel. This is synthetic evidence."
QUOTE = "Please choose a time to speak with our hiring panel."
CASES = (
    ("APPLICATION_RECEIVED", "Your materials are safely in our queue."),
    ("APPLICATION_RECEIVED", "我们已将你的资料放入待审列表。"),
    ("REJECTED", "We have chosen another candidate for this opening."),
    ("REJECTED", "这个岗位已经由另一位候选人获得。"),
    ("INTERVIEW_INVITED", QUOTE),
    ("INTERVIEW_INVITED", "请选一个时间与招聘小组交谈。"),
    ("UNKNOWN", "We will be in touch when the review ends."),
    ("UNKNOWN", "评估结束后我们会再联系。"),
)


class MailOutcomeTests(unittest.TestCase):
    def make_inbox(self, body=BODY):
        private = MemoryPrivate()
        inbox = ApplicationStatusInbox(private)
        message = StatusMessage("synthetic-outcome-1", "2026-10-31T12:00:00-04:00",
                                "example.test", False, "Synthetic update", body)
        result = inbox.collect(SyntheticStatusAdapter([message]), APPLICATIONS)
        return private, inbox, message, result

    def record(self, inbox, **overrides):
        kwargs = dict(application_id=A, reported_status="INTERVIEW_INVITED", evidence_quote=QUOTE,
                      allowed_application_ids={A}, confirmed=True)
        kwargs.update(overrides)
        return inbox.record_outcome(inbox.events[0]['event_id'], **kwargs)

    def test_eight_bilingual_manual_outcomes_keep_original_proposal_and_exact_evidence(self):
        for status, body in CASES:
            with self.subTest(status=status, body=body):
                private, inbox, message, initial = self.make_inbox(body)
                before = deepcopy(inbox.events[0])
                raw = private.get_bytes(before['evidence_ref'])
                result = self.record(inbox, reported_status=status, evidence_quote=body)
                event = inbox.events[0]
                self.assertEqual(event['status_proposal'], before['status_proposal'])
                self.assertEqual(event['evidence_sha256'], before['evidence_sha256'])
                self.assertEqual(private.get_bytes(event['evidence_ref']), raw)
                record = event['review_history'][-1]
                self.assertEqual(record['decision'], 'RECORD_OUTCOME')
                self.assertEqual(record['application_id'], A)
                self.assertEqual(record['outcome']['status'], status)
                self.assertEqual(record['outcome']['origin'], 'USER_MESSAGE_REVIEW')
                self.assertEqual(record['outcome']['evidence_sha256'], event['evidence_sha256'])
                span = record['outcome']['evidence_span']
                self.assertEqual((message.subject + '\n' + message.body)[span['start']:span['end']], body)
                self.assertFalse(result['application_state_changed'])
                self.assertEqual(result['real_external_actions'], 0)
                self.assertEqual(ApplicationStatusInbox(private, prior_review_ref=result['review_ref']).events, inbox.events)
                self.assertEqual(json.loads(private.get_bytes(initial['review_ref']))[0], before)

    def test_protected_or_unrecognized_statuses_cannot_be_saved(self):
        for status in ('SUBMITTED', 'CONFIRMED', 'APPROVED', 'OFFER', '', None, True, [], {}):
            with self.subTest(status=status):
                private, inbox, _, _ = self.make_inbox()
                before, stored = deepcopy(inbox.events), dict(private.data)
                with self.assertRaises(JobOpsError) as error:
                    self.record(inbox, reported_status=status)
                self.assertEqual(error.exception.code, 'MAIL_OUTCOME_STATUS_INVALID')
                self.assertEqual(inbox.events, before)
                self.assertEqual(private.data, stored)

    def test_confirmation_and_an_existing_application_are_required(self):
        for overrides in ({'confirmed': False}, {'confirmed': 1}, {'application_id': None}, {'application_id': 'APP-UNKNOWN'}):
            with self.subTest(overrides=overrides):
                private, inbox, _, _ = self.make_inbox()
                before = dict(private.data)
                with self.assertRaises(JobOpsError):
                    self.record(inbox, **overrides)
                self.assertEqual(private.data, before)

    def test_quote_must_be_bounded_present_and_unambiguous(self):
        for body, quote in ((BODY, ''), (BODY, '   '), (BODY, 'not in the message'),
                            (BODY, 'x' * 501), (BODY, None), ('repeat repeat', 'repeat'), ('aaaaa', 'aaa')):
            with self.subTest(body=body, quote=quote):
                private, inbox, _, _ = self.make_inbox(body)
                before = dict(private.data)
                with self.assertRaises(JobOpsError) as error:
                    self.record(inbox, evidence_quote=quote)
                self.assertIn(error.exception.code, {'MAIL_OUTCOME_EVIDENCE_REQUIRED', 'MAIL_OUTCOME_EVIDENCE_AMBIGUOUS'})
                self.assertEqual(private.data, before)

    def test_unicode_quote_offsets_are_python_characters_not_browser_utf16(self):
        private, inbox, message, _ = self.make_inbox('Synthetic 😀\n请选一个时间与招聘小组交谈。')
        quote = '请选一个时间与招聘小组交谈。'
        self.record(inbox, evidence_quote='  ' + quote + '\n')
        span = inbox.events[0]['review_history'][-1]['outcome']['evidence_span']
        self.assertEqual((message.subject + '\n' + message.body)[span['start']:span['end']], quote)

    def test_changed_or_unreadable_evidence_cannot_receive_a_review(self):
        for replacement in (b'not json', canonical_json({'subject': 'changed', 'body': 'PRIVATE_ERROR_DETAIL'})):
            with self.subTest(replacement=replacement):
                private, inbox, _, _ = self.make_inbox()
                before = deepcopy(inbox.events)
                private.data[inbox.events[0]['evidence_ref']] = replacement
                with self.assertRaises(JobOpsError) as error:
                    self.record(inbox)
                self.assertEqual(error.exception.code, 'MAIL_EVIDENCE_MISMATCH')
                self.assertNotIn('PRIVATE_ERROR_DETAIL', str(error.exception))
                self.assertEqual(inbox.events, before)

    def test_failed_save_keeps_prior_journal_and_allows_explicit_retry(self):
        private, inbox, _, initial = self.make_inbox()
        before = deepcopy(inbox.events)
        with patch.object(private, 'import_bytes', side_effect=OSError('PRIVATE_ERROR_DETAIL')):
            with self.assertRaises(JobOpsError) as error:
                self.record(inbox)
        self.assertEqual(error.exception.code, 'MAIL_REVIEW_SAVE_FAILED')
        self.assertNotIn('PRIVATE_ERROR_DETAIL', str(error.exception))
        self.assertEqual(inbox.events, before)
        self.assertEqual(ApplicationStatusInbox(private, prior_review_ref=initial['review_ref']).events, before)
        self.record(inbox)
        self.assertEqual(len(inbox.events[0]['review_history']), 1)

    def test_reimport_and_paging_keep_review_and_original_interpretation(self):
        private, inbox, message, _ = self.make_inbox()
        self.record(inbox)
        before = deepcopy(inbox.events)
        self.assertEqual(inbox.collect(SyntheticStatusAdapter([message]), APPLICATIONS)['added'], 0)
        self.assertEqual(inbox.events, before)
        # A separate scoped inbox exercises the existing checkpoint-prefix rule.
        paged = ApplicationStatusInbox(private)
        messages = [message, StatusMessage('synthetic-outcome-2', message.received_at, 'example.test',
                                          False, 'Synthetic next update', 'Please wait.')]
        adapter = SyntheticPagedStatusAdapter(messages, source_id='synthetic-outcome-scope')
        first = paged.collect_page(adapter, APPLICATIONS, limit=1)
        self.record(paged)
        reviewed = deepcopy(paged.events[0])
        second = paged.collect_page(adapter, APPLICATIONS, checkpoint_ref=first['checkpoint_ref'], limit=1)
        self.assertTrue(second['complete'])
        self.assertEqual(paged.events[0], reviewed)

    def test_later_link_dismiss_or_undo_retires_current_outcome_without_erasing_history(self):
        from jobops.mail_review import current_user_outcome
        for decision in ('MANUAL_LINK', 'CLEAR_REVIEW', 'REJECT_LINK'):
            with self.subTest(decision=decision):
                private, inbox, _, _ = self.make_inbox()
                self.record(inbox)
                first = deepcopy(inbox.events[0]['review_history'][0])
                evidence = json.loads(private.get_bytes(inbox.events[0]['evidence_ref']))
                self.assertEqual(current_user_outcome(inbox.events[0], evidence)['status'], 'INTERVIEW_INVITED')
                if decision == 'REJECT_LINK':
                    inbox.review(inbox.events[0]['event_id'], application_id=None, decision=decision, confirmed=True)
                else:
                    inbox.manual_review(inbox.events[0]['event_id'], application_id=A, decision=decision,
                                        allowed_application_ids={A}, confirmed=True)
                self.assertIsNone(current_user_outcome(inbox.events[0], evidence))
                self.assertEqual(inbox.events[0]['review_history'][0], first)
                self.assertEqual(len(inbox.events[0]['review_history']), 2)

    def test_revised_result_keeps_prior_result_and_marks_uncertainty_explicitly(self):
        from jobops.mail_review import current_user_outcome
        private, inbox, _, _ = self.make_inbox()
        self.record(inbox)
        before = deepcopy(inbox.events[0]['review_history'][0])
        self.record(inbox, reported_status='UNKNOWN', evidence_quote='This is synthetic evidence.')
        evidence = json.loads(private.get_bytes(inbox.events[0]['evidence_ref']))
        self.assertEqual(current_user_outcome(inbox.events[0], evidence)['status'], 'UNKNOWN')
        self.assertEqual(inbox.events[0]['review_history'][0], before)


class MailOutcomeServiceTests(unittest.TestCase):
    def test_public_service_review_reload_stale_rejection_and_undo_preserve_protected_state(self):
        with project_temp() as root:
            service, private, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database)
            service.import_status_eml(eml(BODY))
            initial = service.status_inbox()
            payload = {'event_id': initial['events'][0]['event_id'], 'application_id': 'APP-SYN',
                       'decision': 'RECORD_OUTCOME', 'reported_status': 'INTERVIEW_INVITED',
                       'evidence_quote': QUOTE, 'user_confirmed': True, 'expected_review_ref': initial['review_ref']}
            result = service.review_status_message(payload)
            self.assertFalse(result['application_state_changed'])
            current = service.status_inbox()
            event = current['events'][0]
            self.assertEqual(event['status_proposal'], initial['events'][0]['status_proposal'])
            self.assertEqual(event['user_outcome']['status'], 'INTERVIEW_INVITED')
            self.assertEqual(event['user_outcome']['quote'], QUOTE)
            self.assertFalse(event['authenticated_sender'])
            with self.assertRaises(JobOpsError) as error:
                service.review_status_message(payload)
            self.assertEqual(error.exception.code, 'MAIL_REVIEW_STALE')
            service.review_status_message({**payload, 'decision': 'CLEAR_REVIEW', 'expected_review_ref': current['review_ref']})
            undone = service.status_inbox()['events'][0]
            self.assertIsNone(undone['user_outcome'])
            self.assertIsNone(undone['application_id'])
            self.assertEqual(len(undone['review_history']), 2)
            with service.database.connect() as connection:
                self.assertEqual(connection.execute('SELECT status FROM applications').fetchone()[0], 'SUBMISSION_UNKNOWN')
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM external_action_attempts').fetchone()[0], 0)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)

    def test_corrupt_outcome_span_or_binding_is_not_displayed_as_a_user_result(self):
        from jobops.mail_review import current_user_outcome
        helper = MailOutcomeTests()
        private, inbox, _, _ = helper.make_inbox()
        helper.record(inbox)
        evidence = json.loads(private.get_bytes(inbox.events[0]['evidence_ref']))
        for mutation in ('span', 'shifted-span', 'boolean-offset', 'quote-hash', 'hash', 'application', 'status'):
            with self.subTest(mutation=mutation):
                event = deepcopy(inbox.events[0])
                record = event['review_history'][-1]
                if mutation == 'span':
                    record['outcome']['evidence_span']['start'] = -1
                elif mutation == 'shifted-span':
                    record['outcome']['evidence_span']['start'] += 1
                elif mutation == 'boolean-offset':
                    record['outcome']['evidence_span']['start'] = False
                elif mutation == 'quote-hash':
                    record['outcome']['quote_sha256'] = 'sha256:' + '0' * 64
                elif mutation == 'hash':
                    record['outcome']['evidence_sha256'] = 'sha256:' + '0' * 64
                elif mutation == 'application':
                    event['application_id'] = 'APP-OTHER'
                else:
                    record['outcome']['status'] = 'SUBMITTED'
                with self.assertRaises(JobOpsError) as error:
                    current_user_outcome(event, evidence)
                self.assertEqual(error.exception.code, 'MAIL_OUTCOME_RECORD_INVALID')


if __name__ == '__main__':
    unittest.main()
