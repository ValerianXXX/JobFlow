from __future__ import annotations

from typing import Any

from .ats_capabilities import offline_ats_capabilities
from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, load_json, project_root, sha256_bytes


_AUTOMATED = "AUTOMATED_REPRODUCIBLE"
_SYNTHETIC = "SYNTHETIC_ONLY"
_LIVE_REQUIRED = "LIVE_ACCEPTANCE_REQUIRED"
_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
_PERMANENT_BOUNDARY = "PERMANENT_USER_BOUNDARY"


def _capability_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "capability_id", "area", "availability", "evidence_status", "live_acceptance",
        "user_presence", "safety_boundary", "evidence_refs", "known_limit_codes",
    )}
    return sha256_bytes(canonical_json(material))


def _report_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "schema_version", "status", "capability_count", "capabilities",
        "universal_live_compatibility_claimed", "final_submit", "automatic_submission_retry",
        "unattended_operation", "scheduler_mode",
    )}
    return sha256_bytes(canonical_json(material))


def _item(
    capability_id: str,
    area: str,
    availability: str,
    evidence_status: str,
    live_acceptance: str,
    user_presence: str,
    safety_boundary: str,
    evidence_refs: list[str],
    known_limit_codes: list[str],
) -> dict[str, Any]:
    value = {
        "capability_id": capability_id,
        "area": area,
        "availability": availability,
        "evidence_status": evidence_status,
        "live_acceptance": live_acceptance,
        "user_presence": user_presence,
        "safety_boundary": safety_boundary,
        "evidence_refs": sorted(evidence_refs),
        "known_limit_codes": sorted(known_limit_codes),
    }
    value["capability_hash"] = _capability_hash(value)
    return value


