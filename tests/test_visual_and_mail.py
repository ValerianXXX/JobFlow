from __future__ import annotations
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import _support
from PIL import Image
from jobops.errors import JobOpsError
from jobops.mail_tracking import StatusMessage, SyntheticStatusAdapter, ApplicationStatusInbox, propose_status
from jobops.multimodal import read_visual, render_pages, visual_messages
from jobops.onboarding_center import OnboardingCenterService
from jobops.util import sha256_bytes


class VisualTests(unittest.TestCase):
    def fixture(self):
        output = io.BytesIO(); Image.new("RGB", (100, 100), "white").save(output, format="PNG")
        return output.getvalue()

    def engine(self, result):
        class Engine:
            def public_status(self):
                return {"private_transport": "LOCAL_LOOPBACK_HTTP"}
            def execute_structured_task(self, request):
                self.request = request
                return result
        return Engine()

    def test_bounded_regions_and_no_automatic_selection(self):
        engine = self.engine({"pages": [{"page": 1, "blank": False, "blocks": [{"text": "Team reduced cost by 18% in 2024.", "bbox": [0, 0.1, 1, 0.3]}]}]})
        text, meta = read_visual(self.fixture(), ".png", engine)
        self.assertEqual(text, "Team reduced cost by 18% in 2024.")
        self.assertTrue(meta["visual_verification_required"])
        self.assertFalse(meta["automatic_claim_selection"])
        self.assertEqual(meta["visual_regions"][0]["line_start"], 1)
        self.assertNotIn("text", meta["visual_regions"][0])
        self.assertEqual(visual_messages(engine.request)[-1]["type"], "image_url")

    def test_missing_pages_invalid_boxes_and_cloud_consent(self):
        for result in [{"pages": []}, {"pages": [{"page": 1, "blank": False, "blocks": [{"text": "Untrusted", "bbox": [-1, 0, 1, 1]}]}]}]:
            with self.assertRaises(JobOpsError):
                read_visual(self.fixture(), ".png", self.engine(result))
        engine = self.engine({})
        engine.public_status = lambda: {"private_transport": "WSL_EPHEMERAL_STDIN_STDOUT"}
        with self.assertRaises(JobOpsError) as error:
            read_visual(self.fixture(), ".png", engine)
        self.assertEqual(error.exception.code, "VISUAL_PROVIDER_CONSENT_REQUIRED")
        self.assertFalse(hasattr(engine, "request"))

    def test_existing_agent_envelope_is_decoded_before_strict_validation(self):
        payload = {"pages": [{"page": 1, "blank": False, "blocks": [{"text": "Team: 12 stores.", "bbox": [0, 0, 1, 1]}]}]}
        result = {"ok": True, "toolSummary": {"calls": 0, "tools": []}, "result": {"content": json.dumps(payload)}}
        text, _ = read_visual(self.fixture(), ".png", self.engine(result))
        self.assertEqual(text, "Team: 12 stores.")
        result["result"]["content"] = "```json\n" + json.dumps(payload) + "\n```"
        with self.assertRaises(JobOpsError):
            read_visual(self.fixture(), ".png", self.engine(result))

    def test_existing_intake_uses_visual_reader_only_after_opt_in(self):
        engine = self.engine({"pages": [{"page": 1, "blank": False, "blocks": [{"text": "Built a dashboard covering 12 synthetic stores.", "bbox": [0, 0, 1, 1]}]}]})
        service = object.__new__(OnboardingCenterService); service.ai_engine = engine
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_ENABLE": "false"}):
            with self.assertRaises(JobOpsError):
                service._extract_text(self.fixture(), ".png", "resume")
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_ENABLE": "true"}):
            text, _, meta = service._extract_text(self.fixture(), ".png", "resume")
        self.assertIn("12", text); self.assertTrue(meta["visual_verification_required"])

    def test_existing_claim_commit_requires_visual_verification(self):
        service = object.__new__(OnboardingCenterService)
        pending = {"source_id": "SRC-SYNTHETIC", "extraction_summary": {"visual_verification_required": True},
                   "candidates": [{"candidate_id": "C1", "statement": "Built a dashboard covering 12 synthetic stores.", "category": "project"}]}
        with patch("jobops.onboarding_center._ai_analysis_is_complete", return_value=True):
            with self.assertRaises(JobOpsError) as error:
                service._commit_pending_source({}, pending, [{"candidate_id": "C1", "selected": True}])
        self.assertEqual(error.exception.code, "VISUAL_SOURCE_VERIFICATION_REQUIRED")


APPLICATIONS = [{"application_id": "APP-EXAMPLE", "external_reference": "REQ-42", "role": "Product Analyst", "allowed_sender_domains": ["careers.example.test"]}]


def message(body, **changes):
    return StatusMessage(**{"message_id": "synthetic-1", "received_at": "2026-10-27T10:00:00-04:00", "sender_domain": "careers.example.test", "authenticated_sender": True, "subject": "REQ-42 Product Analyst", "body": body, **changes})


class MailTests(unittest.TestCase):
    def test_receipt_rejection_interview_and_uncertainty(self):
        for body, status in [("We received your application.", "APPLICATION_RECEIVED"), ("We are not moving forward.", "REJECTED"), ("We invite you to an interview.", "INTERVIEW_INVITED")]:
            result = propose_status(message(body), APPLICATIONS)
            self.assertEqual(result["status_proposal"], status)
            self.assertEqual(result["application_id"], "APP-EXAMPLE")
            self.assertFalse(result["application_state_changed"])
        result = propose_status(message("We received your application. We are not moving forward."), APPLICATIONS)
        self.assertEqual(result["status_proposal"], "UNKNOWN")
        self.assertIsNone(result["application_id"])

    def test_spoof_ambiguous_role_and_reference_boundaries(self):
        for incoming, applications in [(message("We invite you to an interview.", authenticated_sender=False), APPLICATIONS),
                (message("We invite you to an interview.", subject="REQ-420"), APPLICATIONS),
                (message("Interview invitation", subject="Product Analyst"), APPLICATIONS + [{**APPLICATIONS[0], "application_id": "APP-OTHER"}])]:
            self.assertIsNone(propose_status(incoming, applications)["application_id"])

    def test_duplicate_and_encrypted_review_history(self):
        class Private:
            def __init__(self):
                self.data = {}; self.store = self
            def import_bytes(self, kind, value):
                reference = "secure-ref:" + sha256_bytes(value).split(":")[-1]
                self.data[reference] = value
                return {"secure_ref": reference}
            def get_bytes(self, ref):
                return self.data[ref]
        private = Private(); inbox = ApplicationStatusInbox(private)
        adapter = SyntheticStatusAdapter([message("Interview invitation")])
        first = inbox.collect(adapter, APPLICATIONS)
        self.assertEqual(first["added"], 1)
        resumed = ApplicationStatusInbox(private, prior_review_ref=first["review_ref"])
        self.assertEqual(resumed.collect(adapter, APPLICATIONS)["added"], 0)
        event = resumed.events[0]
        with self.assertRaises(JobOpsError):
            resumed.review(event["event_id"], application_id="APP-EXAMPLE", decision="ACCEPT_LINK")
        confirmed = resumed.review(event["event_id"], application_id="APP-EXAMPLE", decision="ACCEPT_LINK", confirmed=True)
        self.assertFalse(confirmed["application_state_changed"])
        resumed.review(event["event_id"], application_id=None, decision="REJECT_LINK", confirmed=True)
        self.assertEqual(len(resumed.events[0]["review_history"]), 2)
        self.assertNotIn("Interview invitation", json.dumps(first))
