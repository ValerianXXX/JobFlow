from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .errors import ApprovalError
from .util import canonical_json, iso_utc, parse_iso, sha256_bytes, stable_id


EXTERNAL_ACTIONS = {
    "create_recruiting_account",
    "upload_material",
    "submit_application",
    "send_email",
    "contact_recruiter",
    "withdraw_application",
    "accept_legal_terms",
    "electronic_signature",
}


@dataclass(frozen=True, order=True)
class UploadBinding:
    filename: str
    purpose: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"filename": self.filename, "purpose": self.purpose, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "UploadBinding":
        return cls(str(value["filename"]), str(value["purpose"]), str(value["sha256"]))


def _canonical_actions(values: Iterable[str]) -> tuple[str, ...]:
    actions = tuple(sorted(set(str(value).strip() for value in values if str(value).strip())))
    unknown = sorted(set(actions) - EXTERNAL_ACTIONS)
    if unknown:
        raise ApprovalError("EXTERNAL_ACTION_UNKNOWN", "Approval contains an unsupported external action.", actions=unknown)
    if not actions:
        raise ApprovalError("EXTERNAL_ACTIONS_REQUIRED", "Approval must bind at least one exact external action.")
    return actions


@dataclass(frozen=True)
class ApprovalContext:
    application_id: str
    job_id: str
    jd_snapshot_hash: str
    jd_freshness_hash: str
    source_route_hash: str
    canonical_url: str
    ats_tenant: str
    ats_board: str
    ats_job_identity: str
    profile_version: str
    claim_set_hash: str
    form_snapshot_hash: str
    answers_hash: str
    review_packet_hash: str
    uploads: tuple[UploadBinding, ...]
    external_actions: tuple[str, ...]
    site_policy_version: str
    unresolved_stops: tuple[str, ...] = ()
    mandatory_unknowns: tuple[str, ...] = ()

    def normalized(self) -> "ApprovalContext":
        uploads = tuple(sorted(self.uploads))
        if not uploads:
            raise ApprovalError("UPLOAD_BINDINGS_REQUIRED", "Approval must bind every pending upload, including the resume.")
        return ApprovalContext(
            **{
                **self.__dict__,
                "uploads": uploads,
                "external_actions": _canonical_actions(self.external_actions),
                "unresolved_stops": tuple(sorted(set(self.unresolved_stops))),
                "mandatory_unknowns": tuple(sorted(set(self.mandatory_unknowns))),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        value = self.normalized()
        return {
            **value.__dict__,
            "uploads": [item.as_dict() for item in value.uploads],
            "external_actions": list(value.external_actions),
            "unresolved_stops": list(value.unresolved_stops),
            "mandatory_unknowns": list(value.mandatory_unknowns),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApprovalContext":
        return cls(
            application_id=str(value["application_id"]),
            job_id=str(value["job_id"]),
            jd_snapshot_hash=str(value["jd_snapshot_hash"]),
            jd_freshness_hash=str(value["jd_freshness_hash"]),
            source_route_hash=str(value["source_route_hash"]),
            canonical_url=str(value["canonical_url"]),
            ats_tenant=str(value["ats_tenant"]),
            ats_board=str(value["ats_board"]),
            ats_job_identity=str(value["ats_job_identity"]),
            profile_version=str(value["profile_version"]),
            claim_set_hash=str(value["claim_set_hash"]),
            form_snapshot_hash=str(value["form_snapshot_hash"]),
            answers_hash=str(value["answers_hash"]),
            review_packet_hash=str(value["review_packet_hash"]),
            uploads=tuple(UploadBinding.from_dict(item) for item in value["uploads"]),
            external_actions=tuple(value["external_actions"]),
            site_policy_version=str(value["site_policy_version"]),
            unresolved_stops=tuple(value.get("unresolved_stops", [])),
            mandatory_unknowns=tuple(value.get("mandatory_unknowns", [])),
        ).normalized()

    @property
    def context_hash(self) -> str:
        return sha256_bytes(canonical_json(self.as_dict()))


@dataclass(frozen=True)
class ApprovalBinding:
    approval_id: str
    context: ApprovalContext
    context_hash: str
    issued_at: str
    expires_at: str
    nonce: str
    approval_version: int
    status: str
    consumed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            **self.context.as_dict(),
            "context_hash": self.context_hash,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "approval_version": self.approval_version,
            "status": self.status,
            "consumed_at": self.consumed_at,
        }


def issue_approval(
    *,
    context: ApprovalContext,
    user_confirmed: bool,
    ttl_minutes: int = 30,
    now: datetime | None = None,
    nonce: str | None = None,
    approval_version: int = 2,
) -> ApprovalBinding:
    if not user_confirmed:
        raise ApprovalError("EXPLICIT_CONFIRMATION_REQUIRED", "An approval cannot be issued without explicit user confirmation.")
    if not 1 <= ttl_minutes <= 1440:
        raise ApprovalError("APPROVAL_TTL_INVALID", "Approval TTL must be between 1 minute and 24 hours.")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized = context.normalized()
    one_time_nonce = nonce or ("nonce-" + secrets.token_hex(24))
    context_hash = normalized.context_hash
    approval_id = stable_id("APR", context_hash, iso_utc(current), one_time_nonce, str(approval_version))
    return ApprovalBinding(
        approval_id=approval_id,
        context=normalized,
        context_hash=context_hash,
        issued_at=iso_utc(current),
        expires_at=iso_utc(current + timedelta(minutes=ttl_minutes)),
        nonce=one_time_nonce,
        approval_version=approval_version,
        status="APPROVED",
    )


def validate_approval(
    approval: ApprovalBinding,
    *,
    context: ApprovalContext,
    required_actions: Iterable[str] = (),
    now: datetime | None = None,
) -> str:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if parse_iso(approval.expires_at) <= current:
        return "APPROVAL_EXPIRED"
    if approval.status == "CONSUMED" or approval.consumed_at:
        return "APPROVAL_REPLAYED"
    if approval.status != "APPROVED":
        return "APPROVAL_NOT_ACTIVE"
    actual = context.normalized()
    if approval.context_hash != actual.context_hash or approval.context.as_dict() != actual.as_dict():
        return "APPROVAL_INVALIDATED"
    required_values = tuple(required_actions)
    required = set(_canonical_actions(required_values)) if required_values else set()
    if not required.issubset(set(approval.context.external_actions)):
        return "APPROVAL_ACTION_NOT_BOUND"
    return "APPROVAL_VALID"


def submission_confirmation_status(*, confirmation_page: bool, confirmation_number: str | None, confirmation_email: bool) -> str:
    if confirmation_page and (confirmation_number or confirmation_email):
        return "CONFIRMED"
    return "SUBMISSION_UNKNOWN"
