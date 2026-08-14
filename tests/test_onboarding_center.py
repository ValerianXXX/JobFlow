from __future__ import annotations

import io
import http.client
import json
import socket
import shutil
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.request
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from _support import PROJECT, project_temp
from jobops import UI_PROTOCOL_VERSION, __version__
from jobops.ai_runtime import AIAnalysisEngine, LocalSubprocessAIEngine
from jobops.browser_assist import COMPANION_EXTENSION_ORIGIN
from jobops.db import JobOpsDB
from jobops.document_builder import inspect_docx_text_blocks, template_fingerprint
from jobops.document_qa import extract_pdf_text
from jobops.errors import JobOpsError
from jobops.instance_lock import local_instance_lock
from jobops.onboarding_catalog import FIELD_BY_ID, FIELD_IDS, REQUIRED_FIELD_IDS, public_catalog
from jobops.onboarding_center import OnboardingCenterService, _docx_text, _evidence_preview, _json_text, _string_values
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.util import canonical_json, sha256_bytes, sha256_file


class MemorySecureStore:
    """Small encrypted-store contract double; DPAPI itself is covered separately."""

    def __init__(self, private_root: Path) -> None:
        self.private_root = private_root
        self.private_root.mkdir(parents=True, exist_ok=True)
        self.values: dict[str, bytes] = {}
        self.counter = 0

    def put_bytes(self, value: bytes, *, reference: str | None = None) -> dict[str, str | bool]:
        if reference is None:
            self.counter += 1
            reference = f"secure-ref:SYNTHETIC{self.counter:04d}"
        self.values[reference] = bytes(value)
        return {"secure_ref": reference, "ciphertext_sha256": sha256_bytes(b"encrypted:" + value), "created": True}

    def get_bytes(self, reference: str) -> bytes:
        return self.values[reference]

    def ciphertext_sha256(self, reference: str) -> str:
        return sha256_bytes(b"encrypted:" + self.values[reference])

    def test(self, reference: str) -> bool:
        return reference in self.values

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


def full_answers() -> dict[str, dict[str, object]]:
    answers: dict[str, dict[str, object]] = {}
    for field_id in FIELD_IDS:
        field = FIELD_BY_ID[field_id]
        if field["input_type"] == "tags":
            value: object = ["synthetic"]
        elif field["options"]:
            value = field["options"][0]["value"]
        else:
            value = "synthetic"
        if field_id == "minimum_salary":
            value = "100000"
        elif field_id == "available_start_date":
            value = "2026-09-01"
        elif field_id == "github_url":
            value = "https://github.com/synthetic-candidate"
        elif field_id == "portfolio_url":
            value = "https://portfolio.example.test/synthetic-candidate"
        answers[field_id] = {
            "value": value,
            "status": "CONFIRMED",
            "use_policy": field["default_policy"],
        }
    return answers


