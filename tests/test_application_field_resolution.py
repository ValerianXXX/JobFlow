from __future__ import annotations

import json
import shutil
import smtplib
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.adapters import audit_real_external_actions
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.synthetic_lifecycle import SyntheticApplicationLifecycle


class ApplicationFieldResolutionTests(unittest.TestCase):
    def test_greenhouse_question_is_encrypted_rebound_and_required_before_approval(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            private_temp = Path(tempfile.mkdtemp(prefix="jobflow-private-test-"))
            self.addCleanup(shutil.rmtree, private_temp, True)
            onboarding = PrivateOnboarding(
                database,
                WindowsDPAPIStore(
                    PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1",
                    local_app_data=private_temp,
                ),
            )
            orchestrator = JobOpsOrchestrator(PROJECT, database, onboarding)
            refs = orchestrator.secure_onboard_synthetic()
            fixtures = PROJECT / "tests" / "fixtures"
            result = orchestrator.run_to_awaiting(
                fixtures / "synthetic-forward-jd.txt",
                profile_ref=refs["profile_ref"],
                master_resume_ref=refs["master_resume_ref"],
                answer_bank_ref=refs["answer_bank_ref"],
                route_fixture=fixtures / "synthetic-greenhouse-route.json",
                form_fixture=fixtures / "synthetic-greenhouse-form.html",
                research_fixture=fixtures / "synthetic-research.html",
                synthetic=True,
            )
            service = OnboardingCenterService(PROJECT, database, onboarding)
            displayed = service.review_packet(str(result["application_id"]))
            field_state = displayed["field_resolution"]
            self.assertEqual(field_state["unresolved_count"], 1)
            self.assertEqual(field_state["separate_action_gate_count"], 2)
            question = field_state["unresolved_fields"][0]
            self.assertEqual(question["classification"], "work_authorization_stop")
            self.assertIn("legally authorized", question["label"])
            self.assertEqual(question["options"], ["Yes", "No"])

            with self.assertRaises(JobOpsError) as premature:
                service.decide_review_packet({
                    "application_id": result["application_id"],
                    "decision": "APPROVE",
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "user_confirmed": True,
                })
            self.assertEqual(premature.exception.code, "APPLICATION_FIELDS_UNRESOLVED")

            invalid_resolutions = [{
                "control_ref": question["control_ref"],
                "decision": "CONFIRMED_VALUE",
                "value": "Maybe",
            }]
            with self.assertRaises(JobOpsError) as invalid:
                service.resolve_application_fields({
                    "application_id": result["application_id"],
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "resolutions": invalid_resolutions,
                    "user_confirmed": True,
                })
            self.assertEqual(invalid.exception.code, "APPLICATION_FIELD_OPTION_INVALID")
            self.assertEqual(invalid_resolutions[0]["value"], "")

            resolutions = [{
                "control_ref": question["control_ref"],
                "decision": "CONFIRMED_VALUE",
                "value": "Yes",
            }]
            rebound = service.resolve_application_fields({
                "application_id": result["application_id"],
                "expected_packet_hash": displayed["packet"]["content_hash"],
                "resolutions": resolutions,
                "user_confirmed": True,
            })
            self.assertEqual(rebound["status"], "JOB_SPECIFIC_ANSWERS_ENCRYPTED")
            self.assertEqual(rebound["packet_version"], 2)
            self.assertEqual(rebound["remaining_unresolved_count"], 0)
            self.assertNotIn("secure_ref", rebound)
            self.assertEqual(rebound["private_values_emitted"], 0)
            self.assertEqual(rebound["real_external_actions"], 0)
            self.assertEqual(resolutions[0]["value"], "")

            current = service.review_packet(str(result["application_id"]))
            self.assertEqual(current["packet_version"], 2)
            self.assertEqual(current["field_resolution"]["status"], "READY_FOR_PACKET_APPROVAL")
            self.assertEqual(current["field_resolution"]["unresolved_count"], 0)
            self.assertEqual(current["stopped_fields"], 0)
            protected = next(
                item for item in current["packet"]["sensitive_fields"]
                if item["classification"] == "work_authorization_stop"
            )
            self.assertEqual(protected["status"], "RESOLVED_FOR_APPLICATION")
            self.assertEqual(protected["redacted_summary"], "ENCRYPTED_JOB_SPECIFIC_CONFIRMATION")

            with database.connect() as connection:
                packets = connection.execute(
                    "SELECT packet_version,status FROM review_packets ORDER BY packet_version"
                ).fetchall()
                field = connection.execute(
                    """SELECT status,secure_ref,redacted_summary FROM application_fields
                       WHERE classification='work_authorization_stop'"""
                ).fetchone()
                bundle_ref = str(field["secure_ref"])
                ordinary_rows = [
                    tuple(row) for table in (
                        "applications", "application_bindings", "application_fields",
                        "review_packets", "events", "approvals",
                    ) for row in connection.execute(f'SELECT * FROM "{table}"')
                ]
            self.assertEqual([tuple(row) for row in packets], [(1, "NEEDS_REVISION"), (2, "AWAITING_APPROVAL")])
            self.assertEqual(field["status"], "RESOLVED_FOR_APPLICATION")
            self.assertTrue(bundle_ref.startswith("secure-ref:"))
            self.assertEqual(field["redacted_summary"], "ENCRYPTED_JOB_SPECIFIC_CONFIRMATION")
            serialized_rows = json.dumps(ordinary_rows, ensure_ascii=False)
            self.assertNotIn('"value": "Yes"', serialized_rows)
            self.assertNotIn('"value":"Yes"', serialized_rows)
            encrypted_bundle = json.loads(onboarding.read_bytes(bundle_ref))
            self.assertEqual(encrypted_bundle["fields"][0]["value"], "Yes")

            with self.assertRaises(JobOpsError) as stale:
                service.decide_review_packet({
                    "application_id": result["application_id"],
                    "decision": "APPROVE",
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "user_confirmed": True,
                })
            self.assertEqual(stale.exception.code, "REVIEW_PACKET_STALE")

            approved = service.decide_review_packet({
                "application_id": result["application_id"],
                "decision": "APPROVE",
                "expected_packet_hash": current["packet"]["content_hash"],
                "user_confirmed": True,
            })
            self.assertEqual(approved["status"], "APPROVED")
            self.assertEqual(audit_real_external_actions(database)["attempt_count"], 0)
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

            lifecycle = SyntheticApplicationLifecycle(database, onboarding)

            def forbidden(*args, **kwargs):
                raise AssertionError("network, browser, email, or child-process transport attempted")

            with patch.object(socket, "socket", forbidden), patch.object(
                socket, "getaddrinfo", forbidden,
            ), patch.object(socket, "create_connection", forbidden), patch.object(
                urllib.request, "urlopen", forbidden,
            ), patch.object(smtplib, "SMTP", forbidden):
                prepared = lifecycle.prepare_until_final_authorization(
                    application_id=str(result["application_id"]),
                    user_confirmed=True,
                )
                self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
                self.assertEqual(prepared["ephemeral_field_count"], 3)
                self.assertEqual(prepared["ephemeral_file_count"], 1)
                self.assertEqual(prepared["confirmed_stop_field_count"], 1)
                self.assertTrue(prepared["temporary_files_removed"])
                self.assertFalse(prepared["production_activation"])
                with self.assertRaises(JobOpsError) as final_gate:
                    lifecycle.complete_with_fresh_authorization(
                        application_id=str(result["application_id"]),
                        run_id=str(prepared["run_id"]),
                        user_confirmed=False,
                        fake_confirmation_number="SYNTHETIC-GREENHOUSE-RECEIPT",
                    )
                self.assertEqual(final_gate.exception.code, "FINAL_SUBMISSION_CONFIRMATION_REQUIRED")
                completed = lifecycle.complete_with_fresh_authorization(
                    application_id=str(result["application_id"]),
                    run_id=str(prepared["run_id"]),
                    user_confirmed=True,
                    fake_confirmation_number="SYNTHETIC-GREENHOUSE-RECEIPT",
                )
            self.assertEqual(completed["status"], "CONFIRMED")
            self.assertEqual(completed["checkpoint_count"], 8)
            self.assertEqual(completed["network_actions"], 0)
            self.assertEqual(completed["real_external_actions"], 0)
            with database.connect() as connection:
                application_status = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?",
                    (result["application_id"],),
                ).fetchone()[0]
                receipt_count = connection.execute(
                    "SELECT COUNT(*) FROM receipts WHERE application_id=?",
                    (result["application_id"],),
                ).fetchone()[0]
            self.assertEqual(application_status, "CONFIRMED")
            self.assertEqual(receipt_count, 1)
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

            purged = onboarding.purge_synthetic()
            self.assertGreater(purged["synthetic_refs_deleted"], 0)
            private_root = private_temp / "JobOps" / "private"
            self.assertFalse(any(private_root.glob("*.dpapi")))


if __name__ == "__main__":
    unittest.main()
