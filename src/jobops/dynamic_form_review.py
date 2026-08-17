from __future__ import annotations

import json
import secrets
from copy import deepcopy
from dataclasses import replace
from typing import Any

from .application_execution import build_application_execution_plan
from .application_field_resolution import (
    ANSWERABLE_STOP_CLASSES,
    SEPARATE_ACTION_STOP_CLASSES,
    approval_unresolved_stop_ids,
    initial_application_field_status,
)
from .approvals import ApprovalContext
from .ats_browser import build_browser_action_plan, validate_ats_form_snapshot_integrity
from .db import JobOpsDB
from .errors import JobOpsError
from .execution_bundle import (
    ApplicationExecutionBundleManager,
    build_application_execution_bundle,
)
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


MAX_DYNAMIC_PACKET_BYTES = 2 * 1024 * 1024


def _semantic_field(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(value.get("control_type", "")),
        bool(value.get("required", False)),
        str(value.get("classification", "")),
        str(value.get("prompt_hash", "")),
        tuple(str(item) for item in value.get("display_options", [])),
    )


class DynamicFormReviewManager:
    """Rebind an approved application when the same page reveals new controls.

    Many ATS pages render application questions only after contact/location fields
    are entered.  This manager never guesses or fills those new questions.  It
    replaces the exact encrypted form/execution binding, invalidates the old
    approval, and sends only the newly discovered questions back through the
    ordinary review gate.
    """

    def __init__(self, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.database = database
        self.onboarding = onboarding
        self.schemas = project_root() / "schemas"
        self.bundles = ApplicationExecutionBundleManager(database, onboarding)

    @staticmethod
    def _bindings_from_bundle(bundle: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        public_values = {
            str(item["control_ref"]): str(item["value"])
            for item in bundle.get("public_values", [])
        }
        bindings: dict[str, dict[str, str]] = {}
        for action in bundle.get("browser_plan", {}).get("actions", []):
            control_ref = str(action.get("control_ref", ""))
            kind = str(action.get("binding_kind", ""))
            if kind == "SECURE_REF":
                bindings[control_ref] = {"kind": "secure_ref", "value": str(action["binding_ref"])}
            elif kind == "PUBLIC_VALUE_HASH" and control_ref in public_values:
                bindings[control_ref] = {"kind": "public_value", "value": public_values[control_ref]}
        return bindings, public_values

    @staticmethod
    def _safe_questions(
        live: dict[str, Any],
        browser_plan: dict[str, Any],
        previous_by_live_ref: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        actions = {str(item["control_ref"]): item for item in browser_plan["actions"]}
        questions: list[dict[str, Any]] = []
        for field in live["fields"]:
            control_ref = str(field["control_ref"])
            action = actions[control_ref]
            previous = previous_by_live_ref.get(control_ref)
            if previous is not None and (
                str(previous.get("control_type")) != str(field["control_type"])
                or bool(previous.get("required", False)) != bool(field.get("required", False))
                or str(previous.get("classification")) != str(field["classification"])
                or str(previous.get("prompt_hash")) != str(field["prompt_hash"])
                or tuple(str(item) for item in previous.get("options", []))
                != tuple(str(item) for item in field.get("display_options", []))
            ):
                raise JobOpsError(
                    "SITE_CHANGED",
                    "A previously reviewed form question changed while the page was revealing additional questions.",
                )
            if previous is not None:
                item = deepcopy(previous)
                item["id"] = control_ref
                item["label"] = field.get("display_label") or item.get("label") or field["answer_key"]
                item["options"] = list(field.get("display_options", []))
                item["untrusted_prompt_display_only"] = True
                questions.append(item)
                continue
            proposed = action["action"] == "PROPOSE_PREFILL"
            questions.append({
                "id": control_ref,
                "label": field.get("display_label") or field["answer_key"],
                "options": list(field.get("display_options", [])),
                "untrusted_prompt_display_only": True,
                "answer_key": field["answer_key"],
                "prompt_hash": field["prompt_hash"],
                "control_type": field["control_type"],
                "required": bool(field.get("required", False)),
                "classification": field["classification"],
                "reason": field["reason_code"],
                "gate": "PREFILL_ALLOWED" if proposed else "STOP_REQUIRED",
                "action": (
                    "PREFILL_FROM_SECURE_STORE"
                    if action["binding_kind"] == "SECURE_REF"
                    else "PREFILL" if proposed else "STOP"
                ),
                "status": "READY" if proposed else (
                    "SEPARATE_ACTION_GATED"
                    if field["classification"] in SEPARATE_ACTION_STOP_CLASSES
                    else "STOPPED"
                ),
                "secure_ref": action["binding_ref"] if action["binding_kind"] == "SECURE_REF" else None,
                "redacted_summary": (
                    "PRIVATE_VALUE_PRESENT" if action["binding_kind"] == "SECURE_REF"
                    else "PUBLIC_VALUE_HASH_PRESENT" if action["binding_kind"] == "PUBLIC_VALUE_HASH"
                    else "UNANSWERED"
                ),
                "ai_semantic_role": None,
                "ai_semantic_reason": "Newly revealed by the live ATS page; applicant confirmation required.",
            })
        return questions

    def rebind(
        self,
        *,
        application_id: str,
        live_snapshot: dict[str, Any],
        assist_id: str,
        session_id: str,
        allowed_missing_control_refs: set[str] | None = None,
    ) -> dict[str, Any]:
        validate_ats_form_snapshot_integrity(live_snapshot)
        bundle, context, answer_refs = self.bundles.load_current(application_id)
        context = context.normalized()
        if live_snapshot["canonical_url"] != bundle["form_snapshot"]["canonical_url"]:
            raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "Dynamic questions must remain on the exact approved application page.")
        if live_snapshot["source_route_hash"] != context.source_route_hash:
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "Dynamic questions no longer match the approved source route.")

        old_fields = {str(item["control_ref"]): item for item in bundle["form_snapshot"]["fields"]}
        live_fields = {str(item["control_ref"]): item for item in live_snapshot["fields"]}
        old_by_logical: dict[str, list[dict[str, Any]]] = {}
        live_by_logical: dict[str, list[dict[str, Any]]] = {}
        for item in bundle["form_snapshot"]["fields"]:
            old_by_logical.setdefault(str(item["logical_field_hash"]), []).append(item)
        for item in live_snapshot["fields"]:
            live_by_logical.setdefault(str(item["logical_field_hash"]), []).append(item)
        old_to_live: dict[str, str] = {}
        removed_separate: set[str] = set()
        removed_applied: set[str] = set()
        allowed_missing = set(allowed_missing_control_refs or set())
        unknown_allowed = allowed_missing - set(old_fields)
        if unknown_allowed:
            raise JobOpsError(
                "DYNAMIC_MISSING_CONTROL_INVALID",
                "The browser reported an applied control outside the approved form snapshot.",
            )
        for logical_hash, old_items in old_by_logical.items():
            live_items = list(live_by_logical.get(logical_hash, []))
            if len(live_items) > len(old_items):
                protected = any(
                    str(item.get("classification")) not in SEPARATE_ACTION_STOP_CLASSES
                    for item in old_items
                )
                if protected:
                    raise JobOpsError(
                        "SITE_CHANGED",
                        "The live form added a question indistinguishable from a previously reviewed applicant field.",
                    )
            for index, old_item in enumerate(old_items):
                old_ref = str(old_item["control_ref"])
                if index >= len(live_items):
                    if old_ref in allowed_missing:
                        removed_applied.add(old_ref)
                        continue
                    if str(old_item.get("classification")) in SEPARATE_ACTION_STOP_CLASSES:
                        removed_separate.add(old_ref)
                        continue
                    raise JobOpsError(
                        "SITE_CHANGED",
                        "A previously reviewed applicant field disappeared from the live form.",
                    )
                live_item = live_items[index]
                if _semantic_field(old_item) != _semantic_field(live_item):
                    raise JobOpsError(
                        "SITE_CHANGED",
                        "A previously reviewed applicant field changed on the live form.",
                    )
                old_to_live[old_ref] = str(live_item["control_ref"])
        mapped_live_refs = set(old_to_live.values())
        live_to_old = {live_ref: old_ref for old_ref, live_ref in old_to_live.items()}
        new_refs = set(live_fields) - mapped_live_refs
        if not new_refs and not removed_separate:
            return {
                "status": "NO_DYNAMIC_FIELDS",
                "application_id": application_id,
                "dynamic_field_count": 0,
                "real_external_actions": 0,
            }

        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.packet_id,r.packet_version,r.content_hash,r.relative_path,r.status,
                          a.status AS application_status,b.context_hash
                   FROM review_packets r
                   JOIN applications a ON a.application_id=r.application_id
                   JOIN application_bindings b ON b.application_id=r.application_id
                   WHERE r.application_id=? ORDER BY r.packet_version DESC LIMIT 1""",
                (application_id,),
            ).fetchone()
        if (
            row is None
            or str(row["status"]) != "APPROVED"
            or str(row["application_status"]) != "APPROVED"
            or str(row["context_hash"]) != context.context_hash
        ):
            raise JobOpsError("APPLICATION_NOT_APPROVED", "Dynamic form discovery requires the currently approved application packet.")
        packet_metadata = self.onboarding.reference_metadata(str(row["relative_path"]))
        packet_raw = bytearray(self.onboarding.read_bytes(str(row["relative_path"])))
        try:
            packet = json.loads(bytes(packet_raw).decode("utf-8"))
        finally:
            packet_raw[:] = b"\0" * len(packet_raw)
        if not isinstance(packet, dict) or packet.get("content_hash") != row["content_hash"]:
            raise JobOpsError("REVIEW_PACKET_BINDING_INVALID", "The current review packet cannot be rebound safely.")
        validate_named("review-packet", packet, self.schemas)
        old_questions = {str(item["id"]): item for item in packet["form_questions"]}
        previous_by_live_ref = {
            live_ref: old_questions[old_ref]
            for old_ref, live_ref in old_to_live.items()
            if old_ref in old_questions
        }

        old_bindings, old_public_values = self._bindings_from_bundle(bundle)
        bindings = {
            live_ref: old_bindings[old_ref]
            for old_ref, live_ref in old_to_live.items()
            if old_ref in old_bindings
        }
        public_values = {
            live_ref: old_public_values[old_ref]
            for old_ref, live_ref in old_to_live.items()
            if old_ref in old_public_values
        }
        browser_plan = build_browser_action_plan(live_snapshot, bindings)
        questions = self._safe_questions(live_snapshot, browser_plan, previous_by_live_ref)
        execution_plan = build_application_execution_plan(
            application_id=application_id,
            source_route=packet["source_route"],
            form_snapshot_hash=str(live_snapshot["form_snapshot_hash"]),
            browser_plan_hash=str(browser_plan["plan_hash"]),
            form_fields=questions,
            material_plan=packet["material_plan"],
            pending_limit=int(packet["queue"]["pending_limit"]),
            form_blockers=live_snapshot.get("blockers", []),
        )
        execution_bundle = build_application_execution_bundle(
            application_id=application_id,
            form_snapshot=live_snapshot,
            browser_plan=browser_plan,
            execution_plan=execution_plan,
            public_values=public_values,
            material_references=list(bundle["material_references"]),
            operator_task_id=(
                str(bundle["operator_task_id"])
                if isinstance(bundle.get("operator_task_id"), str)
                else None
            ),
        )

        created: list[dict[str, Any]] = []
        try:
            bundle_ref = self.onboarding.import_bytes(
                "application_execution_bundle",
                canonical_json(execution_bundle),
                synthetic=bool(packet_metadata["synthetic"]),
            )
            created.append(bundle_ref)
            updated_packet = deepcopy(packet)
            updated_packet.update({
                "status": "AWAITING_APPROVAL",
                "packet_id": stable_id(
                    "RPK", application_id, str(row["packet_id"]), str(live_snapshot["form_snapshot_hash"]),
                ),
                "form_questions": questions,
                "sensitive_fields": [item for item in questions if item["action"] == "STOP"],
                "execution_plan": execution_plan,
                "execution_bundle_content_hash": bundle_ref["content_sha256"],
            })
            updated_packet.pop("content_hash", None)
            updated_packet["content_hash"] = sha256_bytes(canonical_json(updated_packet))
            validate_named("review-packet", updated_packet, self.schemas)
            raw_packet = canonical_json(updated_packet)
            if len(raw_packet) > MAX_DYNAMIC_PACKET_BYTES:
                raise JobOpsError("REVIEW_PACKET_SIZE_INVALID", "The dynamic review packet exceeds the local display limit.")
            packet_ref = self.onboarding.import_bytes(
                "review_packet", raw_packet, synthetic=bool(packet_metadata["synthetic"]),
            )
            created.append(packet_ref)

            rebound_answer_ref: dict[str, Any] | None = None
            rebound_answers_hash = context.answers_hash
            if answer_refs:
                if len(answer_refs) != 1:
                    raise JobOpsError(
                        "DYNAMIC_ANSWER_BUNDLE_COUNT_INVALID",
                        "The approved application has more than one active job-specific answer bundle.",
                    )
                answer_raw = bytearray(self.onboarding.read_bytes(answer_refs[0]))
                try:
                    answer_bundle = json.loads(bytes(answer_raw).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JobOpsError(
                        "DYNAMIC_ANSWER_BUNDLE_INVALID",
                        "The approved job-specific answers could not be rebound safely.",
                    ) from exc
                finally:
                    answer_raw[:] = b"\0" * len(answer_raw)
                if (
                    not isinstance(answer_bundle, dict)
                    or answer_bundle.get("application_id") != application_id
                    or not isinstance(answer_bundle.get("fields"), list)
                    or not isinstance(answer_bundle.get("non_form_unknowns", []), list)
                ):
                    raise JobOpsError(
                        "DYNAMIC_ANSWER_BUNDLE_INVALID",
                        "The approved job-specific answers could not be rebound safely.",
                    )
                rebound_fields: list[dict[str, Any]] = []
                for item in answer_bundle["fields"]:
                    if not isinstance(item, dict):
                        raise JobOpsError("DYNAMIC_ANSWER_BUNDLE_INVALID", "A confirmed answer entry is invalid.")
                    old_ref = str(item.get("control_ref", ""))
                    live_ref = old_to_live.get(old_ref)
                    if live_ref is None:
                        if old_ref in removed_applied:
                            continue
                        raise JobOpsError(
                            "SITE_CHANGED",
                            "A confirmed job-specific answer no longer has one exact live question.",
                        )
                    rebound_fields.append({**item, "control_ref": live_ref})
                if len({str(item["control_ref"]) for item in rebound_fields}) != len(rebound_fields):
                    raise JobOpsError(
                        "DYNAMIC_ANSWER_BINDING_AMBIGUOUS",
                        "Confirmed answers could not be mapped one-to-one onto the live questions.",
                    )
                rebound_bundle = {
                    **answer_bundle,
                    "source_packet_id": updated_packet["packet_id"],
                    "source_packet_hash": updated_packet["content_hash"],
                    "bundle_nonce": secrets.token_hex(32),
                    "fields": rebound_fields,
                    "created_at": iso_utc(),
                }
                rebound_answer_ref = self.onboarding.import_bytes(
                    "application_answer_bundle",
                    canonical_json(rebound_bundle),
                    synthetic=bool(packet_metadata["synthetic"]),
                )
                created.append(rebound_answer_ref)
                rebound_answers_hash = sha256_bytes(canonical_json({
                    "prior_answers_hash": str(rebound_bundle["prior_answers_hash"]),
                    "answer_bundle_content_hash": str(rebound_answer_ref["content_sha256"]),
                    "fields": sorted(
                        [
                            {"control_ref": str(item["control_ref"]), "decision": str(item["decision"])}
                            for item in rebound_fields
                        ],
                        key=lambda item: item["control_ref"],
                    ),
                    "non_form_unknowns": sorted(
                        list(rebound_bundle.get("non_form_unknowns", [])),
                        key=lambda item: str(item.get("unknown_id", "")),
                    ),
                }))

            resolved_refs = {
                str(item["id"]) for item in questions if item.get("status") == "RESOLVED_FOR_APPLICATION"
            }
            unresolved = tuple(
                sorted(set(approval_unresolved_stop_ids(questions)) - resolved_refs)
            )
            old_question_refs = set(old_questions)
            retained_non_form = set(context.mandatory_unknowns) - old_question_refs
            dynamic_unknowns = {
                str(item["id"]) for item in questions
                if item.get("classification") == "unknown_stop"
                and item.get("status") != "RESOLVED_FOR_APPLICATION"
            }
            new_context = replace(
                context,
                answers_hash=rebound_answers_hash,
                form_snapshot_hash=str(live_snapshot["form_snapshot_hash"]),
                review_packet_hash=str(updated_packet["content_hash"]),
                unresolved_stops=unresolved,
                mandatory_unknowns=tuple(sorted(retained_non_form | dynamic_unknowns)),
            ).normalized()
            now = iso_utc()
            version = int(row["packet_version"]) + 1
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """SELECT r.packet_id,r.content_hash,r.status,a.status AS application_status,b.context_hash
                       FROM review_packets r JOIN applications a ON a.application_id=r.application_id
                       JOIN application_bindings b ON b.application_id=r.application_id
                       WHERE r.application_id=? ORDER BY r.packet_version DESC LIMIT 1""",
                    (application_id,),
                ).fetchone()
                if (
                    current is None
                    or str(current["packet_id"]) != str(row["packet_id"])
                    or str(current["content_hash"]) != str(row["content_hash"])
                    or str(current["status"]) != "APPROVED"
                    or str(current["application_status"]) != "APPROVED"
                    or str(current["context_hash"]) != context.context_hash
                ):
                    raise JobOpsError("REVIEW_PACKET_STALE", "The approved packet changed during dynamic form discovery.")
                connection.execute(
                    "UPDATE review_packets SET status='NEEDS_REVISION' WHERE packet_id=? AND status='APPROVED'",
                    (str(row["packet_id"]),),
                )
                connection.execute(
                    """INSERT INTO review_packets(
                       packet_id,application_id,content_hash,relative_path,status,packet_version,
                       supersedes_packet_id,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        updated_packet["packet_id"], application_id, updated_packet["content_hash"],
                        packet_ref["secure_ref"], "AWAITING_APPROVAL", version, row["packet_id"], now,
                    ),
                )
                connection.execute(
                    "UPDATE applications SET answers_hash=?,status='AWAITING_APPROVAL',last_safe_state='AWAITING_APPROVAL',updated_at=? WHERE application_id=?",
                    (new_context.answers_hash, now, application_id),
                )
                connection.execute(
                    "UPDATE application_bindings SET context_hash=?,context_json=?,updated_at=? WHERE application_id=?",
                    (new_context.context_hash, json.dumps(new_context.as_dict(), ensure_ascii=False, sort_keys=True), now, application_id),
                )
                connection.execute(
                    """INSERT INTO materials(material_id,application_id,kind,path,content_hash,claim_ids_json,created_at)
                       VALUES(?,?,?,?,?,'[]',?)""",
                    (
                        stable_id("MAT", application_id, "execution_bundle", str(bundle_ref["content_sha256"])),
                        application_id, "execution_bundle", bundle_ref["secure_ref"], bundle_ref["content_sha256"], now,
                    ),
                )
                for question in questions:
                    field_id = stable_id("FLD", application_id, str(question["id"]))
                    prior_control_ref = live_to_old.get(str(question["id"]), str(question["id"]))
                    prior_field_id = stable_id("FLD", application_id, prior_control_ref)
                    existing = connection.execute(
                        "SELECT status,secure_ref,redacted_summary,field_hash FROM application_fields WHERE field_id=?",
                        (prior_field_id,),
                    ).fetchone()
                    status = (
                        str(existing["status"])
                        if existing is not None and str(existing["status"]) == "RESOLVED_FOR_APPLICATION"
                        else initial_application_field_status(str(question["classification"]), str(question["action"]))
                    )
                    secure_ref = str(existing["secure_ref"]) if existing is not None and existing["secure_ref"] else question.get("secure_ref")
                    if (
                        rebound_answer_ref is not None
                        and existing is not None
                        and str(existing["status"]) == "RESOLVED_FOR_APPLICATION"
                    ):
                        secure_ref = str(rebound_answer_ref["secure_ref"])
                    redacted = (
                        str(existing["redacted_summary"])
                        if existing is not None and str(existing["status"]) == "RESOLVED_FOR_APPLICATION"
                        else str(question.get("redacted_summary") or "UNANSWERED")
                    )
                    field_hash = (
                        str(existing["field_hash"])
                        if existing is not None and str(existing["status"]) == "RESOLVED_FOR_APPLICATION"
                        else sha256_bytes(canonical_json(question))
                    )
                    record = {
                        "field_id": field_id, "application_id": application_id,
                        "classification": str(question["classification"]),
                        "action": "PREFILL" if str(question["action"]).startswith("PREFILL") else "STOP",
                        "status": status, "secure_ref": secure_ref,
                        "redacted_summary": redacted, "field_hash": field_hash,
                    }
                    validate_named("application-field", record, self.schemas)
                    if existing is None or prior_field_id != field_id:
                        if existing is not None:
                            connection.execute(
                                "DELETE FROM application_fields WHERE field_id=? AND application_id=?",
                                (prior_field_id, application_id),
                            )
                        connection.execute(
                            """INSERT INTO application_fields(
                               field_id,application_id,classification,status,secure_ref,redacted_summary,field_hash,created_at
                               ) VALUES(?,?,?,?,?,?,?,?)""",
                            (field_id, application_id, record["classification"], status, secure_ref, redacted, field_hash, now),
                        )
                    else:
                        connection.execute(
                            """UPDATE application_fields SET classification=?,status=?,secure_ref=?,redacted_summary=?,field_hash=?
                               WHERE field_id=? AND application_id=?""",
                            (record["classification"], status, secure_ref, redacted, field_hash, field_id, application_id),
                        )
                connection.execute("UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'", (application_id,))
                connection.execute("UPDATE final_submission_authorizations SET status='INVALIDATED' WHERE application_id=? AND status='AUTHORIZED'", (application_id,))
                connection.execute(
                    "UPDATE external_action_sessions SET status='REVOKED',revoked_at=? WHERE session_id=? AND status='AUTHORIZED'",
                    (now, session_id),
                )
                connection.execute(
                    "UPDATE browser_assist_runs SET status='REVOKED',updated_at=? WHERE assist_id=?",
                    (now, assist_id),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        application_id, "DYNAMIC_FORM_REVIEW_REQUIRED", "APPROVED", "AWAITING_APPROVAL",
                        json.dumps({
                            "packet_id": updated_packet["packet_id"],
                            "packet_version": version,
                            "new_field_count": len(new_refs),
                            "removed_separate_action_count": len(removed_separate),
                            "removed_applied_control_count": len(removed_applied),
                            "form_snapshot_hash": live_snapshot["form_snapshot_hash"],
                        }, sort_keys=True),
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (
                        assist_id, application_id, "DYNAMIC_FIELDS_DISCOVERED",
                        sha256_bytes(canonical_json({
                            "packet_hash": updated_packet["content_hash"],
                            "new_field_count": len(new_refs),
                        })),
                        now,
                    ),
                )
            return {
                "status": "SUPPLEMENTAL_REVIEW_REQUIRED",
                "application_id": application_id,
                "packet_id": updated_packet["packet_id"],
                "packet_version": version,
                "packet_hash": updated_packet["content_hash"],
                "dynamic_field_count": len(new_refs),
                "removed_separate_action_count": len(removed_separate),
                "removed_applied_control_count": len(removed_applied),
                "private_values_emitted": 0,
                "real_external_actions": 0,
                "automatic_retry": False,
                "next_safe_action": "REVIEW_ONLY_NEWLY_REVEALED_FIELDS",
            }
        except Exception:
            cleanup_failure: Exception | None = None
            for reference in reversed(created):
                if reference.get("deduplicated") is True:
                    continue
                try:
                    self.onboarding.delete(str(reference["secure_ref"]), user_confirmed=True)
                except Exception as exc:  # pragma: no cover - catastrophic local storage failure
                    cleanup_failure = exc
            if cleanup_failure is not None:
                raise JobOpsError(
                    "DYNAMIC_REVIEW_ROLLBACK_FAILED",
                    "A failed dynamic form review could not remove all new encrypted artifacts.",
                ) from cleanup_failure
            raise
