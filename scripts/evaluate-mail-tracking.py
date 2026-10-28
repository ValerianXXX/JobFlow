"""Synthetic messages through the real Windows private store; never opens a mailbox."""
from pathlib import Path
import json
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jobops.demo import create_demo_service
from jobops.mail_tracking import ApplicationStatusInbox, StatusMessage, SyntheticStatusAdapter

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="jobflow-mail-synthetic-") as temporary:
    service = create_demo_service(root, Path(temporary))
    applications = [{"application_id": "APP-SYNTHETIC", "external_reference": "REQ-42", "role": "Product Analyst", "allowed_sender_domains": ["careers.example.test"]}]
    messages = [StatusMessage(str(i), f"2026-10-27T1{i}:00:00+00:00", "careers.example.test", True, "REQ-42 Product Analyst", text)
        for i, text in enumerate(["We received your application.", "We invite you to an interview.", "We are not moving forward."])]
    inbox = ApplicationStatusInbox(service.onboarding)
    first = inbox.collect(SyntheticStatusAdapter(messages), applications)
    resumed = ApplicationStatusInbox(service.onboarding, prior_review_ref=first["review_ref"])
    duplicate = resumed.collect(SyntheticStatusAdapter(messages), applications)
    decision = resumed.review(resumed.events[0]["event_id"], application_id="APP-SYNTHETIC", decision="ACCEPT_LINK", confirmed=True)
    restored = ApplicationStatusInbox(service.onboarding, prior_review_ref=decision["review_ref"])
    assert first["added"] == 3 and duplicate["added"] == 0
    assert len(restored.events[0]["review_history"]) == 1
    assert [row["status_proposal"] for row in restored.events] == ["APPLICATION_RECEIVED", "INTERVIEW_INVITED", "REJECTED"]
    print(json.dumps({"status": "PASS", "store": "REAL_WINDOWS_DPAPI", "messages": 3,
        "duplicate_added": 0, "review_resumed": True, "mailbox_access": False,
        "application_state_changed": False, "real_external_actions": 0}))
