from __future__ import annotations

import copy
import json
import unittest

from _support import PROJECT
from jobops.application_execution import build_application_execution_plan
from jobops.application_readiness import build_application_readiness
from jobops.errors import JobOpsError
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.product_capabilities import product_capability_report
from jobops.publisher_attestation import signer_readiness_challenge_sha256
from jobops.resume_tailoring import build_resume_tailoring_manifest
from jobops.runtime_schema import validate_named
from jobops.sourcing import source_route_hash
from jobops.util import canonical_json, load_json, sha256_bytes, sha256_file


H = "sha256:" + "a" * 64
PRODUCTION_RELEASE_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
T = "2026-08-12T00:00:00Z"
FUTURE = "2099-01-01T00:00:00Z"
JOB = "JOB-ABCDEF123456"
APP = "APP-ABCDEF123456"


def application_wheel_provenance(
    *, wheel: str, build_lock: str, commit: str = "a" * 40
) -> dict[str, object]:
    return {
        "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
        "source_commit": commit,
        "source_git_tree_oid": "b" * 40,
        "source_build_tree_sha256": "sha256:" + "c" * 64,
        "source_archive_sha256": "sha256:" + "d" * 64,
        "build_lock_sha256": build_lock,
        "build_recipe_sha256": "sha256:" + "e" * 64,
        "pass_a_wheel_sha256": wheel,
        "pass_b_wheel_sha256": wheel,
        "reproducible": True,
    }


def dimension() -> dict:
    return {"score": 80, "evidence": ["synthetic fixture"], "calculation": "one local fixture", "gaps": [], "confidence": "MEDIUM", "decision_impact": "Does not override hard gates."}


def runtime_closure_records() -> list[dict[str, object]]:
    return [
        {"path": ".jobops-root", "size": 1, "sha256": H},
        {"path": "app/jobops/cli.py", "size": 1, "sha256": H},
        {"path": "app/jobops/runtime_health.py", "size": 1, "sha256": H},
        {"path": "app/jobops/__init__.py", "size": 1, "sha256": H},
        {"path": "config/windows-cp313-build.lock", "size": 1, "sha256": H},
        {"path": "config/windows-cp313-runtime.lock", "size": 1, "sha256": H},
        {"path": "runtime/python.exe", "size": 1, "sha256": H},
        {"path": "runtime/python313.dll", "size": 1, "sha256": H},
        {"path": "runtime/python313.zip", "size": 1, "sha256": H},
        {"path": "runtime/python313._pth", "size": 1, "sha256": H},
    ]


