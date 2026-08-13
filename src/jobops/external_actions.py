from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from .approvals import ApprovalBinding, ApprovalContext, validate_approval
from .db import JobOpsDB
from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


@dataclass(frozen=True)
class ExternalActionPolicy:
    phase5_authorized: bool
    external_actions_enabled: bool
    site_policy_allowed: bool
    isolated_test_mode: bool
    adapter_kind: str

    @classmethod
    def production_disabled(cls) -> "ExternalActionPolicy":
        return cls(False, False, False, False, "disabled")

    @classmethod
    def isolated_fake(cls) -> "ExternalActionPolicy":
        return cls(True, True, True, True, "fake")


class ExternalActionGateway:
    """The only application-layer route into protected external-action states."""

    def __init__(self, database: JobOpsDB, policy: ExternalActionPolicy) -> None:
        self.database = database
        self.policy = policy

    def _attempt(self, application_id: str, action: str, result_code: str, context_hash: str | None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO external_action_attempts(
                attempt_id,application_id,action,adapter_kind,result_code,context_hash,real_side_effect,created_at)
                VALUES(?,?,?,?,?,?,0,?)""",
                (
                    "XAT-" + secrets.token_hex(12).upper(), application_id, action,
                    self.policy.adapter_kind, result_code, context_hash, iso_utc(),
                ),
            )

    def _raise_attempt(self, context: ApprovalContext, code: str, message: str) -> None:
        self._attempt(context.application_id, "submit_application", code, context.context_hash)
        raise JobOpsError(code, message)

    def persist_approval(self, approval: ApprovalBinding, context: ApprovalContext) -> dict[str, object]:
        normalized = context.normalized()
        result = validate_approval(approval, context=normalized)
        if result != "APPROVAL_VALID":
            raise JobOpsError(result, "Approval does not match the current application context.")
        validate_named("approval", approval.as_dict(), project_root() / "schemas")
        resume = next((item for item in normalized.uploads if item.purpose == "resume"), normalized.uploads[0])
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            application = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (normalized.application_id,)
            ).fetchone()
            binding = connection.execute(
                "SELECT context_hash,context_json FROM application_bindings WHERE application_id=?", (normalized.application_id,)
            ).fetchone()
            if application is None or binding is None:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "Application or persisted binding context is missing.")
            if application["status"] != "AWAITING_APPROVAL":
                raise JobOpsError("APPLICATION_NOT_AWAITING_APPROVAL", "Only an awaiting application can be approved.", status=application["status"])
            if binding["context_hash"] != normalized.context_hash or json.loads(binding["context_json"]) != normalized.as_dict():
                raise JobOpsError("APPROVAL_INVALIDATED", "Persisted application content changed before approval.")
            connection.execute(
                """INSERT INTO approvals(
                approval_id,application_id,job_id,site,resume_hash,answers_hash,bound_at,expires_at,status,external_actions_json,
                context_hash,context_json,jd_snapshot_hash,jd_freshness_hash,source_route_hash,canonical_url,
                ats_tenant,ats_board,ats_job_identity,profile_version,claim_set_hash,form_snapshot_hash,
                review_packet_hash,uploads_json,site_policy_version,nonce,approval_version,issued_at,consumed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval.approval_id, normalized.application_id, normalized.job_id, normalized.canonical_url,
                    resume.sha256, normalized.answers_hash, approval.issued_at, approval.expires_at, "APPROVED",
                    json.dumps(list(normalized.external_actions)), approval.context_hash,
                    json.dumps(normalized.as_dict(), ensure_ascii=False, sort_keys=True), normalized.jd_snapshot_hash,
                    normalized.jd_freshness_hash, normalized.source_route_hash, normalized.canonical_url,
                    normalized.ats_tenant, normalized.ats_board, normalized.ats_job_identity, normalized.profile_version,
                    normalized.claim_set_hash, normalized.form_snapshot_hash, normalized.review_packet_hash,
                    json.dumps([item.as_dict() for item in normalized.uploads], ensure_ascii=False),
                    normalized.site_policy_version, approval.nonce, approval.approval_version, approval.issued_at, None,
                ),
            )
            now = iso_utc()
            connection.execute(
                "UPDATE applications SET status='APPROVED',last_safe_state='APPROVED',updated_at=? WHERE application_id=?",
                (now, normalized.application_id),
            )
            connection.execute(
                "UPDATE review_packets SET status='APPROVED' WHERE application_id=? AND status='AWAITING_APPROVAL'",
                (normalized.application_id,),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (normalized.application_id, "APPROVAL_PERSISTED", "AWAITING_APPROVAL", "APPROVED", json.dumps({"approval_id": approval.approval_id}), now),
            )
        return {"status": "APPROVED", "approval_id": approval.approval_id, "context_hash": approval.context_hash}

    @staticmethod
    def _approval_from_row(row) -> ApprovalBinding:
        context = ApprovalContext.from_dict(json.loads(row["context_json"]))
        return ApprovalBinding(
            approval_id=str(row["approval_id"]), context=context, context_hash=str(row["context_hash"]),
            issued_at=str(row["issued_at"] or row["bound_at"]), expires_at=str(row["expires_at"]),
            nonce=str(row["nonce"]), approval_version=int(row["approval_version"]),
            status=str(row["status"]), consumed_at=row["consumed_at"],
        )

    def begin_submission(self, context: ApprovalContext) -> dict[str, object]:
        normalized = context.normalized()
        if not self.policy.phase5_authorized:
            self._raise_attempt(normalized, "PHASE_NOT_AUTHORIZED", "Phase 5 authorization is absent; submission cannot begin.")
        if not self.policy.external_actions_enabled:
            self._raise_attempt(normalized, "EXTERNAL_ACTIONS_DISABLED", "External actions are disabled by policy.")
        if not self.policy.site_policy_allowed:
            self._raise_attempt(normalized, "SITE_POLICY_BLOCKED", "The current site policy does not allow this action.")
        if not self.policy.isolated_test_mode or self.policy.adapter_kind not in {"fake", "mock", "dry-run"}:
            self._raise_attempt(normalized, "REAL_TRANSPORT_FORBIDDEN", "Only an isolated fake transport may enter synthetic submission.")
        if normalized.unresolved_stops or normalized.mandatory_unknowns:
            self._raise_attempt(normalized, "BLOCKING_FIELDS_UNRESOLVED", "STOP fields or mandatory UNKNOWN values remain unresolved.")

        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                application = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (normalized.application_id,)
                ).fetchone()
                binding = connection.execute(
                    "SELECT context_hash,context_json FROM application_bindings WHERE application_id=?", (normalized.application_id,)
                ).fetchone()
                approval_row = connection.execute(
                    "SELECT * FROM approvals WHERE application_id=? ORDER BY issued_at DESC LIMIT 1", (normalized.application_id,)
                ).fetchone()
                if application is None or binding is None:
                    raise JobOpsError("APPLICATION_BINDING_MISSING", "Application binding is missing.")
                if approval_row is None:
                    raise JobOpsError("APPROVAL_REQUIRED", "A current persisted approval is required.")
                if approval_row["status"] == "CONSUMED" or application["status"] == "SUBMITTING":
                    raise JobOpsError("APPROVAL_REPLAYED", "The one-time approval has already been consumed.")
                if application["status"] != "APPROVED":
                    raise JobOpsError("APPLICATION_NOT_APPROVED", "Application is not in the approved state.", status=application["status"])
                if binding["context_hash"] != normalized.context_hash or json.loads(binding["context_json"]) != normalized.as_dict():
                    raise JobOpsError("APPROVAL_INVALIDATED", "Current application binding differs from the approved content.")
                approval = self._approval_from_row(approval_row)
                decision = validate_approval(approval, context=normalized, required_actions=("submit_application",))
                if decision != "APPROVAL_VALID":
                    raise JobOpsError(decision, "Approval is not valid for submit_application.")
                now = iso_utc()
                updated = connection.execute(
                    "UPDATE approvals SET status='CONSUMED',consumed_at=? WHERE approval_id=? AND status='APPROVED' AND consumed_at IS NULL",
                    (now, approval.approval_id),
                ).rowcount
                if updated != 1:
                    raise JobOpsError("APPROVAL_REPLAYED", "Approval consumption lost a concurrent race.")
                changed = connection.execute(
                    "UPDATE applications SET status='SUBMITTING',last_safe_state='APPROVED',updated_at=? WHERE application_id=? AND status='APPROVED'",
                    (now, normalized.application_id),
                ).rowcount
                if changed != 1:
                    raise JobOpsError("APPLICATION_STATE_RACE", "Application state changed during approval consumption.")
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (normalized.application_id, "APPROVAL_CONSUMED", "APPROVED", "SUBMITTING", json.dumps({"approval_id": approval.approval_id}), now),
                )
                connection.execute(
                    """INSERT INTO external_action_attempts(
                    attempt_id,application_id,action,adapter_kind,result_code,context_hash,real_side_effect,created_at)
                    VALUES(?,?,?,?,?,?,0,?)""",
                    ("XAT-" + secrets.token_hex(12).upper(), normalized.application_id, "submit_application", self.policy.adapter_kind, "FAKE_SUBMISSION_STARTED", normalized.context_hash, now),
                )
            return {"status": "SUBMITTING", "adapter": self.policy.adapter_kind, "real_side_effect": False}
        except JobOpsError as exc:
            self._attempt(normalized.application_id, "submit_application", exc.code, normalized.context_hash)
            raise

    def mark_fake_submitted(self, application_id: str, *, fake_evidence: dict[str, object]) -> dict[str, object]:
        if not self.policy.isolated_test_mode or self.policy.adapter_kind not in {"fake", "mock", "dry-run"}:
            raise JobOpsError("REAL_TRANSPORT_FORBIDDEN", "Only isolated fake submission evidence is accepted.")
        evidence_hash = sha256_bytes(canonical_json(fake_evidence))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM applications WHERE application_id=?", (application_id,)).fetchone()
            if row is None or row["status"] != "SUBMITTING":
                raise JobOpsError("APPLICATION_NOT_SUBMITTING", "Fake submitted state requires a synthetic submission in progress.")
            now = iso_utc()
            connection.execute("UPDATE applications SET status='SUBMITTED',last_safe_state='APPROVED',updated_at=? WHERE application_id=?", (now, application_id))
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "FAKE_TRANSPORT_COMPLETED", "SUBMITTING", "SUBMITTED", json.dumps({"evidence_hash": evidence_hash}), now),
            )
        return {"status": "SUBMITTED", "fake_evidence_hash": evidence_hash, "real_side_effect": False}

    def confirm_with_receipt(self, application_id: str, receipt: dict[str, object]) -> dict[str, object]:
        if not self.policy.isolated_test_mode:
            raise JobOpsError("REAL_RECEIPT_ADAPTER_DISABLED", "Only isolated fake receipt verification is implemented.")
        validate_named("receipt", receipt, project_root() / "schemas")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM applications WHERE application_id=?", (application_id,)).fetchone()
            if row is None or row["status"] != "SUBMITTED":
                raise JobOpsError("APPLICATION_NOT_SUBMITTED", "Confirmation requires a submitted application and verified receipt.")
            now = iso_utc()
            connection.execute(
                "INSERT INTO receipts(receipt_id,application_id,confirmation_type,confirmation_hash,verified_at) VALUES(?,?,?,?,?)",
                (receipt["receipt_id"], application_id, receipt["confirmation_type"], receipt["confirmation_hash"], receipt["verified_at"]),
            )
            connection.execute("UPDATE applications SET status='CONFIRMED',last_safe_state='CONFIRMED',updated_at=? WHERE application_id=?", (now, application_id))
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "RECEIPT_VERIFIED", "SUBMITTED", "CONFIRMED", json.dumps({"receipt_id": receipt["receipt_id"]}), now),
            )
        return {"status": "CONFIRMED", "receipt_id": receipt["receipt_id"], "real_side_effect": False}
