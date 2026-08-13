from __future__ import annotations

import copy
import json
import unittest

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named
from jobops.sourcing import source_route_hash


H = "sha256:" + "a" * 64
T = "2026-08-12T00:00:00Z"
FUTURE = "2099-01-01T00:00:00Z"
JOB = "JOB-ABCDEF123456"
APP = "APP-ABCDEF123456"


def dimension() -> dict:
    return {"score": 80, "evidence": ["synthetic fixture"], "calculation": "one local fixture", "gaps": [], "confidence": "MEDIUM", "decision_impact": "Does not override hard gates."}


def valid_fixtures() -> dict[str, dict]:
    route = {
        "status": "ROUTE_APPROVED", "company_domain": "example.com", "official_entry_url": "https://example.com/careers/a",
        "current_url": "https://example.com/careers/a", "route_kind": "OFFICIAL_DIRECT", "provider": "company",
        "ats_tenant": "example.com", "ats_board": "official", "ats_job_identity": "a", "guest_mode": "GUEST_SELECTED",
        "account_action": "NONE", "official_page_hash": H, "jd_snapshot_hash": H, "route_hash": H,
        "navigation_history": ["https://example.com/careers/a"],
    }
    route["route_hash"] = source_route_hash(route)
    return {
        "application": {"application_id": APP, "job_id": JOB, "status": "AWAITING_APPROVAL", "site": "example.com", "resume_hash": H, "answers_hash": H, "dry_run": True, "secure_profile_ref": "secure-ref:SYNTHETIC01", "sensitive_fields": [], "unknown_fields": []},
        "approval": {
            "approval_id": "APR-ABCDEF123456", "application_id": APP, "job_id": JOB, "jd_snapshot_hash": H, "jd_freshness_hash": H,
            "source_route_hash": H, "canonical_url": "https://example.com/careers/a", "ats_tenant": "example", "ats_board": "official",
            "ats_job_identity": "a", "profile_version": "1", "claim_set_hash": H, "form_snapshot_hash": H, "answers_hash": H,
            "review_packet_hash": H, "uploads": [{"filename": "resume.pdf", "purpose": "resume", "sha256": H}],
            "external_actions": ["upload_material"], "site_policy_version": "1", "unresolved_stops": [], "mandatory_unknowns": [],
            "context_hash": H, "issued_at": T, "expires_at": FUTURE, "nonce": "nonce-" + "a" * 48,
            "approval_version": 2, "status": "APPROVED", "consumed_at": None,
        },
        "audit-event": {"event_id": "EVT-ABCDEF123456", "application_id": APP, "event_type": "STATE_TRANSITION", "from_state": "FORM_VALIDATED", "to_state": "AWAITING_APPROVAL", "payload_hash": H, "created_at": T},
        "candidate-profile": {"profile_ref": "secure-ref:SYNTHETIC01", "profile_version": "1", "candidate_display_name": "Synthetic Candidate", "target_functions": ["analysis"], "target_levels": ["mid"], "locations": ["remote"], "remote_preference": "remote", "minimum_salary": None, "work_authorization": "UNKNOWN", "skills": ["Python"], "years_experience": 3},
        "candidate-profile-draft": {
            "schema_version": 1, "profile_version": "draft-1", "status": "AWAITING_USER_CLAIM_AND_PROFILE_APPROVAL",
            "master_resume_ref": "secure-ref:SYNTHETIC01",
            "candidate_display_name": {"value": "Synthetic Candidate", "status": "APPLICANT_PROVIDED_UNCONFIRMED"},
            "contact_fields_present": ["email"],
            "resume_facts": [{"fact_id": "RSM-ABCDEF123456", "category": "skill", "value": "Python", "status": "APPLICANT_PROVIDED_UNCONFIRMED"}],
            "target_preferences": {"target_roles": {"value": None, "status": "UNKNOWN"}},
            "hard_conditions": {"work_authorization": {"value": None, "status": "UNKNOWN"}},
            "field_status_counts": {"applicant_provided_unconfirmed": 2, "unknown": 2}, "created_at": T,
        },
        "onboarding-answer-bank": {
            "schema_version": 2, "status": "IN_PROGRESS", "locale": "zh",
            "answers": {
                "target_roles": {
                    "value": ["analysis"], "status": "CONFIRMED", "source": "APPLICANT_CONFIRMED",
                    "use_policy": "reuse", "updated_at": T,
                }
            },
            "completion": {"total": 25, "resolved": 1, "remaining": 24, "percent": 4.0},
            "updated_at": T,
        },
        "onboarding-completion": {
            "schema_version": 1, "status": "ONBOARDING_COMPLETE",
            "profile_ref": "secure-ref:SYNTHETIC01", "answer_bank_ref": "secure-ref:SYNTHETIC02",
            "claim_approvals_ref": "secure-ref:SYNTHETIC03",
            "counts": {"answers_resolved": 25, "answers_total": 25, "claims_reviewed": 2, "claims_total": 2, "conflicts_resolved": 1, "conflicts_total": 1},
            "sources": {"resume_or_material": 1, "ai": 1, "direct_answers": 25},
            "locale": "en", "completed_at": T, "real_external_actions": 0, "knowledge_write_operations": 0,
        },
        "claim": {
            "claim_id": "CLM-SYNTHETIC01", "raw_fact": "Synthetic fixture", "allowed_wording": ["Synthetic fixture"], "forbidden_wording": ["Real engagement"],
            "responsibility_boundary": {"candidate": "fixture", "team": "none", "ai": "generated fixture"}, "evidence": [{"kind": "fixture", "value": 1}],
            "source_refs": [{"source_id": "personal_redacted", "relative_path": "fixture.md", "heading": "Evidence", "excerpt": "Synthetic fixture", "excerpt_fingerprint": H, "fingerprint": H}],
            "approved_for_external": True, "lifecycle_status": "approved", "sensitivity": "synthetic", "last_verified_at": T, "expires_at": FUTURE, "content_hash": H, "version": 1,
        },
        "fit-result": {"eligibility_status": "ELIGIBLE", "hard_gaps": [], "unknowns": [], "dimensions": {key: dimension() for key in ("function", "capability", "evidence", "industry", "level", "location", "preference")}, "overall_score": 80, "recommendation": "RECOMMEND", "explanation": ["All hard conditions passed."]},
        "job": {"job_id": JOB, "source_type": "txt", "source_locator": "tests/fixtures/mock-jd.txt", "official_url": "https://example.com/careers/a", "company": "Example", "title": "Analyst", "location": "Remote", "status": "DISCOVERED", "discovered_at": T},
        "official-discovery": {
            "schema_version": 1, "status": "LOCAL_SNAPSHOT_PARSED", "source_mode": "LOCAL_SNAPSHOT_ONLY",
            "source_format": "html", "company_domain": "example.com", "official_entry_url": "https://example.com/careers",
            "snapshot_hash": H, "candidate_count": 1, "ignored_link_count": 0, "deduplicated_link_count": 0,
            "candidates": [{
                "candidate_id": "JDC-ABCDEF123456", "status": "NEEDS_LIVE_FRESHNESS_CHECK",
                "discovered_url": "https://example.com/careers/jobs/a", "route_kind": "OFFICIAL_DIRECT_DISCOVERED",
                "provider": "company", "ats_tenant": "example.com", "ats_board": "official", "ats_job_identity": "a",
                "title": "Analyst", "title_status": "EXTRACTED", "location": "UNKNOWN", "location_status": "UNKNOWN",
                "evidence_kind": "anchor", "snapshot_hash": H, "requires_live_freshness_check": True,
                "requires_route_verification": True, "network_actions": 0, "real_external_actions": 0,
            }],
            "untrusted_page_content_executed": False, "network_actions": 0, "real_external_actions": 0,
            "knowledge_write_operations": 0,
        },
        "jd": {"job_id": JOB, "snapshot_hash": H, "captured_at": T, "company": "Example", "title": "Analyst", "location": "Remote", "salary": None, "work_authorization": None, "deadline": None, "responsibilities": [], "hard_requirements": [], "preferred_qualifications": [], "keywords": []},
        "jd-snapshot": {"snapshot_id": "JDS-ABCDEF123456", "job_id": JOB, "source_format": "txt", "content_hash": H, "relative_path": "workspace/jobs/a.txt", "captured_at": T, "source_url": None},
        "jd-analysis": {"analysis_id": "JDA-ABCDEF123456", "job_id": JOB, "snapshot_hash": H, "company": "Example", "title": "Analyst", "location": "Remote", "level": "UNKNOWN", "responsibilities": [], "requirements": [], "preferred_qualifications": [], "keywords": [], "untrusted_instruction_signals": [], "created_at": T},
        "knowledge-evidence": {"source_id": "personal_redacted", "relative_path": "fixture.md", "file_sha256": H, "heading": "Evidence", "excerpt": "Synthetic fixture", "excerpt_sha256": H, "last_verified_at": T, "expires_at": FUTURE},
        "requirement": {"requirement_id": "REQ-SKILL1", "category": "skill", "text": "Python and SQL", "logic": "ALL", "items": ["Python", "SQL"], "threshold": None},
        "research-finding": {"finding_id": "RFN-ABCDEF123456", "claim": "Synthetic launch", "source_url": "https://example.com/news", "source_type": "official_company", "snapshot_hash": H, "published_at": T, "accessed_at": T, "evidence_excerpt": "Synthetic launch", "evidence_sha256": H, "freshness": "CURRENT", "official": True},
        "source-route": route,
        "site-policy": {"policy_version": "1", "provider": "local_fixture", "real_actions_enabled": False, "guest_first": True, "account_creation_enabled": False, "allowed_actions": ["local_snapshot"], "checked_at": T},
        "material-version": {"material_id": "MAT-ABCDEF123456", "application_id": APP, "kind": "resume_pdf", "relative_path": "reports/fixture.pdf", "content_hash": H, "master_hash": H, "claim_set_hash": H, "version": 1, "qa_status": "PASS", "created_at": T},
        "material-plan": {
            "schema_version": 1, "status": "READY_FOR_REVIEW",
            "resume": {
                "derivation": "TAILORED_COPY_OF_SINGLE_APPROVED_MASTER",
                "master_secure_ref": "secure-ref:SYNTHETIC01", "master_sha256": H,
                "generated_before_application": True,
                "docx_secure_ref": "secure-ref:SYNTHETIC02", "docx_sha256": H,
                "pdf_secure_ref": "secure-ref:SYNTHETIC03", "pdf_sha256": H,
            },
            "cover_letter": {
                "request_status": "NOT_REQUESTED", "generation_status": "NOT_GENERATED",
                "docx_secure_ref": None, "docx_sha256": None,
                "pdf_secure_ref": None, "pdf_sha256": None,
            },
            "public_links": [{
                "field_id": "github", "kind": "github", "required": False,
                "binding_status": "BOUND_CONFIRMED_PUBLIC_VALUE", "value_sha256": H,
                "value_exposed_in_packet": False,
            }],
            "portfolio_file": {
                "request_status": "NOT_REQUESTED", "binding_status": "NOT_REQUESTED",
                "secure_ref": None, "sha256": None, "safe_filename": None,
            },
            "all_uploads_and_submission_blocked": True, "real_external_actions": 0,
        },
        "application-field": {"field_id": "FLD-ABCDEF123456", "application_id": APP, "classification": "ordinary_fixed", "action": "PREFILL", "status": "READY", "secure_ref": None, "redacted_summary": None, "field_hash": H},
        "ats-form-snapshot": {
            "schema_version": 1, "status": "FORM_SNAPSHOT_ANALYZED", "source_mode": "LOCAL_SNAPSHOT_ONLY",
            "provider": "company", "step_kind": "MY_INFORMATION", "canonical_url": "https://example.com/careers/a", "source_route_hash": route["route_hash"],
            "page_content_hash": H, "form_snapshot_hash": H, "field_count": 1, "ignored_hidden_control_count": 1,
            "classification_counts": {"private_fixed": 1},
            "fields": [{
                "control_ref": "CTL-ABCDEF123456", "control_type": "email", "required": True,
                "classification": "private_fixed", "answer_key": "email", "logical_field_hash": H,
                "reason_code": "SECURE_REFERENCE_REQUIRED", "prompt_hash": H,
                "option_count": 0, "existing_value_discarded": True, "binding_status": "UNBOUND", "action": "STOP",
            }],
            "blockers": [], "form_action_statuses": ["SAME_ORIGIN"], "iframe_statuses": [],
            "entered_values_retained": False, "submit_blocked": True, "upload_blocked": True,
            "account_creation_blocked": True, "untrusted_page_content_executed": False,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "browser-action-plan": {
            "schema_version": 1, "status": "LOCAL_PLAN_READY", "form_snapshot_hash": H,
            "source_route_hash": route["route_hash"], "canonical_url": "https://example.com/careers/a", "plan_hash": H,
            "fillable_count": 1, "stopped_count": 0,
            "actions": [{
                "control_ref": "CTL-ABCDEF123456", "classification": "private_fixed", "action": "PROPOSE_PREFILL",
                "binding_kind": "SECURE_REF", "binding_ref": "secure-ref:SYNTHETIC01", "reason_code": "SECURE_REFERENCE_REQUIRED",
            }],
            "submit_blocked": True, "upload_blocked": True, "account_creation_blocked": True,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "ats-vertical-evidence": {
            "schema_version": 1, "status": "LOCAL_ATS_PLAN_VALIDATED", "provider": "greenhouse",
            "source_route_hash": route["route_hash"], "form_snapshot_hash": H, "browser_plan_hash": H,
            "fields_discovered": 2, "fields_proposed": 1, "fields_stopped": 1,
            "browser_adapter_status": "FAKE_PLAN_VALIDATED", "fields_modified": 0,
            "submit_blocked": True, "upload_blocked": True, "account_creation_blocked": True,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "ats-form-sequence": {
            "schema_version": 1, "status": "LOCAL_FORM_SEQUENCE_ANALYZED", "source_mode": "LOCAL_SNAPSHOT_SEQUENCE_ONLY",
            "provider": "workday", "canonical_url": "https://example.wd5.myworkdayjobs.com/careers/job/a",
            "source_route_hash": route["route_hash"], "step_count": 1,
            "steps": [{"step_index": 1, "step_kind": "MY_INFORMATION", "form_snapshot_hash": H, "page_content_hash": H, "field_count": 1, "blocker_count": 0}],
            "unique_field_count": 1, "duplicate_field_count": 0, "blockers": [], "sequence_hash": H,
            "navigation_performed": False, "entered_values_retained": False,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "ats-capability-report": {
            "schema_version": 1, "status": "OFFLINE_ATS_CAPABILITIES", "provider_count": 1,
            "providers": [{
                "provider": "lever", "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
                "saved_snapshot_modes": ["single_html"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
                "dynamic_control_strategy": "opaque_control_ref", "guest_first": True,
                "account_creation_blocked": True, "upload_blocked": True, "submit_blocked": True,
                "live_site_verified": False, "browser_actions": 0, "network_actions": 0,
                "real_external_actions": 0, "contract_hash": H,
            }],
            "live_site_accessed": False, "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "continuous-intake-plan": {
            "schema_version": 1, "status": "MANUAL_TICK_READY", "mode": "MANUAL_TICK_ONLY", "plan_hash": H,
            "job_count": 3, "pending_limit": 5, "awaiting_approval": 2, "reserved_slots": 1,
            "existing_deferred": 4, "slots_available": 2, "jobs_eligible_this_tick": 2,
            "jobs_expected_to_defer": 1, "requires_explicit_invocation": True,
            "background_service_started": False, "system_tasks_registered": 0,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "release-readiness": {
            "schema_version": 1, "status": "PUBLIC_RELEASE_BLOCKED", "version": "0.1.0",
            "head_commit": "a" * 40, "worktree_clean": True, "version_consistent": True,
            "local_verification_status": "PASS", "public_repository_status": "PASS",
            "source_candidate_status": "PASS", "independent_qa_fresh": False,
            "author_identity_status": "REVIEW_REQUIRED", "release_tag_status": "MISSING",
            "manual_release_gates": {
                "repository_metadata": "PENDING", "private_vulnerability_reporting": "PENDING",
                "sanitized_screenshots": "PENDING", "clean_windows_profile": "PENDING",
            },
            "blockers": ["GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED", "INDEPENDENT_QA_STALE_OR_MISSING", "RELEASE_TAG_MISSING"],
            "upload_performed": False, "network_actions": 0, "real_external_actions": 0,
            "next_safe_action": "confirm a public GitHub noreply author identity policy",
        },
        "queue-reservation": {"reservation_id": "RSV-ABCDEF123456", "intake_key": H, "application_id": APP, "status": "CONSUMED", "pending_limit": 3, "pending_count": 1, "reserved_count": 1, "created_at": T, "updated_at": T},
        "recovery-event": {"recovery_id": "RCV-ABCDEF123456", "application_id": APP, "blocked_state": "SUBMISSION_UNKNOWN", "last_safe_state": "APPROVED", "validation_hash": H, "decision": "NO_AUTO_RETRY", "created_at": T},
        "receipt": {"receipt_id": "RCP-ABCDEF123456", "application_id": APP, "source": "fake-receipt", "confirmation_type": "confirmation_number", "confirmation_hash": H, "verified": True, "verified_at": T},
        "review-packet": {"schema_version": 1, "status": "AWAITING_APPROVAL", "packet_id": "RPK-ABCDEF123456", "application_id": APP, "job": {"job_id": JOB}, "jd_captured_at": T, "fit": {"overall_score": 80}, "hard_gaps": [], "resume_bullets": [], "master_resume_diff": {}, "form_questions": [], "sensitive_fields": [], "uploads": [{"filename": "resume.pdf", "sha256": H}], "material_plan": {"status": "READY_FOR_REVIEW"}, "external_actions": ["upload_material"], "source_route": route, "queue": {"pending_limit": 3}, "content_hash": H},
        "onboarding-review": {
            "schema_version": 1, "packet_id": "ONB-ABCDEF123456", "status": "AWAITING_USER_CLAIM_AND_PROFILE_APPROVAL",
            "final_states": ["MASTER_RESUME_SECURELY_IMPORTED", "CANDIDATE_PROFILE_DRAFTED", "CLAIM_REVIEW_PACKET_READY"],
            "selected_file": {"safe_display_name": "resume-2026-aug-reference.pdf", "source_type": "pdf", "sha256_prefix": "sha256:aaaaaaaaaaaa", "size_bytes": 100, "modified_at": T, "paired_pdf": False},
            "master_resume": {"secure_ref": "secure-ref:SYNTHETIC01", "pdf_reference_ref": "secure-ref:SYNTHETIC01", "editable_master_status": "EDITABLE_MASTER_DOCX_MISSING", "structure_status": "LIMITED_PDF_REFERENCE", "page_count": 1, "template_fingerprint": H, "visual_status": "PASS", "visual_record_ref": "secure-ref:SYNTHETIC02"},
            "candidate_profile": {"secure_ref": "secure-ref:SYNTHETIC03", "completeness_percent": 50, "provided_unconfirmed": 4, "unknown": 4, "confirmation_field_count": 8},
            "answer_bank": {"secure_ref": "secure-ref:SYNTHETIC04", "unknown_field_count": 18, "categories": ["job_target"]},
            "claims": {"secure_ref": "secure-ref:SYNTHETIC05", "proposed": 1, "resume_only": 2, "optional": 1, "conflicts": 0, "auto_approved": 0},
            "unknown_hard_conditions": ["work_authorization"],
            "validation": {"runtime_schema": "PASS", "secure_roundtrip": "PASS", "source_file_unchanged": True, "fingerprint_reverified": True, "leak_findings": 0, "staging_residue": 0, "database_consistent": "PASS", "knowledge_write_operations": 0, "project_boundary": "PASS", "external_actions": 0},
            "real_external_actions": 0, "knowledge_bases": "UNCHANGED", "created_at": T,
            "next_safe_action": "review-onboarding --packet-ref secure-ref:SYNTHETIC05",
        },
    }


class RuntimeSchemaMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_dir = PROJECT / "schemas"
        self.fixtures = valid_fixtures()

    def test_every_declared_runtime_schema_has_a_valid_fixture(self) -> None:
        names = {path.name.removesuffix(".schema.json") for path in self.schema_dir.glob("*.schema.json")}
        self.assertEqual(names, set(self.fixtures))
        for name, value in self.fixtures.items():
            with self.subTest(schema=name):
                self.assertIs(validate_named(name, value, self.schema_dir), value)

    def test_every_schema_rejects_missing_extra_and_wrong_type(self) -> None:
        for name, value in self.fixtures.items():
            required = json.loads((self.schema_dir / f"{name}.schema.json").read_text(encoding="utf-8"))["required"]
            mutations = []
            missing = copy.deepcopy(value); missing.pop(required[0]); mutations.append(missing)
            extra = copy.deepcopy(value); extra["unexpected_field"] = True; mutations.append(extra)
            wrong = copy.deepcopy(value); wrong[required[0]] = []; mutations.append(wrong)
            for index, mutation in enumerate(mutations):
                with self.subTest(schema=name, mutation=index), self.assertRaises(JobOpsError):
                    validate_named(name, mutation, self.schema_dir)

    def test_enums_hashes_secure_refs_urls_and_times_are_strict_where_present(self) -> None:
        patterns = {
            "enum": (lambda schema: "enum" in schema, "NOT_IN_ENUM"),
            "sha": (lambda schema: str(schema.get("pattern", "")).startswith("^sha256:"), "sha256:bad"),
            "secure": (lambda schema: "secure-ref" in str(schema.get("pattern", "")), "plaintext-profile"),
            "url": (lambda schema: schema.get("format") == "uri", "not-a-url"),
            "time": (lambda schema: schema.get("format") == "date-time", "not-a-time"),
        }
        coverage = {key: 0 for key in patterns}
        for name, value in self.fixtures.items():
            schema = json.loads((self.schema_dir / f"{name}.schema.json").read_text(encoding="utf-8"))
            for category, (predicate, invalid) in patterns.items():
                field = next((key for key, child in schema.get("properties", {}).items() if predicate(child) and key in value and value[key] is not None), None)
                if field:
                    coverage[category] += 1
                    changed = copy.deepcopy(value); changed[field] = invalid
                    with self.subTest(schema=name, category=category), self.assertRaises(JobOpsError):
                        validate_named(name, changed, self.schema_dir)
        self.assertTrue(all(count > 0 for count in coverage.values()), coverage)

    def test_cross_field_semantic_conflicts_are_rejected(self) -> None:
        conflicts = {
            "approval": ("expires_at", T),
            "claim": ("lifecycle_status", "revoked"),
            "knowledge-evidence": ("expires_at", T),
            "source-route": ("current_url", "https://example.com/careers/b"),
            "queue-reservation": ("reserved_count", 3),
            "requirement": ("threshold", 1),
            "fit-result": ("eligibility_status", "INELIGIBLE"),
            "research-finding": ("source_type", "local_fixture"),
            "application-field": ("classification", "unknown_stop"),
            "recovery-event": ("decision", "RESUME_SAFE_STEP"),
            "receipt": ("verified", False),
            "official-discovery": ("candidate_count", 2),
            "ats-form-snapshot": ("field_count", 2),
            "browser-action-plan": ("fillable_count", 0),
            "ats-vertical-evidence": ("fields_discovered", 3),
            "ats-form-sequence": ("step_count", 2),
            "ats-capability-report": ("provider_count", 2),
            "continuous-intake-plan": ("job_count", 4),
        }
        for name, (field, invalid) in conflicts.items():
            changed = copy.deepcopy(self.fixtures[name]); changed[field] = invalid
            with self.subTest(schema=name), self.assertRaises(JobOpsError) as caught:
                validate_named(name, changed, self.schema_dir)
            self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")


if __name__ == "__main__":
    unittest.main()
