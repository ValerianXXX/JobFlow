from __future__ import annotations

from typing import Any, Iterable

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, project_root, sha256_bytes


ALLOWED_PROVIDERS = {"company", "greenhouse", "lever", "workday"}
HASH_FIELDS = ("route_hash", "form_snapshot_hash", "browser_plan_hash")


def _hash(value: object, *, code: str) -> str:
    material = str(value or "")
    if len(material) != 71 or not material.startswith("sha256:"):
        raise JobOpsError(code, "An application execution binding is missing a valid SHA-256 value.")
    try:
        int(material[7:], 16)
    except ValueError as exc:
        raise JobOpsError(code, "An application execution binding is missing a valid SHA-256 value.") from exc
    return material


def _field_summary(fields: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "fillable": 0,
        "sensitive_review": 0,
        "work_authorization": 0,
        "compensation": 0,
        "legal_or_signature": 0,
        "voluntary_disclosure": 0,
        "account": 0,
        "upload": 0,
        "navigation": 0,
        "final_submit": 0,
        "unknown": 0,
    }
    for field in fields:
        classification = str(field.get("classification", ""))
        action = str(field.get("action", ""))
        if action.startswith("PREFILL") or action == "PROPOSE_PREFILL":
            summary["fillable"] += 1
        elif classification == "sensitive_review":
            summary["sensitive_review"] += 1
        elif classification == "work_authorization_stop":
            summary["work_authorization"] += 1
        elif classification == "compensation_stop":
            summary["compensation"] += 1
        elif classification in {"legal_declaration_stop", "signature_stop"}:
            summary["legal_or_signature"] += 1
        elif classification == "voluntary_disclosure_stop":
            summary["voluntary_disclosure"] += 1
        elif classification == "account_creation_stop":
            summary["account"] += 1
        elif classification == "file_upload_stop":
            summary["upload"] += 1
        elif classification == "navigation_control_stop":
            summary["navigation"] += 1
        elif classification == "final_submit_stop":
            summary["final_submit"] += 1
        elif classification == "unknown_stop":
            summary["unknown"] += 1
    return summary


def _upload_purposes(material_plan: dict[str, Any]) -> list[str]:
    purposes = ["resume"]
    cover = material_plan.get("cover_letter") or {}
    if cover.get("generation_status") == "GENERATED_ON_DEMAND":
        purposes.append("cover_letter")
    portfolio = material_plan.get("portfolio_file") or {}
    if portfolio.get("binding_status") == "BOUND_SECURE_FILE":
        purposes.append("portfolio")
    return purposes


def _step(
    sequence: int,
    phase: str,
    state: str,
    gate: str,
    *,
    item_count: int = 0,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "phase": phase,
        "state": state,
        "gate": gate,
        "item_count": item_count,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }


