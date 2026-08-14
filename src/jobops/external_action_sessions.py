from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .approvals import ApprovalContext, validate_approval
from .db import JobOpsDB
from .errors import JobOpsError
from .external_actions import ExternalActionGateway, ExternalActionPolicy
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id


SESSION_ACTIONS = frozenset({
    "read_official_job",
    "inspect_application_form",
    "prefill_application_form",
    "upload_materials",
})
FINAL_OR_SEPARATE_ACTIONS = frozenset({
    "submit_application",
    "create_recruiting_account",
    "send_email",
    "contact_recruiter",
    "register_system_schedule",
})


def _uploads_hash(context: ApprovalContext) -> str:
    return sha256_bytes(canonical_json([item.as_dict() for item in context.normalized().uploads]))


def _actions(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(str(item).strip() for item in values if str(item).strip())))
    separate = sorted(set(normalized) & FINAL_OR_SEPARATE_ACTIONS)
    if separate:
        raise JobOpsError(
            "SEPARATE_ACTION_AUTHORIZATION_REQUIRED",
            "Final submission, account, messaging and scheduling actions require their own authorization flow.",
            actions=separate,
        )
    unknown = sorted(set(normalized) - SESSION_ACTIONS)
    if unknown:
        raise JobOpsError("EXTERNAL_SESSION_ACTION_UNSUPPORTED", "The action session contains an unsupported scope.", actions=unknown)
    if not normalized:
        raise JobOpsError("EXTERNAL_SESSION_ACTION_REQUIRED", "Choose at least one exact action for the session.")
    return normalized


@dataclass(frozen=True)
class ExternalActionSessionPolicy:
    mode: str
    activation_authorized: bool
    isolated_test_mode: bool

    @classmethod
    def production_disabled(cls) -> "ExternalActionSessionPolicy":
        return cls("PRODUCTION_DISABLED", False, False)

    @classmethod
    def isolated_fake(cls) -> "ExternalActionSessionPolicy":
        return cls("ISOLATED_FAKE", True, True)


@dataclass(frozen=True)
class ExternalActionSession:
    session_id: str
    application_id: str
    application_context_hash: str
    source_route_hash: str
    form_snapshot_hash: str
    uploads_hash: str
    site_policy_version: str
    allowed_actions: tuple[str, ...]
    control_generation: int
    mode: str
    bound_hash: str
    issued_at: str
    expires_at: str
    nonce: str
    session_version: int
    status: str
    revoked_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "allowed_actions": list(self.allowed_actions)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalActionSession":
        return cls(
            session_id=str(value["session_id"]), application_id=str(value["application_id"]),
            application_context_hash=str(value["application_context_hash"]),
            source_route_hash=str(value["source_route_hash"]),
            form_snapshot_hash=str(value["form_snapshot_hash"]), uploads_hash=str(value["uploads_hash"]),
            site_policy_version=str(value["site_policy_version"]),
            allowed_actions=tuple(value["allowed_actions"]), control_generation=int(value["control_generation"]),
            mode=str(value["mode"]), bound_hash=str(value["bound_hash"]), issued_at=str(value["issued_at"]),
            expires_at=str(value["expires_at"]), nonce=str(value["nonce"]),
            session_version=int(value["session_version"]), status=str(value["status"]),
            revoked_at=value.get("revoked_at"),
        )