def valid_fixtures() -> dict[str, dict]:
    route = {
        "status": "ROUTE_APPROVED", "company_domain": "example.com", "official_entry_url": "https://example.com/careers/a",
        "current_url": "https://example.com/careers/a", "route_kind": "OFFICIAL_DIRECT", "provider": "company",
        "ats_tenant": "example.com", "ats_board": "official", "ats_job_identity": "a", "guest_mode": "GUEST_SELECTED",
        "account_action": "NONE", "official_page_hash": H, "jd_snapshot_hash": H, "route_hash": H,
        "navigation_history": ["https://example.com/careers/a"],
    }
    route["route_hash"] = source_route_hash(route)
    execution_plan = build_application_execution_plan(
        application_id=APP,
        source_route={
            "provider": "greenhouse", "route_hash": H,
            "guest_mode": "GUEST_SELECTED", "account_action": "NONE",
        },
        form_snapshot_hash=H,
        browser_plan_hash=H,
        form_fields=[
            {"classification": "private_fixed", "action": "PREFILL_FROM_SECURE_STORE"},
            {"classification": "ordinary_fixed", "action": "PREFILL"},
            {"classification": "work_authorization_stop", "action": "STOP"},
            {"classification": "file_upload_stop", "action": "STOP"},
            {"classification": "final_submit_stop", "action": "STOP"},
        ],
        material_plan={
            "status": "READY_FOR_REVIEW",
            "cover_letter": {"generation_status": "NOT_GENERATED"},
            "portfolio_file": {"binding_status": "NOT_REQUESTED"},
            "all_uploads_and_submission_blocked": True,
            "real_external_actions": 0,
        },
        pending_limit=3,
    )
    external_claim_input = [{
        "claim_id": "CLM-SYNTHETIC01", "category": "project", "claim_kind": "achievement",
        "statement": "The applicant completed a synthetic, evidence-bound project.",
        "decision": "CONFIRMED", "deleted": False,
        "source_bindings": [{
            "kind": "MASTER_RESUME", "secure_ref": "secure-ref:SYNTHETIC01", "content_sha256": H,
        }],
    }]
    external_claim_set = build_external_claim_set(
        onboarding_state_ref="secure-ref:SYNTHETIC01",
        profile_ref="secure-ref:SYNTHETIC02",
        master_resume={"secure_ref": "secure-ref:SYNTHETIC03", "sha256": H, "editable_docx": True},
        claims=external_claim_input,
        allowed_uses=["resume", "cover_letter", "application_narrative"],
        expected_review_hash=claim_review_hash(external_claim_input, H),
        approved_at="2098-01-01T00:00:00Z",
    )
    application_readiness = build_application_readiness(
        onboarding_status="ONBOARDING_COMPLETE", ai_ready=True,
        master_resume={
            "secure_ref": "secure-ref:SYNTHETIC03", "sha256": H, "editable_docx": True,
            "template_fingerprint": H, "template_slots": ["SUMMARY"],
        },
        confirmed_claim_count=1, claim_review_hash=external_claim_set["review_hash"],
        external_claim_status={"current": True, "claim_count": 1},
        queue={"pending_limit": 3, "awaiting_approval": 1, "slots_available": 2},
    )
    resume_tailoring_manifest = build_resume_tailoring_manifest(
        onboarding_state_ref="secure-ref:SYNTHETIC01",
        master_resume={
            "secure_ref": "secure-ref:SYNTHETIC03", "sha256": H,
            "template_fingerprint": H, "editable_docx": True,
        },
        proposal={
            "proposal_hash": H,
            "candidates": [{
                "block_ref": "RBL-ABCDEF123456", "part_name": "word/document.xml",
                "paragraph_index": 3, "original_text_sha256": H,
                "maximum_characters": 300, "allowed_categories": ["project"],
            }],
        },
        selections=[{"block_ref": "RBL-ABCDEF123456", "category": "project"}],
        expected_proposal_hash=H, user_confirmed=True,
    )
    live_acceptance = {
        "schema_version": 1,
        "status": "LIVE_ACCEPTANCE_EVIDENCE",
        "generated_at": T,
        "freshness_days": 30,
        "provider_count": 6,
        "providers": [
            {
                "provider": provider,
                "evidence_scope": "PAGE_ROUTE_SPECIFIC_NOT_UNIVERSAL",
                "current_page_route_runs": 0,
                "expired_page_route_runs": 0,
                "distinct_site_fingerprints": 0,
                "pre_submit_verified_runs": 0,
                "result_observed_runs": 0,
                "blocked_or_failed_runs": 0,
                "passed_stages": [],
                "latest_observed_at": None,
                "universal_live_compatibility": False,
            }
            for provider in ("company", "greenhouse", "lever", "workday", "ashby", "smartrecruiters")
        ],
        "current_page_route_evidence_count": 0,
        "live_site_accessed": False,
        "universal_live_compatibility": False,
        "final_submit": "USER_ONLY",
        "final_submit_actions": 0,
        "automatic_retries": 0,
        "private_values_persisted": 0,
        "page_text_persisted": 0,
    }
    live_acceptance["report_hash"] = sha256_bytes(canonical_json(live_acceptance))
    update_policy = {
        "minimum_updater_version": "0.6.0",
        "minimum_bootstrap_version": "0.6.0",
        "required_structural_status": "BUILT_UNATTESTED",
        "publisher_attestation_required": True,
        "final_submit_user_only": True,
        "automatic_retry_submission_unknown": False,
        "external_actions_during_update": 0,
    }
    runtime_source = load_json(PROJECT / "config" / "windows-runtime-source.json")
    runtime_lock = load_json(PROJECT / "config" / "windows-cp313-runtime.lock")
    build_lock = load_json(PROJECT / "config" / "windows-cp313-build.lock")
    runtime_build_inputs = {
        "runtime_wheel_lock_sha256": runtime_source["builder"]["runtime_lock_sha256"],
        "build_wheel_lock_sha256": runtime_source["builder"]["build_lock_sha256"],
        "wheelhouse_tree_sha256": "sha256:" + "4" * 64,
        "application_wheel_sha256": "sha256:" + "5" * 64,
        "application_wheel_provenance": application_wheel_provenance(
            wheel="sha256:" + "5" * 64,
            build_lock=runtime_source["builder"]["build_lock_sha256"],
        ),
        "builder_toolchain_sha256": "sha256:" + "6" * 64,
        "runtime_wheel_count": len(runtime_lock["packages"]),
        "build_wheel_count": len(build_lock["packages"]),
    }
    runtime_build_evidence = {
        "schema_version": 1,
        "format": "JOBFLOW_RUNTIME_BUILD_EVIDENCE_V1",
        "evidence_kind": "SANITIZED_BUILD_OBSERVATION",
        "issued_at_utc": T,
        "expires_at_utc": "2026-08-13T00:00:00Z",
        "application_version": "0.6.0",
        "source_commit": "a" * 40,
        "platform": "windows-x64",
        "structural_status": "BUILT_UNATTESTED",
        "archive": {
            "name": "JobFlow-v0.6.0-windows-x64-complete.zip",
            "bytes": 123456,
            "sha256": "sha256:" + "1" * 64,
            "archive_prefix": "JobFlow-v0.6.0-windows-x64/",
        },
        "runtime_closure": {
            "manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
            "source_payload_sha256": "sha256:" + "1" * 64,
            "file_count": 412,
            "total_bytes": 9876543,
            "python_version": "3.13.15",
            "platform": "windows-x64",
        },
        "python_source": {
            "version": runtime_source["python"]["version"],
            "artifact_name": runtime_source["python"]["artifact_name"],
            "artifact_bytes": runtime_source["python"]["artifact_bytes"],
            "artifact_sha256": runtime_source["python"]["artifact_sha256"],
            "sigstore_bundle_name": runtime_source["python"]["artifact_name"] + ".sigstore",
            "sigstore_bundle_bytes": runtime_source["python"]["sigstore_bundle_bytes"],
            "sigstore_bundle_sha256": runtime_source["python"]["sigstore_bundle_sha256"],
        },
        "build_inputs": runtime_build_inputs,
        "build_inputs_sha256": sha256_bytes(canonical_json(runtime_build_inputs)),
        "deterministic_build": {
            "pass_a_archive_sha256": "sha256:" + "1" * 64,
            "pass_b_archive_sha256": "sha256:" + "1" * 64,
            "pass_a_tree_sha256": "sha256:" + "3" * 64,
            "pass_b_tree_sha256": "sha256:" + "3" * 64,
            "match": True,
        },
        "independent_verification": {
            "status": "PASS",
            "verifier_sha256": "sha256:" + "7" * 64,
            "archive_sha256": "sha256:" + "1" * 64,
            "closure_manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
        },
        "offline_smoke": {
            "status": "PASS",
            "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
            "archive_sha256": "sha256:" + "1" * 64,
            "closure_manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
            "external_actions": 0,
        },
        "closure_self_claims": {
            "sigstore_verified": False,
            "outer_signature_ready": False,
        },
        "external_actions": 0,
    }
    runtime_build_evidence_sha256 = sha256_bytes(canonical_json(runtime_build_evidence))
    provider_policy_sha256 = "sha256:" + "8" * 64
    signer_challenge_sha256 = signer_readiness_challenge_sha256(
        runtime_build_evidence_sha256=runtime_build_evidence_sha256,
        archive_sha256=runtime_build_evidence["archive"]["sha256"],
        source_commit=runtime_build_evidence["source_commit"],
        provider_policy_sha256=provider_policy_sha256,
        release_key_id=PRODUCTION_RELEASE_KEY_ID,
    )
    publisher_evidence = {
        "schema_version": 1,
        "format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
        "evidence_kind": "SANITIZED_PUBLISHER_OBSERVATION",
        "status": "READY_FOR_PROTECTED_SIGNING",
        "issued_at_utc": "2026-08-12T00:10:00Z",
        "expires_at_utc": "2026-08-12T04:10:00Z",
        "runtime_build_evidence_sha256": runtime_build_evidence_sha256,
        "release": {
            "version": "0.6.0",
            "source_commit": "a" * 40,
            "platform": "windows-x64",
            "archive_name": "JobFlow-v0.6.0-windows-x64-complete.zip",
            "archive_bytes": 123456,
            "archive_sha256": "sha256:" + "1" * 64,
            "archive_prefix": "JobFlow-v0.6.0-windows-x64/",
        },
        "runtime_closure": {
            "manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
            "source_payload_sha256": "sha256:" + "1" * 64,
            "file_count": 412,
            "total_bytes": 9876543,
            "structural_status": "BUILT_UNATTESTED",
        },
        "build_inputs_sha256": runtime_build_evidence["build_inputs_sha256"],
        "psf_sigstore": {
            "status": "VERIFIED",
            "python_artifact_sha256": runtime_source["python"]["artifact_sha256"],
            "sigstore_bundle_sha256": runtime_source["python"]["sigstore_bundle_sha256"],
            "trusted_root_sha256": "sha256:" + "9" * 64,
            "verifier_sha256": "sha256:" + "a" * 64,
            "verifier_version": "3.7.2",
            "certificate_identity": runtime_source["python"]["sigstore_certificate_identity"],
            "certificate_oidc_issuer": runtime_source["python"]["sigstore_certificate_oidc_issuer"],
            "signature_verified": True,
            "transparency_log_inclusion_verified": True,
            "offline_verification": True,
            "network_access": 0,
        },
        "deterministic_rebuild": {
            "verified": True,
            "pass_a_archive_sha256": "sha256:" + "1" * 64,
            "pass_b_archive_sha256": "sha256:" + "1" * 64,
            "pass_a_tree_sha256": "sha256:" + "3" * 64,
            "pass_b_tree_sha256": "sha256:" + "3" * 64,
        },
        "independent_verification": {
            "status": "PASS",
            "runtime_build_evidence_sha256": runtime_build_evidence_sha256,
            "verifier_sha256": "sha256:" + "7" * 64,
            "archive_sha256": "sha256:" + "1" * 64,
            "closure_manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
        },
        "offline_smoke": {
            "status": "PASS",
            "runtime_build_evidence_sha256": runtime_build_evidence_sha256,
            "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
            "external_actions": 0,
        },
        "outer_signing_readiness": {
            "status": "VERIFIED",
            "release_key_id": PRODUCTION_RELEASE_KEY_ID,
            "provider_policy_sha256": provider_policy_sha256,
            "challenge_format": "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1",
            "challenge_sha256": signer_challenge_sha256,
            "challenge_signature_sha256": "sha256:" + "b" * 64,
            "verified_with_pinned_trust": True,
            "secret_material_in_evidence": False,
        },
        "release_safety": {
            "closure_relabelled": False,
            "closure_bytes_modified": False,
            "secret_material_in_evidence": False,
            "external_actions": 0,
        },
    }
    publisher_evidence_sha256 = sha256_bytes(canonical_json(publisher_evidence))
    update_channel = load_json(PROJECT / "config" / "update-channel.json")
    protected_publisher_request = {
        "schema_version": 1,
        "format": "JOBFLOW_PROTECTED_PUBLISHER_REQUEST_V1",
        "status": "AWAITING_PROTECTED_PUBLISHER_EVIDENCE",
        "release": {
            "version": "0.6.0",
            "source_commit": "a" * 40,
            "platform": "windows-x64",
        },
        "archive": {
            **runtime_build_evidence["archive"],
        },
        "runtime_build_evidence": {
            "name": "JobFlow-runtime-build-evidence.json",
            "bytes": len(canonical_json(runtime_build_evidence)),
            "sha256": runtime_build_evidence_sha256,
            "issued_at_utc": runtime_build_evidence["issued_at_utc"],
            "expires_at_utc": runtime_build_evidence["expires_at_utc"],
        },
        "runtime_closure": {
            "manifest_sha256": runtime_build_evidence["runtime_closure"]["manifest_sha256"],
            "tree_sha256": runtime_build_evidence["runtime_closure"]["tree_sha256"],
            "source_payload_sha256": runtime_build_evidence["runtime_closure"]["source_payload_sha256"],
            "file_count": runtime_build_evidence["runtime_closure"]["file_count"],
            "total_bytes": runtime_build_evidence["runtime_closure"]["total_bytes"],
        },
        "build_inputs_sha256": runtime_build_evidence["build_inputs_sha256"],
        "python_source": {
            "artifact_name": runtime_build_evidence["python_source"]["artifact_name"],
            "artifact_bytes": runtime_build_evidence["python_source"]["artifact_bytes"],
            "artifact_sha256": runtime_build_evidence["python_source"]["artifact_sha256"],
            "sigstore_bundle_name": runtime_build_evidence["python_source"]["sigstore_bundle_name"],
            "sigstore_bundle_bytes": runtime_build_evidence["python_source"]["sigstore_bundle_bytes"],
            "sigstore_bundle_sha256": runtime_build_evidence["python_source"]["sigstore_bundle_sha256"],
        },
        "pinned_policy": {
            "windows_runtime_source_sha256": sha256_file(PROJECT / "config" / "windows-runtime-source.json"),
            "update_channel_sha256": sha256_file(PROJECT / "config" / "update-channel.json"),
            "release_key_id": update_channel["signature"]["key_id"],
            "sigstore_certificate_identity": runtime_source["python"]["sigstore_certificate_identity"],
            "sigstore_certificate_oidc_issuer": runtime_source["python"]["sigstore_certificate_oidc_issuer"],
        },
        "required_response": {
            "name": "JobFlow-publisher-evidence.json",
            "schema_name": "publisher-evidence-v1",
            "format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
            "status": "READY_FOR_PROTECTED_SIGNING",
            "maximum_bytes": 262144,
            "signer_challenge_format": "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1",
        },
        "safety": {
            "request_contains_local_paths": False,
            "request_contains_secret_material": False,
            "protected_key_required_by_request": False,
            "external_actions": 0,
        },
    }
    clean_windows_acceptance = {
        "schema_version": 1,
        "format": "JOBFLOW_CLEAN_WINDOWS_ACCEPTANCE_V1",
        "evidence_kind": "SANITIZED_CLEAN_WINDOWS_OBSERVATION",
        "status": "PASS",
        "issued_at_utc": "2026-08-12T00:20:00Z",
        "expires_at_utc": "2026-08-13T00:20:00Z",
        "publisher_evidence_sha256": publisher_evidence_sha256,
        "release": {
            "version": "0.6.0",
            "source_commit": "a" * 40,
            "platform": "windows-x64",
        },
        "signed_bundle": {
            "manifest_sha256": "sha256:" + "c" * 64,
            "signature_sha256": "sha256:" + "d" * 64,
            "archive_name": "JobFlow-v0.6.0-windows-x64-complete.zip",
            "archive_bytes": 123456,
            "archive_sha256": "sha256:" + "1" * 64,
            "release_key_id": PRODUCTION_RELEASE_KEY_ID,
            "signature_verified_with_pinned_trust": True,
        },
        "runtime_closure": {
            "manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
            "structural_status": "BUILT_UNATTESTED",
        },
        "environment": {
            "os_family": "Windows",
            "architecture": "AMD64",
            "account_profile": "FRESH_STANDARD_USER",
            "preexisting_jobflow": False,
        },
        "browser_companion": {
            "version": "0.9.1",
            "chrome_store_version": "0.9.1",
            "edge_store_version": "0.9.1",
            "chrome_install_passed": True,
            "edge_install_passed": True,
            "native_host_registration_passed": True,
            "chrome_pairing_passed": True,
            "edge_pairing_passed": True,
        },
        "checks": {
            "install_passed": True,
            "startup_passed": True,
            "health_passed": True,
            "update_passed": True,
            "rollback_passed": True,
            "uninstall_passed": True,
        },
        "safety": {
            "external_actions": 0,
            "real_job_site_visits": 0,
            "final_submit_attempts": 0,
            "secret_material_in_evidence": False,
        },
    }
    return {
        "runtime-build-evidence-v1": runtime_build_evidence,
        "publisher-evidence-v1": publisher_evidence,
        "protected-publisher-request-v1": protected_publisher_request,
        "clean-windows-acceptance-v1": clean_windows_acceptance,
        "live-acceptance-report": live_acceptance,
        "product-capability-report": product_capability_report(),
        "support-diagnostics": {
            "schema_version": 2,
            "status": "JOBFLOW_SUPPORT_DIAGNOSTICS_READY",
            "generated_at": T,
            "support_url": "https://valerianxxx.github.io/JobFlow/support.html",
            "build": {
                "product": "JobFlow",
                "version": "0.4.1",
                "ui_protocol": 35,
                "database_schema": 15,
                "companion_protocol": 2,
                "expected_companion_version": "0.9.2",
                "observed_companion_version": "0.9.2",
            },
            "runtime": {
                "onboarding_status": "IN_PROGRESS",
                "ai_status": "AI_CONNECTION_NOT_CONFIGURED",
                "ai_connection_kind": None,
                "guided_intake_status": "IDLE",
                "browser_assist_status": "UNAVAILABLE",
                "browser_assist_paired": False,
                "intake_control_status": "READY",
            },
            "counts": {
                "sources": 0,
                "active_claims": 0,
                "conflicts": 0,
                "pending_applications": 0,
                "deferred_jobs": 0,
                "execution_runs": 0,
            },
            "safety": {
                "final_submit": "USER_ONLY",
                "automatic_retry": False,
                "network_mode": "LOCAL_OFFLINE_PLUS_USER_PRESENT_BROWSER_ASSIST",
                "real_website_accesses": 0,
                "external_action_attempts": 0,
                "real_external_actions": 0,
                "knowledge_write_operations": 0,
                "private_values_read": 0,
                "private_values_emitted": 0,
            },
            "current_error_code": None,
            "incidents": {
                "status": "SUPPORT_INCIDENT_CAPTURE_DISABLED",
                "enabled": False,
                "record_count": 0,
                "recent": [],
                "automatic_transmission": False,
                "private_values_read": 0,
                "private_values_emitted": 0,
            },
        },
        "support-incident-state": {
            "schema_version": 1,
            "status": "SUPPORT_INCIDENT_CAPTURE_DISABLED",
            "enabled": False,
            "updated_at": T,
            "records": [],
            "automatic_transmission": False,
            "private_values_read": 0,
            "private_values_emitted": 0,
        },
        "user-present-intake-control": {
            "schema_version": 1,
            "status": "READY",
            "mode": "USER_PRESENT_MANUAL_WAKE_ONLY",
            "generation": 1,
            "configured": True,
            "new_intake_allowed": True,
            "manual_run_allowed": True,
            "paused": False,
            "pause_reason": None,
            "interval_minutes": 60,
            "authorized_until": FUTURE,
            "next_user_run_at": FUTURE,
            "last_user_run_at": None,
            "updated_at": T,
            "requires_explicit_invocation": True,
            "background_service_started": False,
            "system_tasks_registered": 0,
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
            "control_hash": H,
        },
        "authorized-discovery-control": {
            "schema_version": 1,
            "status": "READY",
            "mode": "AUTHORIZED_READ_ONLY_DISCOVERY",
            "configured": True,
            "enabled": True,
            "paused": False,
            "pause_reason": None,
            "generation": 1,
            "interval_minutes": 60,
            "authorized_until": FUTURE,
            "next_run_at": FUTURE,
            "last_run_at": None,
            "last_run_status": None,
            "consecutive_failures": 0,
            "max_new_per_run": 10,
            "inbox_limit": 250,
            "source_count": 1,
            "task_registration_state": "REGISTERED",
            "run_active": False,
            "read_only_network_authorized": True,
            "application_actions_authorized": False,
            "browser_actions_authorized": False,
            "material_upload_authorized": False,
            "final_submit": "USER_ONLY",
            "automatic_retry": False,
            "updated_at": T,
            "control_hash": H,
        },
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
        "final-submission-authorization": {
            "authorization_id": "FSA-ABCDEF123456", "application_id": APP,
            "application_context_hash": H, "execution_plan_hash": H,
            "review_packet_hash": H, "freshness_evidence_hash": H,
            "source_route_hash": H, "form_snapshot_hash": H, "uploads_hash": H,
            "action": "submit_application", "bound_hash": H,
            "issued_at": T, "expires_at": FUTURE, "nonce": "nonce-" + "b" * 48,
            "authorization_version": 1, "status": "AUTHORIZED", "consumed_at": None,
        },
        "application-execution-checkpoint": {
            "schema_version": 1, "checkpoint_id": "ECP-ABCDEF123456",
            "run_id": "RUN-ABCDEF123456", "application_id": APP, "sequence": 5,
            "phase": "AWAITING_FINAL_AUTHORIZATION", "status": "AWAITING_USER",
            "application_context_hash": H, "execution_plan_hash": H,
            "browser_plan_hash": H, "form_snapshot_hash": H,
            "freshness_evidence_hash": H, "evidence_hash": H, "created_at": T,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "external-action-session": {
            "session_id": "EAS-ABCDEF123456", "application_id": APP,
            "application_context_hash": H, "source_route_hash": H,
            "form_snapshot_hash": H, "uploads_hash": H, "site_policy_version": "1",
            "allowed_actions": ["inspect_application_form", "read_official_job"],
            "control_generation": 2, "mode": "ISOLATED_FAKE", "bound_hash": H,
            "issued_at": T, "expires_at": FUTURE, "nonce": "nonce-" + "c" * 48,
            "session_version": 1, "status": "AUTHORIZED", "revoked_at": None,
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
                "pdf_secure_ref": None, "pdf_sha256": None, "narrative_sha256": None,
                "narrative_character_count": None,
                "narrative_target_status": "NOT_REQUESTED", "narrative_target_count": 0,
                "narrative_control_ref": None, "narrative_max_characters": None,
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
        "application-execution-plan": execution_plan,
        "application-execution-bundle": {
            "schema_version": 1, "status": "LOCAL_EXECUTION_BUNDLE_READY",
            "application_id": APP, "provider": "greenhouse",
            "source_route_hash": H, "form_snapshot_hash": H,
            "browser_plan_hash": H, "execution_plan_hash": H,
            "form_snapshot": {}, "browser_plan": {}, "execution_plan": {},
            "public_values": [],
            "material_references": [{
                "purpose": "resume", "filename": "resume.pdf", "sha256": H,
                "secure_ref": "secure-ref:SYNTHETIC01",
            }],
            "bundle_nonce": "a" * 64, "bundle_hash": H,
            "created_at": T, "real_external_actions": 0,
        },
        "external-claim-set": external_claim_set,
        "application-readiness": application_readiness,
        "resume-tailoring-manifest": resume_tailoring_manifest,
        "ats-form-snapshot": {
            "schema_version": 1, "status": "FORM_SNAPSHOT_ANALYZED", "source_mode": "LOCAL_SNAPSHOT_ONLY",
            "provider": "company", "step_kind": "MY_INFORMATION", "canonical_url": "https://example.com/careers/a", "source_route_hash": route["route_hash"],
            "page_content_hash": H, "form_snapshot_hash": H, "field_count": 1, "ignored_hidden_control_count": 1,
            "classification_counts": {"private_fixed": 1},
            "fields": [{
                "control_ref": "CTL-ABCDEF123456", "control_type": "email", "required": True,
                "max_length": None, "max_length_status": "ABSENT",
                "classification": "private_fixed", "answer_key": "email", "logical_field_hash": H,
                "display_label": "Email address", "display_options": [],
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
            "schema_version": 3, "status": "OFFLINE_ATS_CAPABILITIES", "provider_count": 1,
            "providers": [{
                "provider": "lever", "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
                "evidence_scope": "DISCOVERY_AND_FORM_ANALYSIS_ONLY",
                "saved_snapshot_modes": ["single_html"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
                "dynamic_control_strategy": "opaque_control_ref",
                "verified_stages": ["OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS"],
                "unverified_stages": [
                    "PRIVATE_VALUE_FREE_PLAN", "REVIEW_PACKET", "APPROVED_DOM_PREFILL",
                    "APPROVED_FILE_ATTACHMENT", "EXPLICIT_NONFINAL_NAVIGATION", "MULTI_PAGE_RESUME",
                    "RESULT_OBSERVATION", "MODERN_COMPONENT_REBINDING"
                ],
                "evidence_refs": ["tests/test_ats_provider_contracts.py"],
                "evidence_bundle_hash": H,
                "known_limit_codes": ["LIVE_SITE_ACCEPTANCE_REQUIRED"],
                "guest_first": True,
                "account_creation_blocked": True, "upload_blocked": True, "submit_blocked": True,
                "live_site_verified": False, "browser_actions": 0, "network_actions": 0,
                "user_present_prefill": "SHARED_RUNTIME_ONLY_PROVIDER_ACCEPTANCE_REQUIRED",
                "approved_material_upload": "SHARED_RUNTIME_ONLY_PROVIDER_ACCEPTANCE_REQUIRED",
                "nonfinal_navigation": "SHARED_RUNTIME_ONLY_PROVIDER_ACCEPTANCE_REQUIRED",
                "final_submit": "USER_ONLY", "live_compatibility": "NOT_UNIVERSALLY_VERIFIED",
                "real_external_actions": 0, "transport_contract_hash": H,
                "live_transport_registered": False, "automatic_retry": False, "contract_hash": H,
            }],
            "browser_runtime_evidence": {
                "status": "SYNTHETIC_BROWSER_RUNTIME_PASS",
                "verified_stages": [
                    "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT",
                    "EXPLICIT_NONFINAL_NAVIGATION", "MODERN_COMPONENT_REBINDING"
                ],
                "evidence_refs": ["tests/browser_companion_e2e.cjs"],
                "evidence_bundle_hash": H, "live_site_verified": False,
                "final_submit": "USER_ONLY", "automatic_retry": False,
                "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
                "runtime_evidence_hash": H
            },
            "live_site_accessed": False, "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "ats-transport-envelope": {
            "schema_version": 1, "envelope_id": "ATE-ABCDEF123456", "provider": "greenhouse",
            "action": "submit_application", "application_id": APP, "run_id": "RUN-ABCDEF123456",
            "application_context_hash": H, "source_route_hash": H, "form_snapshot_hash": H,
            "execution_plan_hash": H, "request_payload_hash": H,
            "authorization_kind": "FINAL_SUBMISSION_AUTHORIZATION", "authorization_hash": H,
            "transport_contract_hash": H, "mode": "ISOLATED_FAKE",
            "contains_private_values": False, "contains_file_content": False, "created_at": T,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0, "envelope_hash": H,
        },
        "ephemeral-ats-payload-evidence": {
            "schema_version": 1, "status": "ISOLATED_EPHEMERAL_PAYLOAD_VALIDATED",
            "application_id": APP, "application_context_hash": H,
            "browser_plan_hash": H, "form_snapshot_hash": H,
            "field_count": 2, "file_count": 1,
            "field_binding_hash": H, "material_binding_hash": H,
            "application_answer_bundle_count": 0,
            "confirmed_stop_field_count": 0, "skipped_optional_field_count": 0,
            "synthetic_only": True, "production_activation": False,
            "temporary_files_removed": True, "private_values_emitted": 0,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "continuous-intake-plan": {
            "schema_version": 1, "status": "MANUAL_TICK_READY", "mode": "MANUAL_TICK_ONLY", "plan_hash": H,
            "job_count": 3, "pending_limit": 5, "awaiting_approval": 2, "reserved_slots": 1,
            "existing_deferred": 4, "slots_available": 2, "jobs_eligible_this_tick": 2,
            "jobs_expected_to_defer": 1, "requires_explicit_invocation": True,
            "background_service_started": False, "system_tasks_registered": 0,
            "browser_actions": 0, "network_actions": 0, "real_external_actions": 0,
        },
        "continuous-intake-descriptor": {
            "schema_version": 1, "status": "READY_FOR_MANUAL_CONTINUATION",
            "intake_key": H,
            "job": {
                "input": "workspace/jobs/synthetic.txt",
                "profile_ref": "secure-ref:SYNTHETIC01",
                "master_resume_ref": "secure-ref:SYNTHETIC02",
                "answer_bank_ref": "secure-ref:SYNTHETIC03",
                "external_claim_set_ref": "secure-ref:SYNTHETIC04",
                "tailoring_manifest_ref": "secure-ref:SYNTHETIC05",
                "route": "workspace/jobs/route.json", "form": "workspace/jobs/form.html",
                "research": "workspace/jobs/research.html", "source_type": "txt", "synthetic": False,
            },
            "created_at": T, "real_external_actions": 0, "descriptor_hash": H,
        },
        "continuous-intake-result": {
            "schema_version": 1, "status": "COMPLETED_WITH_LOCAL_ERRORS", "mode": "MANUAL_TICK_ONLY",
            "plan_hash": H, "job_count": 3, "prepared_count": 1, "deduplicated_count": 0,
            "deferred_count": 1, "failed_count": 1,
            "results": [
                {
                    "ordinal": 1, "source_type": "txt", "source_mode": "SAVED_LOCAL_EVIDENCE",
                    "status": "PREPARED", "application_id": APP, "error_code": None,
                    "next_safe_action": "REVIEW_APPLICATION_PACKET", "real_external_actions": 0,
                },
                {
                    "ordinal": 2, "source_type": "html", "source_mode": "SAVED_LOCAL_EVIDENCE",
                    "status": "DEFERRED_CAPACITY", "application_id": None, "error_code": None,
                    "next_safe_action": "REVIEW_PENDING_APPLICATIONS", "real_external_actions": 0,
                },
                {
                    "ordinal": 3, "source_type": "pdf", "source_mode": "SAVED_LOCAL_EVIDENCE",
                    "status": "LOCAL_ERROR", "application_id": None, "error_code": "LOCAL_EVIDENCE_INVALID",
                    "next_safe_action": "FIX_LOCAL_EVIDENCE_AND_RETRY_MANUAL_TICK", "real_external_actions": 0,
                },
            ],
            "queue": {"pending_limit": 5, "awaiting_approval": 3, "reserved_slots": 0, "deferred_intake": 1, "slots_available": 2},
            "requires_explicit_invocation": True, "background_service_started": False,
            "system_tasks_registered": 0, "browser_actions": 0, "network_actions": 0,
            "real_external_actions": 0,
        },
        "release-readiness": {
            "schema_version": 1, "status": "PUBLIC_RELEASE_BLOCKED", "version": "0.1.0",
            "public_release_ready": False, "runtime_closure_status": "UNATTESTED",
            "release_attestation_status": "MISSING", "clean_windows_evidence_status": "NOT_CHECKED",
            "release_attestation_failure_code": "RELEASE_ATTESTATION_MISSING",
            "head_commit": "a" * 40, "worktree_clean": True, "version_consistent": True,
            "local_verification_status": "PASS", "public_repository_status": "PASS",
            "source_candidate_status": "PASS", "independent_qa_fresh": False,
            "author_identity_status": "REVIEW_REQUIRED", "release_tag_status": "MISSING",
            "manual_release_gates": {
                "repository_metadata": "PENDING", "private_vulnerability_reporting": "PENDING",
                "sanitized_screenshots": "PENDING", "clean_windows_profile": "PENDING",
                "browser_companion_stores": "PENDING",
            },
            "blockers": ["RELEASE_ATTESTATION_MISSING", "RELEASE_RUNTIME_CLOSURE_UNATTESTED", "GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED", "INDEPENDENT_QA_STALE_OR_MISSING", "RELEASE_TAG_MISSING"],
            "upload_performed": False, "network_actions": 0, "real_external_actions": 0,
            "next_safe_action": "confirm a public GitHub noreply author identity policy",
        },
        "release-toolchain": {
            "schema_version": 1,
            "tools": {
                "node": {
                    "file_names": ["node.exe"],
                    "allowed_signers": [],
                    "allowed_unsigned_sha256": [H],
                },
                "git": {
                    "file_names": ["git.exe"],
                    "allowed_signers": [],
                    "allowed_unsigned_sha256": [H],
                },
                "python": {
                    "file_names": ["python.exe"],
                    "allowed_signers": [],
                    "allowed_unsigned_sha256": [H],
                },
            },
            "python_execution_runtime": {
                "source_policy": "config/windows-runtime-source.json",
                "python_tag": "python313",
                "maximum_files": 256,
                "maximum_entry_bytes": 134217728,
                "maximum_uncompressed_bytes": 268435456,
                "maximum_compression_ratio": 500,
                "required_entries": [
                    "python.exe",
                    "python3.dll",
                    "python313.dll",
                    "python313.zip",
                    "python313._pth",
                    "vcruntime140.dll",
                    "vcruntime140_1.dll",
                    "_hashlib.pyd",
                    "unicodedata.pyd",
                    "select.pyd",
                ],
                "active_pth_entries": ["python313.zip", "."],
            },
            "javascript_dependencies": {
                "packages": ["playwright"],
                "file_count": 1,
                "total_bytes": 1,
                "tree_sha256": H,
            },
        },
        "python-support-policy": load_json(PROJECT / "config" / "python-support-policy.json"),
        "runtime-closure": {
            "schema_version": 1,
            "status": "BUILT_UNATTESTED",
            "artifact_type": "complete-runtime",
            "platform": "windows-x64",
            "application_version": "0.6.0",
            "source_commit": "a" * 40,
            "python": {
                "version": "3.13.15",
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_sha256": H,
                "sigstore_identity": "https://www.python.org/",
                "sigstore_verified": False,
            },
            "build_inputs": {
                "wheel_lock_sha256": H,
                "wheelhouse_tree_sha256": H,
                "application_wheel_sha256": H,
                "application_wheel_provenance": application_wheel_provenance(
                    wheel=H, build_lock=H
                ),
                "builder_toolchain_sha256": H,
                "wheels": [],
            },
            "layout": {
                "python": "runtime/python.exe",
                "python_pth": "runtime/python313._pth",
                "application_root": "app",
                "module": "jobops.cli",
            },
            "file_count": len(runtime_closure_records()),
            "total_bytes": sum(int(item["size"]) for item in runtime_closure_records()),
            "tree_sha256": sha256_bytes(canonical_json(runtime_closure_records())),
            "files": runtime_closure_records(),
            "offline_smoke_tests": {
                "import_passed": True,
                "schema_passed": True,
                "external_actions": 0,
            },
            "protected_builder": {
                "evidence_sha256": H,
                "deterministic_rebuild_match": True,
                "outer_signature_ready": False,
            },
        },
        "update-manifest-v2": {
            "schema_version": 2,
            "product": "JobFlow",
            "channel": "stable",
            "release": {
                "version": "0.6.0",
                "source_commit": "a" * 40,
                "platform": "windows-x64",
            },
            "predecessor": {
                "minimum_version": "0.4.1",
                "maximum_version_exclusive": "0.6.0",
                "disallow_downgrade": True,
                "require_current_runtime_closure": True,
            },
            "asset": {
                "name": "JobFlow-v0.6.0-windows-x64-complete.zip",
                "bytes": 1,
                "sha256": H,
                "archive_prefix": "JobFlow-v0.6.0-windows-x64/",
            },
            "runtime_closure": {
                "manifest_sha256": H,
                "tree_sha256": H,
                "structural_status": "BUILT_UNATTESTED",
                "source_commit": "a" * 40,
                "source_payload_sha256": H,
                "file_count": 2,
                "total_bytes": 2,
                "python_version": "3.13.15",
                "platform": "windows-x64",
                "build_inputs": {
                    "python_artifact_sha256": H,
                    "wheel_lock_sha256": H,
                    "wheelhouse_tree_sha256": H,
                    "application_wheel_sha256": H,
                    "application_wheel_provenance": application_wheel_provenance(
                        wheel=H, build_lock=H
                    ),
                    "builder_toolchain_sha256": H,
                    "wheel_count": 0,
                },
            },
            "publisher_attestation": {
                "status": "ATTESTED",
                "format": "JOBFLOW_PUBLISHER_ATTESTATION_V2",
                "release_key_id": PRODUCTION_RELEASE_KEY_ID,
                "evidence_format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
                "runtime_build_evidence_sha256": runtime_build_evidence_sha256,
                "publisher_evidence_sha256": publisher_evidence_sha256,
                "evidence_expires_at_utc": publisher_evidence["expires_at_utc"],
                "signer_readiness_challenge_sha256": signer_challenge_sha256,
                "runtime_closure_manifest_sha256": H,
                "runtime_tree_sha256": H,
                "build_inputs_sha256": sha256_bytes(canonical_json({
                    "python_artifact_sha256": H,
                    "wheel_lock_sha256": H,
                    "wheelhouse_tree_sha256": H,
                    "application_wheel_sha256": H,
                    "application_wheel_provenance": application_wheel_provenance(
                        wheel=H, build_lock=H
                    ),
                    "builder_toolchain_sha256": H,
                    "wheel_count": 0,
                })),
                "source_commit": "a" * 40,
                "source_payload_sha256": H,
                "file_count": 2,
                "total_bytes": 2,
                "policy_sha256": sha256_bytes(canonical_json(update_policy)),
                "issued_at_utc": T,
            },
            "policy": update_policy,
            "issued_at_utc": T,
        },
        "installed-pointer-v2": {
            "schema_version": 2,
            "product": "JobFlow",
            "version_directory": "v0.6.0-aaaaaaaaaaaa",
            "version": "0.6.0",
            "source_commit": "a" * 40,
            "source_payload_sha256": H,
            "runtime_closure_manifest_sha256": H,
            "runtime_tree_sha256": H,
            "release_key_id": PRODUCTION_RELEASE_KEY_ID,
            "bootstrap_version": "0.6.0",
            "platform": "windows-x64",
        },
        "queue-reservation": {"reservation_id": "RSV-ABCDEF123456", "intake_key": H, "application_id": APP, "status": "CONSUMED", "pending_limit": 3, "pending_count": 1, "reserved_count": 1, "created_at": T, "updated_at": T},
        "recovery-event": {"recovery_id": "RCV-ABCDEF123456", "application_id": APP, "blocked_state": "SUBMISSION_UNKNOWN", "last_safe_state": "APPROVED", "validation_hash": H, "decision": "NO_AUTO_RETRY", "created_at": T},
        "receipt": {"receipt_id": "RCP-ABCDEF123456", "application_id": APP, "source": "fake-receipt", "confirmation_type": "confirmation_number", "confirmation_hash": H, "verified": True, "verified_at": T},
        "review-packet": {"schema_version": 1, "status": "AWAITING_APPROVAL", "packet_id": "RPK-ABCDEF123456", "application_id": APP, "job": {"job_id": JOB}, "jd_captured_at": T, "fit": {"overall_score": 80}, "hard_gaps": [], "resume_bullets": [], "master_resume_diff": {}, "form_questions": [], "sensitive_fields": [], "uploads": [{"filename": "resume.pdf", "sha256": H}], "material_plan": {"status": "READY_FOR_REVIEW"}, "execution_plan": execution_plan, "external_actions": ["upload_material"], "source_route": route, "queue": {"pending_limit": 3}, "ai_operator": {"schema_version": 1, "operator_task_id": None, "turns": [], "final_submit": "USER_ONLY", "automatic_retry": False, "private_values_exposed": 0, "real_external_actions": 0}, "content_hash": H},
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
            "continuous-intake-result": ("job_count", 4),
            "application-execution-checkpoint": ("sequence", 1),
            "external-action-session": ("expires_at", T),
            "ats-transport-envelope": ("authorization_kind", "SCOPED_ACTION_SESSION_USE"),
        }
        for name, (field, invalid) in conflicts.items():
            changed = copy.deepcopy(self.fixtures[name]); changed[field] = invalid
            with self.subTest(schema=name), self.assertRaises(JobOpsError) as caught:
                validate_named(name, changed, self.schema_dir)
            self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")


if __name__ == "__main__":
    unittest.main()