def build_application_execution_plan(
    *,
    application_id: str,
    source_route: dict[str, Any],
    form_snapshot_hash: str,
    browser_plan_hash: str,
    form_fields: Iterable[dict[str, Any]],
    material_plan: dict[str, Any],
    pending_limit: int,
    form_blockers: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the exact future-action runbook without executing any transport.

    The plan deliberately separates packet preparation from live freshness, prefill,
    upload, protected questions, and final submission.  It contains counts and hashes
    only; private values, selectors, filenames, URLs, and secure references stay in
    their existing encrypted/bound records.
    """

    if not isinstance(application_id, str) or not application_id.startswith("APP-"):
        raise JobOpsError("EXECUTION_APPLICATION_INVALID", "The execution plan needs a bounded application identifier.")
    provider = str(source_route.get("provider", ""))
    if provider not in ALLOWED_PROVIDERS:
        raise JobOpsError("EXECUTION_PROVIDER_UNSUPPORTED", "The execution plan provider is unsupported.", provider=provider)
    route_hash = _hash(source_route.get("route_hash"), code="EXECUTION_ROUTE_HASH_INVALID")
    form_hash = _hash(form_snapshot_hash, code="EXECUTION_FORM_HASH_INVALID")
    browser_hash = _hash(browser_plan_hash, code="EXECUTION_BROWSER_PLAN_HASH_INVALID")
    if not isinstance(pending_limit, int) or not 1 <= pending_limit <= 1000:
        raise JobOpsError("EXECUTION_PENDING_LIMIT_INVALID", "The execution plan must bind the active pending-review limit.")
    if material_plan.get("all_uploads_and_submission_blocked") is not True:
        raise JobOpsError("EXECUTION_STOP_GATES_MISSING", "Material upload and submission gates must remain closed while planning.")
    if int(material_plan.get("real_external_actions", -1)) != 0:
        raise JobOpsError("EXECUTION_EXTERNAL_ACTION_DETECTED", "A planning-only material record reported an external action.")

    fields = list(form_fields)
    summary = _field_summary(fields)
    uploads = _upload_purposes(material_plan)
    guest_mode = str(source_route.get("guest_mode", "UNKNOWN"))
    account_action = str(source_route.get("account_action", "NEEDS_USER_INPUT"))
    material_ready = material_plan.get("status") == "READY_FOR_REVIEW"

    blockers: list[str] = []
    if not material_ready:
        blockers.append("REQUIRED_MATERIAL_MISSING")
    if summary["unknown"]:
        blockers.append("UNKNOWN_FORM_FIELD")
    if guest_mode == "GUEST_UNAVAILABLE" or account_action == "NEEDS_ACCOUNT_APPROVAL":
        blockers.append("NEEDS_ACCOUNT_APPROVAL")
    elif guest_mode != "GUEST_SELECTED" or account_action != "NONE":
        blockers.append("GUEST_FLOW_UNCONFIRMED")
    blocker_mapping = {
        "CAPTCHA_STOP": "CAPTCHA_REQUIRED",
        "MFA_STOP": "MFA_REQUIRED",
        "LOGIN_STOP": "LOGIN_REQUIRED",
        "ACCOUNT_CREATION_STOP": "NEEDS_ACCOUNT_APPROVAL",
        "FORM_ACTION_HOST_STOP": "UNSAFE_FORM_ACTION",
        "CROSS_ORIGIN_IFRAME_STOP": "CROSS_ORIGIN_IFRAME",
    }
    for blocker in sorted(set(str(item) for item in form_blockers)):
        mapped = blocker_mapping.get(blocker)
        if mapped and mapped not in blockers:
            blockers.append(mapped)

    status = (
        "NEEDS_ACCOUNT_APPROVAL"
        if "NEEDS_ACCOUNT_APPROVAL" in blockers or "LOGIN_REQUIRED" in blockers
        else "NEEDS_USER_INPUT"
        if blockers
        else "READY_FOR_REVIEW"
    )
    protected_count = sum(
        summary[key]
        for key in (
            "sensitive_review", "work_authorization", "compensation",
            "legal_or_signature", "voluntary_disclosure", "unknown",
        )
    )
    guest_state = "PLANNED" if guest_mode == "GUEST_SELECTED" and account_action == "NONE" else "BLOCKED"
    steps = [
        _step(1, "LIVE_FRESHNESS_RECHECK", "NOT_EXECUTED", "SEPARATE_LIVE_READ_AUTHORIZATION", item_count=1),
        _step(
            2,
            "GUEST_APPLICATION_ENTRY",
            guest_state,
            "NONE_AFTER_FRESHNESS" if guest_state == "PLANNED" else "USER_ACCOUNT_DECISION",
            item_count=1,
        ),
        _step(3, "SAFE_FIELD_PREFILL", "PROPOSED_ONLY", "REVIEW_PACKET_APPROVAL", item_count=summary["fillable"]),
        _step(4, "MATERIAL_UPLOAD", "BLOCKED", "SEPARATE_UPLOAD_AUTHORIZATION", item_count=len(uploads)),
        _step(
            5,
            "PROTECTED_AND_UNKNOWN_FIELDS",
            "BLOCKED" if protected_count else "NOT_REQUIRED",
            "PER_APPLICATION_CONFIRMATION" if protected_count else "NONE",
            item_count=protected_count,
        ),
        _step(6, "FINAL_SUBMISSION", "BLOCKED", "FRESH_EXPLICIT_SUBMISSION_APPROVAL", item_count=1),
    ]

    material_hash = sha256_bytes(canonical_json(material_plan))
    plan_material = {
        "application_id": application_id,
        "provider": provider,
        "route_hash": route_hash,
        "form_snapshot_hash": form_hash,
        "browser_plan_hash": browser_hash,
        "material_plan_hash": material_hash,
        "field_summary": summary,
        "upload_purposes": uploads,
        "blockers": blockers,
        "pending_limit": pending_limit,
        "steps": steps,
    }
    plan = {
        "schema_version": 1,
        "status": status,
        "mode": "PREPARE_AND_QUEUE",
        "application_id": application_id,
        "provider": provider,
        "route_hash": route_hash,
        "form_snapshot_hash": form_hash,
        "browser_plan_hash": browser_hash,
        "material_plan_hash": material_hash,
        "plan_hash": sha256_bytes(canonical_json(plan_material)),
        "field_summary": summary,
        "upload_purposes": uploads,
        "blockers": blockers,
        "steps": steps,
        "queue_behavior": {
            "pending_limit": pending_limit,
            "continue_other_jobs": True,
            "pause_new_intake_at_limit": True,
            "current_job_waits_for_user": True,
        },
        "live_transport_registered": False,
        "stop_before_final_submission": True,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_named("application-execution-plan", plan, project_root() / "schemas")
    return plan


def validate_application_execution_plan_integrity(value: dict[str, Any]) -> None:
    expected_phases = [
        "LIVE_FRESHNESS_RECHECK", "GUEST_APPLICATION_ENTRY", "SAFE_FIELD_PREFILL",
        "MATERIAL_UPLOAD", "PROTECTED_AND_UNKNOWN_FIELDS", "FINAL_SUBMISSION",
    ]
    steps = value.get("steps", [])
    if [item.get("sequence") for item in steps] != list(range(1, 7)):
        raise JobOpsError("EXECUTION_PLAN_INTEGRITY_FAILED", "Execution steps are not complete and ordered.")
    if [item.get("phase") for item in steps] != expected_phases:
        raise JobOpsError("EXECUTION_PLAN_INTEGRITY_FAILED", "Execution phases do not match the fixed safe workflow.")
    material = {
        "application_id": value.get("application_id"),
        "provider": value.get("provider"),
        "route_hash": value.get("route_hash"),
        "form_snapshot_hash": value.get("form_snapshot_hash"),
        "browser_plan_hash": value.get("browser_plan_hash"),
        "material_plan_hash": value.get("material_plan_hash"),
        "field_summary": value.get("field_summary"),
        "upload_purposes": value.get("upload_purposes"),
        "blockers": value.get("blockers"),
        "pending_limit": (value.get("queue_behavior") or {}).get("pending_limit"),
        "steps": steps,
    }
    if value.get("plan_hash") != sha256_bytes(canonical_json(material)):
        raise JobOpsError("EXECUTION_PLAN_INTEGRITY_FAILED", "Execution plan content no longer matches its hash.")
    if any(
        item.get("browser_actions") != 0
        or item.get("network_actions") != 0
        or item.get("real_external_actions") != 0
        for item in steps
    ):
        raise JobOpsError("EXECUTION_PLAN_INTEGRITY_FAILED", "A planning-only execution step contains an external action.")
