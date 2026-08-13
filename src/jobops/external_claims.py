from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .errors import JobOpsError
from .security import validate_secure_reference
from .util import canonical_json, iso_utc, parse_iso, sha256_bytes, stable_id


ALLOWED_EXTERNAL_USES = ("resume", "cover_letter", "application_narrative")
MAX_EXTERNAL_CLAIMS = 1_000


def claim_review_hash(claims: Iterable[dict[str, Any]], master_resume_sha256: str) -> str:
    """Bind an approval prompt to exact reviewed wording without exposing that wording."""

    if not isinstance(master_resume_sha256, str) or not master_resume_sha256.startswith("sha256:"):
        raise JobOpsError("MASTER_RESUME_HASH_INVALID", "External Claim approval requires a hashed Master Resume.")
    material = []
    for item in claims:
        if item.get("decision") != "CONFIRMED" or item.get("deleted") is True:
            continue
        statement = str(item.get("statement") or "").strip()
        if not statement:
            raise JobOpsError("EXTERNAL_CLAIM_WORDING_MISSING", "A confirmed Claim has no exact wording.")
        material.append({
            "claim_id": str(item.get("claim_id") or ""),
            "category": str(item.get("category") or ""),
            "statement_sha256": sha256_bytes(statement.encode("utf-8")),
            "decision": "CONFIRMED",
        })
    material.sort(key=lambda value: (value["claim_id"], value["statement_sha256"]))
    return sha256_bytes(canonical_json({"master_resume_sha256": master_resume_sha256, "claims": material}))


