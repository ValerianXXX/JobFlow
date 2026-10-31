"""Frozen provider-independent paging contracts; no account access or network."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
import _support
from jobops.errors import JobOpsError
from jobops.mail_tracking import ApplicationStatusInbox, StatusMessage
from mail_review_corpus import APPLICATIONS, A, mail_contract_cases
from test_mail_review_contract import MemoryPrivate


class MailSyncContractTests(unittest.TestCase):
    def setup_inbox(self, count=120):
        from jobops.mail_sync import SyntheticPagedStatusAdapter
        private = MemoryPrivate()
        inbox = ApplicationStatusInbox(private)
        messages = [StatusMessage(**case["message"]) for case in mail_contract_cases()[:count]]
        adapter = SyntheticPagedStatusAdapter(messages, source_id="synthetic-account-and-folder-A")
        return private, inbox, adapter, messages

    def test_same_timestamp_pages_replay_and_review_survive_restart(self):
        private, inbox, adapter, _ = self.setup_inbox()
        first = inbox.collect_page(adapter, APPLICATIONS, limit=70)
        self.assertEqual(first["added"], 70)
        self.assertFalse(first["complete"])
        self.assertNotIn("cursor", first)
        self.assertNotIn(adapter.source_id, json.dumps(first))
        reviewed = inbox.manual_review(inbox.events[0]["event_id"], application_id=A, decision="MANUAL_LINK", allowed_application_ids={A}, confirmed=True)
        restored = ApplicationStatusInbox(private, prior_review_ref=reviewed["review_ref"])
        second = restored.collect_page(adapter, APPLICATIONS, checkpoint_ref=first["checkpoint_ref"], limit=70)
        self.assertEqual(second["added"], 50)
        self.assertTrue(second["complete"])
        self.assertEqual(len(restored.events), 120)
        original = deepcopy(restored.events)
        replay = restored.collect_page(adapter, APPLICATIONS, checkpoint_ref=first["checkpoint_ref"], limit=70)
        self.assertEqual(replay["added"], 0)
        self.assertEqual(restored.events, original)
        self.assertEqual(restored.events[0]["review_status"], "USER_MANUALLY_ASSOCIATED")
        for event in restored.events:
            self.assertFalse(event["application_state_changed"])
            self.assertEqual(event["real_external_actions"], 0)

    def test_completed_cursor_can_receive_later_same_timestamp_message(self):
        _, inbox, adapter, messages = self.setup_inbox(1)
        first = inbox.collect_page(adapter, APPLICATIONS)
        adapter.messages.append(replace(messages[0], message_id="synthetic-later-same-timestamp"))
        second = inbox.collect_page(adapter, APPLICATIONS, checkpoint_ref=first["checkpoint_ref"])
        self.assertEqual(second["added"], 1)
        self.assertTrue(second["complete"])

    def test_account_scope_is_checked_before_reading_a_page(self):
        from jobops.mail_sync import SyntheticPagedStatusAdapter
        private, inbox, adapter, messages = self.setup_inbox(1)
        first = inbox.collect_page(adapter, APPLICATIONS)
        other = SyntheticPagedStatusAdapter(messages, source_id="synthetic-other-account")
        with patch.object(other, "read_page") as read:
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(other, APPLICATIONS, checkpoint_ref=first["checkpoint_ref"])
        self.assertEqual(error.exception.code, "MAIL_SYNC_SCOPE_MISMATCH")
        read.assert_not_called()
        second = inbox.collect_page(other, APPLICATIONS)
        self.assertEqual(second["added"], 1)
        self.assertNotEqual(inbox.events[0]["event_id"], inbox.events[1]["event_id"])
        self.assertEqual(inbox.events[0]["evidence_ref"], inbox.events[1]["evidence_ref"])

    def test_changed_payload_for_same_provider_identity_fails_before_writes(self):
        private, inbox, adapter, messages = self.setup_inbox(1)
        inbox.collect_page(adapter, APPLICATIONS)
        original, stored = deepcopy(inbox.events), deepcopy(private.data)
        adapter.messages[0] = replace(messages[0], body="We are not moving forward.")
        with self.assertRaises(JobOpsError) as error:
            inbox.collect_page(adapter, APPLICATIONS)
        self.assertEqual(error.exception.code, "MAIL_MESSAGE_ID_CONFLICT")
        self.assertEqual(inbox.events, original)
        self.assertEqual(private.data, stored)

    def test_two_conflicting_payloads_in_one_page_are_not_silently_deduplicated(self):
        private, inbox, adapter, messages = self.setup_inbox(1)
        adapter.messages.append(replace(messages[0], body="Interview invitation."))
        with self.assertRaises(JobOpsError) as error:
            inbox.collect_page(adapter, APPLICATIONS)
        self.assertEqual(error.exception.code, "MAIL_MESSAGE_ID_CONFLICT")
        self.assertEqual(inbox.events, [])
        self.assertEqual(private.data, {})

    def test_journal_failure_does_not_publish_a_checkpoint(self):
        private, inbox, adapter, _ = self.setup_inbox(1)
        importer = private.import_bytes
        def fail(kind, value):
            if kind == "application_mail_review":
                raise OSError("SYNTHETIC_PRIVATE_DETAIL")
            return importer(kind, value)
        with patch.object(private, "import_bytes", side_effect=fail):
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(adapter, APPLICATIONS)
        self.assertEqual(error.exception.code, "MAIL_IMPORT_FAILED")
        self.assertEqual(private.data, {})
        self.assertEqual(inbox.events, [])

    def test_checkpoint_failure_retains_evidence_and_retry_does_not_duplicate(self):
        private, inbox, adapter, _ = self.setup_inbox(1)
        importer = private.import_bytes
        def fail(kind, value):
            if kind == "application_mail_sync_checkpoint":
                raise OSError("SYNTHETIC_CURSOR_SECRET")
            return importer(kind, value)
        with patch.object(private, "import_bytes", side_effect=fail):
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(adapter, APPLICATIONS)
        self.assertEqual(error.exception.code, "MAIL_SYNC_CHECKPOINT_FAILED")
        self.assertNotIn("SYNTHETIC_CURSOR_SECRET", str(error.exception))
        self.assertEqual(len(inbox.events), 1)
        self.assertEqual(inbox.collect_page(adapter, APPLICATIONS)["added"], 0)

    def test_expired_cursor_requires_explicit_recovery_without_automatic_resync(self):
        _, inbox, adapter, _ = self.setup_inbox(1)
        with patch.object(adapter, "read_page", side_effect=JobOpsError("MAIL_CURSOR_EXPIRED", "SYNTHETIC_CURSOR_SECRET")) as read:
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(adapter, APPLICATIONS)
        self.assertEqual(error.exception.code, "MAIL_CURSOR_EXPIRED")
        self.assertNotIn("SYNTHETIC_CURSOR_SECRET", str(error.exception))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(inbox.events, [])

    def test_invalid_page_and_nonprogress_cursor_fail_closed(self):
        from jobops.mail_sync import StatusPage
        private, inbox, adapter, messages = self.setup_inbox(1)
        for page in (StatusPage(messages * 101, "next", True), StatusPage(messages, None, False), StatusPage(messages, "x" * 4097, True), StatusPage(messages, "next", "true")):
            with self.subTest(page_type=type(page.complete).__name__, size=len(page.messages)):
                with patch.object(adapter, "read_page", return_value=page):
                    with self.assertRaises(JobOpsError):
                        inbox.collect_page(adapter, APPLICATIONS)
                self.assertEqual(inbox.events, [])
        first = inbox.collect_page(adapter, APPLICATIONS)
        cursor = json.loads(private.get_bytes(first["checkpoint_ref"]))["cursor"]
        with patch.object(adapter, "read_page", return_value=StatusPage([], cursor, False)):
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(adapter, APPLICATIONS, checkpoint_ref=first["checkpoint_ref"])
        self.assertEqual(error.exception.code, "MAIL_SYNC_NO_PROGRESS")

    def test_malformed_checkpoint_is_redacted_before_adapter_access(self):
        private, inbox, adapter, _ = self.setup_inbox(1)
        checkpoint = private.import_bytes("application_mail_sync_checkpoint", b'{"schema_version":1}')
        with patch.object(adapter, "read_page") as read:
            with self.assertRaises(JobOpsError) as error:
                inbox.collect_page(adapter, APPLICATIONS, checkpoint_ref=checkpoint["secure_ref"])
        self.assertEqual(error.exception.code, "MAIL_SYNC_CHECKPOINT_INVALID")
        read.assert_not_called()

    def test_invalid_limits_do_not_read_from_adapter(self):
        _, inbox, adapter, _ = self.setup_inbox(1)
        with patch.object(adapter, "read_page") as read:
            for limit in (0, 101, True, "50"):
                with self.assertRaises(JobOpsError):
                    inbox.collect_page(adapter, APPLICATIONS, limit=limit)
        read.assert_not_called()