def product_capability_report() -> dict[str, Any]:
    project = project_root()
    policy = load_json(project / "config" / "policy.json")
    ats = offline_ats_capabilities()
    provider_evidence = {
        item["provider"]: item["offline_evidence_level"]
        for item in ats["providers"]
    }
    capabilities = [
        _item(
            "windows_one_click_install", "distribution", "AVAILABLE", _AUTOMATED, "NOT_APPLICABLE",
            "USER_INITIATED", "LOCAL_USER_SCOPE_ONLY",
            ["tests/test_runtime_paths.py", "tests/test_windows_launchers.py", "tests/test_companion_binding.py"], [],
        ),
        _item(
            "desktop_manual_upgrade_rollback", "distribution", "AVAILABLE", _AUTOMATED, "NOT_APPLICABLE",
            "USER_INITIATED", "HEALTH_CHECKED_VERSION_SWITCH_WITH_PERSISTENT_DATA",
            ["tests/test_runtime_paths.py", "tests/test_windows_launchers.py"],
            ["NO_AUTOMATIC_UPDATE_DOWNLOAD"],
        ),
        _item(
            "secure_candidate_onboarding", "onboarding", "AVAILABLE", _AUTOMATED, "NOT_APPLICABLE",
            "USER_REVIEW_REQUIRED", "DPAPI_SECURE_REF_ONLY",
            ["tests/test_resume_onboarding.py", "tests/test_onboarding_center.py"], [],
        ),
        _item(
            "canonical_profile_reuse", "onboarding", "AVAILABLE", _AUTOMATED, "NOT_APPLICABLE",
            "ONE_CONFIRMATION_PER_APPLICATION", "PRIVATE_VALUES_NEVER_PUBLIC",
            ["tests/test_candidate_profile.py", "tests/test_real_profile_offline_application.py"],
            ["SITE_SPECIFIC_UNRECOGNIZED_FIELD_REQUIRES_REVIEW"],
        ),
        _item(
            "prepared_ai_operator", "ai", "CONDITIONAL", _AUTOMATED, "USER_ENVIRONMENT_REQUIRED",
            "USER_CONNECTS_PREPARED_RUNTIME", "ZERO_TOOL_STDIN_AND_SCOPED_JOBFLOW_TOOLS",
            ["tests/test_ai_connections.py", "tests/test_ai_operator.py"],
            ["SUPPORTED_LOCAL_OR_AGENT_RUNTIME_REQUIRED"],
        ),
        _item(
            "official_company_job_discovery", "discovery", "CONDITIONAL", _LIVE_REQUIRED,
            "REQUIRED_PER_COMPANY_OR_ROUTE", "USER_INITIATED",
            "OFFICIAL_COMPANY_CAREERS_ONLY",
            ["tests/test_live_official_search.py", "tests/test_official_discovery.py"],
            ["NO_BACKGROUND_WEB_CRAWLING", "CURRENT_PAGE_REVALIDATION_REQUIRED"],
        ),
        _item(
            "browser_companion_distribution", "distribution", "AVAILABLE", _AUTOMATED, "STORE_STATUS_EXTERNAL",
            "USER_INSTALL_GESTURE_REQUIRED", "SIGNED_IDENTITY_OR_DETERMINISTIC_DEVELOPMENT_IDENTITY",
            ["browser-companion/manifest.json", "tests/test_browser_companion_store.py"],
            ["STORE_PUBLICATION_STATUS_NOT_PROBED_BY_LOCAL_REPORT"],
        ),
        _item(
            "company_direct_browser_assist", "browser_assist", "CONDITIONAL", provider_evidence["company"],
            "REQUIRED_PER_SITE", "USER_PRESENT", "FINAL_SUBMIT_USER_ONLY",
            ["tests/browser_companion_e2e.cjs", "tests/test_ats_browser_safety.py"],
            ["LIVE_COMPATIBILITY_NOT_UNIVERSALLY_VERIFIED"],
        ),
        _item(
            "greenhouse_browser_assist", "browser_assist", "CONDITIONAL", provider_evidence["greenhouse"],
            "REQUIRED_PER_SITE", "USER_PRESENT", "FINAL_SUBMIT_USER_ONLY",
            ["tests/test_greenhouse_vertical.py", "tests/browser_companion_e2e.cjs"],
            ["LIVE_COMPATIBILITY_NOT_UNIVERSALLY_VERIFIED"],
        ),
        _item(
            "lever_browser_assist", "browser_assist", "CONDITIONAL", provider_evidence["lever"],
            "REQUIRED_PER_SITE", "USER_PRESENT", "FINAL_SUBMIT_USER_ONLY",
            ["tests/test_lever_vertical.py", "tests/browser_companion_e2e.cjs"],
            ["LIVE_COMPATIBILITY_NOT_UNIVERSALLY_VERIFIED"],
        ),
        _item(
            "workday_browser_assist", "browser_assist", "CONDITIONAL", provider_evidence["workday"],
            "REQUIRED_PER_SITE", "USER_PRESENT", "FINAL_SUBMIT_USER_ONLY",
            ["tests/test_workday_vertical.py", "tests/test_workday_sequence.py"],
            ["LIVE_COMPATIBILITY_NOT_UNIVERSALLY_VERIFIED"],
        ),
        _item(
            "bounded_review_queue", "queue", "AVAILABLE", _AUTOMATED, "NOT_APPLICABLE",
            "USER_APPROVAL_REQUIRED", "USER_SELECTED_PENDING_LIMIT",
            ["tests/test_continuous_intake.py", "tests/test_batch_c_queue_orchestrator_cli.py"], [],
        ),
        _item(
            "user_present_local_wake_planner", "scheduling", "AVAILABLE", _AUTOMATED,
            "NOT_APPLICABLE", "USER_ACTION_REQUIRED",
            "NO_BACKGROUND_SERVICE_OR_SYSTEM_TASK",
            ["tests/test_intake_control.py", "tests/test_onboarding_center.py"],
            ["NO_BACKGROUND_WEB_ACCESS", "NO_SYSTEM_TASK_REGISTERED"],
        ),
        _item(
            "submission_result_observation", "recovery", "CONDITIONAL", _AUTOMATED,
            "REQUIRED_PER_SITE", "USER_PRESENT", "UNKNOWN_RESULT_NEVER_AUTOMATICALLY_RETRIED",
            ["tests/test_application_execution.py", "tests/test_real_profile_offline_application.py"],
            ["UNRECOGNIZED_RECEIPT_REQUIRES_USER_CONFIRMATION"],
        ),
        _item(
            "authorized_continuous_scheduler", "scheduling", "NOT_AVAILABLE", _NOT_IMPLEMENTED,
            "NOT_STARTED", "NOT_APPLICABLE", "BACKGROUND_OPERATION_REMAINS_DISABLED",
            ["tests/test_batch_e_offline_adapters_scheduler.py"],
            ["USER_PRESENT_LOCAL_WAKE_ONLY", "NO_SYSTEM_TASK_REGISTERED"],
        ),
        _item(
            "desktop_self_update_rollback", "distribution", "AVAILABLE", _AUTOMATED,
            "NOT_APPLICABLE", "USER_INITIATED", "SIGNED_UPDATE_WITH_POST_SWITCH_HEALTH_ROLLBACK",
            ["tests/test_signed_updates.py", "tests/test_desktop_update.py", "tests/test_windows_launchers.py"],
            ["NO_BACKGROUND_UPDATE_CHECK", "FIXED_INSTALL_REQUIRED"],
        ),
        _item(
            "redacted_support_diagnostics", "support", "AVAILABLE", _AUTOMATED,
            "NOT_APPLICABLE", "USER_INITIATED", "LOCAL_VALUE_FREE_EXPORT_ONLY",
            ["tests/test_onboarding_center.py", "docs/support.html"],
            ["NO_AUTOMATIC_TRANSMISSION", "USER_REVIEWS_AND_ATTACHES_FILE"],
        ),
        _item(
            "opt_in_crash_reporter", "support", "AVAILABLE", _AUTOMATED,
            "NOT_APPLICABLE", "USER_OPT_IN_REQUIRED", "LOCAL_CODE_ONLY_CAPTURE_EXPLICIT_EXPORT",
            ["tests/test_support_incidents.py", "tests/test_onboarding_center.py", "docs/support.html"],
            ["NO_AUTOMATIC_TRANSMISSION", "NO_MESSAGE_STACK_URL_OR_PATH_CAPTURE"],
        ),
        _item(
            "final_application_submit", "submission", "USER_ONLY", _PERMANENT_BOUNDARY,
            "NOT_APPLICABLE", "USER_ACTION_REQUIRED", "NO_TOOL_SUBMIT_CAPABILITY",
            ["tests/browser_companion_e2e.cjs", "tests/test_workflow_gates.py"],
            ["AUTOMATIC_FINAL_SUBMIT_PROHIBITED"],
        ),
    ]
    capabilities.sort(key=lambda item: item["capability_id"])
    report = {
        "schema_version": 1,
        "status": "PRODUCT_CAPABILITY_REPORT",
        "capability_count": len(capabilities),
        "capabilities": capabilities,
        "universal_live_compatibility_claimed": False,
        "final_submit": "USER_ONLY",
        "automatic_submission_retry": False,
        "unattended_operation": False,
        "scheduler_mode": str(policy["scheduler_mode"]),
    }
    report["report_hash"] = _report_hash(report)
    validate_product_capability_integrity(report)
    return report


