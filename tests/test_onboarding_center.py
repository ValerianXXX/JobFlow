from __future__ import annotations

import io
import http.client
import json
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from _support import PROJECT, project_temp
from jobops import UI_PROTOCOL_VERSION, __version__
from jobops.ai_runtime import AIAnalysisEngine, LocalSubprocessAIEngine
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_catalog import FIELD_BY_ID, FIELD_IDS, public_catalog
from jobops.onboarding_center import OnboardingCenterService, _evidence_preview
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.util import canonical_json, sha256_bytes


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
        answers[field_id] = {
            "value": value,
            "status": "CONFIRMED",
            "use_policy": field["default_policy"],
        }
    return answers


class OnboardingCenterTests(unittest.TestCase):
    def make_service(self, root: Path, *, with_ai: bool = True) -> tuple[OnboardingCenterService, PrivateOnboarding, MemorySecureStore, str, bytes]:
        project = root / "project"
        (project / "schemas").mkdir(parents=True)
        (project / "state").mkdir()
        for name in ("candidate-profile", "onboarding-answer-bank", "onboarding-completion"):
            shutil.copy2(PROJECT / "schemas" / f"{name}.schema.json", project / "schemas")
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
        self.assertEqual(len(catalog["fields"]), 25)
        self.assertEqual(len(set(FIELD_IDS)), 25)
        for group in catalog["groups"]:
            self.assertTrue(group["label"]["zh"])
            self.assertTrue(group["label"]["en"])
        for field in catalog["fields"]:
            self.assertTrue(field["label"]["zh"])
            self.assertTrue(field["label"]["en"])

    def test_ui_prioritizes_conflicts_and_wraps_every_long_operation(self) -> None:
        html = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        styles = (PROJECT / "src" / "jobops" / "ui" / "styles.css").read_text(encoding="utf-8")
        self.assertLess(html.index('id="conflictSection"'), html.index('id="claimGroups"'))
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
        self.assertIn("elapsedWithEstimate", script)
        self.assertIn("updateActivity", script)
        self.assertIn("claim-row-conflict", styles)
        self.assertIn("refreshLatest", script)
        self.assertIn('cache:"no-store"', script)
        self.assertIn("jobflow-v2", html)
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
        self.assertIn("aiRepairApplied", script)
        self.assertIn("AI_RESPONSE_REPAIR_FAILED", script)

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
            self.assertEqual(service.bootstrap()["suggestions"], [])

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
            service.preview_source("project_case", ".txt", b"Synthetic source line one.\nSynthetic source line two.")
            bootstrap = service.bootstrap()
            pending = bootstrap["pending_sources"][0]
            self.assertEqual(bootstrap["ai_engine"]["status"], "READY")
            self.assertEqual(pending["extraction_summary"]["analysis_mode"], "AI_CORE_ENTITY_ANALYSIS")
            self.assertEqual(len(pending["candidates"]), 1)
            self.assertFalse(pending["candidates"][0]["selected"])
            self.assertEqual(pending["candidates"][0]["selection_reason"], "AI_DERIVED_REQUIRES_CONFIRMATION")

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
            ("education", "Gamma University", "Finance degree"), ("project", "Project Delta", "Project Lead"),
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

    def test_ai_contract_rejects_duplicate_real_world_entities(self) -> None:
        duplicate = {
            "entity_type": "work", "organization": "Alpha", "role": "Analyst",
            "start_date": "2020", "end_date": "2021", "line_start": 1, "line_end": 1,
        }
        with self.assertRaises(JobOpsError) as blocked:
            LocalSubprocessAIEngine._validated_candidates(
                {"schema_version": 2, "entities": [
                    {"entity_key": "first", **duplicate}, {"entity_key": "second", **duplicate},
                ], "candidates": []},
                source_id="SRC-SYNTHETIC", source_lines=["Alpha Analyst 2020 to 2021."],
            )
        self.assertEqual(blocked.exception.code, "AI_RESPONSE_INVALID")

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
            self.assertEqual(caught.exception.details["fields"], list(FIELD_IDS))

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
                    self.assertEqual(len(payload["catalog"]["fields"]), 25)
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
                connection.close()
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
        self.assertIn('id="blockingNotice"', html)
        self.assertNotIn("showToast(e.message", app)


if __name__ == "__main__":
    unittest.main()
