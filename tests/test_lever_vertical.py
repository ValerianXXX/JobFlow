from __future__ import annotations

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
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.synthetic_lifecycle import SyntheticApplicationLifecycle


class SyntheticLeverVerticalTests(unittest.TestCase):
    def test_saved_lever_form_reaches_verified_fake_receipt_without_transport(self) -> None:
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

            def forbidden(*args, **kwargs):
                raise AssertionError("network, browser, or email transport attempted")

            with patch.object(socket, "socket", forbidden), patch.object(
                socket, "getaddrinfo", forbidden,
            ), patch.object(socket, "create_connection", forbidden), patch.object(
                urllib.request, "urlopen", forbidden,
            ), patch.object(smtplib, "SMTP", forbidden):
                result = orchestrator.run_to_awaiting(
                    fixtures / "synthetic-forward-jd.txt",
                    profile_ref=refs["profile_ref"],
                    master_resume_ref=refs["master_resume_ref"],
                    answer_bank_ref=refs["answer_bank_ref"],
                    route_fixture=fixtures / "synthetic-lever-route.json",
                    form_fixture=fixtures / "synthetic-lever-form.html",
                    research_fixture=fixtures / "synthetic-research.html",
                    synthetic=True,
                )
                self.assertEqual(result["ats_safe_prefill"]["provider"], "lever")
                service = OnboardingCenterService(PROJECT, database, onboarding)
                displayed = service.review_packet(str(result["application_id"]))
                self.assertEqual(displayed["field_resolution"]["unresolved_count"], 0)
                approved = service.decide_review_packet({
                    "application_id": result["application_id"],
                    "decision": "APPROVE",
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "user_confirmed": True,
                })
                self.assertEqual(approved["status"], "APPROVED")
                lifecycle = SyntheticApplicationLifecycle(database, onboarding)
                prepared = lifecycle.prepare_until_final_authorization(
                    application_id=str(result["application_id"]),
                    user_confirmed=True,
                )
                self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
                self.assertEqual(prepared["ephemeral_field_count"], 2)
                self.assertEqual(prepared["confirmed_stop_field_count"], 0)
                completed = lifecycle.complete_with_fresh_authorization(
                    application_id=str(result["application_id"]),
                    run_id=str(prepared["run_id"]),
                    user_confirmed=True,
                    fake_confirmation_number="SYNTHETIC-LEVER-RECEIPT",
                )

            self.assertEqual(completed["status"], "CONFIRMED")
            self.assertEqual(completed["checkpoint_count"], 8)
            self.assertEqual(completed["network_actions"], 0)
            self.assertEqual(completed["real_external_actions"], 0)
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)
            purged = onboarding.purge_synthetic()
            self.assertGreater(purged["synthetic_refs_deleted"], 0)
            self.assertFalse(any((private_temp / "JobOps" / "private").glob("*.dpapi")))


if __name__ == "__main__":
    unittest.main()