class ExternalActionSessionManager:
    """Scoped authorization ledger; this build can activate it only in isolated tests."""

    def __init__(self, database: JobOpsDB, policy: ExternalActionSessionPolicy) -> None:
        self.database = database
        self.policy = policy
        self.schemas = project_root() / "schemas"

    def control_state(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT enabled,generation,mode,updated_at FROM external_action_control WHERE singleton_id=1"
            ).fetchone()
        if row is None:
            raise JobOpsError("EXTERNAL_ACTION_CONTROL_MISSING", "The external-action kill switch is unavailable.")
        return {
            "enabled": bool(row["enabled"]), "generation": int(row["generation"]),
            "mode": str(row["mode"]), "updated_at": str(row["updated_at"]),
            "real_external_actions": 0,
        }

    def enable(self, *, user_confirmed: bool) -> dict[str, Any]:
        if not user_confirmed:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Enabling an action session requires explicit confirmation.")
        if not self.policy.activation_authorized or not self.policy.isolated_test_mode or self.policy.mode != "ISOLATED_FAKE":
            raise JobOpsError(
                "PHASE_NOT_AUTHORIZED",
                "External action sessions cannot be enabled in the production-disabled build.",
            )
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation FROM external_action_control WHERE singleton_id=1"
            ).fetchone()
            generation = int(row["generation"]) + 1
            connection.execute(
                "UPDATE external_action_control SET enabled=1,generation=?,mode=?,updated_at=? WHERE singleton_id=1",
                (generation, self.policy.mode, now),
            )
            connection.execute(
                "UPDATE external_action_sessions SET status='INVALIDATED' WHERE status='AUTHORIZED'"
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(NULL,?,?,?,?,?)",
                (
                    "EXTERNAL_ACTION_CONTROL_ENABLED", "DISABLED", "ISOLATED_FAKE",
                    json.dumps({"generation": generation, "mode": self.policy.mode}), now,
                ),
            )
        return {"status": "ISOLATED_ACTION_CONTROL_ENABLED", "generation": generation, "real_external_actions": 0}

    def disable(self, *, reason: str = "USER_KILL_SWITCH") -> dict[str, Any]:
        safe_reason = str(reason).strip().upper()
        if not safe_reason or len(safe_reason) > 100 or not safe_reason.replace("_", "").isalnum():
            raise JobOpsError("KILL_SWITCH_REASON_INVALID", "The kill-switch reason must be a short safe code.")
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT enabled,generation FROM external_action_control WHERE singleton_id=1"
            ).fetchone()
            generation = int(row["generation"]) + 1
            connection.execute(
                "UPDATE external_action_control SET enabled=0,generation=?,mode='PRODUCTION_DISABLED',updated_at=? WHERE singleton_id=1",
                (generation, now),
            )
            invalidated = connection.execute(
                "UPDATE external_action_sessions SET status='INVALIDATED' WHERE status='AUTHORIZED'"
            ).rowcount
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(NULL,?,?,?,?,?)",
                (
                    "EXTERNAL_ACTION_KILL_SWITCH", "ENABLED" if row["enabled"] else "DISABLED", "DISABLED",
                    json.dumps({"generation": generation, "reason": safe_reason, "invalidated_sessions": invalidated}), now,
                ),
            )
        return {
            "status": "EXTERNAL_ACTIONS_DISABLED", "generation": generation,
            "invalidated_sessions": invalidated, "real_external_actions": 0,
        }

    def issue(
        self,
        *,
        context: ApprovalContext,
        allowed_actions: Iterable[str],
        user_confirmed: bool,
        ttl_minutes: int = 30,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> ExternalActionSession:
        if not user_confirmed:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "An action session requires explicit user confirmation.")
        if not 1 <= ttl_minutes <= 120:
            raise JobOpsError("EXTERNAL_SESSION_TTL_INVALID", "An action session must expire within 1–120 minutes.")
        control = self.control_state()
        if not control["enabled"] or control["mode"] != self.policy.mode:
            raise JobOpsError("EXTERNAL_ACTION_KILL_SWITCH_ACTIVE", "The external-action kill switch is active.")
        if not self.policy.activation_authorized or not self.policy.isolated_test_mode:
            raise JobOpsError("PHASE_NOT_AUTHORIZED", "This build cannot issue a production external-action session.")
        normalized = context.normalized()
        actions = _actions(allowed_actions)
        if "upload_materials" in actions and "upload_material" not in normalized.external_actions:
            raise JobOpsError("UPLOAD_ACTION_NOT_REVIEWED", "Material upload was not included in the approved review packet.")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        material = {
            "application_id": normalized.application_id,
            "application_context_hash": normalized.context_hash,
            "source_route_hash": normalized.source_route_hash,
            "form_snapshot_hash": normalized.form_snapshot_hash,
            "uploads_hash": _uploads_hash(normalized),
            "site_policy_version": normalized.site_policy_version,
            "allowed_actions": list(actions),
            "control_generation": int(control["generation"]),
            "mode": self.policy.mode,
        }
        bound_hash = sha256_bytes(canonical_json(material))
        one_time_nonce = nonce or ("nonce-" + secrets.token_hex(24))
        session = ExternalActionSession(
            session_id=stable_id("EAS", bound_hash, iso_utc(current), one_time_nonce),
            application_id=normalized.application_id,
            application_context_hash=normalized.context_hash,
            source_route_hash=normalized.source_route_hash,
            form_snapshot_hash=normalized.form_snapshot_hash,
            uploads_hash=_uploads_hash(normalized),
            site_policy_version=normalized.site_policy_version,
            allowed_actions=actions,
            control_generation=int(control["generation"]),
            mode=self.policy.mode,
            bound_hash=bound_hash,
            issued_at=iso_utc(current), expires_at=iso_utc(current + timedelta(minutes=ttl_minutes)),
            nonce=one_time_nonce, session_version=1, status="AUTHORIZED", revoked_at=None,
        )
        validate_named("external-action-session", session.as_dict(), self.schemas)
        return session

    def _validate(
        self,
        session: ExternalActionSession,
        *,
        context: ApprovalContext,
        action: str,
        now: datetime | None = None,
    ) -> str:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if parse_iso(session.expires_at) <= current:
            return "EXTERNAL_ACTION_SESSION_EXPIRED"
        if session.status != "AUTHORIZED" or session.revoked_at:
            return "EXTERNAL_ACTION_SESSION_NOT_ACTIVE"
        control = self.control_state()
        if not control["enabled"]:
            return "EXTERNAL_ACTION_KILL_SWITCH_ACTIVE"
        if session.control_generation != control["generation"] or session.mode != control["mode"]:
            return "EXTERNAL_ACTION_SESSION_INVALIDATED"
        if action not in session.allowed_actions:
            return "EXTERNAL_ACTION_NOT_AUTHORIZED"
        normalized = context.normalized()
        material = {
            "application_id": normalized.application_id,
            "application_context_hash": normalized.context_hash,
            "source_route_hash": normalized.source_route_hash,
            "form_snapshot_hash": normalized.form_snapshot_hash,
            "uploads_hash": _uploads_hash(normalized),
            "site_policy_version": normalized.site_policy_version,
            "allowed_actions": list(session.allowed_actions),
            "control_generation": session.control_generation,
            "mode": session.mode,
        }
        if session.bound_hash != sha256_bytes(canonical_json(material)):
            return "EXTERNAL_ACTION_SESSION_INVALIDATED"
        for key, value in material.items():
            if getattr(session, key) != (tuple(value) if key == "allowed_actions" else value):
                return "EXTERNAL_ACTION_SESSION_INVALIDATED"
        return "EXTERNAL_ACTION_SESSION_VALID"

    def persist(self, session: ExternalActionSession, *, context: ApprovalContext) -> dict[str, Any]:
        normalized = context.normalized()
        probe_action = session.allowed_actions[0] if session.allowed_actions else ""
        decision = self._validate(session, context=normalized, action=probe_action)
        if decision != "EXTERNAL_ACTION_SESSION_VALID":
            raise JobOpsError(decision, "The scoped action session is not valid for the current application.")
        validate_named("external-action-session", session.as_dict(), self.schemas)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            application = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (normalized.application_id,),
            ).fetchone()
            binding = connection.execute(
                "SELECT context_hash,context_json FROM application_bindings WHERE application_id=?",
                (normalized.application_id,),
            ).fetchone()
            approval_row = connection.execute(
                "SELECT * FROM approvals WHERE application_id=? ORDER BY issued_at DESC LIMIT 1",
                (normalized.application_id,),
            ).fetchone()
            if application is None or binding is None or approval_row is None:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "The approved application binding is incomplete.")
            if application["status"] != "APPROVED":
                raise JobOpsError("APPLICATION_NOT_APPROVED", "The review packet must be approved before an action session.")
            if binding["context_hash"] != normalized.context_hash or json.loads(binding["context_json"]) != normalized.as_dict():
                raise JobOpsError("EXTERNAL_ACTION_SESSION_INVALIDATED", "The application changed after review approval.")
            approval = ExternalActionGateway._approval_from_row(approval_row)
            approval_decision = validate_approval(approval, context=normalized)
            if approval_decision != "APPROVAL_VALID":
                raise JobOpsError(approval_decision, "The review-packet approval is no longer current.")
            connection.execute(
                "UPDATE external_action_sessions SET status='INVALIDATED' WHERE application_id=? AND status='AUTHORIZED'",
                (normalized.application_id,),
            )
            connection.execute(
                """INSERT INTO external_action_sessions(
                session_id,application_id,application_context_hash,source_route_hash,form_snapshot_hash,
                uploads_hash,site_policy_version,allowed_actions_json,control_generation,mode,bound_hash,
                issued_at,expires_at,nonce,session_version,status,revoked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session.session_id, session.application_id, session.application_context_hash,
                    session.source_route_hash, session.form_snapshot_hash, session.uploads_hash,
                    session.site_policy_version, json.dumps(list(session.allowed_actions)),
                    session.control_generation, session.mode, session.bound_hash, session.issued_at,
                    session.expires_at, session.nonce, session.session_version, session.status, session.revoked_at,
                ),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    normalized.application_id, "EXTERNAL_ACTION_SESSION_PERSISTED", "APPROVED", "APPROVED",
                    json.dumps({
                        "session_id": session.session_id, "bound_hash": session.bound_hash,
                        "allowed_actions": list(session.allowed_actions), "control_generation": session.control_generation,
                    }), iso_utc(),
                ),
            )
        return {
            "status": "EXTERNAL_ACTION_SESSION_AUTHORIZED", "session_id": session.session_id,
            "allowed_actions": list(session.allowed_actions), "expires_at": session.expires_at,
            "mode": session.mode, "real_external_actions": 0,
        }

    @staticmethod
    def _session_from_row(row) -> ExternalActionSession:
        value = dict(row)
        value["allowed_actions"] = json.loads(value.pop("allowed_actions_json"))
        return ExternalActionSession.from_dict(value)

    def validate_scope(
        self,
        *,
        session_id: str,
        context: ApprovalContext,
        required_actions: Iterable[str],
    ) -> dict[str, Any]:
        """Preflight one complete action scope without consuming any action."""

        actions = _actions(required_actions)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM external_action_sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            used = {
                str(item["action"])
                for item in connection.execute(
                    "SELECT action FROM external_action_session_uses WHERE session_id=?", (session_id,),
                ).fetchall()
            }
        if row is None:
            raise JobOpsError("EXTERNAL_ACTION_SESSION_NOT_FOUND", "The scoped action session does not exist.")
        session = self._session_from_row(row)
        for action in actions:
            decision = self._validate(session, context=context, action=action)
            if decision != "EXTERNAL_ACTION_SESSION_VALID":
                raise JobOpsError(decision, "The scoped action session does not cover the complete execution preflight.")
        replayed = sorted(set(actions) & used)
        if replayed:
            raise JobOpsError(
                "EXTERNAL_ACTION_SESSION_REPLAYED",
                "At least one required action in this scoped session has already been used.",
                actions=replayed,
            )
        return {
            "status": "EXTERNAL_ACTION_SCOPE_VALID",
            "session_id": session_id,
            "required_actions": list(actions),
            "required_action_count": len(actions),
            "real_external_actions": 0,
        }

    def record_isolated_use(
        self,
        *,
        session_id: str,
        context: ApprovalContext,
        action: str,
        request_hash: str,
        result_code: str,
    ) -> dict[str, Any]:
        if not self.policy.isolated_test_mode or self.policy.mode != "ISOLATED_FAKE":
            raise JobOpsError("REAL_TRANSPORT_FORBIDDEN", "Only isolated fake session use can be recorded in this build.")
        if len(request_hash) != 71 or not request_hash.startswith("sha256:"):
            raise JobOpsError("EXTERNAL_ACTION_REQUEST_HASH_INVALID", "The action request must be represented by a SHA-256 hash.")
        try:
            int(request_hash[7:], 16)
        except ValueError as exc:
            raise JobOpsError("EXTERNAL_ACTION_REQUEST_HASH_INVALID", "The action request must be represented by a SHA-256 hash.") from exc
        safe_result = str(result_code).strip().upper()
        if not safe_result or len(safe_result) > 100 or not safe_result.replace("_", "").isalnum():
            raise JobOpsError("EXTERNAL_ACTION_RESULT_INVALID", "The isolated result must be a short safe code.")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM external_action_sessions WHERE session_id=?", (session_id,),
            ).fetchone()
        if row is None:
            raise JobOpsError("EXTERNAL_ACTION_SESSION_NOT_FOUND", "The scoped action session does not exist.")
        session = self._session_from_row(row)
        decision = self._validate(session, context=context, action=action)
        if decision != "EXTERNAL_ACTION_SESSION_VALID":
            raise JobOpsError(decision, "The scoped action session cannot authorize this operation.")
        used_at = iso_utc()
        use_id = stable_id("EAU", session_id, action, request_hash, used_at)
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO external_action_session_uses(
                    use_id,session_id,application_id,action,request_hash,adapter_kind,result_code,
                    real_side_effect,used_at) VALUES(?,?,?,?,?,?,?,0,?)""",
                    (
                        use_id, session_id, context.application_id, action, request_hash,
                        "fake", safe_result, used_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        context.application_id, "ISOLATED_EXTERNAL_ACTION_SESSION_USED", "APPROVED", "APPROVED",
                        json.dumps({
                            "session_id": session_id, "use_id": use_id, "action": action,
                            "request_hash": request_hash, "result_code": safe_result,
                        }), used_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise JobOpsError("EXTERNAL_ACTION_SESSION_REPLAYED", "This session action has already been used.") from exc
            raise
        return {
            "status": "ISOLATED_ACTION_RECORDED", "use_id": use_id,
            "session_id": session_id, "action": action, "adapter_kind": "fake",
            "real_external_actions": 0,
        }

    def revoke(self, session_id: str) -> dict[str, Any]:
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT application_id,status FROM external_action_sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if row is None:
                raise JobOpsError("EXTERNAL_ACTION_SESSION_NOT_FOUND", "The scoped action session does not exist.")
            changed = connection.execute(
                "UPDATE external_action_sessions SET status='REVOKED',revoked_at=? WHERE session_id=? AND status='AUTHORIZED'",
                (now, session_id),
            ).rowcount
            if changed:
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        row["application_id"], "EXTERNAL_ACTION_SESSION_REVOKED", "APPROVED", "APPROVED",
                        json.dumps({"session_id": session_id}), now,
                    ),
                )
        return {
            "status": "REVOKED" if changed else str(row["status"]),
            "session_id": session_id, "real_external_actions": 0,
        }