def build_external_claim_set(
    *,
    onboarding_state_ref: str,
    profile_ref: str,
    master_resume: dict[str, Any],
    claims: list[dict[str, Any]],
    allowed_uses: Iterable[str],
    expected_review_hash: str,
    approved_at: str | None = None,
    validity_days: int = 365,
) -> dict[str, Any]:
    validate_secure_reference(onboarding_state_ref)
    validate_secure_reference(profile_ref)
    validate_secure_reference(str(master_resume.get("secure_ref", "")))
    master_hash = str(master_resume.get("sha256", ""))
    if not master_hash.startswith("sha256:"):
        raise JobOpsError("MASTER_RESUME_HASH_INVALID", "The Master Resume content hash is invalid.")
    uses = sorted({str(value) for value in allowed_uses})
    if not uses or any(value not in ALLOWED_EXTERNAL_USES for value in uses):
        raise JobOpsError("EXTERNAL_CLAIM_USES_INVALID", "Choose at least one supported external Claim use.")
    if not 1 <= len(claims) <= MAX_EXTERNAL_CLAIMS:
        raise JobOpsError("EXTERNAL_CLAIM_COUNT_INVALID", "At least one confirmed Claim is required for material generation.")
    review_hash = claim_review_hash(claims, master_hash)
    if expected_review_hash != review_hash:
        raise JobOpsError("EXTERNAL_CLAIM_REVIEW_STALE", "The reviewed Claim wording or Master Resume changed; review the current set again.")

    now = parse_iso(approved_at) if approved_at else datetime.now(timezone.utc)
    expires = now + timedelta(days=max(1, min(int(validity_days), 365)))
    approved_claims: list[dict[str, Any]] = []
    for item in claims:
        if item.get("decision") != "CONFIRMED" or item.get("deleted") is True:
            continue
        statement = str(item.get("statement") or "").strip()
        bindings = item.get("source_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise JobOpsError("EXTERNAL_CLAIM_SOURCE_MISSING", "Every externally approved Claim needs encrypted source evidence.")
        normalized_bindings = []
        for binding in bindings:
            if not isinstance(binding, dict):
                raise JobOpsError("EXTERNAL_CLAIM_SOURCE_INVALID", "An encrypted Claim source binding is invalid.")
            secure_ref = str(binding.get("secure_ref", ""))
            validate_secure_reference(secure_ref)
            content_hash = str(binding.get("content_sha256", ""))
            if not content_hash.startswith("sha256:"):
                raise JobOpsError("EXTERNAL_CLAIM_SOURCE_INVALID", "An encrypted Claim source hash is invalid.")
            kind = str(binding.get("kind", ""))
            if kind not in {"MASTER_RESUME", "UPLOADED_MATERIAL"}:
                raise JobOpsError("EXTERNAL_CLAIM_SOURCE_INVALID", "An encrypted Claim source kind is invalid.")
            normalized_bindings.append({"kind": kind, "secure_ref": secure_ref, "content_sha256": content_hash})
        normalized_bindings = sorted(
            {canonical_json(value).decode("utf-8"): value for value in normalized_bindings}.values(),
            key=lambda value: (value["kind"], value["content_sha256"], value["secure_ref"]),
        )
        boundary = item.get("responsibility_boundary") if isinstance(item.get("responsibility_boundary"), dict) else {}
        approved_claims.append({
            "claim_id": str(item.get("claim_id") or ""),
            "category": str(item.get("category") or ""),
            "claim_kind": str(item.get("claim_kind") or "summary"),
            "allowed_wording": [statement],
            "responsibility_boundary": {
                "candidate": str(boundary.get("candidate") or "APPLICANT_CONFIRMED_EXACT_WORDING"),
                "team": str(boundary.get("team") or "NO_INDEPENDENT_OWNERSHIP_INFERENCE"),
                "ai": str(boundary.get("ai") or "AI_ASSISTED_EXTRACTION_NOT_PERSONAL_EVIDENCE"),
            },
            "source_bindings": normalized_bindings,
            "allowed_uses": uses,
            "approved_for_external": True,
            "applicant_confirmed": True,
        })
    approved_claims.sort(key=lambda value: value["claim_id"])
    approved_at_value = iso_utc(now)
    content = {
        "schema_version": 1,
        "status": "EXTERNAL_CLAIMS_APPROVED",
        "onboarding_state_ref": onboarding_state_ref,
        "profile_ref": profile_ref,
        "master_resume": {
            "secure_ref": str(master_resume["secure_ref"]),
            "sha256": master_hash,
            "editable_docx": bool(master_resume.get("editable_docx")),
        },
        "review_hash": review_hash,
        "allowed_uses": uses,
        "claim_count": len(approved_claims),
        "claims": approved_claims,
        "approved_at": approved_at_value,
        "expires_at": iso_utc(expires),
        "applicant_confirmed": True,
        "real_external_actions": 0,
    }
    content["claim_set_id"] = stable_id("CLS", onboarding_state_ref, review_hash, approved_at_value)
    content["content_hash"] = sha256_bytes(canonical_json(content))
    return content


def validate_external_claim_set_integrity(value: dict[str, Any]) -> None:
    expected = value.get("content_hash")
    material = {key: item for key, item in value.items() if key != "content_hash"}
    if expected != sha256_bytes(canonical_json(material)):
        raise JobOpsError("EXTERNAL_CLAIM_SET_HASH_INVALID", "The encrypted external Claim set failed its integrity check.")
    if value.get("claim_count") != len(value.get("claims", [])):
        raise JobOpsError("EXTERNAL_CLAIM_SET_COUNT_INVALID", "The encrypted external Claim count is inconsistent.")
    if value.get("applicant_confirmed") is not True or any(
        item.get("approved_for_external") is not True or item.get("applicant_confirmed") is not True
        for item in value.get("claims", [])
    ):
        raise JobOpsError("EXTERNAL_CLAIM_APPROVAL_INVALID", "External Claim use must be explicitly approved by the applicant.")
    if parse_iso(str(value.get("expires_at"))) <= datetime.now(timezone.utc):
        raise JobOpsError("EXTERNAL_CLAIM_SET_EXPIRED", "The external Claim approval has expired.")
