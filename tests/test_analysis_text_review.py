"""Synthetic source-review lifecycle, privacy and stale-coordinate contracts."""
from __future__ import annotations

import http.client
import json
import threading
import unittest
from unittest import mock

from _support import project_temp
import test_onboarding_center as support
from jobops.ai_runtime import AI_QUALITY_CONTRACT
from jobops.errors import JobOpsError
from jobops.onboarding_server import create_server
from jobops.util import canonical_json, sha256_bytes


TEXT = "Synthetic transcript marker.\n\n仅远程工作，入职时间待确认。\n<img src=x onerror=alert(1)>\n"


class AnalysisTextReviewTests(unittest.TestCase):
    def prepare(self, root, text=TEXT, **summary_changes):
        service, private, store, _, _ = support.OnboardingCenterTests().make_service(root)
        summary = {"analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "quality_contract": AI_QUALITY_CONTRACT,
                   "ai_chunks": 1, "ai_input_characters": len(text), "ai_covered_characters": len(text),
                   "ai_input_truncated": False, **summary_changes}
        with mock.patch.object(service.ai_engine, "analyze_document", return_value=([], summary)):
            receipt = service.preview_source("supporting_material", ".txt", text.encode())
        self.addCleanup(service.close)
        source = service.bootstrap()["pending_sources"][0]
        descriptor = source["metadata"].get("analysis_text_review", {})
        payload = {"source_id": receipt["source_id"], "expected_source_sha256": sha256_bytes(text.encode()),
                   "expected_text_sha256": descriptor.get("text_sha256"), "line_start": 1}
        return service, private, store, payload

    def test_exact_numbered_input_is_explicit_and_not_in_bootstrap_or_project(self):
        with project_temp() as root:
            service, _, _, payload = self.prepare(root)
            with mock.patch.object(service.ai_engine, "analyze_document", side_effect=AssertionError("No model on read")):
                result = service.source_review_text(payload)
            self.assertEqual(result["lines"], [{"number": i, "text": line} for i, line in enumerate(TEXT.strip().splitlines(), 1)])
            self.assertFalse(result["fact_completeness_verified"])
            self.assertEqual(result["real_external_actions"], 0)
            self.assertNotIn("Synthetic transcript marker", json.dumps(service.bootstrap()))
            for path in service.project.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"Synthetic transcript marker", path.read_bytes(), str(path))

    def test_confirmation_and_service_reopen_preserve_snapshot_without_claim_selection(self):
        with project_temp() as root:
            service, _, _, payload = self.prepare(root)
            before = service.bootstrap()
            service.commit_source(payload["source_id"], [])
            fresh = type(service)(service.project, service.database, service.onboarding, ai_engine=service.ai_engine)
            self.addCleanup(fresh.close)
            self.assertEqual(fresh.source_review_text(payload)["lines"][2]["text"], "仅远程工作，入职时间待确认。")
            after = fresh.bootstrap()
            self.assertEqual(after["answers"], before["answers"])
            self.assertEqual(after["claims"], before["claims"])
            self.assertEqual(after["real_external_actions"], 0)
            self.assertNotIn("_analysis_transcript", json.dumps(after))

    def test_reanalysis_stales_old_coordinates_and_discard_restores_saved_snapshot(self):
        with project_temp() as root:
            service, _, _, payload = self.prepare(root)
            service.commit_source(payload["source_id"], [])
            changed = "A new extraction.\n" + TEXT.strip()
            summary = {"analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "quality_contract": AI_QUALITY_CONTRACT,
                       "ai_chunks": 1, "ai_input_characters": len(changed), "ai_covered_characters": len(changed), "ai_input_truncated": False}
            with mock.patch.object(service, "_extract_text", return_value=(changed, 0, {})), mock.patch.object(service.ai_engine, "analyze_document", return_value=([], summary)):
                service.reprocess_source(payload["source_id"])
            with self.assertRaises(JobOpsError) as caught:
                service.source_review_text(payload)
            self.assertEqual(caught.exception.code, "SOURCE_TEXT_REVIEW_STALE")
            descriptor = service.bootstrap()["pending_sources"][0]["metadata"]["analysis_text_review"]
            self.assertEqual(service.source_review_text({**payload, "expected_text_sha256": descriptor["text_sha256"]})["lines"][0]["text"], "A new extraction.")
            service.discard_source_preview(payload["source_id"])
            self.assertEqual(service.source_review_text(payload)["lines"][0]["text"], "Synthetic transcript marker.")

    def test_changed_original_or_snapshot_fails_closed(self):
        for changed_part in ("original", "text"):
            with self.subTest(changed_part=changed_part), project_temp() as root:
                service, private, _, payload = self.prepare(root)
                ref, state = service.ensure_state()
                metadata = state["pending_sources"][0]["metadata"]
                if changed_part == "original":
                    private.rotate(metadata["secure_ref"], b"Different original")
                else:
                    metadata["_analysis_transcript"]["text"] = "Changed extracted text"
                    service._save_state(ref, state)
                with self.assertRaises(JobOpsError) as caught:
                    service.source_review_text(payload)
                self.assertEqual(caught.exception.code, "SOURCE_TEXT_REVIEW_MISMATCH")
                self.assertNotIn("Changed extracted", str(caught.exception))

    def test_request_rejects_unknown_keys_bad_ranges_and_wrong_source(self):
        with project_temp() as root:
            service, _, _, payload = self.prepare(root)
            for change in ({"line_start": True}, {"line_start": 0}, {"line_start": 5}, {"line_start": "1"},
                           {"secure_ref": "secure-ref:OTHER"}, {"source_id": "OTHER"}, {"expected_text_sha256": None}):
                with self.subTest(change=change), self.assertRaises(JobOpsError):
                    service.source_review_text({**payload, **change})

    def test_pages_are_bounded_and_never_skip_blank_lines(self):
        text = "\n".join(f"Synthetic line {i}." if i % 3 else "" for i in range(1, 246))
        with project_temp() as root:
            service, _, _, payload = self.prepare(root, text)
            collected, start = [], 1
            while start:
                result = service.source_review_text({**payload, "line_start": start})
                self.assertLessEqual(len(result["lines"]), 100)
                self.assertLessEqual(sum(len(item["text"]) for item in result["lines"]), 32768)
                collected.extend(item["text"] for item in result["lines"])
                start = result["next_line"]
            self.assertEqual(collected, text.splitlines())

    def test_legacy_segmented_and_oversize_sources_have_no_misleading_coordinates(self):
        for mode in ("legacy", "segmented", "oversize"):
            with self.subTest(mode=mode), project_temp() as root:
                text = TEXT if mode != "oversize" else "Synthetic long material.\n" * 90000
                service, _, _, payload = self.prepare(root, text, **({"segmented_oversize_lines": 1} if mode == "segmented" else {}))
                if mode == "legacy":
                    ref, state = service.ensure_state()
                    state["pending_sources"][0]["metadata"].pop("_analysis_transcript", None)
                    service._save_state(ref, state)
                with self.assertRaises(JobOpsError):
                    service.source_review_text(payload)

    def test_discard_and_delete_revoke_access(self):
        for keep in (False, True):
            with self.subTest(keep=keep), project_temp() as root:
                service, _, _, payload = self.prepare(root)
                if keep:
                    service.commit_source(payload["source_id"], [])
                    service.delete_source(payload["source_id"], user_confirmed=True)
                else:
                    service.discard_source_preview(payload["source_id"])
                with self.assertRaises(JobOpsError):
                    service.source_review_text(payload)

    def test_http_session_origin_cache_and_no_mutation(self):
        with project_temp() as root:
            service, _, _, payload = self.prepare(root)
            before = service.onboarding.read_bytes(service.ensure_state()[0])
            server = create_server(service, token="synthetic-transcript-session")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for headers, expected in (({}, 403), ({"X-JobOps-Session": "synthetic-transcript-session", "Origin": "https://untrusted.example.test"}, 403),
                                          ({"X-JobOps-Session": "synthetic-transcript-session"}, 200)):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    conn.request("POST", "/api/source-review-text", canonical_json(payload), {"Content-Type": "application/json", **headers})
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertIn("no-store", response.getheader("Cache-Control", ""))
                    body = json.loads(response.read())
                    conn.close()
                    if expected == 200:
                        self.assertEqual(body["lines"][0]["number"], 1)
                    else:
                        self.assertNotIn("Synthetic transcript marker", str(body))
                self.assertEqual(service.onboarding.read_bytes(service.ensure_state()[0]), before)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
