from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore


class ApplicationNarrativeTests(unittest.TestCase):
    def _run_cover_textarea_application(self, temp: Path, *, form_html: str | None = None):
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        private_temp = Path(tempfile.mkdtemp(prefix="jobflow-private-narrative-test-"))
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
        form_fixture = fixtures / "synthetic-cover-textarea-form.html"
        if form_html is not None:
            form_fixture = temp / "synthetic-cover-textarea-form.html"
            form_fixture.write_text(form_html, encoding="utf-8")
        result = orchestrator.run_to_awaiting(
            fixtures / "synthetic-forward-jd.txt",
            profile_ref=refs["profile_ref"],
            master_resume_ref=refs["master_resume_ref"],
            answer_bank_ref=refs["answer_bank_ref"],
            route_fixture=fixtures / "synthetic-greenhouse-route.json",
            form_fixture=form_fixture,
            research_fixture=fixtures / "synthetic-research.html",
            synthetic=True,
        )
        return database, onboarding, OnboardingCenterService(PROJECT, database, onboarding), result

    def test_cover_textarea_generation_preview_and_encrypted_resolution_are_application_bound(self) -> None:
        with project_temp() as temp:
            database, onboarding, service, result = self._run_cover_textarea_application(temp)
            application_id = str(result["application_id"])
            displayed = service.review_packet(application_id)
            unresolved = displayed["field_resolution"]["unresolved_fields"]
            self.assertEqual(len(unresolved), 1)
            field = unresolved[0]
            self.assertEqual(field["answer_key"], "cover_letter")
            self.assertEqual(field["control_type"], "textarea")
            self.assertTrue(field["generated_narrative_available"])
            self.assertIn("USE_GENERATED_NARRATIVE", field["allowed_decisions"])
            self.assertEqual(field["max_characters"], 1200)
            cover_binding = displayed["packet"]["material_plan"]["cover_letter"]
            self.assertEqual(cover_binding["narrative_target_status"], "BOUND_EXACT_CONTROL")
            self.assertEqual(cover_binding["narrative_target_count"], 1)
            self.assertEqual(cover_binding["narrative_control_ref"], field["control_ref"])
            self.assertEqual(cover_binding["narrative_max_characters"], 1200)

            preview = service.preview_application_narrative({
                "application_id": application_id,
                "expected_packet_hash": displayed["packet"]["content_hash"],
                "control_ref": field["control_ref"],
            })
            self.assertEqual(preview["status"], "APPLICATION_NARRATIVE_PREVIEW_READY")
            self.assertTrue(preview["narrative"].startswith("Dear Hiring Team,"))
            self.assertLessEqual(len(preview["narrative"]), 4_000)
            self.assertLessEqual(len(preview["narrative"]), 1200)
            self.assertEqual(preview["max_characters"], 1200)
            self.assertEqual(
                preview["source_content_hash"],
                displayed["packet"]["material_plan"]["cover_letter"]["narrative_sha256"],
            )

            with database.connect() as connection:
                narrative_row = connection.execute(
                    "SELECT path,content_hash,claim_ids_json FROM materials WHERE application_id=? AND kind='application_narrative'",
                    (application_id,),
                ).fetchone()
                cover_rows = connection.execute(
                    "SELECT kind,claim_ids_json FROM materials WHERE application_id=? AND kind IN ('cover_letter_docx','cover_letter_pdf','application_narrative') ORDER BY kind",
                    (application_id,),
                ).fetchall()
                approvals_before = int(connection.execute(
                    "SELECT COUNT(*) FROM approvals WHERE application_id=? AND status='APPROVED'",
                    (application_id,),
                ).fetchone()[0])
                dump_before = "\n".join(connection.iterdump())
            self.assertTrue(str(narrative_row["path"]).startswith("secure-ref:"))
            self.assertEqual(str(narrative_row["content_hash"]), preview["source_content_hash"])
            self.assertEqual(len({str(row["claim_ids_json"]) for row in cover_rows}), 1)
            self.assertNotIn(preview["narrative"], dump_before)
            self.assertEqual(approvals_before, 0)
            plaintext_suffixes = {".txt", ".md", ".docx", ".pdf", ".json"}
            self.assertFalse(any(
                path.is_file() and path.suffix.casefold() in plaintext_suffixes
                for path in onboarding.store.private_root.rglob("*")
            ))

            with self.assertRaises(JobOpsError) as wrong_field:
                service.preview_application_narrative({
                    "application_id": application_id,
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "control_ref": "resume",
                })
            self.assertEqual(wrong_field.exception.code, "APPLICATION_NARRATIVE_FIELD_INVALID")

            browser_input = [{
                "control_ref": field["control_ref"],
                "decision": "USE_GENERATED_NARRATIVE",
                "value": "",
            }]
            rebound = service.resolve_application_fields({
                "application_id": application_id,
                "expected_packet_hash": displayed["packet"]["content_hash"],
                "resolutions": browser_input,
                "user_confirmed": True,
            })
            self.assertEqual(rebound["status"], "JOB_SPECIFIC_ANSWERS_ENCRYPTED")
            self.assertEqual(browser_input[0]["value"], "")

            with database.connect() as connection:
                bundle_ref = str(connection.execute(
                    "SELECT secure_ref FROM application_fields WHERE application_id=? AND status='RESOLVED_FOR_APPLICATION'",
                    (application_id,),
                ).fetchone()[0])
                approvals_after = int(connection.execute(
                    "SELECT COUNT(*) FROM approvals WHERE application_id=? AND status='APPROVED'",
                    (application_id,),
                ).fetchone()[0])
                application_status = str(connection.execute(
                    "SELECT status FROM applications WHERE application_id=?",
                    (application_id,),
                ).fetchone()[0])
                dump_after = "\n".join(connection.iterdump())
            bundle = json.loads(onboarding.read_bytes(bundle_ref))
            stored = bundle["fields"][0]
            self.assertEqual(stored["value"], preview["narrative"])
            self.assertEqual(stored["value_origin"], "APPLICATION_NARRATIVE")
            self.assertEqual(stored["source_content_hash"], preview["source_content_hash"])
            self.assertNotIn(preview["narrative"], dump_after)
            self.assertEqual(approvals_after, 0)
            self.assertEqual(application_status, "AWAITING_APPROVAL")

            with self.assertRaises(JobOpsError) as injected:
                service.resolve_application_fields({
                    "application_id": application_id,
                    "expected_packet_hash": rebound["packet_hash"],
                    "resolutions": [{
                        "control_ref": field["control_ref"],
                        "decision": "USE_GENERATED_NARRATIVE",
                        "value": "browser supplied text",
                    }],
                    "user_confirmed": True,
                })
            self.assertEqual(injected.exception.code, "APPLICATION_FIELD_RESOLUTION_INVALID")

            purged = onboarding.purge_synthetic()
            self.assertGreater(purged["synthetic_refs_deleted"], 0)

    def test_ambiguous_or_oversize_cover_textarea_never_offers_generated_narrative(self) -> None:
        ambiguous_html = """<!doctype html><html><body><form action='/example/jobs/987654'>
        <label for='cover-one'>Cover Letter</label><textarea id='cover-one' name='cover_letter' required></textarea>
        <label for='cover-two'>Motivation Letter</label><textarea id='cover-two' name='motivation_letter' required></textarea>
        <button type='submit'>Submit application</button></form></body></html>"""
        with project_temp() as temp:
            _, onboarding, service, result = self._run_cover_textarea_application(temp, form_html=ambiguous_html)
            displayed = service.review_packet(str(result["application_id"]))
            cover = displayed["packet"]["material_plan"]["cover_letter"]
            self.assertEqual(cover["narrative_target_status"], "AMBIGUOUS")
            self.assertEqual(cover["narrative_target_count"], 2)
            self.assertTrue(all(
                "USE_GENERATED_NARRATIVE" not in field["allowed_decisions"]
                for field in displayed["field_resolution"]["unresolved_fields"]
            ))
            with self.assertRaises(JobOpsError) as ambiguous:
                service.preview_application_narrative({
                    "application_id": result["application_id"],
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "control_ref": displayed["field_resolution"]["unresolved_fields"][0]["control_ref"],
                })
            self.assertEqual(ambiguous.exception.code, "APPLICATION_NARRATIVE_FIELD_INVALID")
            onboarding.purge_synthetic()

        oversize_html = """<!doctype html><html><body><form action='/example/jobs/987654'>
        <label for='cover'>Cover Letter</label><textarea id='cover' name='cover_letter' maxlength='10' required></textarea>
        <button type='submit'>Submit application</button></form></body></html>"""
        with project_temp() as temp:
            _, onboarding, service, result = self._run_cover_textarea_application(temp, form_html=oversize_html)
            displayed = service.review_packet(str(result["application_id"]))
            field = displayed["field_resolution"]["unresolved_fields"][0]
            self.assertEqual(field["max_characters"], 10)
            self.assertFalse(field["generated_narrative_available"])
            self.assertNotIn("USE_GENERATED_NARRATIVE", field["allowed_decisions"])
            with self.assertRaises(JobOpsError) as too_long:
                service.preview_application_narrative({
                    "application_id": result["application_id"],
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "control_ref": field["control_ref"],
                })
            self.assertEqual(too_long.exception.code, "APPLICATION_NARRATIVE_TOO_LONG")
            with self.assertRaises(JobOpsError) as use_too_long:
                service.resolve_application_fields({
                    "application_id": result["application_id"],
                    "expected_packet_hash": displayed["packet"]["content_hash"],
                    "resolutions": [{
                        "control_ref": field["control_ref"],
                        "decision": "USE_GENERATED_NARRATIVE", "value": "",
                    }],
                    "user_confirmed": True,
                })
            self.assertEqual(use_too_long.exception.code, "APPLICATION_NARRATIVE_TOO_LONG")
            onboarding.purge_synthetic()

    def test_preview_endpoint_is_no_store_and_ui_clears_preview_text(self) -> None:
        with project_temp() as temp:
            _, onboarding, service, result = self._run_cover_textarea_application(temp)
            displayed = service.review_packet(str(result["application_id"]))
            field = displayed["field_resolution"]["unresolved_fields"][0]
            server = create_server(service, token="synthetic-narrative-session")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    server.url + "api/application-narrative-preview",
                    data=json.dumps({
                        "application_id": result["application_id"],
                        "expected_packet_hash": displayed["packet"]["content_hash"],
                        "control_ref": field["control_ref"],
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read())
                    self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                    self.assertEqual(payload["status"], "APPLICATION_NARRATIVE_PREVIEW_READY")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            app_source = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
            self.assertIn('text.textContent=result.narrative', app_source)
            self.assertIn('text.textContent=""', app_source)
            self.assertIn('USE_GENERATED_NARRATIVE', app_source)
            self.assertIn('maxlength="${maxCharacters}"', app_source)
            self.assertIn('APPLICATION_NARRATIVE_PREVIEW_TIMEOUT_MS=120000', app_source)
            self.assertIn('window.addEventListener("pagehide"', app_source)
            self.assertIn('document.addEventListener("visibilitychange"', app_source)
            self.assertIn('clearApplicationNarrativePreviews(event.target.closest(".application-field-row")', app_source)
            onboarding.purge_synthetic()


if __name__ == "__main__":
    unittest.main()
