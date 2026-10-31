from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import _support
from jobops.errors import JobOpsError
from jobops.mail_tracking import ApplicationStatusInbox, StatusMessage, SyntheticStatusAdapter, propose_status
from jobops.util import sha256_bytes
from mail_review_corpus import APPLICATIONS, A, mail_contract_cases
from workbench_fixtures import mail_cases


class MemoryPrivate:
    """Storage fault test double, not an encryption or native DPAPI claim."""
    def __init__(self):
        self.store = self
        self.data = {}

    def import_bytes(self, kind, value):
        ref = "secure-ref:" + sha256_bytes(kind.encode() + value).split(":")[-1]
        duplicate = ref in self.data
        self.data[ref] = value
        return {"secure_ref": ref, "deduplicated": duplicate}

    def get_bytes(self, ref):
        return self.data[ref]

    def delete(self, ref, **kwargs):
        del self.data[ref]


class MailReviewContractTests(unittest.TestCase):
    def test_html_history_is_excluded_without_changing_retained_evidence_identity(self):
        from dataclasses import asdict
        from email.message import EmailMessage
        from jobops.mail_tracking import LocalEmlAdapter
        from jobops.util import canonical_json
        fixtures = [
            ("<p>We are not moving forward.</p><blockquote>We invite you to an interview.</blockquote>", "REJECTED"),
            ("<div>邀请您参加面试。</div><blockquote>本次申请未通过。</blockquote>", "INTERVIEW_INVITED"),
            ("<p>Please wait.</p><div class='gmail_quote'>Interview invitation.</div>", "UNKNOWN"),
            ("<div>Please wait.</div><div type='cite'><p>Application received.</p></div>", "UNKNOWN"),
            ("<blockquote>Interview invitation.</blockquote><p>We are not moving forward.</p>", "REJECTED"),
            ("<p>We invite you to an interview.</p><blockquote>Interview invitation.</blockquote>", "INTERVIEW_INVITED"),
        ]
        for body, expected in fixtures:
            with self.subTest(body=body):
                message = EmailMessage()
                message["From"] = "Synthetic <recruiting@north.example.test>"
                message["Date"] = "Sat, 31 Oct 2026 08:00:00 -0400"
                message["Subject"] = "REQ-42"
                message.set_content(body, subtype="html")
                adapter = LocalEmlAdapter(message.as_bytes())
                evidence = canonical_json(asdict(adapter.message))
                original_identity = sha256_bytes(evidence)
                private = MemoryPrivate()
                inbox = ApplicationStatusInbox(private)
                inbox.collect(adapter, APPLICATIONS)
                event = inbox.events[0]
                self.assertEqual(event["status_proposal"], expected)
                self.assertIsNone(event["application_id"])
                self.assertEqual(event["event_id"], original_identity)
                self.assertEqual(private.get_bytes(event["evidence_ref"]), evidence)
                self.assertEqual(private.get_bytes(event["original_ref"]), adapter.raw)
                self.assertEqual(inbox.collect(adapter, APPLICATIONS)["added"], 0)

    def test_frozen_bilingual_corpus(self):
        cases = mail_contract_cases()
        self.assertEqual(len(cases), 120)
        self.assertEqual(sum(case["language"] == "zh" for case in cases), 60)
        for case in cases:
            with self.subTest(case=case["id"]):
                result = propose_status(StatusMessage(**case["message"]), APPLICATIONS)
                self.assertEqual(result["status_proposal"], case["expected_status"])
                self.assertEqual(set(result["candidate_application_ids"]), set(case["expected_candidates"]))
                self.assertEqual(result["application_id"], case["expected_confident"])
                self.assertFalse(result["application_state_changed"])
                self.assertEqual(result["real_external_actions"], 0)
                text = case["message"]["subject"] + "\n" + case["message"]["body"]
                for span in result["status_evidence_spans"]:
                    self.assertTrue(0 <= span["start"] < span["end"] <= len(text))
                    self.assertTrue(text[span["start"]:span["end"]].strip())

    def test_legacy_sixty_local_files_stay_untrusted(self):
        from jobops.mail_tracking import LocalEmlAdapter
        for case in mail_cases():
            with self.subTest(case=case["id"]):
                result = propose_status(LocalEmlAdapter(case["raw"]).read_messages()[0], APPLICATIONS)
                self.assertEqual(result["status_proposal"], case["expected_status"])
                self.assertIsNone(result["application_id"])

    def test_all_fixed_messages_replay_without_duplicating_or_losing_review(self):
        private = MemoryPrivate()
        inbox = ApplicationStatusInbox(private)
        messages = [StatusMessage(**case["message"]) for case in mail_contract_cases()]
        first = inbox.collect(SyntheticStatusAdapter(messages[:100]), APPLICATIONS, limit=100)
        second = inbox.collect(SyntheticStatusAdapter(messages[100:]), APPLICATIONS, limit=100)
        self.assertEqual((first["added"], second["added"]), (100, 20))
        reviewed = inbox.manual_review(inbox.events[0]["event_id"], application_id=A, decision="MANUAL_LINK", allowed_application_ids={A}, confirmed=True)
        restored = ApplicationStatusInbox(private, prior_review_ref=reviewed["review_ref"])
        original = deepcopy(restored.events)
        for chunk in (list(reversed(messages[:100])), list(reversed(messages[100:]))):
            self.assertEqual(restored.collect(SyntheticStatusAdapter(chunk), APPLICATIONS, limit=100)["added"], 0)
        self.assertEqual(restored.events, original)

    def failed_decision(self, decision):
        private = MemoryPrivate()
        inbox = ApplicationStatusInbox(private)
        adapter = SyntheticStatusAdapter([StatusMessage(**mail_contract_cases()[0]["message"])])
        initial = inbox.collect(adapter, APPLICATIONS)
        original = deepcopy(inbox.events)
        kwargs = dict(application_id=A, decision=decision, confirmed=True)
        review = inbox.review
        if decision in {"MANUAL_LINK", "CLEAR_REVIEW"}:
            review = inbox.manual_review
            kwargs["allowed_application_ids"] = {A}
        with patch.object(private, "import_bytes", side_effect=OSError("SYNTHETIC_PRIVATE_STORAGE_DETAIL")):
            try:
                review(inbox.events[0]["event_id"], **kwargs)
            except Exception as error:
                failure = error
            else:
                self.fail("A failed persistence operation must not report success.")
        self.assertEqual(inbox.events, original, "A failed save must not publish a decision in memory.")
        self.assertIsInstance(failure, JobOpsError)
        self.assertEqual(failure.code, "MAIL_REVIEW_SAVE_FAILED")
        self.assertNotIn("SYNTHETIC_PRIVATE_STORAGE_DETAIL", str(failure))
        self.assertEqual(ApplicationStatusInbox(private, prior_review_ref=initial["review_ref"]).events, original)
        subsequent = inbox.collect(adapter, APPLICATIONS)
        self.assertEqual(json.loads(private.get_bytes(subsequent["review_ref"])), original)
        result = review(inbox.events[0]["event_id"], **kwargs)
        self.assertFalse(result["application_state_changed"])
        self.assertEqual(len(inbox.events[0]["review_history"]), 1)

    def test_accept_save_failure_is_transactional(self):
        self.failed_decision("ACCEPT_LINK")

    def test_reject_save_failure_is_transactional(self):
        self.failed_decision("REJECT_LINK")

    def test_manual_save_failure_is_transactional(self):
        self.failed_decision("MANUAL_LINK")

    def test_clear_save_failure_is_transactional(self):
        self.failed_decision("CLEAR_REVIEW")