def validate_product_capability_integrity(value: dict[str, Any]) -> None:
    project = project_root()
    validate_named("product-capability-report", value, project / "schemas")
    capabilities = value["capabilities"]
    if value["capability_count"] != len(capabilities):
        raise JobOpsError("PRODUCT_CAPABILITY_COUNT_INVALID", "Capability count does not match the report contents.")
    if len({item["capability_id"] for item in capabilities}) != len(capabilities):
        raise JobOpsError("PRODUCT_CAPABILITY_DUPLICATE", "Each product capability may appear only once.")
    if any(item["capability_hash"] != _capability_hash(item) for item in capabilities):
        raise JobOpsError("PRODUCT_CAPABILITY_INTEGRITY_FAILED", "A product capability hash no longer matches its contents.")
    if value["report_hash"] != _report_hash(value):
        raise JobOpsError("PRODUCT_CAPABILITY_REPORT_INTEGRITY_FAILED", "The product capability report hash is invalid.")
    for item in capabilities:
        for relative in item["evidence_refs"]:
            candidate = (project / relative).resolve()
            try:
                candidate.relative_to(project.resolve())
            except ValueError as exc:
                raise JobOpsError("PRODUCT_CAPABILITY_EVIDENCE_PATH_INVALID", "Capability evidence must remain project-local.") from exc
            if not candidate.is_file():
                raise JobOpsError(
                    "PRODUCT_CAPABILITY_EVIDENCE_MISSING",
                    "A declared capability evidence file is missing.",
                    capability_id=item["capability_id"], evidence_ref=relative,
                )
    policy = load_json(project / "config" / "policy.json")
    if value["scheduler_mode"] != policy["scheduler_mode"]:
        raise JobOpsError("PRODUCT_CAPABILITY_POLICY_DRIFT", "Scheduler disclosure no longer matches policy.")
    if value["final_submit"] != "USER_ONLY" or policy["final_submit_implementation_present"] is not False:
        raise JobOpsError("PRODUCT_CAPABILITY_POLICY_DRIFT", "Final Submit must remain a user-only action.")
    if value["automatic_submission_retry"] is not False or policy["submission_unknown_auto_retry"] is not False:
        raise JobOpsError("PRODUCT_CAPABILITY_POLICY_DRIFT", "Unknown submission outcomes must never retry automatically.")
    if value["unattended_operation"] is not False or policy["unattended_submission_enabled"] is not False:
        raise JobOpsError("PRODUCT_CAPABILITY_POLICY_DRIFT", "Unattended submission must remain disabled.")
    if value["universal_live_compatibility_claimed"] is not False:
        raise JobOpsError("PRODUCT_CAPABILITY_OVERCLAIM", "The local evidence cannot claim universal live compatibility.")