class OnboardingCenterTests(unittest.TestCase):
    def test_dashboard_exposes_only_safe_queue_summaries(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            now = "2026-08-13T00:00:00Z"
            with service.database.connect() as connection:
                connection.execute(
                    "INSERT INTO jobs(job_id,source_type,source_locator,official_url,company,title,location,status,discovered_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("JOB-DASH", "synthetic", "fixture", "https://careers.example.test/jobs/1", "Synthetic Company", "Synthetic Role", "Remote", "FORM_VALIDATED", now, now),
                )
                connection.execute(
                    "INSERT INTO applications(application_id,job_id,site,status,resume_hash,answers_hash,dry_run,secure_profile_ref,last_safe_state,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("APP-DASH", "JOB-DASH", "https://careers.example.test/jobs/1", "AWAITING_APPROVAL", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 1, "secure-ref:SYNTHETIC_DASH", "AWAITING_APPROVAL", now),
                )
                connection.execute(
                    "INSERT INTO review_packets(packet_id,application_id,content_hash,relative_path,status,created_at) VALUES(?,?,?,?,?,?)",
                    ("PKT-DASH", "APP-DASH", "sha256:" + "3" * 64, "secure-ref:SYNTHETIC_PACKET", "AWAITING_APPROVAL", now),
                )
                connection.execute("UPDATE review_packets SET status='NEEDS_REVISION' WHERE packet_id='PKT-DASH'")
                connection.execute(
                    """INSERT INTO review_packets(
                    packet_id,application_id,content_hash,relative_path,status,packet_version,supersedes_packet_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    ("PKT-DASH-V2", "APP-DASH", "sha256:" + "4" * 64, "secure-ref:SYNTHETIC_PACKET_V2", "AWAITING_APPROVAL", 2, "PKT-DASH", now),
                )
                connection.execute(
                    "INSERT INTO intake_queue(intake_key,source_type,source_locator,status,reservation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    ("private-source-locator-key", "offline_snapshot", "private/source/path.txt", "DEFERRED", None, now, now),
                )
                connection.execute(
                    """INSERT INTO application_execution_runs(
                    run_id,application_id,application_context_hash,execution_plan_hash,browser_plan_hash,
                    form_snapshot_hash,freshness_evidence_hash,status,checkpoint_sequence,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "RUN-DASHBOARD01", "APP-DASH", "sha256:" + "5" * 64,
                        "sha256:" + "6" * 64, "sha256:" + "7" * 64,
                        "sha256:" + "8" * 64, "sha256:" + "9" * 64,
                        "AWAITING_FINAL_AUTHORIZATION", 5, now, now,
                    ),
                )
                connection.execute(
                    """INSERT INTO application_execution_checkpoints(
                    checkpoint_id,run_id,application_id,sequence,phase,status,evidence_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        "ECP-DASHBOARD01", "RUN-DASHBOARD01", "APP-DASH", 5,
                        "AWAITING_FINAL_AUTHORIZATION", "AWAITING_USER", "sha256:" + "a" * 64, now,
                    ),
                )
            dashboard = service.bootstrap()["dashboard"]
            self.assertEqual(dashboard["queue"]["awaiting_approval"], 1)
            self.assertEqual(dashboard["queue"]["slots_available"], 9)
            self.assertEqual(dashboard["pending_applications"][0]["company"], "Synthetic Company")
            self.assertEqual(len(dashboard["pending_applications"]), 1)
            self.assertEqual(dashboard["pending_applications"][0]["packet_id"], "PKT-DASH-V2")
            self.assertEqual(dashboard["pending_applications"][0]["packet_version"], 2)
            self.assertEqual(dashboard["pending_applications"][0]["packet_hash_prefix"], "sha256:" + "4" * 8)
            self.assertEqual(dashboard["safety"]["real_external_actions"], 0)
            self.assertEqual(dashboard["safety"]["real_website_accesses"], 0)
            self.assertFalse(dashboard["safety"]["external_action_control_enabled"])
            self.assertEqual(dashboard["safety"]["external_action_control_mode"], "PRODUCTION_DISABLED")
            self.assertEqual(dashboard["deferred_intake"][0]["status"], "DEFERRED")
            self.assertTrue(dashboard["deferred_intake"][0]["safe_intake_id"].startswith("sha256:"))
            self.assertEqual(len(dashboard["execution_runs"]), 1)
            self.assertEqual(dashboard["execution_runs"][0]["status"], "AWAITING_FINAL_AUTHORIZATION")
            self.assertEqual(dashboard["execution_runs"][0]["checkpoint_sequence"], 5)
            self.assertEqual(dashboard["execution_runs"][0]["last_phase"], "AWAITING_FINAL_AUTHORIZATION")
            self.assertFalse(dashboard["execution_runs"][0]["automatic_retry"])
            self.assertEqual(
                dashboard["execution_runs"][0]["next_safe_action"],
                "USER_FINAL_CONFIRMATION_REQUIRED",
            )
            self.assertEqual(dashboard["execution_status_counts"], {"AWAITING_FINAL_AUTHORIZATION": 1})
            self.assertEqual(dashboard["startup_execution_reconciliation"]["automatic_retries"], 0)
            serialized = json.dumps(dashboard, ensure_ascii=False)
            for forbidden in ("secure_profile_ref", "answers_hash", "resume_hash", "relative_path", "context_json", "private/source/path.txt", "private-source-locator-key"):
                self.assertNotIn(forbidden, serialized)

            with service.database.connect() as connection:
                connection.execute("UPDATE applications SET status='CLOSED' WHERE application_id='APP-DASH'")
                connection.execute("UPDATE review_packets SET status='REJECTED' WHERE application_id='APP-DASH'")
            recent = service.bootstrap()["dashboard"]["recent_applications"]
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["application_id"], "APP-DASH")
            self.assertEqual(recent[0]["packet_id"], "PKT-DASH-V2")
            self.assertEqual(recent[0]["packet_version"], 2)
            self.assertEqual(recent[0]["packet_status"], "REJECTED")

            with self.assertRaises(JobOpsError) as unconfirmed:
                service.disable_external_actions({"user_confirmed": False})
            self.assertEqual(unconfirmed.exception.code, "EXPLICIT_CONFIRMATION_REQUIRED")
            stopped = service.disable_external_actions({"user_confirmed": True})
            self.assertEqual(stopped["status"], "EXTERNAL_ACTIONS_DISABLED")
            self.assertEqual(stopped["real_external_actions"], 0)
            for operation in (service.prepare_synthetic_execution, service.complete_synthetic_execution):
                with self.assertRaises(JobOpsError) as synthetic_only:
                    operation({"application_id": "APP-DASH", "user_confirmed": True})
                self.assertEqual(synthetic_only.exception.code, "SYNTHETIC_DEMO_ONLY")

    def test_bootstrap_discloses_only_truthful_offline_ats_capabilities(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            report = service.bootstrap()["ats_capabilities"]
            self.assertEqual(report["provider_count"], 4)
            self.assertEqual(
                {item["provider"] for item in report["providers"]},
                {"company", "greenhouse", "lever", "workday"},
            )
            self.assertFalse(report["live_site_accessed"])
            self.assertEqual(report["network_actions"], 0)
            self.assertEqual(report["real_external_actions"], 0)
            for provider in report["providers"]:
                self.assertFalse(provider["live_site_verified"])
                self.assertTrue(provider["upload_blocked"])
                self.assertTrue(provider["submit_blocked"])

    def test_local_interactive_service_has_a_single_instance_lock(self) -> None:
        with project_temp() as root:
            with local_instance_lock(root / "locks"):
                with self.assertRaises(JobOpsError) as blocked:
                    with local_instance_lock(root / "locks"):
                        pass
                self.assertEqual(blocked.exception.code, "JOBFLOW_ALREADY_RUNNING")
            with local_instance_lock(root / "locks"):
                pass

    def make_service(self, root: Path, *, with_ai: bool = True) -> tuple[OnboardingCenterService, PrivateOnboarding, MemorySecureStore, str, bytes]:
        project = root / "project"
        (project / "schemas").mkdir(parents=True)
        (project / "state").mkdir()
        for name in (
            "candidate-profile", "onboarding-answer-bank", "onboarding-completion", "official-discovery",
            "external-claim-set", "application-readiness", "resume-tailoring-manifest",
        ):
            shutil.copy2(PROJECT / "schemas" / f"{name}.schema.json", project / "schemas")
        (project / "config").mkdir()
        shutil.copy2(PROJECT / "config" / "policy.json", project / "config" / "policy.json")
        database = JobOpsDB(project / "state" / "jobops.db")
        database.initialize()
        store = MemorySecureStore(root / "local" / "JobOps" / "private")
        onboarding = PrivateOnboarding(database, store)  # type: ignore[arg-type]
        profile = {
            "schema_version": 1,
            "candidate_display_name": {"value": "Synthetic Candidate", "status": "APPLICANT_PROVIDED_UNCONFIRMED"},
            "resume_facts": [
                {"category": "skill", "value": "Python"},
                {"category": "language", "value": "English"},
                {"category": "experience", "value": "2020-2026"},
            ],
        }
        profile_bytes = canonical_json(profile)
        profile_record = onboarding.import_bytes("candidate_profile", profile_bytes, synthetic=True)
        entity = {
            "entity_id": "ENT-SYNTHETIC01", "entity_fingerprint": "ENTKEY-SYNTHETIC01",
            "entity_key": "synthetic-project", "entity_type": "project",
            "organization": "Synthetic Organization", "role": "Project Contributor",
            "start_date": "2025", "end_date": "2026", "line_start": 1, "line_end": 2,
        }
        claims = {
            "claims": [
                {"claim_id": "CLM-SYNTHETIC01", "category": "project", "resume_statement": "The applicant completed a synthetic project with a documented responsibility boundary.", "lifecycle_status": "RESUME_ONLY_REQUIRES_CONFIRMATION", "supporting_evidence": [], "confidence": "LOW", "conflict": False, "ai_validated": True, "analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "claim_kind": "responsibility", "entity_id": entity["entity_id"], "entity": entity},
                {"claim_id": "CLM-SYNTHETIC02", "category": "project", "resume_statement": "The applicant improved synthetic review accuracy by 20%.", "lifecycle_status": "CONFLICT_REQUIRES_REVIEW", "supporting_evidence": [{"source_id": "personal_redacted", "heading": "Synthetic evidence", "excerpt": "The applicant improved synthetic review accuracy by 30%."}], "confidence": "LOW", "conflict": True, "ai_validated": True, "analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "claim_kind": "achievement", "entity_id": entity["entity_id"], "entity": entity},
            ]
        }
        onboarding.import_bytes("claim_candidates", canonical_json(claims), synthetic=True)
        engine = LocalSubprocessAIEngine([sys.executable, str(PROJECT / "tests" / "fixtures" / "fake_jobops_ai.py")]) if with_ai else AIAnalysisEngine()
        return OnboardingCenterService(project, database, onboarding, ai_engine=engine), onboarding, store, str(profile_record["secure_ref"]), profile_bytes

    def test_catalog_is_complete_and_bilingual(self) -> None:
        catalog = public_catalog()
        self.assertEqual(len(catalog["fields"]), 27)
        self.assertEqual(len(set(FIELD_IDS)), 27)
        self.assertEqual(catalog["required_field_count"], 25)
        self.assertEqual(len(set(REQUIRED_FIELD_IDS)), 25)
        for group in catalog["groups"]:
            self.assertTrue(group["label"]["zh"])
            self.assertTrue(group["label"]["en"])
        for field in catalog["fields"]:
            self.assertTrue(field["label"]["zh"])
            self.assertTrue(field["label"]["en"])

    def test_pdf_import_compares_local_extraction_modes_and_selects_cleaner_grounding_text(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)

            def extraction(_path: Path, *, layout: bool, **_kwargs: object) -> tuple[str, int]:
                if layout:
                    return "\ue001\n\ue002\n\ue003\nA\nB\nC\n" * 10, 1
                return "At Synthetic Studio, a Project Analyst built a complete local workflow.", 1

            with mock.patch("jobops.onboarding_center.extract_pdf_text", side_effect=extraction):
                text, excluded, selection = service._extract_text(b"synthetic-pdf", ".pdf", "resume")

            self.assertIn("complete local workflow", text)
            self.assertEqual(excluded, 0)
            self.assertEqual(selection["pdf_extraction_strategy"], "LOGICAL_READING_ORDER")
            self.assertEqual(selection["pdf_extraction_candidates_compared"], 2)
            self.assertEqual(selection["document_quality"]["status"], "PASS")
            self.assertFalse(selection["document_quality"]["contains_document_text"])

    def test_unreadable_pdf_fails_before_ai_or_private_import(self) -> None:
        with project_temp() as root:
            service, _, store, _, _ = self.make_service(root)
            before = set(store.values)
            with mock.patch.object(
                service, "_extract_text", return_value=("", 0, {"document_page_count": 2}),
            ):
                with self.assertRaises(JobOpsError) as caught:
                    service._prepare_source("resume", ".pdf", b"synthetic-pdf")
            self.assertEqual(caught.exception.code, "ONBOARDING_DOCUMENT_QUALITY_FAILED")
            self.assertEqual(caught.exception.details["document_quality"]["status"], "FAIL")
            self.assertIn("OCR_REQUIRED", caught.exception.details["document_quality"]["reason_codes"])
            self.assertEqual(set(store.values), before)

    def test_ui_prioritizes_conflicts_and_wraps_every_long_operation(self) -> None:
        html = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        styles = (PROJECT / "src" / "jobops" / "ui" / "styles.css").read_text(encoding="utf-8")
        self.assertLess(html.index('id="conflictSection"'), html.index('id="claimGroups"'))
        self.assertIn("function arrangePrimaryWorkflow()", script)
        self.assertIn("finish.after(dashboard)", script)
        self.assertIn("arrangePrimaryWorkflow();", script)
        self.assertIn('id="sourceIntakeNotice"', html)
        self.assertEqual(html.count("data-start-revision"), 2)
        self.assertIn("sourceIntakeDemoTitle", script)
        self.assertIn("sourceIntakeReadonlyTitle", script)
        self.assertIn("sourceIntakeAiTitle", script)
        self.assertIn("source-intake-notice", styles)
        self.assertIn('id="activityIndicator"', html)
        for activity in (
            'withActivity("loadingInitial"',
            'withActivity("savingAnswers"', 'withActivity("savingReview"',
            'withActivity("completingOnboarding"',
        ):
            self.assertIn(activity, script)
        self.assertIn('beginActivity("importing"', script)
        self.assertIn("function uploadApi", script)
        self.assertIn("XMLHttpRequest", script)
        self.assertIn("uploadStage", script)
        self.assertIn("longRunningNoCountdown", script)
        self.assertNotIn("projectedTotal", script)
        self.assertIn("activity-spinner", styles)
        self.assertIn("activity-progress-scan", styles)
        self.assertIn('id="activityProgress"', html)
        self.assertIn('id="demoBanner"', html)
        self.assertIn('id="atsCapabilityList"', html)
        self.assertIn('id="prepareOfflineApplication"', html)
        self.assertIn('id="applicationJdFile"', html)
        self.assertIn('id="applicationOfficialFile"', html)
        self.assertIn('id="applicationFormFile"', html)
        self.assertIn("function buildOfflineApplicationBundle", script)
        self.assertIn('uploadApi("prepare-offline-application"', script)
        self.assertIn('withActivity("preparingOfflineApplication"', script)
        self.assertIn("data-i18n-placeholder", html)
        self.assertIn("dataset.i18nPlaceholder", script)
        self.assertIn("ats_capabilities", script)
        self.assertIn("atsLiveUnverified", script)
        self.assertIn("ats-capability-list", styles)
        self.assertIn("demo_mode", script)
        self.assertIn("elapsedWithEstimate", script)
        self.assertIn("updateActivity", script)
        self.assertIn("function learnedActivityEstimate", script)
        self.assertIn("activityDurations", script)
        self.assertIn('data-i18n="reviewPacketTitlePlaceholder"', html)
        self.assertIn('aria-current="step"', html)
        self.assertIn('aria-pressed="true"', html)
        self.assertEqual(html.count('data-step-label="'), 4)
        self.assertIn('el.setAttribute("aria-label",`${number} ${t(el.dataset.stepLabel)}`.trim())', script)
        for accessible_label in (
            "answerValueLabel", "answerStatusLabel", "answerPolicyLabel",
            "claimDecisionLabel", "splitInputLabel",
        ):
            self.assertIn(accessible_label, script)
        self.assertIn('aria-label="${escapeHtml(accessibleName)}"', script)
        self.assertIn("scroll-margin-top: 94px", styles)
        self.assertIn(":focus-visible", styles)
        self.assertIn("scroll-behavior: auto", styles)
        self.assertIn("claim-row-conflict", styles)
        self.assertIn("refreshLatest", script)
        self.assertIn('cache:"no-store"', script)
        self.assertIn(f"jobflow-v{UI_PROTOCOL_VERSION}", html)
        self.assertIn('value="chatgpt_export_large"', html)
        self.assertIn("雷霆大文件", script)
        self.assertIn("ZIPzilla Express", script)
        self.assertIn("STANDARD_CHATGPT_EXPORT_BYTES", script)
        self.assertIn('id="analyzeAllSources"', html)
        self.assertIn("async function analyzeAllSources()", script)
        self.assertIn("reupload-source", script)
        self.assertIn('class="envelope-backdrop"', html)
        self.assertIn("JobFlow · 找工流水线", html)
        self.assertIn("一次填写，连续投递", html)
        self.assertNotIn("一次性安全入职中心", html)
        self.assertIn("Choose & analyze with AI", script)
        self.assertIn("selectForMerge", script)
        self.assertIn("include-all-preview", script)
        self.assertIn("legacyQuarantined", script)
        self.assertIn("delete-source", script)
        self.assertIn('id="aiEngineBanner"', html)
        self.assertIn('id="aiConnectButton"', html)
        self.assertIn('id="aiConnectionPanel"', html)
        self.assertIn('data-ai-mode="agent"', html)
        self.assertIn('data-ai-mode="local_model"', html)
        self.assertIn('withActivity(activity', script)
        self.assertIn('api("connect-ai"', script)
        self.assertIn("ai-choice-grid", styles)
        self.assertIn("Windows 与 WSL", script)
        self.assertIn("Windows and WSL", script)
        self.assertIn("AI_WSL_HERMES_AUTH_REQUIRED", script)
        self.assertIn("AI_WSL_LOCAL_BRIDGE_MISSING", script)
        self.assertIn("aiConnectionErrorMessage", script)
        self.assertIn("sourceAnalysisErrorMessage", script)
        self.assertIn("aiNumberFailureDetailed", script)
        self.assertIn("numericFormatReview", script)
        self.assertIn("adjacentWrapReview", script)
        self.assertIn("expanded_line_start", script)
        self.assertIn("numeric_format_normalizations", script)
        self.assertIn("aiRepairApplied", script)
        self.assertIn("AI_RESPONSE_REPAIR_FAILED", script)
        self.assertIn('SECURE_CIPHERTEXT_HASH_MISMATCH:"privateWriteRepair"', script)
        self.assertIn('ONBOARDING_INITIAL_INDEX_WRITE_FAILED:"privateWriteRetry"', script)
        self.assertIn("完整扫描 ZIP", script)
        self.assertIn("Full ZIP scan", script)
        self.assertIn("analysisPassedSelected", script)

    def test_draft_is_private_and_language_is_persisted(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            result = service.save_answers({"locale": "en", "answers": full_answers()})
            self.assertEqual(result["completion"]["resolved"], 25)
            status = service.redacted_status()
            self.assertEqual(status["current_locale"], "en")
            index_text = service.index_path.read_text(encoding="utf-8")
            self.assertNotIn("100000", index_text)
            self.assertNotIn("Synthetic Candidate", index_text)
            with service.database.connect() as connection:
                dump = "\n".join(connection.iterdump())
            self.assertNotIn("Synthetic Candidate", dump)

    def test_answer_save_failure_restores_every_private_reference(self) -> None:
        with project_temp() as root:
            service, onboarding, _, _, _ = self.make_service(root)
            state_ref, _ = service.ensure_state()
            initial_state = onboarding.read_bytes(state_ref)
            with service.database.connect() as connection:
                initial_active = {
                    row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")
                }
            with mock.patch.object(service, "_save_state", side_effect=OSError("synthetic state failure")):
                with self.assertRaises(JobOpsError) as first_failure:
                    service.save_answers({"locale": "en", "answers": full_answers()})
            self.assertEqual(first_failure.exception.code, "ONBOARDING_ANSWER_SAVE_FAILED")
            self.assertEqual(onboarding.read_bytes(state_ref), initial_state)
            with service.database.connect() as connection:
                self.assertEqual(
                    {row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")},
                    initial_active,
                )

            service.save_answers({"locale": "en", "answers": full_answers()})
            _, saved_state = service.ensure_state()
            answer_ref = str(saved_state["answer_bank_ref"])
            saved_answer = onboarding.read_bytes(answer_ref)
            saved_state_bytes = onboarding.read_bytes(state_ref)
            changed = full_answers()
            changed["minimum_salary"]["value"] = "200000"
            with mock.patch.object(service, "_save_state", side_effect=OSError("synthetic state failure")):
                with self.assertRaises(JobOpsError) as second_failure:
                    service.save_answers({"locale": "zh", "answers": changed})
            self.assertEqual(second_failure.exception.code, "ONBOARDING_ANSWER_SAVE_FAILED")
            self.assertEqual(onboarding.read_bytes(answer_ref), saved_answer)
            self.assertEqual(onboarding.read_bytes(state_ref), saved_state_bytes)

    def test_redacted_index_failure_restores_encrypted_state(self) -> None:
        with project_temp() as root:
            service, onboarding, _, _, _ = self.make_service(root)
            reference, state = service.ensure_state()
            previous = onboarding.read_bytes(reference)
            state["locale"] = "en"
            with mock.patch("jobops.onboarding_center.write_json", side_effect=OSError("synthetic index failure")):
                with self.assertRaises(JobOpsError) as failed:
                    service._save_state(reference, state)
            self.assertEqual(failed.exception.code, "ONBOARDING_STATE_INDEX_WRITE_FAILED")
            self.assertEqual(onboarding.read_bytes(reference), previous)
            self.assertFalse(service.index_path.with_suffix(service.index_path.suffix + ".tmp").exists())

    def test_initial_index_failure_removes_uncommitted_encrypted_state(self) -> None:
        with project_temp() as root:
            service, _, store, _, _ = self.make_service(root)
            values_before = dict(store.values)
            with mock.patch.object(service, "_write_index", side_effect=OSError("synthetic index failure")):
                with self.assertRaises(JobOpsError) as blocked:
                    service.ensure_state()
            self.assertEqual(blocked.exception.code, "ONBOARDING_INITIAL_INDEX_WRITE_FAILED")
            with service.database.connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind='onboarding_center_state' AND status='ACTIVE'"
                ).fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(store.values, values_before)

    def test_ai_sources_are_filtered_claims_not_silent_profile_facts(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            summary = "Target roles: Product manager\nPreferred locations: Toronto"
            imported = service.import_source("ai_summary", ".txt", summary.encode("utf-8"))
            self.assertEqual(imported["source_status"], "AI_FILTERED_REQUIRES_CONFIRMATION")
            self.assertTrue(imported["raw_retained"])
            self.assertEqual(imported["suggestion_count"], 0)
            self.assertEqual(imported["selected_claims"], 1)
            self.assertEqual(service.bootstrap()["answers"]["target_roles"]["status"], "UNKNOWN")

            buffer = io.BytesIO()
            payload = [{"mapping": {
                "safe": {"message": {"author": {"role": "user"}, "content": {"parts": ["Target industries: software"]}}},
                "secret": {"message": {"author": {"role": "user"}, "content": {"parts": ["password: do-not-retain"]}}},
            }}]
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", json.dumps(payload))
            export = service.import_source("chatgpt_export", ".zip", buffer.getvalue())
            self.assertFalse(export["raw_retained"])
            self.assertGreaterEqual(export["excluded_secret_fragments"], 1)
            bootstrap = service.bootstrap()
            self.assertEqual(bootstrap["suggestions"], [])
            export_source = next(item for item in bootstrap["sources"] if item["category"] == "chatgpt_export")
            self.assertTrue(export_source["archive_scan_complete"])
            self.assertEqual(export_source["user_fragments_scanned"], 2)
            self.assertEqual(export_source["safe_fragments_considered"], 1)
            self.assertEqual(export_source["ai_selected_fragments"], 1)
            self.assertEqual(export_source["ai_omitted_fragments"], 0)
            self.assertFalse(export_source["ai_selection_bounded"])

    def test_chatgpt_export_discloses_bounded_high_signal_selection(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            mapping = {
                f"user-{index}": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": [f"I built synthetic project workflow {index} and documented the result."]},
                    }
                }
                for index in range(5)
            }
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", json.dumps([{"mapping": mapping}]))
            with mock.patch("jobops.onboarding_center.MAX_CHATGPT_FRAGMENT_CANDIDATES", 2):
                service.import_source("chatgpt_export", ".zip", buffer.getvalue())

            source = service.bootstrap()["sources"][0]
            self.assertTrue(source["archive_scan_complete"])
            self.assertEqual(source["user_fragments_scanned"], 5)
            self.assertEqual(source["safe_fragments_considered"], 5)
            self.assertEqual(source["ai_selected_fragments"], 2)
            self.assertEqual(source["ai_omitted_fragments"], 3)
            self.assertTrue(source["ai_selection_bounded"])
            self.assertEqual(source["ai_selection_mode"], "HIGH_SIGNAL_BOUNDED")

    def test_official_chatgpt_export_uses_user_messages_only(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            payload = [{"mapping": {
                "user": {"message": {"author": {"role": "user"}, "content": {"parts": ["Target roles: Product manager"]}}},
                "assistant": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Preferred locations: invented-city"]}}},
            }}]
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", json.dumps(payload))
            service.import_source("chatgpt_export", ".zip", buffer.getvalue())
            statements = [item["statement"] for item in service.bootstrap()["claims"] if item.get("source_kind") == "ai_filtered_uploaded_material"]
            self.assertTrue(any("Product manager" in item for item in statements))
            self.assertFalse(any("invented-city" in item for item in statements))

    def test_large_chatgpt_export_streams_from_private_staging_and_retains_no_raw_zip(self) -> None:
        with project_temp() as root:
            service, onboarding, store, _, _ = self.make_service(root)
            payload = [{"mapping": {
                "user": {"message": {"author": {"role": "user"}, "content": {"parts": [
                    "I led a synthetic project in 2025 and improved review accuracy by 20%."
                ]}}},
                "assistant": {"message": {"author": {"role": "assistant"}, "content": {"parts": [
                    "Invented assistant-only experience must not be imported."
                ]}}},
            }}]
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", json.dumps(payload))
                archive.writestr("ignored/attachment.txt", "not indexed")
            raw_zip = buffer.getvalue()
            with onboarding.staging_directory() as staging:
                target = staging / "large-export.zip"
                target.write_bytes(raw_zip)
                preview = service.preview_large_chatgpt_export(
                    target,
                    extension=".zip",
                    source_hash=sha256_bytes(raw_zip),
                    upload_size=len(raw_zip),
                )
                self.assertTrue(target.exists())
            self.assertFalse(target.exists())
            self.assertEqual(preview["status"], "SOURCE_PREVIEW_READY")
            pending = service.bootstrap()["pending_sources"][0]
            self.assertEqual(pending["metadata"]["category"], "chatgpt_export")
            self.assertFalse(pending["metadata"]["raw_retained"])
            self.assertEqual(pending["extraction_summary"]["intake_mode"], "LIGHTNING_STREAM")
            self.assertFalse(any(value == raw_zip for value in store.values.values()))

    def test_standard_chatgpt_export_redirects_oversize_files_to_streaming_mode(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", json.dumps([{"content": "Synthetic personal project fact."}]))
            with mock.patch("jobops.onboarding_center.LARGE_EXPORT_THRESHOLD_BYTES", 1):
                with self.assertRaises(JobOpsError) as blocked:
                    service.preview_source("chatgpt_export", ".zip", buffer.getvalue())
            self.assertEqual(blocked.exception.code, "CHATGPT_EXPORT_LIGHTNING_REQUIRED")
            self.assertEqual(service.bootstrap()["pending_sources"], [])

    def test_docx_extraction_is_bounded_and_rejects_ambiguous_main_parts(self) -> None:
        namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        xml = (
            f'<w:document xmlns:w="{namespace}"><w:body><w:p><w:r><w:t>'
            + ("Synthetic resume text. " * 32)
            + "</w:t></w:r></w:p></w:body></w:document>"
        ).encode("utf-8")
        document = io.BytesIO()
        with zipfile.ZipFile(document, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)
        self.assertIn("Synthetic resume text", _docx_text(document.getvalue()))

        with mock.patch("jobops.onboarding_center.MAX_DOCX_XML_BYTES", 128):
            with self.assertRaises(JobOpsError) as oversized:
                _docx_text(document.getvalue())
        self.assertEqual(oversized.exception.code, "ONBOARDING_DOCUMENT_TOO_LARGE")

        with mock.patch("jobops.onboarding_center.MAX_DOCX_XML_COMPRESSION_RATIO", 1):
            with self.assertRaises(JobOpsError) as unsafe_ratio:
                _docx_text(document.getvalue())
        self.assertEqual(unsafe_ratio.exception.code, "ONBOARDING_DOCUMENT_COMPRESSION_UNSAFE")

        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", xml)
                archive.writestr("word/document.xml", xml)
        with self.assertRaises(JobOpsError) as duplicate_main:
            _docx_text(duplicate.getvalue())
        self.assertEqual(duplicate_main.exception.code, "ONBOARDING_DOCUMENT_AMBIGUOUS")

        ambiguous = io.BytesIO()
        with mock.patch("jobops.onboarding_center.MAX_DOCX_MEMBERS", 1):
            with zipfile.ZipFile(ambiguous, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", xml[:100])
                archive.writestr("word/styles.xml", b"<styles/>")
            with self.assertRaises(JobOpsError) as too_many:
                _docx_text(ambiguous.getvalue())
        self.assertEqual(too_many.exception.code, "ONBOARDING_DOCUMENT_TOO_LARGE")

    def test_retained_material_limit_does_not_reduce_chatgpt_export_transport_limit(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            with mock.patch("jobops.onboarding_center.MAX_RETAINED_SOURCE_BYTES", 8):
                with self.assertRaises(JobOpsError) as retained:
                    service._prepare_source("project_case", ".txt", b"123456789")
                self.assertEqual(retained.exception.code, "ONBOARDING_SOURCE_SIZE_INVALID")

                with self.assertRaises(JobOpsError) as export:
                    service._prepare_source("chatgpt_export", ".zip", b"123456789")
                self.assertNotEqual(export.exception.code, "ONBOARDING_SOURCE_SIZE_INVALID")

    def test_json_text_traversal_is_iterative_and_complexity_bounded(self) -> None:
        self.assertEqual(list(_string_values({"first": ["one", {"second": "two"}]})), ["one", "two"])
        with mock.patch("jobops.onboarding_center.MAX_JSON_DEPTH", 2):
            with self.assertRaises(JobOpsError) as deep:
                list(_string_values([[[["too deep"]]]]))
        self.assertEqual(deep.exception.code, "ONBOARDING_JSON_COMPLEXITY_LIMIT")

        with mock.patch("jobops.onboarding_center.MAX_JSON_NODES", 3):
            with self.assertRaises(JobOpsError) as wide:
                _json_text(b'["one","two","three"]')
        self.assertEqual(wide.exception.code, "ONBOARDING_JSON_COMPLEXITY_LIMIT")

    def test_pdf_extraction_rejects_page_count_before_text_analysis(self) -> None:
        from pypdf import PdfWriter

        with project_temp() as root:
            path = root / "synthetic-pages.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.add_blank_page(width=612, height=792)
            with path.open("wb") as handle:
                writer.write(handle)
            with self.assertRaises(JobOpsError) as too_many_pages:
                extract_pdf_text(path, page_limit=1, character_limit=1_000)
        self.assertEqual(too_many_pages.exception.code, "PDF_PAGE_LIMIT_EXCEEDED")
        self.assertEqual(too_many_pages.exception.details["page_count"], 2)

    def test_source_preview_prevents_unreviewed_line_claims(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            text = (
                "I designed a synthetic workflow that connected research, approvals,\n"
                "implementation, and a 4,000-record evaluation set to improve review accuracy by 20%.\n"
            )
            preview = service.preview_source("project_case", ".txt", text.encode("utf-8"))
            before = service.bootstrap()
            self.assertEqual(preview["status"], "SOURCE_PREVIEW_READY")
            self.assertEqual(len(before["pending_sources"]), 1)
            self.assertEqual(len(before["claims"]), 2)
            pending = before["pending_sources"][0]
            selected = [{
                "candidate_id": item["candidate_id"], "selected": True,
                "statement": item["statement"].replace("synthetic workflow", "reviewed workflow"),
                "category": item["category"],
            } for item in pending["candidates"]]
            service.commit_source(preview["source_id"], selected)
            after = service.bootstrap()
            new_claims = [item for item in after["claims"] if item.get("source_id") == preview["source_id"]]
            self.assertEqual(len(after["pending_sources"]), 0)
            self.assertTrue(new_claims)
            self.assertTrue(any("reviewed workflow" in item["statement"] for item in new_claims))
            source = after["sources"][0]
            self.assertTrue(source["analysis_complete"])
            self.assertEqual(source["ai_input_characters"], source["ai_covered_characters"])
            self.assertFalse(source["ai_input_truncated"])
            self.assertGreaterEqual(source["ai_chunks"], 1)

    def test_source_preview_and_compatibility_import_remove_refs_when_state_save_fails(self) -> None:
        with project_temp() as root:
            service, _, store, _, _ = self.make_service(root)
            service.ensure_state()
            baseline_refs = set(store.values)
            content = b"Built a synthetic workflow with a documented review boundary."
            with mock.patch.object(service, "_save_state", side_effect=OSError("synthetic preview state failure")):
                with self.assertRaises(JobOpsError) as preview_failure:
                    service.preview_source("project_case", ".txt", content)
            self.assertEqual(preview_failure.exception.code, "SOURCE_PREVIEW_SAVE_FAILED")
            self.assertEqual(set(store.values), baseline_refs)
            self.assertEqual(service.bootstrap()["pending_sources"], [])

            with mock.patch.object(service, "_save_state", side_effect=OSError("synthetic import state failure")):
                with self.assertRaises(JobOpsError) as import_failure:
                    service.import_source("project_case", ".txt", content)
            self.assertEqual(import_failure.exception.code, "SOURCE_IMPORT_SAVE_FAILED")
            self.assertEqual(set(store.values), baseline_refs)
            self.assertEqual(service.bootstrap()["sources"], [])

    def test_incomplete_ai_coverage_cannot_enter_claim_review(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            preview = service.preview_source(
                "project_case", ".txt",
                b"Built a synthetic project and documented a complete review workflow.",
            )
            reference, state = service.ensure_state()
            pending = state["pending_sources"][0]
            pending["extraction_summary"]["ai_input_truncated"] = True
            pending["extraction_summary"]["ai_covered_characters"] -= 1
            service._save_state(reference, state)

            with self.assertRaises(JobOpsError) as blocked:
                service.commit_source(preview["source_id"], None)
            self.assertEqual(blocked.exception.code, "AI_ANALYSIS_REQUIRED")
            self.assertEqual(service.bootstrap()["sources"], [])

    def test_unconfigured_ai_blocks_import_and_emits_no_candidates(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root, with_ai=False)
            with self.assertRaises(JobOpsError) as blocked:
                service.preview_source(
                    "project_case", ".txt",
                    b"Built a synthetic workflow and improved review accuracy by 20%.",
                )
            bootstrap = service.bootstrap()
            self.assertEqual(blocked.exception.code, "AI_ENGINE_REQUIRED")
            self.assertEqual(bootstrap["ai_engine"]["status"], "NOT_CONFIGURED")
            self.assertEqual(bootstrap["ai_connection"]["credentials_read"], 0)
            self.assertEqual([item["mode"] for item in bootstrap["ai_connection"]["options"][:2]], ["agent", "local_model"])
            self.assertEqual(bootstrap["pending_sources"], [])
            self.assertEqual(len(bootstrap["claims"]), 2)

    def test_local_ai_engine_is_primary_and_still_requires_confirmation(self) -> None:
        with project_temp() as root:
            original, _, _, _, _ = self.make_service(root)
            engine = LocalSubprocessAIEngine([sys.executable, str(PROJECT / "tests" / "fixtures" / "fake_jobops_ai.py")])
            service = OnboardingCenterService(original.project, original.database, original.onboarding, ai_engine=engine)
            service.preview_source(
                "project_case", ".txt",
                b"Built a synthetic project and documented a source-grounded review workflow.",
            )
            bootstrap = service.bootstrap()
            pending = bootstrap["pending_sources"][0]
            self.assertEqual(bootstrap["ai_engine"]["status"], "READY")
            self.assertEqual(pending["extraction_summary"]["analysis_mode"], "AI_CORE_ENTITY_ANALYSIS")
            self.assertEqual(len(pending["candidates"]), 1)
            self.assertFalse(pending["candidates"][0]["selected"])
            self.assertEqual(pending["candidates"][0]["selection_reason"], "AI_DERIVED_REQUIRES_CONFIRMATION")

    def test_local_ai_engine_stops_output_during_generation_at_the_memory_limit(self) -> None:
        engine = LocalSubprocessAIEngine([
            sys.executable,
            "-c",
            "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*(6*1024*1024)); sys.stdout.flush()",
        ])
        with self.assertRaises(JobOpsError) as blocked:
            engine.analyze_document(
                "Built a synthetic project and documented its review boundary.",
                source_id="SRC-BOUNDED-OUTPUT",
                source_type="project_case",
            )
        self.assertEqual(blocked.exception.code, "AI_ENGINE_FAILED")

    def test_ai_contract_keeps_work_internship_education_and_project_distinct(self) -> None:
        lines = [
            "Worked as an Analyst at Alpha from 2020 to 2021.",
            "Completed an internship as an Intern at Beta in 2022.",
            "Earned a Finance degree at Gamma University in 2023.",
            "Built Project Delta for a client in 2024.",
        ]
        entities = []
        candidates = []
        for index, (category, organization, role) in enumerate((
            ("work", "Alpha", "Analyst"), ("internship", "Beta", "Intern"),
            ("education", "Gamma University", "Finance degree"), ("project", "Project Delta", "Project"),
        ), start=1):
            key = f"entity-{index}"
            entities.append({
                "entity_key": key, "entity_type": category, "organization": organization, "role": role,
                "start_date": "", "end_date": "", "line_start": index, "line_end": index,
            })
            candidates.append({
                "statement": lines[index - 1], "category": category, "claim_kind": "entity_summary",
                "entity_key": key, "confidence": "HIGH", "line_start": index, "line_end": index,
                "reason": "Explicit source wording.",
            })
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": entities, "candidates": candidates},
            source_id="SRC-SYNTHETIC", source_lines=lines,
        )
        self.assertEqual([item["category"] for item in validated], ["work", "internship", "education", "project"])

    def test_ai_contract_consolidates_duplicate_real_world_entities(self) -> None:
        with self.assertRaises(JobOpsError) as malformed_schema:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": {}, "entities": [], "candidates": []},
                source_id="SRC-SYNTHETIC", source_lines=["Synthetic source."],
            )
        self.assertEqual(malformed_schema.exception.code, "AI_RESPONSE_INVALID")

        duplicate = {
            "entity_type": "work", "organization": "Alpha", "role": "Analyst",
            "start_date": "2020", "end_date": "2021",
        }
        lines = [
            "Worked as an Analyst at Alpha from 2020 to 2021.",
            "Worked as an Analyst at Alpha from 2020 to 2021.",
        ]
        candidate = {
            "statement": lines[1], "category": "work", "claim_kind": "entity_summary",
            "entity_key": "second", "confidence": "HIGH", "line_start": 2, "line_end": 2,
            "reason": "Repeated model entity.",
        }
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [
                {"entity_key": "first", **duplicate, "line_start": 1, "line_end": 1},
                {"entity_key": "second", **duplicate, "line_start": 2, "line_end": 2},
            ], "candidates": [candidate]},
            source_id="SRC-SYNTHETIC", source_lines=lines,
        )
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["entity"]["entity_key"], "first")
        self.assertIn("DUPLICATE_ENTITY_CONSOLIDATED", validated[0]["provenance"]["structural_normalizations"])
        self.assertTrue(validated[0]["provenance"]["classification_review_required"])

    def test_ai_contract_consolidates_repeated_key_only_for_the_same_grounded_entity(self) -> None:
        repeated_lines = [
            "Worked as an Analyst at Alpha from 2020 to 2021.",
            "Worked as an Analyst at Alpha from 2020 to 2021.",
        ]
        repeated_entity = {
            "entity_key": "alpha", "entity_type": "work", "organization": "Alpha", "role": "Analyst",
            "start_date": "2020", "end_date": "2021",
        }
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [
                {**repeated_entity, "line_start": 1, "line_end": 1},
                {**repeated_entity, "line_start": 2, "line_end": 2},
            ], "candidates": [{
                "statement": repeated_lines[1], "category": "work", "claim_kind": "entity_summary",
                "entity_key": "alpha", "confidence": "HIGH", "line_start": 2, "line_end": 2,
                "reason": "Repeated model key.",
            }]},
            source_id="SRC-REPEATED-KEY", source_lines=repeated_lines,
        )
        self.assertIn("DUPLICATE_ENTITY_CONSOLIDATED", validated[0]["provenance"]["structural_normalizations"])

        different_lines = [
            "Worked as an Analyst at Alpha from 2020 to 2021.",
            "Worked as a Manager at Beta from 2022 to 2023.",
        ]
        with self.assertRaises(JobOpsError) as ambiguous:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": 2, "entities": [{
                    **repeated_entity, "line_start": 1, "line_end": 1,
                }, {
                    "entity_key": "alpha", "entity_type": "work", "organization": "Beta", "role": "Manager",
                    "start_date": "2022", "end_date": "2023", "line_start": 2, "line_end": 2,
                }], "candidates": []},
                source_id="SRC-AMBIGUOUS-KEY", source_lines=different_lines,
            )
        self.assertEqual(ambiguous.exception.code, "AI_RESPONSE_INVALID")

    def test_ai_contract_normalizes_obvious_internship_misclassification_and_rejects_header_fragments(self) -> None:
        source = ["Worked as a Strategy Intern at Beta from April 2025 to July 2025."]
        entity = {
            "entity_key": "beta", "entity_type": "work", "organization": "Beta", "role": "Strategy Intern",
            "start_date": "April 2025", "end_date": "July 2025", "line_start": 1, "line_end": 1,
        }
        candidate = {
            "statement": source[0], "category": "work", "claim_kind": "entity_summary",
            "entity_key": "beta", "confidence": "HIGH", "line_start": 1, "line_end": 1,
            "reason": "Synthetic classification mismatch.",
        }
        normalized = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [entity], "candidates": [candidate]},
            source_id="SRC-CATEGORY", source_lines=source,
        )
        self.assertEqual(normalized[0]["category"], "internship")
        self.assertEqual(normalized[0]["entity"]["entity_type"], "internship")
        self.assertIn("EXPLICIT_INTERNSHIP_TYPE_NORMALIZED", normalized[0]["provenance"]["structural_normalizations"])
        self.assertIn("PARENT_ENTITY_TYPE_INHERITED", normalized[0]["provenance"]["structural_normalizations"])
        self.assertTrue(normalized[0]["provenance"]["classification_review_required"])

        entity["entity_type"] = "internship"
        header = {
            "statement": "Strategy Intern at Beta, April 2025 to July 2025.", "category": "internship",
            "claim_kind": "entity_summary", "entity_key": "beta", "confidence": "HIGH",
            "line_start": 1, "line_end": 1, "reason": "Synthetic header.",
        }
        with self.assertRaises(JobOpsError) as fragment_error:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": 2, "entities": [entity], "candidates": [header]},
                source_id="SRC-HEADER", source_lines=source,
            )
        self.assertEqual(fragment_error.exception.code, "AI_RESPONSE_INVALID")

    def test_ai_contract_rejects_weak_grounding_and_merges_near_duplicate_claims(self) -> None:
        weak_source = ["Led a merchant database project containing 4,000 records."]
        entity = {
            "entity_key": "merchant", "entity_type": "project", "organization": "merchant database",
            "role": "project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 1,
        }
        unsupported = {
            "statement": "Led the merchant database project containing 4,000 records and independently increased global revenue across five markets.",
            "category": "project", "claim_kind": "achievement", "entity_key": "merchant", "confidence": "HIGH",
            "line_start": 1, "line_end": 1, "reason": "Synthetic overclaim.",
        }
        with self.assertRaises(JobOpsError) as grounding_error:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": 2, "entities": [entity], "candidates": [unsupported]},
                source_id="SRC-GROUNDING", source_lines=weak_source,
            )
        self.assertEqual(grounding_error.exception.code, "AI_RESPONSE_INVALID")

        duplicate_lines = [
            "Led a synthetic project and improved review accuracy by 20%.",
            "Successfully led a synthetic project and improved review accuracy by 20%.",
        ]
        duplicate_entity = {
            "entity_key": "synthetic", "entity_type": "project", "organization": "synthetic",
            "role": "project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 2,
        }
        candidates = [{
            "statement": "• Led a synthetic project and improved review accuracy by 20%.",
            "category": "project", "claim_kind": "achievement", "entity_key": "synthetic", "confidence": "HIGH",
            "line_start": 1, "line_end": 1, "reason": "Synthetic claim.",
        }, {
            "statement": "Successfully led a synthetic project and improved review accuracy by 20%.",
            "category": "project", "claim_kind": "achievement", "entity_key": "synthetic", "confidence": "HIGH",
            "line_start": 2, "line_end": 2, "reason": "Synthetic duplicate.",
        }]
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [duplicate_entity], "candidates": candidates},
            source_id="SRC-NEAR-DUPLICATE", source_lines=duplicate_lines,
        )
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["statement"], "Led a synthetic project and improved review accuracy by 20%.")

    def test_ai_contract_canonicalizes_month_names_when_consolidating_duplicate_entities(self) -> None:
        lines = [
            "Worked as an Analyst at Alpha from July 2020 to June 2021.",
            "Worked as an Analyst at Alpha from Jul 2020 to Jun 2021.",
        ]
        entities = [{
            "entity_key": "first", "entity_type": "work", "organization": "Alpha", "role": "Analyst",
            "start_date": "July 2020", "end_date": "June 2021", "line_start": 1, "line_end": 1,
        }, {
            "entity_key": "second", "entity_type": "work", "organization": "Alpha", "role": "Analyst",
            "start_date": "Jul 2020", "end_date": "Jun 2021", "line_start": 2, "line_end": 2,
        }]
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": entities, "candidates": [{
                "statement": lines[1], "category": "work", "claim_kind": "entity_summary",
                "entity_key": "second", "confidence": "HIGH", "line_start": 2, "line_end": 2,
                "reason": "Month alias duplicate.",
            }]},
            source_id="SRC-MONTHS", source_lines=lines,
        )
        self.assertEqual(len(validated), 1)
        self.assertIn("DUPLICATE_ENTITY_CONSOLIDATED", validated[0]["provenance"]["structural_normalizations"])

    def test_ai_contract_recovers_adjacent_docx_pdf_entity_headers_and_marks_review(self) -> None:
        lines = [
            "Alpha Advisory",
            "Strategy Intern",
            "April 2025 to July 2025",
            "Mapped the customer journey and documented the operating workflow.",
        ]
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [{
                "entity_key": "alpha", "entity_type": "work", "organization": "Alpha Advisory",
                "role": "Strategy Intern", "start_date": "April 2025", "end_date": "July 2025",
                "line_start": 2, "line_end": 2,
            }], "candidates": [{
                "statement": lines[3], "category": "work", "claim_kind": "responsibility",
                "entity_key": "alpha", "confidence": "HIGH", "line_start": 4, "line_end": 4,
                "reason": "Physical document layout split the entity header.",
            }]},
            source_id="SRC-WRAPPED-ENTITY", source_lines=lines,
        )
        self.assertEqual(validated[0]["category"], "internship")
        self.assertEqual(validated[0]["entity"]["line_start"], 1)
        self.assertEqual(validated[0]["entity"]["line_end"], 3)
        self.assertIn("ADJACENT_ENTITY_HEADER_LINES", validated[0]["provenance"]["structural_normalizations"])
        self.assertTrue(validated[0]["provenance"]["classification_review_required"])

    def test_ai_contract_does_not_expand_entity_header_beyond_two_total_lines(self) -> None:
        lines = [
            "Alpha Advisory",
            "Strategy Analyst",
            "New York",
            "April 2025 to July 2025",
            "Mapped the customer journey and documented the operating workflow.",
        ]
        with self.assertRaises(JobOpsError) as rejected:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": 2, "entities": [{
                    "entity_key": "alpha", "entity_type": "work", "organization": "Alpha Advisory",
                    "role": "Strategy Analyst", "start_date": "April 2025", "end_date": "July 2025",
                    "line_start": 2, "line_end": 2,
                }], "candidates": []},
                source_id="SRC-TOO-WIDE-ENTITY", source_lines=lines,
            )
        self.assertEqual(rejected.exception.code, "AI_RESPONSE_INVALID")

    def test_duplicate_entity_prefers_explicit_internship_and_normalizes_child_aliases(self) -> None:
        lines = [
            "Alpha Advisory Strategy Analyst April 2025 to July 2025",
            "Worked as an Alpha Advisory Strategy Analyst Internship from April 2025 to July 2025.",
        ]
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [{
                "entity_key": "alpha-work", "entity_type": "work", "organization": "Alpha Advisory",
                "role": "Strategy Analyst", "start_date": "April 2025", "end_date": "July 2025",
                "line_start": 1, "line_end": 1,
            }, {
                "entity_key": "alpha-intern", "entity_type": "internship", "organization": "Alpha Advisory",
                "role": "Strategy Analyst", "start_date": "April 2025", "end_date": "July 2025",
                "line_start": 2, "line_end": 2,
            }], "candidates": [{
                "statement": lines[1], "category": "achievement", "claim_kind": "role",
                "entity_key": "alpha-intern", "confidence": "HIGH", "line_start": 2, "line_end": 2,
                "reason": "Duplicate entity and generic child labels.",
            }]},
            source_id="SRC-EXPLICIT-INTERN-DUPLICATE", source_lines=lines,
        )
        self.assertEqual(validated[0]["category"], "internship")
        self.assertEqual(validated[0]["claim_kind"], "entity_summary")
        self.assertIn("DUPLICATE_ENTITY_CONSOLIDATED", validated[0]["provenance"]["structural_normalizations"])
        self.assertIn("GENERIC_CATEGORY_REPLACED_BY_PARENT", validated[0]["provenance"]["structural_normalizations"])
        self.assertIn("CLAIM_KIND_ALIAS_NORMALIZED", validated[0]["provenance"]["structural_normalizations"])

    def test_ai_proposed_internship_without_literal_marker_is_reviewable_not_discarded(self) -> None:
        source = ["Worked as a Summer Analyst at Beta during 2025."]
        validated = LocalSubprocessAIEngine._validated_candidates(
            {"schema_version": 2, "entities": [{
                "entity_key": "beta", "entity_type": "internship", "organization": "Beta",
                "role": "Summer Analyst", "start_date": "2025", "end_date": "",
                "line_start": 1, "line_end": 1,
            }], "candidates": [{
                "statement": source[0], "category": "internship", "claim_kind": "entity_summary",
                "entity_key": "beta", "confidence": "MEDIUM", "line_start": 1, "line_end": 1,
                "reason": "AI-proposed type requires the user's review.",
            }]},
            source_id="SRC-SUMMER-ANALYST", source_lines=source,
        )
        self.assertEqual(validated[0]["category"], "internship")
        self.assertIn("AI_INTERNSHIP_TYPE_REQUIRES_CONFIRMATION", validated[0]["provenance"]["structural_normalizations"])
        self.assertTrue(validated[0]["provenance"]["classification_review_required"])

    def test_same_ai_claim_from_two_sources_is_stored_once(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            text = b"Built a synthetic project workflow with a documented review boundary."
            first = service.import_source("project_case", ".txt", text)
            second = service.import_source("supporting_material", ".txt", text)
            self.assertEqual(first["selected_claims"], 1)
            self.assertEqual(second["selected_claims"], 0)
            self.assertEqual(second["duplicate_claims_merged"], 1)
            _, private_state = service.ensure_state()
            active = [item for item in private_state["material_claims"] if item.get("ai_validated") and not item.get("deleted")]
            self.assertEqual(len(active), 1)

    def test_deleting_primary_source_retains_claim_supported_by_second_source(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            text = b"Built a synthetic project workflow with a documented review boundary."
            first = service.import_source("project_case", ".txt", text)
            second = service.import_source("supporting_material", ".txt", text)
            deleted = service.delete_source(first["source_id"], user_confirmed=True)
            self.assertEqual(deleted["removed_claims"], 0)
            self.assertEqual(deleted["retained_shared_claims"], 1)
            retained = [item for item in service.bootstrap()["claims"] if item.get("source_id") == second["source_id"]]
            self.assertEqual(len(retained), 1)

    def test_source_delete_removes_linked_claims_suggestions_and_ciphertext(self) -> None:
        with project_temp() as root:
            service, _, store, _, _ = self.make_service(root)
            preview = service.preview_source(
                "project_case", ".txt",
                b"Built a synthetic workflow and improved review accuracy by 20%.",
            )
            pending = service.bootstrap()["pending_sources"][0]
            selections = [{
                "candidate_id": item["candidate_id"], "selected": True,
                "statement": item["statement"], "category": item["category"],
            } for item in pending["candidates"]]
            service.commit_source(preview["source_id"], selections)
            _, private_state = service.ensure_state()
            source_ref = next(item["secure_ref"] for item in private_state["sources"] if item["source_id"] == preview["source_id"])
            self.assertIn(source_ref, store.values)
            with self.assertRaises(JobOpsError) as blocked:
                service.delete_source(preview["source_id"], user_confirmed=False)
            self.assertEqual(blocked.exception.code, "SOURCE_DELETE_CONFIRMATION_REQUIRED")
            deleted = service.delete_source(preview["source_id"], user_confirmed=True)
            self.assertEqual(deleted["status"], "SOURCE_DELETED")
            self.assertNotIn(source_ref, store.values)
            bootstrap = service.bootstrap()
            self.assertFalse(any(item["source_id"] == preview["source_id"] for item in bootstrap["sources"]))
            self.assertFalse(any(item.get("source_id") == preview["source_id"] for item in bootstrap["claims"]))

    def test_source_delete_failure_restores_source_claims_and_private_reference(self) -> None:
        with project_temp() as root:
            service, onboarding, store, _, _ = self.make_service(root)
            imported = service.import_source(
                "project_case", ".txt",
                b"Built a synthetic workflow with a documented review boundary.",
            )
            before = service.bootstrap()
            _, private_state = service.ensure_state()
            source_ref = next(
                item["secure_ref"] for item in private_state["sources"]
                if item["source_id"] == imported["source_id"]
            )
            with mock.patch.object(
                onboarding, "delete",
                side_effect=JobOpsError("PRIVATE_DELETE_STORAGE_FAILED", "Synthetic private deletion failure."),
            ):
                with self.assertRaises(JobOpsError) as failed:
                    service.delete_source(imported["source_id"], user_confirmed=True)
            self.assertEqual(failed.exception.code, "SOURCE_PRIVATE_DELETE_FAILED")
            after = service.bootstrap()
            self.assertEqual(after["sources"], before["sources"])
            self.assertEqual(after["claims"], before["claims"])
            self.assertTrue(store.test(source_ref))

    def test_preview_discard_failure_restores_pending_preview(self) -> None:
        with project_temp() as root:
            service, onboarding, store, _, _ = self.make_service(root)
            preview = service.preview_source(
                "project_case", ".txt",
                b"Built a synthetic workflow with a documented review boundary.",
            )
            before = service.bootstrap()["pending_sources"]
            _, private_state = service.ensure_state()
            source_ref = str(private_state["pending_sources"][0]["metadata"]["secure_ref"])
            with mock.patch.object(
                onboarding, "delete",
                side_effect=JobOpsError("PRIVATE_DELETE_STORAGE_FAILED", "Synthetic private deletion failure."),
            ):
                with self.assertRaises(JobOpsError) as failed:
                    service.discard_source_preview(preview["source_id"])
            self.assertEqual(failed.exception.code, "SOURCE_PREVIEW_PRIVATE_DELETE_FAILED")
            self.assertEqual(service.bootstrap()["pending_sources"], before)
            self.assertTrue(store.test(source_ref))

    def test_source_delete_keeps_other_ai_filtered_sources(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            first = service.import_source("ai_summary", ".txt", b"Target roles: Product manager")
            second = service.import_source("ai_summary", ".txt", b"Target roles: Data analyst")
            self.assertFalse(any(item.get("field_id") == "target_roles" for item in service.bootstrap()["conflicts"]))
            service.delete_source(second["source_id"], user_confirmed=True)
            self.assertFalse(any(item.get("field_id") == "target_roles" for item in service.bootstrap()["conflicts"]))
            self.assertTrue(any(item["source_id"] == first["source_id"] for item in service.bootstrap()["sources"]))

    def test_conflict_preview_rejects_unrelated_markdown_dump(self) -> None:
        preview = _evidence_preview(
            "Built a 4,000 merchant market-intelligence database.",
            "| variable | value |\n|---|---|\nAPI route manifest with 27 paths and 37 methods.\nhttps://example.test/noise",
        )
        self.assertFalse(preview["relevant"])
        self.assertEqual(preview["summary"], "")
        self.assertIn("4,000 merchant", preview["resume_metrics"])

    def test_legacy_rule_claims_and_false_conflicts_are_quarantined(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            reference, state = service.ensure_state()
            state["material_claims"].append({
                "claim_id": "CLM-LEGACY", "category": "achievement", "statement": "A broken legacy fragment",
                "decision": "CONFIRMED", "approved_for_external": False, "deleted": False,
            })
            state["conflict_resolutions"]["CLM-LEGACY"] = {"status": "RESOLVED", "resolution": "USE_RESUME"}
            service._save_state(reference, state)
            bootstrap = service.bootstrap()
            self.assertFalse(any(item["claim_id"] == "CLM-LEGACY" for item in bootstrap["claims"]))
            self.assertGreaterEqual(bootstrap["claim_quality"]["quarantined_legacy_claims"], 1)
            self.assertGreaterEqual(bootstrap["claim_quality"]["suppressed_invalid_conflicts"], 1)

    def test_completion_requires_every_gate_and_preserves_old_profile(self) -> None:
        with project_temp() as root:
            service, onboarding, _, old_profile_ref, old_profile_bytes = self.make_service(root)
            with self.assertRaises(JobOpsError) as caught:
                service.complete(user_confirmed=True)
            self.assertEqual(caught.exception.code, "ONBOARDING_ANSWERS_INCOMPLETE")
            self.assertEqual(caught.exception.details["remaining"], 25)
            self.assertEqual(caught.exception.details["fields"], list(REQUIRED_FIELD_IDS))

            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            claim_conflict = next(item for item in bootstrap["conflicts"] if item.get("claim_id") == "CLM-SYNTHETIC02")
            self.assertEqual(claim_conflict["kind"], "CLAIM_EVIDENCE_CONFLICT")
            self.assertEqual(claim_conflict["reason"], "COMPARABLE_VALUE_MISMATCH")
            self.assertEqual(claim_conflict["difference"]["resume_value"], "20")
            self.assertEqual(claim_conflict["difference"]["evidence_value"], "30")
            decisions = {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]}
            resolutions = {
                item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
                for item in bootstrap["conflicts"]
            }
            service.save_review({"profile_review": "CONFIRMED", "claim_decisions": decisions, "conflict_resolutions": resolutions})
            completed = service.complete(user_confirmed=True)
            self.assertEqual(completed["status"], "ONBOARDING_COMPLETE")
            self.assertEqual(completed["real_external_actions"], 0)
            self.assertEqual(completed["knowledge_write_operations"], 0)
            self.assertEqual(onboarding.read_bytes(old_profile_ref), old_profile_bytes)
            current_profile = json.loads(onboarding.read_bytes(completed["profile_ref"]))
            self.assertEqual(current_profile["profile_ref"], completed["profile_ref"])
            approvals = json.loads(onboarding.read_bytes(completed["claim_approvals_ref"]))
            self.assertFalse(approvals["approved_for_external"])
            self.assertEqual(list((root / "local" / "JobOps" / "private" / "staging").glob("*")), [])
            with self.assertRaises(JobOpsError) as caught_again:
                service.complete(user_confirmed=True)
            self.assertEqual(caught_again.exception.code, "ONBOARDING_ALREADY_COMPLETE")

    def test_external_claim_material_approval_is_explicit_encrypted_and_stale_on_revision(self) -> None:
        with project_temp() as root:
            service, onboarding, _, _, _ = self.make_service(root)
            master_record = onboarding.import_bytes("master_resume_docx", b"synthetic editable master", synthetic=True)
            state_ref, state = service.ensure_state()
            state["master_resume"] = {
                "secure_ref": master_record["secure_ref"], "sha256": master_record["content_sha256"],
                "extension": ".docx", "source_id": "SRC-SYNTHETICMASTER",
                "editable_docx": True, "template_fingerprint": "sha256:" + "7" * 64,
                "template_slots": ["SUMMARY"], "designated_at": "2026-08-13T00:00:00Z",
            }
            service._save_state(state_ref, state)
            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
                "conflict_resolutions": {
                    item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
                    for item in bootstrap["conflicts"]
                },
            })
            service.complete(user_confirmed=True)
            pending = service.bootstrap()
            self.assertTrue(pending["external_claim_approval"]["available"])
            self.assertFalse(pending["external_claim_approval"]["current"])
            self.assertEqual(pending["application_readiness"]["status"], "NEEDS_EXTERNAL_CLAIM_APPROVAL")

            with self.assertRaises(JobOpsError) as unconfirmed:
                service.approve_external_claims({
                    "user_confirmed": False,
                    "expected_review_hash": pending["external_claim_approval"]["review_hash"],
                    "allowed_uses": pending["external_claim_approval"]["allowed_uses"],
                })
            self.assertEqual(unconfirmed.exception.code, "EXTERNAL_CLAIM_CONFIRMATION_REQUIRED")

            approved = service.approve_external_claims({
                "user_confirmed": True,
                "expected_review_hash": pending["external_claim_approval"]["review_hash"],
                "allowed_uses": pending["external_claim_approval"]["allowed_uses"],
            })
            self.assertEqual(approved["status"], "EXTERNAL_CLAIMS_APPROVED")
            encrypted_value = json.loads(onboarding.read_bytes(str(approved["claim_set_ref"])))
            self.assertTrue(encrypted_value["applicant_confirmed"])
            self.assertTrue(all(item["applicant_confirmed"] for item in encrypted_value["claims"]))
            ready = service.bootstrap()
            self.assertTrue(ready["external_claim_approval"]["current"])
            self.assertEqual(ready["application_readiness"]["status"], "READY_FOR_OFFLINE_APPLICATION_PREPARATION")
            self.assertEqual(ready["application_readiness"]["real_external_actions"], 0)

            service.start_revision()
            revised = service.bootstrap()
            self.assertFalse(revised["external_claim_approval"]["current"])
            self.assertEqual(revised["application_readiness"]["status"], "NEEDS_ONBOARDING")

    def test_plain_docx_gets_an_ai_mapped_user_approved_tailoring_manifest(self) -> None:
        with project_temp() as root:
            service, onboarding, _, _, _ = self.make_service(root)
            fixture = PROJECT / "tests" / "fixtures" / "complex-master-resume.docx"
            master_record = onboarding.import_bytes("onboarding_source_document", fixture.read_bytes(), synthetic=True)
            block = next(item for item in inspect_docx_text_blocks(fixture) if item["text_length"] >= 20)
            fingerprint = template_fingerprint(fixture)
            state_ref, state = service.ensure_state()
            source_id = "SRC-SYNTHETIC-DOCX"
            state["master_resume"] = {
                "secure_ref": master_record["secure_ref"], "sha256": sha256_file(fixture),
                "extension": ".docx", "source_id": source_id, "editable_docx": True,
                "template_fingerprint": sha256_bytes(canonical_json(fingerprint.as_dict())),
                "template_slots": [], "designated_at": "2026-08-13T00:00:00Z",
            }
            state["material_claims"].append({
                "claim_id": "CLM-SYNTHETIC-MANIFEST", "category": "project",
                "statement": "Built a synthetic, evidence-bound workflow for a reviewed local application.",
                "source_ref": master_record["secure_ref"], "source_id": source_id,
                "source_candidate_id": "EXT-SYNTHETIC-MANIFEST",
                "provenance": {"line_start": block["line_number"], "line_end": block["line_number"]},
                "provenance_claim_ids": [], "decision": "PENDING", "approved_for_external": False,
                "deleted": False, "ai_validated": True, "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
                "confidence": "HIGH", "claim_kind": "achievement", "entity_id": None, "entity": None,
                "applicant_category_override": False,
            })
            service._save_state(state_ref, state)
            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
                "conflict_resolutions": {
                    item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
                    for item in bootstrap["conflicts"]
                },
            })
            service.complete(user_confirmed=True)
            before = service.bootstrap()
            self.assertTrue(before["tailoring_manifest"]["available"])
            self.assertFalse(before["tailoring_manifest"]["current"])
            proposal = service.tailoring_manifest_proposal()
            self.assertEqual(proposal["candidate_count"], 1)
            candidate = proposal["candidates"][0]
            self.assertEqual(candidate["allowed_categories"], ["project"])
            approved = service.approve_tailoring_manifest({
                "user_confirmed": True, "expected_proposal_hash": proposal["proposal_hash"],
                "selections": [{"block_ref": candidate["block_ref"], "category": "project"}],
            })
            manifest = json.loads(onboarding.read_bytes(str(approved["manifest_ref"])))
            self.assertNotIn(candidate["text"], json.dumps(manifest))
            current = service.bootstrap()
            self.assertTrue(current["tailoring_manifest"]["current"])
            self.assertEqual(current["application_readiness"]["master_resume"]["tailoring_mode"], "APPROVED_BLOCK_MANIFEST")
            self.assertEqual(current["application_readiness"]["master_resume"]["tailoring_block_count"], 1)
            self.assertEqual(current["application_readiness"]["real_external_actions"], 0)

            service.start_revision()
            self.assertFalse(service.bootstrap()["tailoring_manifest"]["current"])

    def test_completion_failure_removes_partial_refs_and_restores_answer_bank(self) -> None:
        with project_temp() as root:
            service, onboarding, store, _, _ = self.make_service(root)
            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
                "conflict_resolutions": {
                    item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
                    for item in bootstrap["conflicts"]
                },
            })
            _, state = service.ensure_state()
            answer_ref = str(state["answer_bank_ref"])
            previous_answer = onboarding.read_bytes(answer_ref)
            with service.database.connect() as connection:
                previous_active = {
                    row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")
                }
            previous_store_refs = set(store.values)
            with mock.patch.object(service, "_save_state", side_effect=OSError("synthetic final state failure")):
                with self.assertRaises(JobOpsError) as failed:
                    service.complete(user_confirmed=True)
            self.assertEqual(failed.exception.code, "ONBOARDING_COMPLETION_WRITE_FAILED")
            self.assertEqual(service.bootstrap()["status"], "IN_PROGRESS")
            self.assertEqual(onboarding.read_bytes(answer_ref), previous_answer)
            self.assertEqual(set(store.values), previous_store_refs)
            with service.database.connect() as connection:
                self.assertEqual(
                    {row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")},
                    previous_active,
                )
            self.assertEqual(service.complete(user_confirmed=True)["status"], "ONBOARDING_COMPLETE")

    def test_completed_snapshot_requires_versioned_revision_for_edits(self) -> None:
        with project_temp() as root:
            service, onboarding, _, _, _ = self.make_service(root)
            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
                "conflict_resolutions": {item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None} for item in bootstrap["conflicts"]},
            })
            service.complete(user_confirmed=True)
            old_ref = service.bootstrap()["state_ref"]
            old_bytes = onboarding.read_bytes(old_ref)
            with self.assertRaises(JobOpsError) as blocked:
                service.save_review({"claim_decisions": {}})
            self.assertEqual(blocked.exception.code, "ONBOARDING_REVISION_REQUIRED")

            revised = service.start_revision()
            self.assertEqual(revised["revision_number"], 2)
            self.assertTrue(revised["changed"])
            editable = service.bootstrap()
            self.assertEqual(editable["status"], "IN_PROGRESS")
            self.assertEqual(editable["revision_number"], 2)
            same_revision = service.start_revision()
            self.assertEqual(same_revision["status"], "ONBOARDING_ALREADY_EDITABLE")
            self.assertEqual(same_revision["revision_number"], 2)
            self.assertFalse(same_revision["changed"])
            self.assertEqual(same_revision["state_ref"], editable["state_ref"])
            claim = editable["claims"][0]
            service.save_review({
                "profile_review": "PENDING", "claim_decisions": {claim["claim_id"]: "PENDING"},
                "claim_edits": {claim["claim_id"]: {"statement": "Edited synthetic statement with a complete responsibility boundary.", "category": "project", "deleted": False}},
                "conflict_resolutions": {},
            })
            self.assertEqual(onboarding.read_bytes(old_ref), old_bytes)
            self.assertEqual(service.bootstrap()["claims"][0]["statement"], "Edited synthetic statement with a complete responsibility boundary.")

    def test_revision_index_failure_removes_partial_private_references(self) -> None:
        with project_temp() as root:
            service, _, store, _, _ = self.make_service(root)
            service.save_answers({"locale": "zh", "answers": full_answers()})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
                "conflict_resolutions": {
                    item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
                    for item in bootstrap["conflicts"]
                },
            })
            service.complete(user_confirmed=True)
            with service.database.connect() as connection:
                previous_active = {
                    row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")
                }
            previous_store_refs = set(store.values)
            with mock.patch.object(service, "_write_index", side_effect=OSError("synthetic revision index failure")):
                with self.assertRaises(JobOpsError) as failed:
                    service.start_revision()
            self.assertEqual(failed.exception.code, "ONBOARDING_REVISION_WRITE_FAILED")
            self.assertEqual(service.bootstrap()["status"], "ONBOARDING_COMPLETE")
            self.assertEqual(set(store.values), previous_store_refs)
            with service.database.connect() as connection:
                self.assertEqual(
                    {row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE status='ACTIVE'")},
                    previous_active,
                )
            self.assertEqual(service.start_revision()["status"], "ONBOARDING_REVISION_STARTED")

    def test_claims_can_be_merged_and_split_without_overwriting_sources(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            claims = service.bootstrap()["claims"]
            merged = service.transform_claims({
                "action": "MERGE", "claim_ids": [item["claim_id"] for item in claims],
                "statement": "Merged synthetic statement with a complete and reviewable responsibility boundary.",
                "category": "project",
            })
            self.assertEqual(len(merged["created_claim_ids"]), 1)
            current = service.bootstrap()["claims"]
            self.assertEqual(sum(not item["deleted"] for item in current), 1)
            split = service.transform_claims({
                "action": "SPLIT", "claim_ids": merged["created_claim_ids"], "category": "project",
                "statements": [
                    "First complete synthetic Claim created from the merged statement.",
                    "Second complete synthetic Claim created from the merged statement.",
                ],
            })
            self.assertEqual(len(split["created_claim_ids"]), 2)

    def test_ambiguous_hard_condition_cannot_complete(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            answers = full_answers()
            answers["work_authorization"]["value"] = "UNSURE"
            service.save_answers({"locale": "en", "answers": answers})
            bootstrap = service.bootstrap()
            service.save_review({
                "profile_review": "CONFIRMED",
                "claim_decisions": {item["claim_id"]: "REJECTED" for item in bootstrap["claims"]},
                "conflict_resolutions": {item["conflict_id"]: {"resolution": "EXCLUDE", "manual_value": None} for item in bootstrap["conflicts"]},
            })
            with self.assertRaises(JobOpsError) as caught:
                service.complete(user_confirmed=True)
            self.assertEqual(caught.exception.code, "ONBOARDING_HARD_CONDITIONS_UNRESOLVED")

    def test_local_server_is_token_gated_and_serves_bilingual_ui(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            server = create_server(service, token="synthetic-session-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(host + "/session/wrong/api/bootstrap", timeout=5)
                self.assertEqual(blocked.exception.code, 403)
                with urllib.request.urlopen(server.url, timeout=5) as response:
                    html = response.read().decode("utf-8")
                    self.assertIn("中文", html)
                    self.assertIn("data-locale=\"en\"", html)
                    self.assertIn("id=\"activityIndicator\"", html)
                    self.assertIn("JobFlow", html)
                    self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                    self.assertIn("connect-src 'self'", response.headers["Content-Security-Policy"])
                with urllib.request.urlopen(server.url + "api/bootstrap", timeout=5) as response:
                    payload = json.loads(response.read())
                    self.assertEqual(payload["build"], {
                        "product": "JobFlow", "version": __version__,
                        "ui_protocol": UI_PROTOCOL_VERSION,
                    })
                    self.assertEqual(payload["real_external_actions"], 0)
                    self.assertEqual(len(payload["catalog"]["fields"]), 27)
                    self.assertEqual(payload["catalog"]["required_field_count"], 25)
                preflight = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                preflight.request(
                    "OPTIONS",
                    "/assist/not-a-secret/pair",
                    headers={
                        "Origin": COMPANION_EXTENSION_ORIGIN,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Private-Network": "true",
                    },
                )
                preflight_response = preflight.getresponse()
                preflight_response.read()
                self.assertEqual(preflight_response.status, 204)
                self.assertEqual(preflight_response.headers["Access-Control-Allow-Origin"], COMPANION_EXTENSION_ORIGIN)
                self.assertEqual(preflight_response.headers["Access-Control-Allow-Private-Network"], "true")
                preflight.close()
                intake_preflight = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                intake_preflight.request(
                    "OPTIONS", "/intake/not-a-secret/pair",
                    headers={"Origin": COMPANION_EXTENSION_ORIGIN, "Access-Control-Request-Method": "POST"},
                )
                intake_response = intake_preflight.getresponse()
                intake_response.read()
                self.assertEqual(intake_response.status, 204)
                self.assertEqual(intake_response.headers["Access-Control-Allow-Origin"], COMPANION_EXTENSION_ORIGIN)
                intake_preflight.close()
                untrusted_preflight = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                untrusted_preflight.request(
                    "OPTIONS", "/assist/not-a-secret/pair",
                    headers={"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                )
                untrusted_response = untrusted_preflight.getresponse()
                untrusted_response.read()
                self.assertEqual(untrusted_response.status, 403)
                self.assertIsNone(untrusted_response.headers.get("Access-Control-Allow-Origin"))
                untrusted_preflight.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                session_path = "/session/synthetic-session-token/"
                headers = {
                    "X-JobOps-Session": "synthetic-session-token",
                    "Origin": host,
                    "Content-Type": "application/json",
                }
                connection.request("POST", session_path + "api/start-revision", body=b"{}", headers=headers)
                revision_response = connection.getresponse()
                revision_payload = json.loads(revision_response.read())
                self.assertEqual(revision_response.status, 200)
                self.assertEqual(revision_payload["status"], "ONBOARDING_ALREADY_EDITABLE")
                connection.request("GET", session_path + "api/bootstrap", headers={"X-JobOps-Session": "synthetic-session-token"})
                bootstrap_response = connection.getresponse()
                bootstrap_payload = json.loads(bootstrap_response.read())
                self.assertEqual(bootstrap_response.status, 200)
                self.assertEqual(bootstrap_payload["status"], "IN_PROGRESS")
                export_buffer = io.BytesIO()
                with zipfile.ZipFile(export_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("conversations.json", json.dumps([{"mapping": {
                        "user": {"message": {"author": {"role": "user"}, "content": {"parts": [
                            "I built a synthetic project and improved review accuracy by 20%."
                        ]}}}
                    }}]))
                export_bytes = export_buffer.getvalue()
                connection.request(
                    "POST",
                    session_path + "api/import?source_type=chatgpt_export_large&extension=.zip",
                    body=export_bytes,
                    headers={
                        "X-JobOps-Session": "synthetic-session-token",
                        "Origin": host,
                        "Content-Type": "application/octet-stream",
                    },
                )
                upload_response = connection.getresponse()
                upload_payload = json.loads(upload_response.read())
                self.assertEqual(upload_response.status, 200)
                self.assertEqual(upload_payload["status"], "SOURCE_PREVIEW_READY")
                self.assertEqual(list((service.onboarding.store.private_root / "staging").glob("*")), [])
                snapshot = (PROJECT / "tests" / "fixtures" / "synthetic-official-job-list.html").read_bytes()
                connection.request(
                    "POST",
                    session_path + "api/discover-official-jobs?official_url=https%3A%2F%2Fexample.com%2Fcareers&company_domain=example.com&source_format=html",
                    body=snapshot,
                    headers={
                        "X-JobOps-Session": "synthetic-session-token",
                        "Origin": host,
                        "Content-Type": "application/octet-stream",
                    },
                )
                discovery_response = connection.getresponse()
                discovery_payload = json.loads(discovery_response.read())
                self.assertEqual(discovery_response.status, 200)
                self.assertEqual(discovery_payload["status"], "LOCAL_SNAPSHOT_PARSED")
                self.assertEqual(discovery_payload["candidate_count"], 2)
                self.assertFalse(discovery_payload["snapshot_persisted"])
                self.assertEqual(discovery_payload["candidate_queue_mutations"], 0)
                self.assertEqual(discovery_payload["network_actions"], 0)
                self.assertEqual(discovery_payload["real_external_actions"], 0)
                with service.database.connect() as database_connection:
                    self.assertEqual(database_connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
                    self.assertEqual(database_connection.execute("SELECT COUNT(*) FROM intake_queue").fetchone()[0], 0)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_local_binary_upload_transport_fails_closed(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            server = create_server(service, token="synthetic-session-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = f"http://127.0.0.1:{server.server_port}"
            path = "/session/synthetic-session-token/api/discover-official-jobs?official_url=https%3A%2F%2Fexample.com%2Fcareers&company_domain=example.com&source_format=html"
            try:
                wrong_type = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                wrong_type.request(
                    "POST", path, body=b"<p>safe</p>",
                    headers={
                        "X-JobOps-Session": "synthetic-session-token", "Origin": host,
                        "Content-Type": "text/html",
                    },
                )
                wrong_type_response = wrong_type.getresponse()
                wrong_type_payload = json.loads(wrong_type_response.read())
                wrong_type.close()
                self.assertEqual(wrong_type_response.status, 400)
                self.assertEqual(wrong_type_payload["code"], "REQUEST_CONTENT_TYPE_INVALID")
                self.assertTrue(wrong_type_response.will_close)

                cross_origin = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                cross_origin.request(
                    "POST", path, body=b"<p>safe</p>",
                    headers={
                        "X-JobOps-Session": "synthetic-session-token", "Origin": "https://untrusted.example.test",
                        "Content-Type": "application/octet-stream",
                    },
                )
                cross_origin_response = cross_origin.getresponse()
                cross_origin_payload = json.loads(cross_origin_response.read())
                cross_origin.close()
                self.assertEqual(cross_origin_response.status, 403)
                self.assertEqual(cross_origin_payload["code"], "LOCAL_SESSION_REQUIRED")
                self.assertTrue(cross_origin_response.will_close)

                chunked = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                chunked.putrequest("POST", path)
                chunked.putheader("X-JobOps-Session", "synthetic-session-token")
                chunked.putheader("Origin", host)
                chunked.putheader("Content-Type", "application/octet-stream")
                chunked.putheader("Transfer-Encoding", "chunked")
                chunked.endheaders()
                chunked.send(b"b\r\n<p>safe</p>\r\n0\r\n\r\n")
                chunked_response = chunked.getresponse()
                chunked_payload = json.loads(chunked_response.read())
                chunked.close()
                self.assertEqual(chunked_response.status, 400)
                self.assertEqual(chunked_payload["code"], "REQUEST_TRANSFER_ENCODING_FORBIDDEN")
                self.assertTrue(chunked_response.will_close)

                incomplete_binary = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                incomplete_binary.putrequest("POST", path)
                incomplete_binary.putheader("X-JobOps-Session", "synthetic-session-token")
                incomplete_binary.putheader("Origin", host)
                incomplete_binary.putheader("Content-Type", "application/octet-stream")
                incomplete_binary.putheader("Content-Length", "64")
                incomplete_binary.endheaders(b"<p>safe</p>")
                assert incomplete_binary.sock is not None
                incomplete_binary.sock.shutdown(socket.SHUT_WR)
                incomplete_binary_response = incomplete_binary.getresponse()
                incomplete_binary_payload = json.loads(incomplete_binary_response.read())
                incomplete_binary.close()
                self.assertEqual(incomplete_binary_response.status, 400)
                self.assertEqual(incomplete_binary_payload["code"], "ONBOARDING_UPLOAD_INTERRUPTED")
                self.assertEqual(incomplete_binary_payload["details"]["expected_bytes"], 64)
                self.assertEqual(incomplete_binary_payload["details"]["received_bytes"], 11)
                self.assertTrue(incomplete_binary_response.will_close)

                json_path = "/session/synthetic-session-token/api/save"
                wrong_json_type = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                wrong_json_type.request(
                    "POST", json_path, body=b"{}",
                    headers={
                        "X-JobOps-Session": "synthetic-session-token", "Origin": host,
                        "Content-Type": "text/plain",
                    },
                )
                wrong_json_response = wrong_json_type.getresponse()
                wrong_json_payload = json.loads(wrong_json_response.read())
                wrong_json_type.close()
                self.assertEqual(wrong_json_response.status, 400)
                self.assertEqual(wrong_json_payload["code"], "REQUEST_CONTENT_TYPE_INVALID")
                self.assertTrue(wrong_json_response.will_close)

                incomplete_json = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                incomplete_json.putrequest("POST", json_path)
                incomplete_json.putheader("X-JobOps-Session", "synthetic-session-token")
                incomplete_json.putheader("Origin", host)
                incomplete_json.putheader("Content-Type", "application/json")
                incomplete_json.putheader("Content-Length", "8")
                incomplete_json.endheaders(b"{}")
                assert incomplete_json.sock is not None
                incomplete_json.sock.shutdown(socket.SHUT_WR)
                incomplete_json_response = incomplete_json.getresponse()
                incomplete_json_payload = json.loads(incomplete_json_response.read())
                incomplete_json.close()
                self.assertEqual(incomplete_json_response.status, 400)
                self.assertEqual(incomplete_json_payload["code"], "ONBOARDING_UPLOAD_INTERRUPTED")
                self.assertEqual(incomplete_json_payload["details"]["expected_bytes"], 8)
                self.assertEqual(incomplete_json_payload["details"]["received_bytes"], 2)
                self.assertTrue(incomplete_json_response.will_close)

                duplicate_length = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                duplicate_length.putrequest("POST", json_path)
                duplicate_length.putheader("X-JobOps-Session", "synthetic-session-token")
                duplicate_length.putheader("Origin", host)
                duplicate_length.putheader("Content-Type", "application/json")
                duplicate_length.putheader("Content-Length", "2")
                duplicate_length.putheader("Content-Length", "2")
                duplicate_length.endheaders(b"{}")
                duplicate_response = duplicate_length.getresponse()
                duplicate_payload = json.loads(duplicate_response.read())
                duplicate_length.close()
                self.assertEqual(duplicate_response.status, 400)
                self.assertEqual(duplicate_payload["code"], "REQUEST_LENGTH_INVALID")
                self.assertTrue(duplicate_response.will_close)

                chunked_json = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                chunked_json.putrequest("POST", json_path)
                chunked_json.putheader("X-JobOps-Session", "synthetic-session-token")
                chunked_json.putheader("Origin", host)
                chunked_json.putheader("Content-Type", "application/json")
                chunked_json.putheader("Transfer-Encoding", "chunked")
                chunked_json.endheaders()
                chunked_json.send(b"2\r\n{}\r\n0\r\n\r\n")
                chunked_json_response = chunked_json.getresponse()
                chunked_json_payload = json.loads(chunked_json_response.read())
                chunked_json.close()
                self.assertEqual(chunked_json_response.status, 400)
                self.assertEqual(chunked_json_payload["code"], "REQUEST_TRANSFER_ENCODING_FORBIDDEN")
                self.assertTrue(chunked_json_response.will_close)

                with service.database.connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM intake_queue").fetchone()[0], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_local_application_bundle_protocol_uses_only_manifest_metadata_and_file_bytes(self) -> None:
        with project_temp() as root:
            service, _, _, _, _ = self.make_service(root)
            server = create_server(service, token="synthetic-session-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = f"http://127.0.0.1:{server.server_port}"
            path = "/session/synthetic-session-token/api/prepare-offline-application"
            manifest = {
                "schema_version": 1,
                "metadata": {
                    "official_url": "https://example.com/careers/role",
                    "application_url": "https://example.com/careers/role",
                    "guest_available": True,
                    "evidence_excerpt": "Synthetic company evidence.",
                },
                "files": [
                    {"key": "jd", "extension": ".txt", "size": 2},
                    {"key": "official", "extension": ".html", "size": 3},
                    {"key": "form", "extension": ".html", "size": 4},
                ],
            }
            encoded = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
            body = struct.pack(">I", len(encoded)) + encoded + b"jd" + b"off" + b"form"
            try:
                with mock.patch.object(service, "prepare_offline_application_bundle", return_value={
                    "status": "AWAITING_APPROVAL", "real_external_actions": 0,
                }) as prepare:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("POST", path, body=body, headers={
                        "X-JobOps-Session": "synthetic-session-token", "Origin": host,
                        "Content-Type": "application/octet-stream",
                    })
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    connection.close()
                self.assertEqual(response.status, 200)
                self.assertEqual(result["status"], "AWAITING_APPROVAL")
                prepare.assert_called_once_with(
                    metadata=manifest["metadata"],
                    files={"jd": (".txt", b"jd"), "official": (".html", b"off"), "form": (".html", b"form")},
                )

                invalid = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                invalid.request("POST", path, body=struct.pack(">I", 1) + b"{", headers={
                    "X-JobOps-Session": "synthetic-session-token", "Origin": host,
                    "Content-Type": "application/octet-stream",
                })
                invalid_response = invalid.getresponse()
                invalid_result = json.loads(invalid_response.read())
                invalid.close()
                self.assertEqual(invalid_response.status, 400)
                self.assertEqual(invalid_result["code"], "APPLICATION_BUNDLE_PROTOCOL_INVALID")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_ui_localizes_gate_errors_and_detects_stale_service(self) -> None:
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"const UI_PROTOCOL_VERSION = {UI_PROTOCOL_VERSION};", app)
        self.assertIn('SERVICE_RESTART_REQUIRED:"serviceRestartRequired"', app)
        self.assertIn('PROFILE_REVIEW_REQUIRED:"profileReviewRequired"', app)
        self.assertIn('ONBOARDING_ANSWERS_INCOMPLETE:"answersIncomplete"', app)
        self.assertIn('CLAIM_REVIEW_INCOMPLETE:"claimReviewIncomplete"', app)
        self.assertIn('CONFLICT_REVIEW_INCOMPLETE:"conflictReviewIncomplete"', app)
        self.assertIn('SOURCE_PRIVATE_DELETE_FAILED:"privateDeleteRetry"', app)
        self.assertIn('SOURCE_DELETE_ROLLBACK_FAILED:"privateDeleteRepair"', app)
        self.assertIn("const MAX_RETAINED_SOURCE_BYTES = 64 * 1024 * 1024;", app)
        self.assertIn('privateDeleteRetry: "本机加密副本暂时无法删除', app)
        self.assertIn('privateDeleteRetry: "The local encrypted copy could not be deleted', app)
        self.assertIn('ONBOARDING_COMPLETION_WRITE_FAILED:"privateWriteRetry"', app)
        self.assertIn('ONBOARDING_COMPLETION_ROLLBACK_FAILED:"privateWriteRepair"', app)
        self.assertIn('APPLICATION_BUNDLE_PROTOCOL_INVALID:"applicationBundleInvalid"', app)
        self.assertIn('id="blockingNotice"', html)
        self.assertIn('id="pipelineDashboard"', html)
        self.assertIn('id="deferredDashboardList"', html)
        self.assertIn('id="recentDashboardList"', html)
        self.assertIn('id="executionRunsList"', html)
        self.assertIn('id="saveQueueLimit"', html)
        self.assertIn('id="emergencyStop"', html)
        self.assertIn('id="reviewPacketPanel"', html)
        self.assertIn('id="applicationFieldResolutionPanel"', html)
        self.assertIn('id="applicationFieldResolutionList"', html)
        self.assertIn('id="applicationFieldResolutionConfirm"', html)
        self.assertIn('id="saveApplicationFieldResolutions"', html)
        self.assertIn('id="officialSnapshotFile"', html)
        self.assertIn('id="analyzeOfficialSnapshot"', html)
        self.assertIn('id="officialCandidateList"', html)
        self.assertIn('id="applicationReadinessStatus"', html)
        self.assertIn('id="externalClaimConfirm"', html)
        self.assertIn('id="approveExternalClaims"', html)
        self.assertIn('id="tailoringManifestPanel"', html)
        self.assertIn('id="openTailoringManifest"', html)
        self.assertIn('id="approveTailoringManifest"', html)
        self.assertIn("function renderDashboard()", app)
        self.assertIn("function executionRunStatusLabel(", app)
        self.assertIn("function executionNextActionLabel(", app)
        self.assertIn("function renderApplicationReadiness()", app)
        self.assertIn("function renderTailoringProposal(", app)
        self.assertIn("function renderOfficialDiscovery", app)
        self.assertIn("function clearOfficialDiscovery", app)
        self.assertIn('event.target.matches("#officialCompanyDomain,#officialCareersUrl,#officialSnapshotFile")', app)
        self.assertIn('discover-official-jobs?official_url=', app)
        self.assertIn("function renderReviewPacket()", app)
        self.assertIn("function renderApplicationFieldResolution(", app)
        self.assertIn("function collectApplicationFieldResolutions()", app)
        self.assertIn('api("queue-limit"', app)
        self.assertIn('api("external-action-kill-switch"', app)
        self.assertIn('api("review-packet"', app)
        self.assertIn('api("resolve-application-fields"', app)
        self.assertIn('api("queue-decision"', app)
        self.assertIn('api("approve-external-claims"', app)
        self.assertIn('api("tailoring-manifest-proposal"', app)
        self.assertIn('api("approve-tailoring-manifest"', app)
        self.assertIn('id="packetDecisionConfirm"', html)
        self.assertIn('id="confirmPacketDecision"', html)
        self.assertNotIn("showToast(e.message", app)


if __name__ == "__main__":
    unittest.main()
