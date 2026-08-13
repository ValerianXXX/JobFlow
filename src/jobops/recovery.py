from __future__ import annotations

import json
from typing import Any

from .db import JobOpsDB
from .errors import JobOpsError
from .runtime_schema import validate_named
from .state_machine import BLOCKING_STATES, PRIMARY_STATES
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


NEVER_AUTO_RESUME = {"NEEDS_ACCOUNT_APPROVAL", "BLOCKED_LOGIN", "BLOCKED_CAPTCHA", "APPROVAL_EXPIRED", "INELIGIBLE", "SUBMISSION_UNKNOWN"}


def recovery_guidance(state: str) -> dict[str, str]:
    mapping = {
        "NEEDS_USER_INPUT": ("REVIEW_REQUIRED", "Confirm the unresolved fields, then rerun validation."),
        "NEEDS_ACCOUNT_APPROVAL": ("REVIEW_REQUIRED", "Guest flow is unavailable; account creation needs separate approval and remains disabled."),
        "BLOCKED_LOGIN": ("REVIEW_REQUIRED", "User login is required; no automated credential or session use is permitted."),
        "BLOCKED_CAPTCHA": ("REVIEW_REQUIRED", "CAPTCHA cannot be bypassed or automated."),
        "SITE_CHANGED": ("REANALYZE", "Capture a new local page snapshot and rebuild route, form, packet, and approval bindings."),
        "APPROVAL_EXPIRED": ("REVIEW_REQUIRED", "Rebuild the current review packet and obtain a fresh approval."),
        "MATERIALS_NEEDS_CORRECTION": ("RESUME_SAFE_STEP", "Correct materials and rerun document QA from MATERIALS_DRAFTED."),
        "INELIGIBLE": ("CLOSE", "Close the application or explicitly override and reanalyze; never auto-continue."),
        "SUBMISSION_UNKNOWN": ("NO_AUTO_RETRY", "Manually verify external evidence; never retry submission automatically."),
    }
    if state not in mapping:
        raise JobOpsError("RECOVERY_STATE_UNKNOWN", "No recovery rule exists for this state.", state=state)
    decision, action = mapping[state]
    return {"state": state, "decision": decision, "next_safe_action": action}


class RecoveryManager:
    def __init__(self, database: JobOpsDB) -> None:
        self.database = database

    def explain(self, application_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT status,last_safe_state FROM applications WHERE application_id=?", (application_id,)).fetchone()
        if row is None:
            raise JobOpsError("APPLICATION_NOT_FOUND", "Application does not exist.")
        result = recovery_guidance(str(row["status"])) if row["status"] in BLOCKING_STATES else {"state": str(row["status"]), "decision": "NONE", "next_safe_action": "No recovery is required."}
        return {"application_id": application_id, "last_safe_state": str(row["last_safe_state"]), **result}

    def resume_safe_step(self, application_id: str, *, validation_material: dict[str, Any], explicit_ineligible_override: bool = False) -> dict[str, Any]:
        validation_hash = sha256_bytes(canonical_json(validation_material))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status,last_safe_state FROM applications WHERE application_id=?", (application_id,)).fetchone()
            if row is None:
                raise JobOpsError("APPLICATION_NOT_FOUND", "Application does not exist.")
            blocked, last_safe = str(row["status"]), str(row["last_safe_state"])
            if blocked == "SUBMISSION_UNKNOWN":
                raise JobOpsError("SUBMISSION_UNKNOWN_NO_RETRY", "Unknown submissions cannot be retried or automatically resumed.")
            if blocked == "INELIGIBLE" and not explicit_ineligible_override:
                raise JobOpsError("INELIGIBLE_OVERRIDE_REQUIRED", "Ineligible work cannot continue without an explicit human override and reanalysis.")
            if blocked in NEVER_AUTO_RESUME and blocked != "INELIGIBLE":
                raise JobOpsError("MANUAL_RECOVERY_REQUIRED", "This blocking state requires human review and fresh validation.", state=blocked)
            if last_safe not in PRIMARY_STATES or last_safe in {"APPROVED", "SUBMITTING", "SUBMITTED", "CONFIRMED"}:
                raise JobOpsError("LAST_SAFE_STATE_INVALID", "Recovery cannot enter a protected or non-safe state.", last_safe_state=last_safe)
            binding = connection.execute("SELECT context_hash FROM application_bindings WHERE application_id=?", (application_id,)).fetchone()
            expected = validation_material.get("context_hash")
            if binding is not None and expected != binding["context_hash"]:
                raise JobOpsError("RECOVERY_VALIDATION_CHANGED", "Current inputs no longer match the last persisted safe binding.")
            now = iso_utc()
            if blocked == "INELIGIBLE":
                target, decision = "NEEDS_USER_INPUT", "REANALYZE_REQUIRED"
                connection.execute(
                    "UPDATE applications SET status=?,last_safe_state=?,updated_at=? WHERE application_id=?",
                    (target, last_safe, now, application_id),
                )
            else:
                target = blocked
                decision = "REANALYZE_REQUIRED" if blocked == "SITE_CHANGED" else "REPROCESS_REQUIRED"
            recovery_id = stable_id("RCV", application_id, blocked, validation_hash, now)
            recovery_event = {
                "recovery_id": recovery_id, "application_id": application_id,
                "blocked_state": blocked, "last_safe_state": last_safe,
                "validation_hash": validation_hash, "decision": decision, "created_at": now,
            }
            validate_named("recovery-event", recovery_event, project_root() / "schemas")
            connection.execute(
                "INSERT INTO recovery_events(recovery_id,application_id,blocked_state,last_safe_state,validation_hash,decision,created_at) VALUES(?,?,?,?,?,?,?)",
                (recovery_id, application_id, blocked, last_safe, validation_hash, decision, now),
            )
            if blocked in {"SITE_CHANGED", "APPROVAL_EXPIRED", "MATERIALS_NEEDS_CORRECTION", "INELIGIBLE"}:
                connection.execute("UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'", (application_id,))
                connection.execute("UPDATE review_packets SET status='NEEDS_REVISION' WHERE application_id=? AND status IN ('AWAITING_APPROVAL','APPROVED')", (application_id,))
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "RECOVERY_REPROCESS_REQUESTED", blocked, target, json.dumps({"recovery_id": recovery_id, "validation_hash": validation_hash}), now),
            )
        return {
            "status": target, "application_id": application_id, "recovery_id": recovery_id,
            "decision": decision, "next_safe_action": "run-to-awaiting-approval",
        }
