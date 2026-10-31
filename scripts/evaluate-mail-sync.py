"""Synthetic paging/recovery through Windows DPAPI; no mailbox or model access."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from jobops.demo import DEMO_APPLICATION_ID, create_demo_service
from jobops.errors import JobOpsError
from jobops.mail_tracking import ApplicationStatusInbox, StatusMessage
from jobops.mail_sync import SyntheticPagedStatusAdapter


def run():
    if platform.system() != "Windows":
        raise SystemExit("This probe requires the real Windows DPAPI store.")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="jobflow-mail-native-") as temporary:
        root = Path(temporary).resolve(strict=True)
        assert root.parent == Path(tempfile.gettempdir()).resolve(strict=True)
        service = create_demo_service(PROJECT, root)
        try:
            before = service.application_workspace({"application_id": DEMO_APPLICATION_ID})["status"]
            applications = [{"application_id": DEMO_APPLICATION_ID, "external_reference": "REQ-42",
                "allowed_sender_domains": ["native.example.test"]}]
            messages = [StatusMessage(f"synthetic-native-{index}", "2026-10-31T12:00:00+00:00", "native.example.test", True, "REQ-42", body)
                for index, body in enumerate(["We received your application.", "This is not an interview invitation.", "We invite you to an interview."])]
            adapter = SyntheticPagedStatusAdapter(messages, source_id="synthetic-native-account-folder")
            private = service.onboarding
            inbox = ApplicationStatusInbox(private)
            first = inbox.collect_page(adapter, applications, limit=1)
            event = inbox.events[0]
            reviewed = inbox.manual_review(event["event_id"], application_id=DEMO_APPLICATION_ID,
                decision="MANUAL_LINK", allowed_application_ids={DEMO_APPLICATION_ID}, confirmed=True)
            original = deepcopy(inbox.events)
            importer = private.import_bytes
            def fail_review(kind, raw, **kwargs):
                if kind == "application_mail_review":
                    raise OSError("SYNTHETIC_PRIVATE_FAULT_DETAIL")
                return importer(kind, raw, **kwargs)
            with patch.object(private, "import_bytes", side_effect=fail_review):
                try:
                    inbox.manual_review(event["event_id"], application_id=None, decision="CLEAR_REVIEW",
                        allowed_application_ids={DEMO_APPLICATION_ID}, confirmed=True)
                except JobOpsError as error:
                    assert error.code == "MAIL_REVIEW_SAVE_FAILED"
                    assert "SYNTHETIC_PRIVATE_FAULT_DETAIL" not in str(error)
                else:
                    raise AssertionError("Fault injection must fail the save.")
            assert inbox.events == original
            restored = ApplicationStatusInbox(private, prior_review_ref=reviewed["review_ref"])
            second = restored.collect_page(adapter, applications, checkpoint_ref=first["checkpoint_ref"], limit=1)
            assert second["added"] == 1 and restored.events[1]["status_proposal"] == "UNKNOWN"
            def fail_checkpoint(kind, raw, **kwargs):
                if kind == "application_mail_sync_checkpoint":
                    raise OSError("SYNTHETIC_CURSOR_DETAIL")
                return importer(kind, raw, **kwargs)
            with patch.object(private, "import_bytes", side_effect=fail_checkpoint):
                try:
                    restored.collect_page(adapter, applications, checkpoint_ref=second["checkpoint_ref"], limit=1)
                except JobOpsError as error:
                    assert error.code == "MAIL_SYNC_CHECKPOINT_FAILED"
                    assert "SYNTHETIC_CURSOR_DETAIL" not in str(error)
                else:
                    raise AssertionError("Fault injection must fail the checkpoint.")
            latest = service._latest_ref("application_mail_review")
            restarted = ApplicationStatusInbox(private, prior_review_ref=latest)
            final = restarted.collect_page(adapter, applications, checkpoint_ref=second["checkpoint_ref"], limit=1)
            assert final["added"] == 0 and final["complete"] is True
            assert len(restarted.events) == 3
            assert restarted.events[0]["review_status"] == "USER_MANUALLY_ASSOCIATED"
            assert len(restarted.events[0]["review_history"]) == 1
            assert json.loads(private.read_bytes(final["checkpoint_ref"]))["cursor"] == "synthetic-page-v1:3"
            for event in restarted.events:
                assert private.read_bytes(event["evidence_ref"])
            # The ordinary SQLite index and encrypted store must not contain the
            # known synthetic body in plaintext. This is not a general leak proof.
            sentinel = messages[1].body.encode()
            assert all(sentinel not in file.read_bytes() for file in private.store.private_root.rglob("*") if file.is_file())
            assert all(sentinel not in file.read_bytes() for file in (service.project / "state").glob("jobops.db*"))
            after = service.application_workspace({"application_id": DEMO_APPLICATION_ID})["status"]
            assert before == after
        finally:
            service.close()
    assert not root.exists()
    return {"status": "PASS", "store": "REAL_WINDOWS_DPAPI", "synthetic_messages": 3, "page_size": 1,
        "same_timestamp_pages": True, "review_save_fault_recovered": True,
        "checkpoint_save_fault_recovered": True, "review_preserved_after_restart": True,
        "duplicate_added_on_retry": 0, "known_plaintext_probe": "PASS", "temporary_state_removed": True,
        "application_state_changed": False, "mailbox_access": False, "model_requests": 0,
        "real_external_actions": 0, "elapsed_seconds": round(time.perf_counter() - started, 3),
        "python": platform.python_version(), "platform": platform.system(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an observation.")
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
