from __future__ import annotations

import http.client
import io
import json
import threading
import unittest
from unittest.mock import patch
from email.message import EmailMessage

from PIL import Image

from _support import project_temp
import test_onboarding_center as support
from jobops.application_workspace import application_workspace, next_action
from jobops.errors import JobOpsError
from jobops.mail_tracking import LocalEmlAdapter, propose_status
from jobops.onboarding_server import create_server
from jobops.source_review import source_page
from jobops.util import canonical_json, sha256_bytes


def eml(body="We invite you to an interview.", *, html=False):
    message = EmailMessage()
    message["From"] = "Synthetic Recruiting <careers@example.test>"
    message["Date"] = "Wed, 28 Oct 2026 08:00:00 -0400"
    message["Subject"] = "Synthetic Product Analyst REQ-42"
    message["Authentication-Results"] = "example.test; dkim=pass; spf=pass"
    message.set_content(body, subtype="html" if html else "plain", charset="utf-8")
    return message.as_bytes()


def seed_application(database, application_id="APP-SYN"):
    now = "2026-10-28T12:00:00Z"
    with database.connect() as c:
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "JOB-SYN",
                "synthetic",
                "fixture",
                None,
                "Example Synthetic",
                "Product Analyst",
                "Remote",
                "SNAPSHOTTED",
                now,
                now,
            ),
        )
        c.execute(
            "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                application_id,
                "JOB-SYN",
                "example.test",
                "SUBMISSION_UNKNOWN",
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                1,
                None,
                "APPROVED",
                now,
            ),
        )
        c.execute(
            "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (
                application_id,
                "TEST",
                "APPROVED",
                "SUBMISSION_UNKNOWN",
                '{"private":"NEVER_DISPLAY_THIS_PAYLOAD"}',
                now,
            ),
        )


