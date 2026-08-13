from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import JobOpsError
from .util import parse_iso


@dataclass(frozen=True)
class ClaimDecision:
    allowed: bool
    code: str
    reason: str


REQUIRED_CLAIM_FIELDS = {
    "claim_id", "raw_fact", "allowed_wording", "forbidden_wording",
    "responsibility_boundary", "evidence", "source_refs",
    "approved_for_external", "sensitivity", "last_verified_at", "expires_at",
}


def validate_claim_shape(claim: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_CLAIM_FIELDS - set(claim))
    if missing:
        raise JobOpsError("CLAIM_FIELDS_MISSING", "The claim is missing required fields.", missing=missing)
    boundary = claim.get("responsibility_boundary")
    if not isinstance(boundary, dict) or set(("candidate", "team", "ai")) - set(boundary):
        raise JobOpsError("CLAIM_BOUNDARY_MISSING", "Candidate, team and AI responsibility boundaries are all required.")
    if not isinstance(claim.get("allowed_wording"), list) or not claim["allowed_wording"]:
        raise JobOpsError("CLAIM_WORDING_MISSING", "At least one allowed wording is required.")
    if not isinstance(claim.get("evidence"), list) or not claim["evidence"]:
        raise JobOpsError("CLAIM_EVIDENCE_MISSING", "At least one evidence item is required.")
    if not isinstance(claim.get("source_refs"), list) or not claim["source_refs"]:
        raise JobOpsError("CLAIM_SOURCE_MISSING", "At least one knowledge source reference is required.")


def external_use_decision(claim: dict[str, Any], *, wording: str, now: datetime | None = None) -> ClaimDecision:
    validate_claim_shape(claim)
    if claim.get("sensitivity") == "application-private":
        return ClaimDecision(False, "PRIVATE_CLAIM_BLOCKED", "Application-private facts cannot be emitted from the ordinary claim registry.")
    if claim.get("approved_for_external") is not True:
        return ClaimDecision(False, "CLAIM_NOT_APPROVED", "The claim is not approved for external use.")
    if claim.get("lifecycle_status", "approved") != "approved":
        return ClaimDecision(False, "CLAIM_LIFECYCLE_BLOCKED", "The claim lifecycle is not approved.")
    current = now or datetime.now(timezone.utc)
    if parse_iso(str(claim["last_verified_at"])) > current.astimezone(timezone.utc):
        return ClaimDecision(False, "CLAIM_VERIFICATION_IN_FUTURE", "The claim verification time cannot be in the future.")
    if parse_iso(str(claim["expires_at"])) <= current.astimezone(timezone.utc):
        return ClaimDecision(False, "CLAIM_EXPIRED", "The claim verification has expired.")
    if wording not in claim["allowed_wording"]:
        return ClaimDecision(False, "WORDING_NOT_ALLOWLISTED", "The requested wording is not on the claim allowlist.")
    forbidden = [str(value).casefold() for value in claim.get("forbidden_wording", [])]
    if any(value and value in wording.casefold() for value in forbidden):
        return ClaimDecision(False, "FORBIDDEN_WORDING", "The requested wording contains a forbidden phrase.")
    for source in claim["source_refs"]:
        if source.get("source_id") != "personal_redacted":
            return ClaimDecision(False, "NON_PERSONAL_SOURCE", "AI, business and navigation knowledge cannot establish a personal experience claim.")
        normalized_path = str(source.get("relative_path", "")).replace("\\", "/").casefold()
        if normalized_path.startswith("个人ai应用实验室/01-使用画像与演变/"):
            return ClaimDecision(False, "QUESTION_ONLY_SOURCE", "Redacted usage profiles may only generate confirmation questions, not external application claims.")
        if not str(source.get("fingerprint", "")).startswith("sha256:"):
            return ClaimDecision(False, "SOURCE_FINGERPRINT_MISSING", "Every external claim source must carry a content fingerprint.")
    return ClaimDecision(True, "APPROVED", "The claim is approved, current, evidenced and uses allowlisted wording.")


def verify_claim_evidence(claim: dict[str, Any], gateway, *, now: datetime | None = None) -> list[dict[str, str]]:
    """Resolve every claim source through the read-only gateway and recheck its exact anchor."""
    from .errors import SecurityBoundaryError
    from .util import sha256_bytes, sha256_file

    validate_claim_shape(claim)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if parse_iso(str(claim["last_verified_at"])) > current:
        raise JobOpsError("CLAIM_VERIFICATION_IN_FUTURE", "Claim last_verified_at cannot be in the future.")
    if current >= parse_iso(str(claim["expires_at"])):
        raise JobOpsError("CLAIM_EXPIRED", "Claim evidence verification has expired.")
    if not all(str(claim["responsibility_boundary"].get(key, "")).strip() for key in ("candidate", "team", "ai")):
        raise JobOpsError("CLAIM_BOUNDARY_INCOMPLETE", "Candidate, team and AI responsibility boundaries must be explicit.")
    for evidence in claim.get("evidence", []):
        if isinstance(evidence.get("value"), (int, float)) and not str(evidence.get("scope", "")).strip():
            raise JobOpsError("CLAIM_NUMERIC_SCOPE_MISSING", "Numeric evidence requires an explicit scope.")
    verified: list[dict[str, str]] = []
    for source in claim["source_refs"]:
        if source.get("source_id") != "personal_redacted":
            raise JobOpsError("NON_PERSONAL_SOURCE", "Only personal_redacted may establish a personal claim.")
        relative = str(source.get("relative_path", ""))
        definition = gateway.definitions["personal_redacted"]
        question_only = [str(value).replace("\\", "/").casefold() for value in definition.get("question_only_prefixes", [])]
        normalized_relative = relative.replace("\\", "/").casefold()
        if any(normalized_relative == prefix or normalized_relative.startswith(prefix + "/") for prefix in question_only):
            raise JobOpsError("QUESTION_ONLY_SOURCE", "Question-only usage profiles cannot support external claims.")
        try:
            path = gateway.safe_path("personal_redacted", relative)
        except (SecurityBoundaryError, OSError) as exc:
            raise JobOpsError("EVIDENCE_PATH_INVALID", "Claim evidence path is missing, excluded or outside the allowlist.") from exc
        if not path.is_file():
            raise JobOpsError("EVIDENCE_PATH_INVALID", "Claim evidence must resolve to a real file.")
        current_hash = sha256_file(path)
        if current_hash != source.get("fingerprint"):
            raise JobOpsError("EVIDENCE_FILE_CHANGED", "Claim evidence file hash differs from the approved source hash.")
        text = gateway.read_text("personal_redacted", relative)
        heading = str(source.get("heading", "")).strip()
        excerpt = str(source.get("excerpt", "")).strip()
        if not heading or not excerpt:
            raise JobOpsError("EVIDENCE_LOCATOR_MISSING", "Claim evidence needs an exact heading and excerpt locator.")
        headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
        if heading not in headings or excerpt not in text:
            raise JobOpsError("EVIDENCE_ANCHOR_MISSING", "The approved heading or excerpt no longer exists in the source.")
        excerpt_hash = sha256_bytes(excerpt.encode("utf-8"))
        if excerpt_hash != source.get("excerpt_fingerprint"):
            raise JobOpsError("EVIDENCE_EXCERPT_CHANGED", "Claim excerpt fingerprint does not match its locator text.")
        verified.append({"source_id": "personal_redacted", "relative_path": relative, "file_sha256": current_hash, "excerpt_sha256": excerpt_hash})
    return verified
