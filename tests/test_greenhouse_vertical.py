from __future__ import annotations

import json
import shutil
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.adapters import audit_real_external_actions
from jobops.approvals import ApprovalContext
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore


class SyntheticGreenhouseVerticalTests(unittest.TestCase):
    def test_official_route_to_safe_form_plan_and_review_queue_is_one_bound_chain(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            private_temp = Path(tempfile.mkdtemp(prefix="jobflow-private-test-"))
            self.addCleanup(shutil.rmtree, private_temp, True)
            store = WindowsDPAPIStore(
                PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1",
                local_app_data=private_temp,
            )
            onboarding = PrivateOnboarding(database, store)
            orchestrator = JobOpsOrchestrator(PROJECT, database, onboarding)
            refs = orchestrator.secure_onboard_synthetic()
            fixtures = PROJECT / "tests" / "fixtures"

            def forbidden(*args, **kwargs):
                raise AssertionError("network transport attempted")

            with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(
                urllib.request, "urlopen", forbidden
            ):
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

            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            evidence = result["ats_safe_prefill"]
            self.assertEqual(evidence["provider"], "greenhouse")
            self.assertEqual(evidence["fields_discovered"], 5)
            self.assertEqual(evidence["fields_proposed"], 2)
            self.assertEqual(evidence["fields_stopped"], 3)
            self.assertEqual(evidence["browser_adapter_status"], "FAKE_PLAN_VALIDATED")
            self.assertEqual(evidence["fields_modified"], 0)
            self.assertEqual(evidence["browser_actions"], 0)
            self.assertEqual(evidence["network_actions"], 0)
            self.assertEqual(evidence["real_external_actions"], 0)

            with database.connect() as connection:
                route = connection.execute("SELECT route_hash,current_url,route_json FROM source_routes").fetchone()
                fields = connection.execute(
                    "SELECT classification,status,secure_ref,redacted_summary FROM application_fields ORDER BY classification"
                ).fetchall()
                binding = json.loads(connection.execute("SELECT context_json FROM application_bindings").fetchone()[0])
            self.assertEqual(json.loads(route["route_json"])["provider"], "greenhouse")
            self.assertEqual(route["route_hash"], binding["source_route_hash"])
            self.assertEqual(binding["form_snapshot_hash"], evidence["form_snapshot_hash"])
            self.assertEqual(sum(row["status"] == "READY" for row in fields), 2)
            self.assertEqual(sum(row["status"] == "STOP_REQUIRED" for row in fields), 1)
            self.assertEqual(sum(row["status"] == "SEPARATE_ACTION_GATED" for row in fields), 2)
            self.assertTrue(all(row["secure_ref"] is None or str(row["secure_ref"]).startswith("secure-ref:") for row in fields))

            packet = OnboardingCenterService(PROJECT, database, onboarding).review_packet(result["application_id"])["packet"]
            serialized = json.dumps(packet, ensure_ascii=False)
            self.assertNotIn("DO_NOT_RETAIN_SYNTHETIC_TOKEN", serialized)
            self.assertIn("Full name", serialized)
            self.assertEqual(packet["source_route"]["provider"], "greenhouse")
            self.assertEqual(len(packet["form_questions"]), 5)
            self.assertEqual(audit_real_external_actions(database)["attempt_count"], 0)
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

            production_gateway = ExternalActionGateway(database, ExternalActionPolicy.production_disabled())
            with self.assertRaises(JobOpsError) as blocked:
                production_gateway.begin_submission(ApprovalContext.from_dict(binding))
            self.assertEqual(blocked.exception.code, "PHASE_NOT_AUTHORIZED")
            self.assertEqual(audit_real_external_actions(database)["attempt_count"], 1)
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

            purge = onboarding.purge_synthetic()
            self.assertGreater(purge["synthetic_refs_deleted"], 0)
            self.assertFalse(any((temp / "local" / "JobOps" / "private").glob("*.dpapi")))


if __name__ == "__main__":
    unittest.main()
