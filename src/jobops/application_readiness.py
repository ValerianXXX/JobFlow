from __future__ import annotations

from typing import Any

from .util import iso_utc


def build_application_readiness(
    *,
    onboarding_status: str,
    ai_ready: bool,
    master_resume: dict[str, Any] | None,
    confirmed_claim_count: int,
    claim_review_hash: str | None,
    external_claim_status: dict[str, Any],
    queue: dict[str, Any],
) -> dict[str, Any]:
    master = master_resume or {}
    master_present = bool(master.get("secure_ref") and master.get("sha256"))
    editable = bool(master_present and master.get("editable_docx"))
    slots = sorted({str(value) for value in master.get("template_slots", []) if str(value)})
    external_current = bool(external_claim_status.get("current"))
    blockers: list[dict[str, str]] = []
    if onboarding_status != "ONBOARDING_COMPLETE":
        blockers.append({"code": "ONBOARDING_INCOMPLETE", "stage": "PROFILE", "user_action_required": "COMPLETE_ONBOARDING"})
    if not ai_ready:
        blockers.append({"code": "AI_NOT_READY", "stage": "AI", "user_action_required": "CONNECT_AND_VERIFY_AI"})
    if not master_present:
        blockers.append({"code": "MASTER_RESUME_MISSING", "stage": "MATERIALS", "user_action_required": "UPLOAD_RESUME"})
    elif not editable:
        blockers.append({"code": "EDITABLE_MASTER_DOCX_MISSING", "stage": "MATERIALS", "user_action_required": "UPLOAD_EDITABLE_DOCX"})
    if confirmed_claim_count < 1:
        blockers.append({"code": "CONFIRMED_CLAIMS_MISSING", "stage": "CLAIMS", "user_action_required": "CONFIRM_AT_LEAST_ONE_CLAIM"})
    elif not external_current:
        blockers.append({"code": "EXTERNAL_CLAIM_APPROVAL_REQUIRED", "stage": "CLAIMS", "user_action_required": "APPROVE_CONFIRMED_CLAIMS"})
    if editable and not slots:
        blockers.append({"code": "MASTER_TAILORING_MANIFEST_REQUIRED", "stage": "MATERIALS", "user_action_required": "BUILD_SAFE_TAILORING_MANIFEST"})

    if not blockers:
        status = "READY_FOR_OFFLINE_APPLICATION_PREPARATION"
    else:
        first = blockers[0]["code"]
        status = {
            "ONBOARDING_INCOMPLETE": "NEEDS_ONBOARDING",
            "AI_NOT_READY": "NEEDS_AI",
            "MASTER_RESUME_MISSING": "NEEDS_MASTER_RESUME",
            "EDITABLE_MASTER_DOCX_MISSING": "NEEDS_EDITABLE_MASTER_RESUME",
            "CONFIRMED_CLAIMS_MISSING": "NEEDS_CONFIRMED_CLAIMS",
            "EXTERNAL_CLAIM_APPROVAL_REQUIRED": "NEEDS_EXTERNAL_CLAIM_APPROVAL",
            "MASTER_TAILORING_MANIFEST_REQUIRED": "NEEDS_TEMPLATE_PREPARATION",
        }[first]
    return {
        "schema_version": 1,
        "status": status,
        "blockers": blockers,
        "onboarding_complete": onboarding_status == "ONBOARDING_COMPLETE",
        "ai_structured_ready": ai_ready,
        "master_resume": {
            "present": master_present,
            "editable_docx": editable,
            "template_fingerprint_present": bool(master.get("template_fingerprint")),
            "tailoring_mode": "EXPLICIT_TEMPLATE_SLOTS" if slots else ("MANIFEST_REQUIRED" if editable else "UNAVAILABLE"),
            "template_slot_count": len(slots),
        },
        "claims": {
            "confirmed_count": max(0, int(confirmed_claim_count)),
            "review_hash": claim_review_hash,
            "external_approval_current": external_current,
            "externally_approved_count": max(0, int(external_claim_status.get("claim_count", 0))),
        },
        "queue": {
            "pending_limit": int(queue.get("pending_limit", 0)),
            "awaiting_approval": int(queue.get("awaiting_approval", 0)),
            "slots_available": int(queue.get("slots_available", 0)),
            "continues_after_awaiting_approval": True,
        },
        "capabilities": {
            "saved_official_job_discovery": True,
            "offline_application_preparation": not blockers,
            "tailored_resume_generation": not blockers,
            "on_demand_cover_letter_generation": not blockers,
            "review_packet_generation": not blockers,
            "live_site_access": False,
            "real_prefill": False,
            "real_upload": False,
            "final_submission": False,
        },
        "generated_at": iso_utc(),
        "real_external_actions": 0,
    }
