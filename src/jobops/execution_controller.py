from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .adapters import FakeBrowserPrefillAdapter, FakeReceiptAdapter, FakeSubmissionAdapter
from .application_execution import validate_application_execution_plan_integrity
from .approvals import ApprovalContext, validate_approval
from .ats_browser import validate_browser_action_plan_integrity
from .db import JobOpsDB
from .errors import JobOpsError
from .external_actions import ExternalActionGateway, ExternalActionPolicy
from .final_submission import issue_final_submission_authorization
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


def _sha256(value: object, code: str) -> str:
    material = str(value or "")
    if len(material) != 71 or not material.startswith("sha256:"):
        raise JobOpsError(code, "An isolated execution checkpoint requires a valid SHA-256 value.")
    try:
        int(material[7:], 16)
    except ValueError as exc:
        raise JobOpsError(code, "An isolated execution checkpoint requires a valid SHA-256 value.") from exc
    return material


@dataclass(frozen=True)
class _ValidatedInputs:
    context: ApprovalContext
    execution_plan: dict[str, Any]
    browser_plan: dict[str, Any]
    freshness_evidence_hash: str
    prefill_evidence: dict[str, Any]


class IsolatedApplicationExecutionController:
    """Exercise the complete application lifecycle without a real transport.

    This controller is deliberately unavailable through the production UI and uses
    only the in-memory/local fake adapters.  It proves binding, checkpoint, replay,
    crash and receipt semantics while keeping every external-action counter at zero.
    """

    def __init__(self, database: JobOpsDB) -> None:
        self.database = database
        self.gateway = ExternalActionGateway(database, ExternalActionPolicy.isolated_fake())
        self.browser = FakeBrowserPrefillAdapter()
        self.submission = FakeSubmissionAdapter(database)
        self.receipt = FakeReceiptAdapter()
        self.schemas = project_root() / "schemas"

    def _validate_inputs(
        self,
        *,
        context: ApprovalContext,
        execution_plan: dict[str, Any],
        browser_plan: dict[str, Any],
        current_form_snapshot_hash: str,
        freshness_evidence_hash: str,
    ) -> _ValidatedInputs:
        normalized = context.normalized()
        validate_named("application-execution-plan", execution_plan, self.schemas)
        validate_application_execution_plan_integrity(execution_plan)
        validate_browser_action_plan_integrity(browser_plan)
        current_form_hash = _sha256(current_form_snapshot_hash, "EXECUTION_CURRENT_FORM_HASH_INVALID")
        freshness_hash = _sha256(freshness_evidence_hash, "EXECUTION_FRESHNESS_HASH_INVALID")
        if execution_plan.get("status") != "READY_FOR_REVIEW" or execution_plan.get("blockers"):
            raise JobOpsError("EXECUTION_PLAN_NOT_READY", "The application execution plan still has a blocker.")
        if execution_plan.get("application_id") != normalized.application_id:
            raise JobOpsError("EXECUTION_APPLICATION_MISMATCH", "The execution plan belongs to another application.")
        if execution_plan.get("route_hash") != normalized.source_route_hash:
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The current route no longer matches the approved application.")
        if execution_plan.get("form_snapshot_hash") != normalized.form_snapshot_hash:
            raise JobOpsError("EXECUTION_FORM_CHANGED", "The reviewed form no longer matches the approved application.")
        if current_form_hash != normalized.form_snapshot_hash:
            raise JobOpsError("SITE_CHANGED", "The current form snapshot differs from the approved form.")
        if browser_plan.get("plan_hash") != execution_plan.get("browser_plan_hash"):
            raise JobOpsError("EXECUTION_BROWSER_PLAN_CHANGED", "The browser proposal differs from the reviewed execution plan.")
        if browser_plan.get("form_snapshot_hash") != current_form_hash:
            raise JobOpsError("SITE_CHANGED", "The browser proposal belongs to a different form snapshot.")
        if browser_plan.get("source_route_hash") != normalized.source_route_hash:
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The browser proposal belongs to a different source route.")
        if normalized.unresolved_stops or normalized.mandatory_unknowns:
            raise JobOpsError("EXECUTION_FIELDS_UNRESOLVED", "Protected STOP fields or mandatory UNKNOWN values remain unresolved.")

        with self.database.connect() as connection:
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
        if application is None or binding is None:
            raise JobOpsError("APPLICATION_BINDING_MISSING", "The application or its content binding is missing.")
        if application["status"] != "APPROVED":
            raise JobOpsError("APPLICATION_NOT_APPROVED", "The review packet must be approved before execution preparation.")
        if binding["context_hash"] != normalized.context_hash or json.loads(binding["context_json"]) != normalized.as_dict():
            raise JobOpsError("APPROVAL_INVALIDATED", "The current application differs from the approved review packet.")
        if approval_row is None:
            raise JobOpsError("APPROVAL_REQUIRED", "A current review-packet approval is required.")
        approval = self.gateway._approval_from_row(approval_row)
        approval_result = validate_approval(approval, context=normalized, required_actions=("submit_application",))
        if approval_result != "APPROVAL_VALID":
            raise JobOpsError(approval_result, "The review-packet approval is no longer current.")

        prefill = self.browser.prefill({
            "plan": browser_plan,
            "current_form_snapshot_hash": current_form_hash,
            "isolation_policy": "ISOLATED_FAKE_ONLY",
        })
        if (
            prefill.get("status") != "FAKE_PLAN_VALIDATED"
            or prefill.get("fields_modified") != 0
            or prefill.get("uploaded_files") != []
            or prefill.get("browser_actions") != 0
            or prefill.get("network_actions") != 0
            or prefill.get("real_side_effects") != 0
        ):
            raise JobOpsError("FAKE_PREFILL_EVIDENCE_INVALID", "The isolated prefill adapter reported an external modification.")
        return _ValidatedInputs(normalized, execution_plan, browser_plan, freshness_hash, prefill)

    def _checkpoint(
        self,
        *,
        run_id: str,
        application_id: str,
        sequence: int,
        phase: str,
        status: str,
        context_hash: str,
        execution_plan_hash: str,
        browser_plan_hash: str,
        form_snapshot_hash: str,
        freshness_evidence_hash: str,
        evidence: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        evidence_hash = sha256_bytes(canonical_json(evidence))
        value = {
            "schema_version": 1,
            "checkpoint_id": stable_id("ECP", run_id, str(sequence), phase, evidence_hash),
            "run_id": run_id,
            "application_id": application_id,
            "sequence": sequence,
            "phase": phase,
            "status": status,
            "application_context_hash": context_hash,
            "execution_plan_hash": execution_plan_hash,
            "browser_plan_hash": browser_plan_hash,
            "form_snapshot_hash": form_snapshot_hash,
            "freshness_evidence_hash": freshness_evidence_hash,
            "evidence_hash": evidence_hash,
            "created_at": created_at,
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
        }
        validate_named("application-execution-checkpoint", value, self.schemas)
        return value

    @staticmethod
    def _insert_checkpoint(connection, checkpoint: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO application_execution_checkpoints(
            checkpoint_id,run_id,application_id,sequence,phase,status,evidence_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                checkpoint["checkpoint_id"], checkpoint["run_id"], checkpoint["application_id"],
                checkpoint["sequence"], checkpoint["phase"], checkpoint["status"],
                checkpoint["evidence_hash"], checkpoint["created_at"],
            ),
        )

    def prepare_until_final_authorization(
        self,
        *,
        context: ApprovalContext,
        execution_plan: dict[str, Any],
        browser_plan: dict[str, Any],
        current_form_snapshot_hash: str,
        freshness_evidence_hash: str,
    ) -> dict[str, Any]:
        checked = self._validate_inputs(
            context=context,
            execution_plan=execution_plan,
            browser_plan=browser_plan,
            current_form_snapshot_hash=current_form_snapshot_hash,
            freshness_evidence_hash=freshness_evidence_hash,
        )
        now = iso_utc()
        run_id = stable_id(
            "RUN", checked.context.application_id, checked.context.context_hash,
            str(checked.execution_plan["plan_hash"]), checked.freshness_evidence_hash, now,
        )
        common = {
            "run_id": run_id,
            "application_id": checked.context.application_id,
            "context_hash": checked.context.context_hash,
            "execution_plan_hash": str(checked.execution_plan["plan_hash"]),
            "browser_plan_hash": str(checked.browser_plan["plan_hash"]),
            "form_snapshot_hash": checked.context.form_snapshot_hash,
            "freshness_evidence_hash": checked.freshness_evidence_hash,
            "created_at": now,
        }
        checkpoints = [
            self._checkpoint(sequence=1, phase="PLAN_VALIDATED", status="PASS", evidence={
                "execution_plan_hash": checked.execution_plan["plan_hash"],
                "application_context_hash": checked.context.context_hash,
            }, **common),
            self._checkpoint(sequence=2, phase="FRESHNESS_BOUND", status="PASS", evidence={
                "freshness_evidence_hash": checked.freshness_evidence_hash,
                "route_hash": checked.context.source_route_hash,
                "form_snapshot_hash": checked.context.form_snapshot_hash,
            }, **common),
            self._checkpoint(sequence=3, phase="PREFILL_PROPOSAL_VALIDATED", status="PASS", evidence={
                "browser_plan_hash": checked.browser_plan["plan_hash"],
                "proposed_field_count": checked.prefill_evidence["proposed_field_count"],
                "fields_modified": 0,
            }, **common),
            self._checkpoint(sequence=4, phase="AWAITING_FINAL_AUTHORIZATION", status="AWAITING_USER", evidence={
                "review_packet_hash": checked.context.review_packet_hash,
                "upload_hashes": [item.sha256 for item in checked.context.uploads],
                "required_gate": "FRESH_EXPLICIT_SUBMISSION_APPROVAL",
            }, **common),
        ]
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE application_execution_runs SET status='INVALIDATED',updated_at=?
                   WHERE application_id=? AND status='AWAITING_FINAL_AUTHORIZATION'""",
                (now, checked.context.application_id),
            )
            connection.execute(
                """INSERT INTO application_execution_runs(
                run_id,application_id,application_context_hash,execution_plan_hash,browser_plan_hash,
                form_snapshot_hash,freshness_evidence_hash,status,checkpoint_sequence,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, checked.context.application_id, checked.context.context_hash,
                    checked.execution_plan["plan_hash"], checked.browser_plan["plan_hash"],
                    checked.context.form_snapshot_hash, checked.freshness_evidence_hash,
                    "AWAITING_FINAL_AUTHORIZATION", 4, now, now,
                ),
            )
            for checkpoint in checkpoints:
                self._insert_checkpoint(connection, checkpoint)
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    checked.context.application_id, "ISOLATED_EXECUTION_PREPARED", "APPROVED", "APPROVED",
                    json.dumps({"run_id": run_id, "plan_hash": checked.execution_plan["plan_hash"]}), now,
                ),
            )
        return {
            "status": "AWAITING_FINAL_AUTHORIZATION",
            "run_id": run_id,
            "application_id": checked.context.application_id,
            "checkpoint_count": 4,
            "proposed_field_count": checked.prefill_evidence["proposed_field_count"],
            "fields_modified": 0,
            "uploaded_files": 0,
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
            "next_safe_action": "OBTAIN_FRESH_EXPLICIT_FINAL_SUBMISSION_AUTHORIZATION",
        }

    def _load_awaiting_run(self, run_id: str, checked: _ValidatedInputs):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM application_execution_runs WHERE run_id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise JobOpsError("EXECUTION_RUN_NOT_FOUND", "The isolated execution run does not exist.")
        if row["status"] == "SUBMISSION_UNKNOWN":
            raise JobOpsError("SUBMISSION_UNKNOWN_NO_RETRY", "An unknown submission must never be retried automatically.")
        if row["status"] != "AWAITING_FINAL_AUTHORIZATION":
            raise JobOpsError("EXECUTION_RUN_NOT_AWAITING_FINAL", "This execution run is no longer awaiting final authorization.")
        expected = {
            "application_id": checked.context.application_id,
            "application_context_hash": checked.context.context_hash,
            "execution_plan_hash": checked.execution_plan["plan_hash"],
            "browser_plan_hash": checked.browser_plan["plan_hash"],
            "form_snapshot_hash": checked.context.form_snapshot_hash,
            "freshness_evidence_hash": checked.freshness_evidence_hash,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise JobOpsError("EXECUTION_RUN_INVALIDATED", "The isolated run no longer matches the current application evidence.")
        return row

    def _append_checkpoint(
        self,
        *,
        run_id: str,
        checked: _ValidatedInputs,
        sequence: int,
        phase: str,
        checkpoint_status: str,
        run_status: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        now = iso_utc()
        checkpoint = self._checkpoint(
            run_id=run_id,
            application_id=checked.context.application_id,
            sequence=sequence,
            phase=phase,
            status=checkpoint_status,
            context_hash=checked.context.context_hash,
            execution_plan_hash=str(checked.execution_plan["plan_hash"]),
            browser_plan_hash=str(checked.browser_plan["plan_hash"]),
            form_snapshot_hash=checked.context.form_snapshot_hash,
            freshness_evidence_hash=checked.freshness_evidence_hash,
            evidence=evidence,
            created_at=now,
        )
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE application_execution_runs SET status=?,checkpoint_sequence=?,updated_at=?
                   WHERE run_id=? AND checkpoint_sequence=?""",
                (run_status, sequence, now, run_id, sequence - 1),
            ).rowcount
            if changed != 1:
                raise JobOpsError("EXECUTION_CHECKPOINT_RACE", "The execution run checkpoint changed concurrently.")
            self._insert_checkpoint(connection, checkpoint)
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    checked.context.application_id, "ISOLATED_EXECUTION_CHECKPOINT", None, None,
                    json.dumps({
                        "run_id": run_id, "sequence": sequence, "phase": phase,
                        "checkpoint_id": checkpoint["checkpoint_id"],
                    }), now,
                ),
            )
        return checkpoint

    def complete_with_fresh_authorization(
        self,
        *,
        run_id: str,
        context: ApprovalContext,
        execution_plan: dict[str, Any],
        browser_plan: dict[str, Any],
        current_form_snapshot_hash: str,
        freshness_evidence_hash: str,
        user_confirmed: bool,
        fake_confirmation_number: str | None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            existing_run = connection.execute(
                "SELECT status FROM application_execution_runs WHERE run_id=?", (run_id,),
            ).fetchone()
        if existing_run is None:
            raise JobOpsError("EXECUTION_RUN_NOT_FOUND", "The isolated execution run does not exist.")
        if existing_run["status"] == "SUBMISSION_UNKNOWN":
            raise JobOpsError("SUBMISSION_UNKNOWN_NO_RETRY", "An unknown submission must never be retried automatically.")
        if existing_run["status"] != "AWAITING_FINAL_AUTHORIZATION":
            raise JobOpsError("EXECUTION_RUN_NOT_AWAITING_FINAL", "This execution run cannot consume another final authorization.")
        checked = self._validate_inputs(
            context=context,
            execution_plan=execution_plan,
            browser_plan=browser_plan,
            current_form_snapshot_hash=current_form_snapshot_hash,
            freshness_evidence_hash=freshness_evidence_hash,
        )
        self._load_awaiting_run(run_id, checked)
        authorization = issue_final_submission_authorization(
            context=checked.context,
            execution_plan=checked.execution_plan,
            freshness_evidence_hash=checked.freshness_evidence_hash,
            user_confirmed=user_confirmed,
        )
        self.gateway.persist_final_submission_authorization(
            authorization,
            context=checked.context,
            execution_plan=checked.execution_plan,
            freshness_evidence_hash=checked.freshness_evidence_hash,
        )

        submission_started = False
        try:
            self.gateway.begin_submission(
                checked.context,
                execution_plan=checked.execution_plan,
                freshness_evidence_hash=checked.freshness_evidence_hash,
            )
            submission_started = True
            self._append_checkpoint(
                run_id=run_id, checked=checked, sequence=5,
                phase="FINAL_AUTHORIZATION_CONSUMED", checkpoint_status="CONSUMED",
                run_status="SUBMISSION_STARTED",
                evidence={"authorization_id": authorization.authorization_id, "bound_hash": authorization.bound_hash},
            )
            transport = self.submission.submit({
                "application_id": checked.context.application_id,
                "run_id": run_id,
                "application_context_hash": checked.context.context_hash,
                "isolation_policy": "ISOLATED_FAKE_ONLY",
            })
            if transport.get("status") != "FAKE_SUBMISSION_RECORDED" or transport.get("real_side_effects") != 0:
                raise JobOpsError("FAKE_SUBMISSION_EVIDENCE_INVALID", "The isolated submission adapter returned invalid evidence.")
            self.gateway.mark_fake_submitted(
                checked.context.application_id,
                fake_evidence={"run_id": run_id, "attempt_id": transport["attempt_id"], "adapter": "fake"},
            )
            self._append_checkpoint(
                run_id=run_id, checked=checked, sequence=6,
                phase="FAKE_SUBMISSION_RECORDED", checkpoint_status="RECORDED",
                run_status="SUBMITTED",
                evidence={"attempt_id": transport["attempt_id"], "adapter_kind": "fake"},
            )
            receipt_result = self.receipt.verify({
                "source": "fake-receipt",
                "confirmation_number": fake_confirmation_number,
                "received_at": iso_utc(),
            })
            if receipt_result.get("status") != "CONFIRMED" or receipt_result.get("verified") is not True:
                self.gateway.mark_submission_unknown(
                    checked.context.application_id,
                    evidence={"run_id": run_id, "reason": "VERIFIED_RECEIPT_MISSING"},
                )
                self._append_checkpoint(
                    run_id=run_id, checked=checked, sequence=7,
                    phase="SUBMISSION_UNKNOWN", checkpoint_status="UNKNOWN",
                    run_status="SUBMISSION_UNKNOWN",
                    evidence={"reason": "VERIFIED_RECEIPT_MISSING"},
                )
                return {
                    "status": "SUBMISSION_UNKNOWN",
                    "run_id": run_id,
                    "automatic_retry": False,
                    "browser_actions": 0,
                    "network_actions": 0,
                    "real_external_actions": 0,
                    "next_safe_action": "MANUALLY_VERIFY_SUBMISSION_EVIDENCE",
                }
            verified_at = iso_utc()
            receipt = {
                "receipt_id": stable_id("RCP", checked.context.application_id, run_id, str(receipt_result["receipt_hash"])),
                "application_id": checked.context.application_id,
                "source": "fake-receipt",
                "confirmation_type": "confirmation_number",
                "confirmation_hash": str(receipt_result["receipt_hash"]),
                "verified": True,
                "verified_at": verified_at,
            }
            validate_named("receipt", receipt, self.schemas)
            confirmed = self.gateway.confirm_with_receipt(checked.context.application_id, receipt)
            self._append_checkpoint(
                run_id=run_id, checked=checked, sequence=7,
                phase="RECEIPT_VERIFIED", checkpoint_status="CONFIRMED",
                run_status="CONFIRMED",
                evidence={"receipt_id": confirmed["receipt_id"], "confirmation_hash": receipt["confirmation_hash"]},
            )
            return {
                "status": "CONFIRMED",
                "run_id": run_id,
                "application_id": checked.context.application_id,
                "receipt_id": confirmed["receipt_id"],
                "checkpoint_count": 7,
                "automatic_retry": False,
                "browser_actions": 0,
                "network_actions": 0,
                "real_external_actions": 0,
            }
        except Exception as exc:
            if submission_started:
                try:
                    self.gateway.mark_submission_unknown(
                        checked.context.application_id,
                        evidence={"run_id": run_id, "failure_code": getattr(exc, "code", "ISOLATED_EXECUTION_FAILURE")},
                    )
                except JobOpsError:
                    pass
            raise
