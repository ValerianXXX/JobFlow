from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from _support import PROJECT, project_temp
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
            with self.assertRaises(JobOpsError) as connection:
                service.connect_ai({"mode": "agent"})
            self.assertEqual(connection.exception.code, "DEMO_AI_CONNECTION_DISABLED")

    def test_demo_review_packet_can_be_decided_locally_without_external_actions(self) -> None:
        with project_temp() as root:
            service, _ = self.make_service(root)
            displayed = service.review_packet(DEMO_APPLICATION_ID)
            self.assertEqual(displayed["status"], "AWAITING_APPROVAL")
            self.assertEqual(displayed["packet"]["job"]["company"], "Synthetic Demo Studio")
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

    def test_demo_server_exposes_only_local_synthetic_state(self) -> None:
        with project_temp() as root:
            service, _ = self.make_service(root)
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
        self.assertIn("合成演示 · 不使用真实资料", app)
        self.assertIn("Synthetic demo · no real data", app)
        self.assertIn('data-target="review"', ui)
        self.assertIn('href="#pendingReviewTitle"', ui)


if __name__ == "__main__":
    unittest.main()
