from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from _support import PROJECT, project_temp
from jobops.adapters import audit_real_external_actions
from jobops.demo import DEMO_APPLICATION_ID, DEMO_SOURCE, create_demo_service
from jobops.errors import JobOpsError
from jobops.onboarding_server import create_server
from jobops.util import sha256_bytes


class MemorySecureStore:
    def __init__(self, private_root: Path) -> None:
        self.private_root = private_root
        self.private_root.mkdir(parents=True)
        self.values: dict[str, bytes] = {}
        self.counter = 0

    def put_bytes(self, value: bytes, *, reference: str | None = None) -> dict[str, str | bool]:
        if reference is None:
            self.counter += 1
            reference = f"secure-ref:SYNTHETICDEMO{self.counter:04d}"
        self.values[reference] = bytes(value)
        return {"secure_ref": reference, "ciphertext_sha256": sha256_bytes(b"cipher:" + value), "created": True}

    def get_bytes(self, reference: str) -> bytes:
        return self.values[reference]

    def ciphertext_sha256(self, reference: str) -> str:
        return sha256_bytes(b"cipher:" + self.values[reference])

    def test(self, reference: str) -> bool:
        return reference in self.values

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class SyntheticDemoTests(unittest.TestCase):
    def make_service(self, root: Path):  # type: ignore[no-untyped-def]
        store = MemorySecureStore(root / "local" / "private")
        return create_demo_service(PROJECT, root / "runtime", secure_store=store), store

    def test_demo_is_synthetic_temporary_and_blocks_real_intake(self) -> None:
        with project_temp() as root:
            service, store = self.make_service(root)
            bootstrap = service.bootstrap()
            self.assertTrue(bootstrap["demo_mode"])
            self.assertTrue(bootstrap["demo_constraints"]["synthetic_only"])
            self.assertFalse(bootstrap["demo_constraints"]["file_intake_enabled"])
            self.assertFalse(bootstrap["demo_constraints"]["ai_connection_enabled"])
            self.assertTrue(bootstrap["demo_constraints"]["isolated_execution_rehearsal"])
            self.assertEqual(bootstrap["demo_constraints"]["application_id"], DEMO_APPLICATION_ID)
            self.assertEqual(bootstrap["real_external_actions"], 0)
            self.assertEqual(len(bootstrap["sources"]), 1)
            self.assertGreaterEqual(len(bootstrap["claims"]), 2)
            self.assertEqual(len(bootstrap["conflicts"]), 1)
            self.assertEqual(bootstrap["dashboard"]["queue"]["awaiting_approval"], 1)
            self.assertEqual(len(bootstrap["dashboard"]["pending_applications"]), 1)
            self.assertEqual(
                bootstrap["dashboard"]["pending_applications"][0]["company"],
                "Synthetic Demo Studio",
            )
            self.assertTrue(store.values)
            with self.assertRaises(JobOpsError) as intake:
                service.preview_source("resume", ".txt", b"real user data")
            self.assertEqual(intake.exception.code, "DEMO_FILE_INTAKE_DISABLED")
            with self.assertRaises(JobOpsError) as direct_intake:
                service.import_source("resume", ".txt", b"real user data")
            self.assertEqual(direct_intake.exception.code, "DEMO_FILE_INTAKE_DISABLED")
            with self.assertRaises(JobOpsError) as discovery_intake:
                service.discover_official_jobs(
                    b"<html>real saved page</html>",
                    official_entry_url="https://example.test/careers",
                    company_domain="example.test",
                    source_format="html",
                )
            self.assertEqual(discovery_intake.exception.code, "DEMO_FILE_INTAKE_DISABLED")
            with self.assertRaises(JobOpsError) as connection:
                service.connect_ai({"mode": "agent"})
            self.assertEqual(connection.exception.code, "DEMO_AI_CONNECTION_DISABLED")

    def test_demo_review_packet_can_be_decided_locally_without_external_actions(self) -> None:
        with project_temp() as root:
            service, _ = self.make_service(root)
            displayed = service.review_packet(DEMO_APPLICATION_ID)
            self.assertEqual(displayed["status"], "AWAITING_APPROVAL")
            self.assertEqual(displayed["packet"]["job"]["company"], "Synthetic Demo Studio")
            questions = displayed["packet"]["form_questions"]
            self.assertEqual(
                {item["redacted_summary"] for item in questions if item["action"].startswith("PREFILL")},
                {"PRIVATE_VALUE_PRESENT", "PUBLIC_VALUE_HASH_PRESENT"},
            )
            self.assertTrue(any(item["answer_key"] == "resume" for item in displayed["packet"]["sensitive_fields"]))
            self.assertEqual(displayed["packet"]["external_actions"], ["upload_material", "submit_application"])
            outcome = service.decide_review_packet(
                {
                    "application_id": DEMO_APPLICATION_ID,
                    "decision": "APPROVE",
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "user_confirmed": True,
                }
            )
            self.assertEqual(outcome["status"], "APPROVED")
            self.assertEqual(outcome["real_external_actions"], 0)
            dashboard = service.bootstrap()["dashboard"]
            self.assertEqual(dashboard["queue"]["awaiting_approval"], 0)
            self.assertEqual(dashboard["safety"]["external_action_attempts"], 0)
            self.assertEqual(dashboard["safety"]["real_external_actions"], 0)

    def test_demo_can_rehearse_the_complete_two_confirmation_lifecycle(self) -> None:
        with project_temp() as root:
            service, _ = self.make_service(root)
            displayed = service.review_packet(DEMO_APPLICATION_ID)
            service.decide_review_packet({
                "application_id": DEMO_APPLICATION_ID,
                "decision": "APPROVE",
                "expected_packet_hash": displayed["packet"]["content_hash"],
                "user_confirmed": True,
            })
            with self.assertRaises(JobOpsError) as missing_rehearsal_consent:
                service.prepare_synthetic_execution({
                    "application_id": DEMO_APPLICATION_ID,
                    "user_confirmed": False,
                })
            self.assertEqual(
                missing_rehearsal_consent.exception.code,
                "SYNTHETIC_EXECUTION_CONFIRMATION_REQUIRED",
            )
            prepared = service.prepare_synthetic_execution({
                "application_id": DEMO_APPLICATION_ID,
                "user_confirmed": True,
            })
            self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
            self.assertTrue(prepared["temporary_files_removed"])
            self.assertEqual(prepared["network_actions"], 0)
            self.assertEqual(prepared["real_external_actions"], 0)
            with self.assertRaises(JobOpsError) as missing_final_consent:
                service.complete_synthetic_execution({
                    "application_id": DEMO_APPLICATION_ID,
                    "run_id": prepared["run_id"],
                    "user_confirmed": False,
                })
            self.assertEqual(missing_final_consent.exception.code, "FINAL_SUBMISSION_CONFIRMATION_REQUIRED")
            completed = service.complete_synthetic_execution({
                "application_id": DEMO_APPLICATION_ID,
                "run_id": prepared["run_id"],
                "user_confirmed": True,
            })
            self.assertEqual(completed["status"], "CONFIRMED")
            self.assertEqual(completed["checkpoint_count"], 8)
            self.assertEqual(completed["network_actions"], 0)
            self.assertEqual(completed["real_external_actions"], 0)
            dashboard = service.bootstrap()["dashboard"]
            self.assertEqual(dashboard["execution_runs"][0]["status"], "CONFIRMED")
            self.assertEqual(audit_real_external_actions(service.database)["real_external_actions"], 0)

    def test_demo_server_exposes_only_local_synthetic_state(self) -> None:
        with project_temp() as root:
            service, _ = self.make_service(root)
            displayed = service.review_packet(DEMO_APPLICATION_ID)
            service.decide_review_packet({
                "application_id": DEMO_APPLICATION_ID,
                "decision": "APPROVE",
                "expected_packet_hash": displayed["packet"]["content_hash"],
                "user_confirmed": True,
            })
            server = create_server(service, token="synthetic-demo-test")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "GET",
                    "/session/synthetic-demo-test/api/bootstrap",
                    headers={"X-JobOps-Session": "synthetic-demo-test"},
                )
                response = connection.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                response.close()
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(body["demo_mode"])
                self.assertEqual(body["privacy"]["network"], "LOCALHOST_ONLY")
                self.assertEqual(body["real_external_actions"], 0)
                serialized = json.dumps(body)
                self.assertIn("Synthetic Demo Studio", serialized)
                self.assertNotIn(str(PROJECT), serialized)
                self.assertNotIn("@", serialized)

                def post(route: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
                    request = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    request.request(
                        "POST",
                        f"/session/synthetic-demo-test/api/{route}",
                        body=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "X-JobOps-Session": "synthetic-demo-test",
                        },
                    )
                    result = request.getresponse()
                    result_body = json.loads(result.read().decode("utf-8"))
                    status = result.status
                    result.close()
                    request.close()
                    return status, result_body

                status, prepared = post("prepare-synthetic-execution", {
                    "application_id": DEMO_APPLICATION_ID,
                    "user_confirmed": True,
                })
                self.assertEqual(status, 200)
                self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
                self.assertEqual(prepared["real_external_actions"], 0)
                status, completed = post("complete-synthetic-execution", {
                    "application_id": DEMO_APPLICATION_ID,
                    "run_id": prepared["run_id"],
                    "user_confirmed": True,
                })
                self.assertEqual(status, 200)
                self.assertEqual(completed["status"], "CONFIRMED")
                self.assertEqual(completed["network_actions"], 0)
                self.assertEqual(completed["real_external_actions"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_public_demo_launchers_are_bilingual_and_path_safe(self) -> None:
        launcher = (PROJECT / "Start JobFlow Demo.cmd").read_text(encoding="utf-8")
        script = (PROJECT / "scripts" / "start-jobflow-demo.ps1").read_text(encoding="utf-8")
        ui = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('cd /d "%~dp0"', launcher)
        self.assertIn('Join-Path $PSScriptRoot ".."', script)
        self.assertIn('"jobops.cli", "demo"', script)
        self.assertIn('id="demoBanner"', ui)
        self.assertIn('id="demoExecutionPanel"', ui)
        self.assertIn("合成演示 · 不使用真实资料", app)
        self.assertIn("Synthetic demo · no real data", app)
        self.assertIn("renderPrefillProposal", app)
        self.assertIn('api("prepare-synthetic-execution"', app)
        self.assertIn('api("complete-synthetic-execution"', app)
        self.assertIn('data-target="review"', ui)
        self.assertIn('href="#pendingReviewTitle"', ui)


if __name__ == "__main__":
    unittest.main()
