from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .application_execution import validate_application_execution_plan_integrity
from .approvals import ApprovalContext
from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id


def _sha256(value: object, code: str) -> str:
    material = str(value or "")
    if len(material) != 71 or not material.startswith("sha256:"):
        raise JobOpsError(code, "A final-submission binding requires a valid SHA-256 value.")
    try:
        int(material[7:], 16)
    except ValueError as exc:
        raise JobOpsError(code, "A final-submission binding requires a valid SHA-256 value.") from exc
    return material


def _bound_material(
    context: ApprovalContext,
    execution_plan: dict[str, Any],
    freshness_evidence_hash: str,
) -> dict[str, Any]:
    normalized = context.normalized()
    validate_named("application-execution-plan", execution_plan, project_root() / "schemas")
    validate_application_execution_plan_integrity(execution_plan)
    if execution_plan.get("application_id") != normalized.application_id:
        raise JobOpsError("FINAL_SUBMISSION_APPLICATION_MISMATCH", "The execution plan belongs to another application.")
    if execution_plan.get("status") != "READY_FOR_REVIEW" or execution_plan.get("blockers"):
        raise JobOpsError("FINAL_SUBMISSION_PLAN_BLOCKED", "The execution plan still contains a user, account, or material blocker.")
    if normalized.unresolved_stops or normalized.mandatory_unknowns:
        raise JobOpsError("FINAL_SUBMISSION_FIELDS_UNRESOLVED", "STOP fields or mandatory UNKNOWN values remain unresolved.")
    if execution_plan.get("route_hash") != normalized.source_route_hash:
        raise JobOpsError("FINAL_SUBMISSION_ROUTE_CHANGED", "The current route differs from the reviewed route.")
    if execution_plan.get("form_snapshot_hash") != normalized.form_snapshot_hash:
        raise JobOpsError("FINAL_SUBMISSION_FORM_CHANGED", "The current form differs from the reviewed form.")
    final_step = next((item for item in execution_plan.get("steps", []) if item.get("phase") == "FINAL_SUBMISSION"), None)
    if not isinstance(final_step, dict) or final_step.get("gate") != "FRESH_EXPLICIT_SUBMISSION_APPROVAL":
        raise JobOpsError("FINAL_SUBMISSION_GATE_MISSING", "The execution plan does not stop for a fresh final confirmation.")
    uploads_hash = sha256_bytes(canonical_json([item.as_dict() for item in normalized.uploads]))
    return {
        "application_id": normalized.application_id,
        "application_context_hash": normalized.context_hash,
        "execution_plan_hash": _sha256(execution_plan.get("plan_hash"), "FINAL_SUBMISSION_PLAN_HASH_INVALID"),
        "review_packet_hash": _sha256(normalized.review_packet_hash, "FINAL_SUBMISSION_PACKET_HASH_INVALID"),
        "freshness_evidence_hash": _sha256(freshness_evidence_hash, "FINAL_SUBMISSION_FRESHNESS_HASH_INVALID"),
        "source_route_hash": _sha256(normalized.source_route_hash, "FINAL_SUBMISSION_ROUTE_HASH_INVALID"),
        "form_snapshot_hash": _sha256(normalized.form_snapshot_hash, "FINAL_SUBMISSION_FORM_HASH_INVALID"),
        "uploads_hash": uploads_hash,
        "action": "submit_application",
    }


@dataclass(frozen=True)
class FinalSubmissionAuthorization:
    authorization_id: str
    application_id: str
    application_context_hash: str
    execution_plan_hash: str
    review_packet_hash: str
    freshness_evidence_hash: str
    source_route_hash: str
    form_snapshot_hash: str
    uploads_hash: str
    action: str
    bound_hash: str
    issued_at: str
    expires_at: str
    nonce: str
    authorization_version: int
    status: str
    consumed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FinalSubmissionAuthorization":
        return cls(**{name: value.get(name) for name in cls.__dataclass_fields__})


def issue_final_submission_authorization(
    *,
    context: ApprovalContext,
    execution_plan: dict[str, Any],
    freshness_evidence_hash: str,
    user_confirmed: bool,
    ttl_minutes: int = 10,
    now: datetime | None = None,
    nonce: str | None = None,
) -> FinalSubmissionAuthorization:
    if not user_confirmed:
        raise JobOpsError("FINAL_SUBMISSION_CONFIRMATION_REQUIRED", "Final submission requires a fresh explicit user confirmation.")
    if not 1 <= ttl_minutes <= 30:
        raise JobOpsError("FINAL_SUBMISSION_TTL_INVALID", "Final submission authorization must expire within 1–30 minutes.")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    material = _bound_material(context, execution_plan, freshness_evidence_hash)
    bound_hash = sha256_bytes(canonical_json(material))
    one_time_nonce = nonce or ("nonce-" + secrets.token_hex(24))
    authorization = FinalSubmissionAuthorization(
        authorization_id=stable_id("FSA", bound_hash, iso_utc(current), one_time_nonce),
        **material,
        bound_hash=bound_hash,
        issued_at=iso_utc(current),
        expires_at=iso_utc(current + timedelta(minutes=ttl_minutes)),
        nonce=one_time_nonce,
        authorization_version=1,
        status="AUTHORIZED",
        consumed_at=None,
    )
    validate_named("final-submission-authorization", authorization.as_dict(), project_root() / "schemas")
    return authorization


def validate_final_submission_authorization(
    authorization: FinalSubmissionAuthorization,
    *,
    context: ApprovalContext,
    execution_plan: dict[str, Any],
    freshness_evidence_hash: str,
    now: datetime | None = None,
) -> str:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if parse_iso(authorization.expires_at) <= current:
        return "FINAL_SUBMISSION_AUTHORIZATION_EXPIRED"
    if authorization.status == "CONSUMED" or authorization.consumed_at:
        return "FINAL_SUBMISSION_AUTHORIZATION_REPLAYED"
    if authorization.status != "AUTHORIZED":
        return "FINAL_SUBMISSION_AUTHORIZATION_NOT_ACTIVE"
    try:
        material = _bound_material(context, execution_plan, freshness_evidence_hash)
    except JobOpsError:
        return "FINAL_SUBMISSION_AUTHORIZATION_INVALIDATED"
    expected_hash = sha256_bytes(canonical_json(material))
    for key, value in material.items():
        if getattr(authorization, key) != value:
            return "FINAL_SUBMISSION_AUTHORIZATION_INVALIDATED"
    if authorization.bound_hash != expected_hash:
        return "FINAL_SUBMISSION_AUTHORIZATION_INVALIDATED"
    return "FINAL_SUBMISSION_AUTHORIZATION_VALID"