class WorkbenchTests(unittest.TestCase):
    def test_inbox_rejects_evidence_replaced_after_review_journal_creation(self):
        with project_temp() as root:
            service, private, _, _, _ = support.OnboardingCenterTests().make_service(
                root
            )
            service.import_status_eml(eml())
            _, inbox = service._status_inbox()
            private.rotate(
                inbox.events[0]["evidence_ref"],
                canonical_json({"body": "altered synthetic evidence"}),
            )
            with self.assertRaises(JobOpsError) as caught:
                service.status_inbox()
            self.assertEqual(caught.exception.code, "MAIL_EVIDENCE_MISMATCH")

    def test_collector_writes_hashed_bytes_and_legacy_crlf_is_verified_without_repair(
        self,
    ):
        from jobops.collector import JobCollector

        with project_temp() as root:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database)
            collector = JobCollector(
                service.database,
                service.project / "workspace" / "jobs",
                service.project,
            )
            record = collector.collect_text(
                "Synthetic role\r\nInspect workflow evidence."
            )
            path = service.project / record["snapshot_path"]
            self.assertEqual(sha256_bytes(path.read_bytes()), record["snapshot_hash"])
            with service.database.connect() as connection:
                connection.execute(
                    "UPDATE applications SET job_id=? WHERE application_id='APP-SYN'",
                    (record["job_id"],),
                )
            self.assertEqual(
                service.application_workspace({"application_id": "APP-SYN"})[
                    "snapshots"
                ][0]["hash_basis"],
                "RAW_BYTES",
            )
            legacy = path.read_bytes().replace(b"\n", b"\r\n")
            path.write_bytes(legacy)
            self.assertEqual(
                service.application_workspace({"application_id": "APP-SYN"})[
                    "snapshots"
                ][0]["hash_basis"],
                "CANONICAL_LF",
            )
            self.assertEqual(path.read_bytes(), legacy)
            path.write_bytes(legacy.replace(b"Synthetic", b"Altered"))
            self.assertEqual(
                service.application_workspace({"application_id": "APP-SYN"})[
                    "snapshots"
                ][0]["availability"],
                "UNAVAILABLE",
            )

    def test_failed_journal_write_preserves_old_evidence_and_removes_only_new_writes(
        self,
    ):
        with project_temp() as root:
            service, private, _, _, _ = support.OnboardingCenterTests().make_service(
                root
            )
            original = private.import_bytes("application_mail_source", eml())
            with service.database.connect() as connection:
                prior = set(
                    row[0]
                    for row in connection.execute(
                        "SELECT secure_ref FROM private_refs WHERE status='ACTIVE'"
                    )
                )
            importer = private.import_bytes

            def fail_journal(kind, raw, **kwargs):
                if kind == "application_mail_review":
                    raise OSError("synthetic write failure")
                return importer(kind, raw, **kwargs)

            with patch.object(private, "import_bytes", side_effect=fail_journal):
                with self.assertRaises(JobOpsError) as caught:
                    service.import_status_eml(eml())
            self.assertEqual(caught.exception.code, "MAIL_IMPORT_FAILED")
            self.assertEqual(private.read_bytes(original["secure_ref"]), eml())
            self.assertEqual(service.status_inbox()["events"], [])
            with service.database.connect() as connection:
                active = set(
                    row[0]
                    for row in connection.execute(
                        "SELECT secure_ref FROM private_refs WHERE status='ACTIVE'"
                    )
                )
                self.assertEqual(active, prior)
            self.assertEqual(service.import_status_eml(eml())["added"], 1)

    def test_workspace_uses_hash_verified_snapshot_and_no_private_paths_or_payloads(
        self,
    ):
        with project_temp() as root:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database)
            path = service.project / "workspace" / "jobs" / "source.txt"
            path.parent.mkdir(parents=True)
            raw = b"Synthetic job: analyze public examples."
            path.write_bytes(raw)
            with service.database.connect() as c:
                c.execute(
                    "INSERT INTO jd_snapshots VALUES(?,?,?,?,?)",
                    (
                        "SNP-SYN",
                        "JOB-SYN",
                        sha256_bytes(raw),
                        "workspace/jobs/source.txt",
                        "2026-10-28T12:00:00Z",
                    ),
                )
            result = service.application_workspace({"application_id": "APP-SYN"})
            self.assertEqual(result["snapshots"][0]["text"], raw.decode())
            self.assertEqual(result["next_action"], "VERIFY_WITH_EMPLOYER_NO_RETRY")
            self.assertNotIn("NEVER_DISPLAY_THIS_PAYLOAD", json.dumps(result))
            self.assertNotIn("snapshot_path", json.dumps(result))
            path.write_bytes(b"changed")
            result = application_workspace(service.database, service.project, "APP-SYN")
            self.assertEqual(result["snapshots"][0]["availability"], "UNAVAILABLE")
            self.assertNotIn("text", result["snapshots"][0])
            with service.database.connect() as c:
                c.execute("UPDATE jd_snapshots SET snapshot_path='../private.txt'")
            self.assertNotIn(
                "text",
                service.application_workspace({"application_id": "APP-SYN"})[
                    "snapshots"
                ][0],
            )
            self.assertEqual(
                next_action("AWAITING_APPROVAL", 2), "RESOLVE_REQUIRED_FIELDS"
            )
            self.assertEqual(next_action("CLOSED"), "VIEW_ARCHIVED_RECORD")
            self.assertEqual(next_action("OFFER"), "REVIEW_RECORDED_OUTCOME")
            for value in ["../../secret", None, {}]:
                with self.assertRaises(JobOpsError):
                    service.application_workspace({"application_id": value})

    def test_eml_is_not_authenticated_and_does_not_load_remote_html(self):
        adapter = LocalEmlAdapter(
            eml(
                '<p>Interview invitation</p><script>LEAK_SCRIPT</script><img src="https://tracking.invalid/pixel">',
                html=True,
            )
        )
        message = adapter.read_messages()[0]
        self.assertFalse(message.authenticated_sender)
        self.assertNotIn("LEAK_SCRIPT", message.body)
        self.assertNotIn("tracking.invalid", message.body)
        proposal = propose_status(
            message,
            [
                {
                    "application_id": "APP-SYN",
                    "external_reference": "REQ-42",
                    "allowed_sender_domains": [message.sender_domain],
                }
            ],
        )
        self.assertIsNone(proposal["application_id"])
        self.assertEqual(proposal["status_proposal"], "INTERVIEW_INVITED")
        for invalid in [
            b"",
            b"Subject: synthetic\r\n\r\ntext",
            b"x" * (2 * 1024 * 1024 + 1),
            eml("", html=True),
        ]:
            with self.assertRaises(JobOpsError):
                LocalEmlAdapter(invalid)

    def test_import_resume_dedup_manual_association_undo_and_stale_review(self):
        with project_temp() as root:
            service, private, store, _, _ = (
                support.OnboardingCenterTests().make_service(root)
            )
            seed_application(service.database)
            result = service.import_status_eml(eml())
            self.assertEqual(result["added"], 1)
            self.assertEqual(service.import_status_eml(eml())["added"], 0)
            view = service.status_inbox()
            self.assertEqual(len(view["events"]), 1)
            event = view["events"][0]
            self.assertEqual(event["source_kind"], "LOCAL_EML_UNAUTHENTICATED")
            payload = dict(
                event_id=event["event_id"],
                application_id="APP-SYN",
                decision="MANUAL_LINK",
                user_confirmed=True,
                expected_review_ref=view["review_ref"],
            )
            service.review_status_message(payload)
            with self.assertRaises(JobOpsError) as stale:
                service.review_status_message(payload)
            self.assertEqual(stale.exception.code, "MAIL_REVIEW_STALE")
            view = service.status_inbox()
            self.assertEqual(
                view["events"][0]["review_status"], "USER_MANUALLY_ASSOCIATED"
            )
            self.assertFalse(view["events"][0]["authenticated_sender"])
            service.review_status_message(
                {
                    **payload,
                    "decision": "CLEAR_REVIEW",
                    "expected_review_ref": view["review_ref"],
                }
            )
            view = service.status_inbox()
            self.assertIsNone(view["events"][0]["application_id"])
            self.assertEqual(len(view["events"][0]["review_history"]), 2)
            self.assertEqual(
                service.application_workspace({"application_id": "APP-SYN"})["status"],
                "SUBMISSION_UNKNOWN",
            )
            with service.database.connect() as c:
                self.assertEqual(
                    c.execute(
                        "SELECT COUNT(*) FROM private_refs WHERE kind='application_mail_source'"
                    ).fetchone()[0],
                    1,
                )
            self.assertNotIn(
                "Interview invitation",
                service.database.path.read_bytes().decode("latin1"),
            )

    def test_original_page_hash_bounds_and_private_source_lookup(self):
        image = Image.new("RGB", (200, 100), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        raw = output.getvalue()
        result = source_page(raw, ".png", expected_hash=sha256_bytes(raw), page=1)
        self.assertTrue(result["image_data_url"].startswith("data:image/png;base64,"))
        for change in [
            {"page": True},
            {"page": 2},
            {"expected_hash": "sha256:" + "0" * 64},
        ]:
            with self.assertRaises(JobOpsError):
                source_page(
                    raw,
                    ".png",
                    **{"expected_hash": sha256_bytes(raw), "page": 1, **change},
                )
        with project_temp() as root:
            service, private, _, _, _ = support.OnboardingCenterTests().make_service(
                root
            )
            ref, state = service.ensure_state()
            imported = private.import_bytes(
                "onboarding_source_document", raw, synthetic=True
            )
            state["pending_sources"] = [
                {
                    "source_id": "SRC-SYN",
                    "metadata": {
                        "source_id": "SRC-SYN",
                        "secure_ref": imported["secure_ref"],
                        "extension": ".png",
                        "sha256": sha256_bytes(raw),
                        "raw_retained": True,
                    },
                }
            ]
            service._save_state(ref, state)
            self.assertEqual(
                service.source_review_page({"source_id": "SRC-SYN", "page": 1})[
                    "page_count"
                ],
                1,
            )
            with self.assertRaises(JobOpsError):
                service.source_review_page(
                    {"source_id": imported["secure_ref"], "page": 1}
                )

    def test_http_session_origin_and_binary_upload_contract(self):
        with project_temp() as root:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database)
            server = create_server(service, token="synthetic-workbench-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:

                def request(route, body, headers):
                    conn = http.client.HTTPConnection(
                        "127.0.0.1", server.server_port, timeout=5
                    )
                    conn.request("POST", "/api/" + route, body, headers)
                    response = conn.getresponse()
                    status = response.status
                    value = json.loads(response.read())
                    conn.close()
                    return status, value

                headers = {
                    "Content-Type": "application/json",
                    "X-JobOps-Session": "synthetic-workbench-token",
                }
                self.assertEqual(
                    request(
                        "status-inbox", b"{}", {"Content-Type": "application/json"}
                    )[0],
                    403,
                )
                self.assertEqual(
                    request(
                        "status-inbox",
                        b"{}",
                        {**headers, "Origin": "https://untrusted.example.test"},
                    )[0],
                    403,
                )
                self.assertEqual(request("status-inbox", b"{}", headers)[0], 200)
                status, value = request(
                    "import-status-eml",
                    eml(),
                    {**headers, "Content-Type": "application/octet-stream"},
                )
                self.assertEqual(status, 200, value)
                self.assertEqual(value["added"], 1)
                self.assertEqual(request("import-status-eml", eml(), headers)[0], 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
