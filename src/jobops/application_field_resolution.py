from __future__ import annotations

import json
import re
import secrets
from copy import deepcopy
from dataclasses import replace
from typing import Any, Iterable

from .application_execution import validate_application_execution_plan_integrity
from .application_materials import APPLICATION_NARRATIVE_CLASSIFICATION, narrative_effective_max_characters
from .application_narrative import decrypted_application_narrative
from .approvals import ApprovalContext
from .db import JobOpsDB
from .errors import JobOpsError
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


MAX_RESOLUTION_FIELDS = 100
MAX_RESOLUTION_VALUE_CHARACTERS = 4_000
MAX_APPLICATION_ANSWER_BUNDLE_BYTES = 512 * 1024
MAX_REVIEW_PACKET_BYTES = 2 * 1024 * 1024

ANSWERABLE_STOP_CLASSES = frozenset({
    "private_fixed",
    "sensitive_review",
    "work_authorization_stop",
    "compensation_stop",
    "legal_declaration_stop",
    "signature_stop",
    "voluntary_disclosure_stop",
    "unknown_stop",
    APPLICATION_NARRATIVE_CLASSIFICATION,
})

SEPARATE_ACTION_STOP_CLASSES = frozenset({
    "account_creation_stop",
    "file_upload_stop",
    "navigation_control_stop",
    "final_submit_stop",
})

RESOLUTION_DECISIONS = frozenset({
    "CONFIRMED_VALUE",
    "PREFER_NOT_TO_ANSWER",
    "NOT_APPLICABLE",
})
GENERATED_NARRATIVE_DECISION = "USE_GENERATED_NARRATIVE"
NON_FORM_RESOLUTION_DECISION = "ACKNOWLEDGED_UNKNOWN"

_PLACEHOLDER_OPTIONS = frozenset({
    "", "select", "select one", "choose", "choose one", "please select",
    "请选择", "请选择一项", "--", "-",
})


def initial_application_field_status(classification: str, action: str) -> str:
    if str(action).startswith("PREFILL"):
        return "READY"
    if classification in SEPARATE_ACTION_STOP_CLASSES:
        return "SEPARATE_ACTION_GATED"
    return "STOP_REQUIRED"


def approval_unresolved_stop_ids(fields: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(item["id"])
        for item in fields
        if item.get("action") == "STOP"
        and item.get("classification") in ANSWERABLE_STOP_CLASSES
    )


def _safe_control_ref(value: object) -> str:
    control_ref = str(value or "").strip()
    if not re.fullmatch(r"(?:CTL-[A-F0-9]{12}|[A-Za-z][A-Za-z0-9_.:-]{0,199})", control_ref):
        raise JobOpsError(
            "APPLICATION_FIELD_REFERENCE_INVALID",
            "A job-specific answer targets an invalid form control.",
        )
    return control_ref


def _safe_display(value: object, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    return text[:limit]


def _question_map(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    questions = packet.get("form_questions")
    if not isinstance(questions, list) or len(questions) > 500:
        raise JobOpsError("REVIEW_PACKET_INVALID", "The review packet has an invalid form-question list.")
    output: dict[str, dict[str, Any]] = {}
    for item in questions:
        if not isinstance(item, dict):
            raise JobOpsError("REVIEW_PACKET_INVALID", "A review-packet form question is invalid.")
        control_ref = _safe_control_ref(item.get("id"))
        if control_ref in output:
            raise JobOpsError("REVIEW_PACKET_INVALID", "The review packet repeats one form control.")
        output[control_ref] = item
    return output


def _is_cover_letter_textarea(question: dict[str, Any]) -> bool:
    return (
        str(question.get("classification", "")) == APPLICATION_NARRATIVE_CLASSIFICATION
        and
        str(question.get("answer_key", "")).casefold() == "cover_letter"
        and str(question.get("control_type", "")).casefold() == "textarea"
    )


def _review_bound_narrative_hash(packet: dict[str, Any]) -> str | None:
    cover = packet.get("material_plan", {}).get("cover_letter", {})
    value = cover.get("narrative_sha256")
    if value is None:
        return None
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(value)):
        raise JobOpsError(
            "APPLICATION_NARRATIVE_BINDING_INVALID",
            "The review packet has an invalid generated narrative binding.",
        )
    return str(value)


def _required_review_bound_narrative_hash(packet: dict[str, Any]) -> str:
    value = _review_bound_narrative_hash(packet)
    if value is None:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_MISSING",
            "This review packet has no generated narrative bound to the Cover Letter textarea.",
        )
    return value


def _review_bound_narrative_target(
    packet: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    *,
    required: bool,
) -> tuple[str, int] | None:
    cover = packet.get("material_plan", {}).get("cover_letter", {})
    if not isinstance(cover, dict):
        raise JobOpsError("APPLICATION_NARRATIVE_BINDING_INVALID", "The review packet has an invalid Cover Letter binding.")
    targets = sorted(control_ref for control_ref, item in questions.items() if _is_cover_letter_textarea(item))
    status = str(cover.get("narrative_target_status") or "")
    target_count = cover.get("narrative_target_count")
    target_ref = cover.get("narrative_control_ref")
    target_max = cover.get("narrative_max_characters")
    if target_count != len(targets):
        raise JobOpsError(
            "APPLICATION_NARRATIVE_BINDING_INVALID",
            "The review packet narrative target count no longer matches its application controls.",
        )
    if len(targets) != 1 or status not in {"BOUND_EXACT_CONTROL", "INVALID_MAX_LENGTH"}:
        if required:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_FIELD_INVALID",
                "A generated narrative requires exactly one current Cover Letter textarea.",
            )
        return None
    bound_ref = _safe_control_ref(target_ref)
    if bound_ref != targets[0]:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_BINDING_INVALID",
            "The generated narrative is not bound to the current Cover Letter textarea.",
        )
    effective_max = narrative_effective_max_characters(questions[bound_ref])
    if effective_max is None:
        if status != "INVALID_MAX_LENGTH" or target_max is not None:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_BINDING_INVALID",
                "The generated narrative length binding is inconsistent with the current textarea.",
            )
        if required:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_MAX_LENGTH_INVALID",
                "The current Cover Letter textarea has an invalid or zero maximum length.",
            )
        return None
    if status != "BOUND_EXACT_CONTROL" or target_max != effective_max:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_BINDING_INVALID",
            "The generated narrative character limit is not bound to the current textarea.",
        )
    character_count = cover.get("narrative_character_count")
    if isinstance(character_count, bool) or not isinstance(character_count, int) or character_count < 1:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_BINDING_INVALID",
            "The generated narrative character count is missing or invalid.",
        )
    if character_count > effective_max:
        if required:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_TOO_LONG",
                "The generated narrative exceeds the current Cover Letter textarea limit.",
                maximum=effective_max,
            )
        return None
    return bound_ref, effective_max


def field_resolution_summary(
    packet: dict[str, Any],
    context: ApprovalContext,
    *,
    generated_narrative_available: bool = False,
) -> dict[str, Any]:
    questions = _question_map(packet)
    narrative_target = _review_bound_narrative_target(packet, questions, required=False)
    unresolved = set(context.normalized().unresolved_stops)
    answerable: list[dict[str, Any]] = []
    separate_action_count = 0
    resolved_count = 0
    for control_ref, item in questions.items():
        classification = str(item.get("classification", ""))
        status = str(item.get("status", ""))
        if classification in SEPARATE_ACTION_STOP_CLASSES:
            separate_action_count += 1
        if status == "RESOLVED_FOR_APPLICATION":
            resolved_count += 1
        if control_ref not in unresolved or classification not in ANSWERABLE_STOP_CLASSES:
            continue
        options = [
            _safe_display(option, limit=200)
            for option in item.get("options", [])
            if _safe_display(option, limit=200).casefold() not in _PLACEHOLDER_OPTIONS
        ]
        allowed_decisions = [
            "CONFIRMED_VALUE",
            *([
                GENERATED_NARRATIVE_DECISION
            ] if generated_narrative_available and narrative_target is not None and narrative_target[0] == control_ref else []),
            *([] if bool(item.get("required", False)) else ["PREFER_NOT_TO_ANSWER", "NOT_APPLICABLE"]),
        ]
        answerable.append({
            "control_ref": control_ref,
            "label": _safe_display(item.get("label") or item.get("answer_key") or control_ref),
            "answer_key": str(item.get("answer_key") or "UNKNOWN"),
            "classification": classification,
            "control_type": str(item.get("control_type") or "other"),
            "required": bool(item.get("required", False)),
            "options": options,
            "allowed_decisions": allowed_decisions,
            "generated_narrative_available": GENERATED_NARRATIVE_DECISION in allowed_decisions,
            "max_characters": (
                narrative_effective_max_characters(item) or MAX_RESOLUTION_VALUE_CHARACTERS
                if str(item.get("control_type", "")).casefold() == "textarea"
                else MAX_RESOLUTION_VALUE_CHARACTERS
            ),
        })
    non_form_unknowns = sorted(
        item for item in context.normalized().mandatory_unknowns if item not in questions
    )
    return {
        "status": "READY_FOR_PACKET_APPROVAL" if not answerable and not non_form_unknowns else "NEEDS_JOB_SPECIFIC_CONFIRMATION",
        "unresolved_count": len(answerable),
        "resolved_count": resolved_count,
        "separate_action_gate_count": separate_action_count,
        "unresolved_fields": answerable,
        "remaining_non_form_unknowns": non_form_unknowns,
        "private_values_emitted": 0,
        "real_external_actions": 0,
    }


class ApplicationFieldResolutionManager:
    """Encrypt job-specific answers and rebind the exact review packet.

    Values exist only in the local request and one DPAPI-protected bundle.  SQLite
    receives an opaque secure reference, randomized bundle hash, and redacted state.
    """

    def __init__(self, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.database = database
        self.onboarding = onboarding
        self.schemas = project_root() / "schemas"

    def _load_current(
        self,
        application_id: str,
        expected_packet_hash: str,
    ) -> tuple[dict[str, Any], ApprovalContext, dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.packet_id,r.packet_version,r.content_hash,r.relative_path,r.status,
                          a.status AS application_status,b.context_hash,b.context_json
                   FROM review_packets r
                   JOIN applications a ON a.application_id=r.application_id
                   JOIN application_bindings b ON b.application_id=r.application_id
                   WHERE r.application_id=?
                   ORDER BY r.packet_version DESC LIMIT 1""",
                (application_id,),
            ).fetchone()
        if row is None:
            raise JobOpsError("REVIEW_PACKET_NOT_FOUND", "The selected review packet does not exist.")
        if row["application_status"] != "AWAITING_APPROVAL" or row["status"] != "AWAITING_APPROVAL":
            raise JobOpsError(
                "APPLICATION_NOT_AWAITING_APPROVAL",
                "Job-specific answers can be changed only before the current packet is approved.",
            )
        if str(row["content_hash"]) != expected_packet_hash:
            raise JobOpsError(
                "REVIEW_PACKET_STALE",
                "The review packet changed after it was displayed. Review the current packet again.",
            )
        raw = self.onboarding.read_bytes(str(row["relative_path"]))
        if not raw or len(raw) > MAX_REVIEW_PACKET_BYTES:
            raise JobOpsError("REVIEW_PACKET_SIZE_INVALID", "The encrypted review packet exceeds the local display limit.")
        try:
            packet = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet is not valid JSON.") from exc
        if not isinstance(packet, dict):
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet must be an object.")
        validate_named("review-packet", packet, self.schemas)
        validate_application_execution_plan_integrity(packet["execution_plan"])
        if (
            packet.get("application_id") != application_id
            or packet.get("packet_id") != row["packet_id"]
            or packet.get("content_hash") != expected_packet_hash
        ):
            raise JobOpsError("REVIEW_PACKET_BINDING_INVALID", "The review packet binding is invalid.")
        context = ApprovalContext.from_dict(json.loads(str(row["context_json"])))
        if context.context_hash != row["context_hash"] or context.review_packet_hash != expected_packet_hash:
            raise JobOpsError("APPLICATION_BINDING_MISSING", "The current approval binding is inconsistent.")
        return packet, context, dict(row)

    def preview_generated_narrative(
        self,
        *,
        application_id: str,
        expected_packet_hash: str,
        control_ref: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"APP-[A-F0-9]{12}", application_id):
            raise JobOpsError("APPLICATION_ID_INVALID", "The selected application identifier is invalid.")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_packet_hash):
            raise JobOpsError("REVIEW_PACKET_HASH_INVALID", "The selected review packet hash is invalid.")
        target = _safe_control_ref(control_ref)
        packet, context, _ = self._load_current(application_id, expected_packet_hash)
        questions = _question_map(packet)
        narrative_target = _review_bound_narrative_target(packet, questions, required=True)
        question = questions.get(target)
        if (
            question is None
            or narrative_target is None
            or narrative_target[0] != target
            or target not in set(context.unresolved_stops)
            or str(question.get("classification", "")) not in ANSWERABLE_STOP_CLASSES
            or not _is_cover_letter_textarea(question)
        ):
            raise JobOpsError(
                "APPLICATION_NARRATIVE_FIELD_INVALID",
                "A generated narrative may be previewed only for this application's current Cover Letter textarea.",
            )
        with decrypted_application_narrative(
            self.database,
            self.onboarding,
            application_id,
            expected_content_hash=_required_review_bound_narrative_hash(packet),
        ) as narrative:
            if len(narrative.text) > narrative_target[1]:
                raise JobOpsError(
                    "APPLICATION_NARRATIVE_TOO_LONG",
                    "The generated narrative exceeds the current Cover Letter textarea limit.",
                    maximum=narrative_target[1],
                )
            return {
                "status": "APPLICATION_NARRATIVE_PREVIEW_READY",
                "application_id": application_id,
                "control_ref": target,
                "source_content_hash": narrative.content_hash,
                "narrative": narrative.text,
                "max_characters": narrative_target[1],
                "private_transport": "LOCAL_SESSION_ONLY",
                "private_values_persisted_to_project": 0,
                "real_external_actions": 0,
            }

    @staticmethod
    def _normalize_resolutions(
        raw_resolutions: object,
        questions: dict[str, dict[str, Any]],
        required_refs: set[str],
    ) -> list[dict[str, Any]]:
        if raw_resolutions is None and not required_refs:
            return []
        if not isinstance(raw_resolutions, list) or len(raw_resolutions) > MAX_RESOLUTION_FIELDS:
            raise JobOpsError(
                "APPLICATION_FIELD_RESOLUTIONS_INCOMPLETE",
                "Confirm every highlighted job-specific question together.",
            )
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_resolutions:
            if not isinstance(raw, dict) or set(raw) - {"control_ref", "decision", "value"}:
                raise JobOpsError("APPLICATION_FIELD_RESOLUTION_INVALID", "A job-specific answer is invalid.")
            control_ref = _safe_control_ref(raw.get("control_ref"))
            if control_ref in seen or control_ref not in required_refs:
                raise JobOpsError("APPLICATION_FIELD_RESOLUTION_INVALID", "A job-specific answer is repeated or not currently requested.")
            seen.add(control_ref)
            question = questions[control_ref]
            classification = str(question.get("classification", ""))
            if classification not in ANSWERABLE_STOP_CLASSES:
                raise JobOpsError("APPLICATION_FIELD_RESOLUTION_INVALID", "A separately gated action cannot be answered as a form value.")
            decision = str(raw.get("decision", "")).strip().upper()
            if decision not in RESOLUTION_DECISIONS:
                raise JobOpsError("APPLICATION_FIELD_RESOLUTION_INVALID", "Choose a supported job-specific answer decision.")
            required = bool(question.get("required", False))
            if required and decision != "CONFIRMED_VALUE":
                raise JobOpsError("APPLICATION_FIELD_VALUE_REQUIRED", "A required application question needs one explicit answer.")
            value: str | None = None
            if decision == "CONFIRMED_VALUE":
                candidate = raw.get("value")
                if not isinstance(candidate, str):
                    raise JobOpsError("APPLICATION_FIELD_VALUE_REQUIRED", "A confirmed application answer must be text.")
                value = candidate.strip()
                if (
                    not value
                    or len(value) > MAX_RESOLUTION_VALUE_CHARACTERS
                    or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
                ):
                    raise JobOpsError("APPLICATION_FIELD_VALUE_INVALID", "A confirmed application answer is empty or too large.")
                options = [
                    _safe_display(option, limit=200)
                    for option in question.get("options", [])
                    if _safe_display(option, limit=200).casefold() not in _PLACEHOLDER_OPTIONS
                ]
                if options:
                    matched = next((option for option in options if option.casefold() == value.casefold()), None)
                    if matched is None:
                        raise JobOpsError("APPLICATION_FIELD_OPTION_INVALID", "Choose one of the options shown in the current application form.")
                    value = matched
            normalized.append({
                "control_ref": control_ref,
                "answer_key": str(question.get("answer_key") or "UNKNOWN"),
                "classification": classification,
                "decision": decision,
                "value": value,
            })
        if seen != required_refs:
            raise JobOpsError(
                "APPLICATION_FIELD_RESOLUTIONS_INCOMPLETE",
                "Confirm every highlighted job-specific question together.",
            )
        return sorted(normalized, key=lambda item: item["control_ref"])

    @staticmethod
    def _normalize_non_form_resolutions(
        raw_resolutions: object,
        required_unknowns: set[str],
    ) -> list[dict[str, str]]:
        if raw_resolutions is None and not required_unknowns:
            return []
        if not isinstance(raw_resolutions, list) or len(raw_resolutions) > MAX_RESOLUTION_FIELDS:
            raise JobOpsError(
                "APPLICATION_NON_FORM_RESOLUTIONS_INCOMPLETE",
                "Acknowledge every highlighted non-form uncertainty together.",
            )
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in raw_resolutions:
            if not isinstance(raw, dict) or set(raw) != {"unknown_id", "decision"}:
                raise JobOpsError("APPLICATION_NON_FORM_RESOLUTION_INVALID", "A non-form uncertainty decision is invalid.")
            unknown_id = str(raw.get("unknown_id", "")).strip()
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,199}", unknown_id)
                or unknown_id in seen
                or unknown_id not in required_unknowns
            ):
                raise JobOpsError("APPLICATION_NON_FORM_RESOLUTION_INVALID", "A non-form uncertainty is repeated or no longer current.")
            if str(raw.get("decision", "")).strip().upper() != NON_FORM_RESOLUTION_DECISION:
                raise JobOpsError(
                    "APPLICATION_NON_FORM_RESOLUTION_INVALID",
                    "A non-form uncertainty may only be acknowledged; it cannot be converted into a fact.",
                )
            seen.add(unknown_id)
            normalized.append({"unknown_id": unknown_id, "decision": NON_FORM_RESOLUTION_DECISION})
        if seen != required_unknowns:
            raise JobOpsError(
                "APPLICATION_NON_FORM_RESOLUTIONS_INCOMPLETE",
                "Acknowledge every highlighted non-form uncertainty together.",
            )
        return sorted(normalized, key=lambda item: item["unknown_id"])

    @staticmethod
    def _mark_packet_fields(
        packet: dict[str, Any],
        resolved_refs: set[str],
    ) -> None:
        for collection_name in ("form_questions", "sensitive_fields"):
            collection = packet.get(collection_name, [])
            for item in collection:
                if not isinstance(item, dict):
                    continue
                classification = str(item.get("classification", ""))
                control_ref = str(item.get("id", ""))
                if control_ref in resolved_refs:
                    item["status"] = "RESOLVED_FOR_APPLICATION"
                    item["redacted_summary"] = "ENCRYPTED_JOB_SPECIFIC_CONFIRMATION"
                elif classification in SEPARATE_ACTION_STOP_CLASSES:
                    item["status"] = "SEPARATE_ACTION_GATED"
                    item["redacted_summary"] = "NO_FORM_ANSWER_REQUIRED"

    @staticmethod
    def _clear_plaintext_inputs(raw_resolutions: object) -> None:
        if isinstance(raw_resolutions, list):
            for item in raw_resolutions:
                if isinstance(item, dict) and "value" in item:
                    item["value"] = ""

    def _existing_answer_bundle(
        self,
        application_id: str,
        context: ApprovalContext,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT secure_ref FROM application_fields
                   WHERE application_id=? AND status='RESOLVED_FOR_APPLICATION' AND secure_ref IS NOT NULL""",
                (application_id,),
            ).fetchall()
        references = [str(row["secure_ref"]) for row in rows]
        if not references:
            return [], []
        if len(references) != 1:
            raise JobOpsError(
                "APPLICATION_ANSWER_BUNDLE_COUNT_INVALID",
                "The current application must have one complete job-specific answer bundle.",
            )
        metadata = self.onboarding.reference_metadata(references[0])
        if metadata["kind"] != "application_answer_bundle" or metadata["status"] != "ACTIVE":
            raise JobOpsError(
                "APPLICATION_ANSWER_BUNDLE_INVALID",
                "The current encrypted job-specific answers are unavailable.",
            )
        raw = bytearray(self.onboarding.read_bytes(references[0]))
        try:
            bundle = json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError(
                "APPLICATION_ANSWER_BUNDLE_INVALID",
                "The current encrypted job-specific answers are invalid.",
            ) from exc
        finally:
            raw[:] = b"\0" * len(raw)
        if (
            not isinstance(bundle, dict)
            or bundle.get("application_id") != application_id
            or not isinstance(bundle.get("fields"), list)
            or not isinstance(bundle.get("non_form_unknowns", []), list)
        ):
            raise JobOpsError(
                "APPLICATION_ANSWER_BUNDLE_INVALID",
                "The current encrypted job-specific answers are invalid.",
            )
        fields = [deepcopy(item) for item in bundle["fields"] if isinstance(item, dict)]
        non_form = [deepcopy(item) for item in bundle.get("non_form_unknowns", []) if isinstance(item, dict)]
        if len(fields) != len(bundle["fields"]) or len(non_form) != len(bundle.get("non_form_unknowns", [])):
            raise JobOpsError("APPLICATION_ANSWER_BUNDLE_INVALID", "A stored job-specific answer is invalid.")
        expected_hash = sha256_bytes(canonical_json({
            "prior_answers_hash": str(bundle.get("prior_answers_hash", "")),
            "answer_bundle_content_hash": str(metadata["content_sha256"]),
            "fields": sorted(
                [
                    {"control_ref": str(item.get("control_ref", "")), "decision": str(item.get("decision", ""))}
                    for item in fields
                ],
                key=lambda item: item["control_ref"],
            ),
            "non_form_unknowns": sorted(non_form, key=lambda item: str(item.get("unknown_id", ""))),
        }))
        if expected_hash != context.answers_hash:
            raise JobOpsError(
                "APPLICATION_ANSWER_BINDING_CHANGED",
                "The current encrypted job-specific answers differ from the application context.",
            )
        return fields, non_form

    def resolve(
        self,
        *,
        application_id: str,
        expected_packet_hash: str,
        raw_resolutions: object,
        user_confirmed: bool,
        raw_non_form_resolutions: object = None,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Saving job-specific answers requires explicit confirmation.")
        if not re.fullmatch(r"APP-[A-F0-9]{12}", application_id):
            raise JobOpsError("APPLICATION_ID_INVALID", "The selected application identifier is invalid.")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_packet_hash):
            raise JobOpsError("REVIEW_PACKET_HASH_INVALID", "The selected review packet hash is invalid.")

        created_references: list[dict[str, Any]] = []
        prepared_resolutions: object = None
        try:
            prepared_resolutions = deepcopy(raw_resolutions)
            packet, old_context, row = self._load_current(application_id, expected_packet_hash)
            questions = _question_map(packet)
            unresolved = set(old_context.unresolved_stops)
            required_refs = {
                control_ref for control_ref in unresolved
                if control_ref in questions
                and str(questions[control_ref].get("classification", "")) in ANSWERABLE_STOP_CLASSES
            }
            generated_sources: dict[str, str] = {}
            if isinstance(prepared_resolutions, list):
                for raw in prepared_resolutions:
                    if not isinstance(raw, dict):
                        continue
                    if str(raw.get("decision", "")).strip().upper() != GENERATED_NARRATIVE_DECISION:
                        continue
                    supplied_value = raw.get("value")
                    if set(raw) - {"control_ref", "decision", "value"} or not (
                        supplied_value is None or supplied_value == ""
                    ):
                        raise JobOpsError(
                            "APPLICATION_FIELD_RESOLUTION_INVALID",
                            "The generated narrative decision cannot include a browser-supplied value.",
                        )
                    control_ref = _safe_control_ref(raw.get("control_ref"))
                    question = questions.get(control_ref)
                    narrative_target = _review_bound_narrative_target(packet, questions, required=True)
                    if (
                        control_ref not in required_refs
                        or question is None
                        or narrative_target is None
                        or narrative_target[0] != control_ref
                        or not _is_cover_letter_textarea(question)
                    ):
                        raise JobOpsError(
                            "APPLICATION_NARRATIVE_FIELD_INVALID",
                            "A generated narrative may be used only for this application's current Cover Letter textarea.",
                        )
                    with decrypted_application_narrative(
                        self.database,
                        self.onboarding,
                        application_id,
                        expected_content_hash=_required_review_bound_narrative_hash(packet),
                    ) as narrative:
                        if len(narrative.text) > narrative_target[1]:
                            raise JobOpsError(
                                "APPLICATION_NARRATIVE_TOO_LONG",
                                "The generated narrative exceeds the current Cover Letter textarea limit.",
                                maximum=narrative_target[1],
                            )
                        raw["decision"] = "CONFIRMED_VALUE"
                        raw["value"] = narrative.text
                        generated_sources[control_ref] = narrative.content_hash
            resolutions = self._normalize_resolutions(prepared_resolutions, questions, required_refs)
            for item in resolutions:
                source_hash = generated_sources.get(str(item["control_ref"]))
                if source_hash:
                    item["value_origin"] = "APPLICATION_NARRATIVE"
                    item["source_content_hash"] = source_hash
            required_non_form_unknowns = set(old_context.mandatory_unknowns) - set(questions)
            non_form_resolutions = self._normalize_non_form_resolutions(
                raw_non_form_resolutions, required_non_form_unknowns,
            )
            if not resolutions and not non_form_resolutions:
                raise JobOpsError(
                    "APPLICATION_FIELD_RESOLUTIONS_INCOMPLETE",
                    "There are no current job-specific questions or uncertainties to confirm.",
                )
            existing_fields, existing_non_form = self._existing_answer_bundle(application_id, old_context)
            existing_refs: set[str] = set()
            for item in existing_fields:
                control_ref = _safe_control_ref(item.get("control_ref"))
                question = questions.get(control_ref)
                if (
                    control_ref in existing_refs
                    or question is None
                    or str(question.get("status", "")) != "RESOLVED_FOR_APPLICATION"
                    or str(question.get("classification", "")) != str(item.get("classification", ""))
                    or str(question.get("answer_key", "")) != str(item.get("answer_key", ""))
                ):
                    raise JobOpsError(
                        "APPLICATION_ANSWER_FORM_BINDING_CHANGED",
                        "A prior confirmed answer no longer matches the current reviewed form.",
                    )
                existing_refs.add(control_ref)
            new_refs = {str(item["control_ref"]) for item in resolutions}
            if existing_refs & new_refs:
                raise JobOpsError(
                    "APPLICATION_FIELD_RESOLUTION_INVALID",
                    "A previously confirmed field was submitted as a new answer.",
                )
            combined_fields = sorted(
                [*existing_fields, *resolutions], key=lambda item: str(item["control_ref"]),
            )
            existing_non_form_ids = {str(item.get("unknown_id", "")) for item in existing_non_form}
            if existing_non_form_ids & {str(item["unknown_id"]) for item in non_form_resolutions}:
                raise JobOpsError(
                    "APPLICATION_NON_FORM_RESOLUTION_INVALID",
                    "A previously acknowledged uncertainty was submitted again.",
                )
            combined_non_form = sorted(
                [*existing_non_form, *non_form_resolutions], key=lambda item: str(item["unknown_id"]),
            )
            resolved_refs = {str(item["control_ref"]) for item in resolutions}
            created_at = iso_utc()
            old_packet_metadata = self.onboarding.reference_metadata(str(row["relative_path"]))
            if old_packet_metadata["kind"] != "review_packet" or old_packet_metadata["status"] != "ACTIVE":
                raise JobOpsError("REVIEW_PACKET_BINDING_INVALID", "The current encrypted review packet is unavailable.")
            bundle_ref: dict[str, Any] | None = None
            if combined_fields:
                private_bundle = {
                    "schema_version": 1,
                    "application_id": application_id,
                    "source_packet_id": str(row["packet_id"]),
                    "source_packet_hash": expected_packet_hash,
                    "prior_answers_hash": old_context.answers_hash,
                    "bundle_nonce": secrets.token_hex(32),
                    "fields": combined_fields,
                    "non_form_unknowns": combined_non_form,
                    "created_at": created_at,
                }
                raw_bundle = bytearray(canonical_json(private_bundle))
                try:
                    if len(raw_bundle) > MAX_APPLICATION_ANSWER_BUNDLE_BYTES:
                        raise JobOpsError("APPLICATION_ANSWER_BUNDLE_TOO_LARGE", "The encrypted job-specific answer bundle is too large.")
                    bundle_ref = self.onboarding.import_bytes(
                        "application_answer_bundle",
                        bytes(raw_bundle),
                        synthetic=bool(old_packet_metadata["synthetic"]),
                    )
                    created_references.append(bundle_ref)
                finally:
                    raw_bundle[:] = b"\0" * len(raw_bundle)
                    private_bundle.clear()
                for item in combined_fields:
                    item["value"] = None

            answer_bundle_content_hash = (
                str(bundle_ref["content_sha256"])
                if bundle_ref is not None
                else sha256_bytes(canonical_json({
                    "application_id": application_id,
                    "source_packet_hash": expected_packet_hash,
                    "non_form_unknowns": non_form_resolutions,
                }))
            )

            updated_packet = deepcopy(packet)
            self._mark_packet_fields(updated_packet, resolved_refs)
            updated_packet["status"] = "AWAITING_APPROVAL"
            updated_packet["packet_id"] = stable_id(
                "RPK",
                application_id,
                str(row["packet_id"]),
                answer_bundle_content_hash,
            )
            updated_packet.pop("content_hash", None)
            updated_packet["content_hash"] = sha256_bytes(canonical_json(updated_packet))
            validate_named("review-packet", updated_packet, self.schemas)
            validate_application_execution_plan_integrity(updated_packet["execution_plan"])
            raw_packet = canonical_json(updated_packet)
            if len(raw_packet) > MAX_REVIEW_PACKET_BYTES:
                raise JobOpsError("REVIEW_PACKET_SIZE_INVALID", "The revised review packet exceeds the local display limit.")
            packet_ref = self.onboarding.import_bytes(
                "review_packet",
                raw_packet,
                synthetic=bool(old_packet_metadata["synthetic"]),
            )
            created_references.append(packet_ref)

            separate_refs = {
                control_ref for control_ref, question in questions.items()
                if str(question.get("classification", "")) in SEPARATE_ACTION_STOP_CLASSES
            }
            remaining_stops = tuple(sorted(unresolved - resolved_refs - separate_refs))
            remaining_unknowns = set(old_context.mandatory_unknowns) - resolved_refs
            remaining_unknowns -= {item["unknown_id"] for item in non_form_resolutions}
            if any(
                item["answer_key"] == "work_authorization"
                and item["decision"] == "CONFIRMED_VALUE"
                for item in resolutions
            ):
                remaining_unknowns.discard("candidate_work_authorization")
            answer_binding_hash = sha256_bytes(canonical_json({
                "prior_answers_hash": old_context.answers_hash,
                "answer_bundle_content_hash": answer_bundle_content_hash,
                "fields": [
                    {"control_ref": item["control_ref"], "decision": item["decision"]}
                    for item in combined_fields
                ],
                "non_form_unknowns": combined_non_form,
            }))
            new_context = replace(
                old_context,
                answers_hash=answer_binding_hash,
                review_packet_hash=str(updated_packet["content_hash"]),
                unresolved_stops=remaining_stops,
                mandatory_unknowns=tuple(sorted(remaining_unknowns)),
            ).normalized()

            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """SELECT r.packet_id,r.packet_version,r.content_hash,r.status,
                              a.status AS application_status,b.context_hash
                       FROM review_packets r
                       JOIN applications a ON a.application_id=r.application_id
                       JOIN application_bindings b ON b.application_id=r.application_id
                       WHERE r.application_id=? ORDER BY r.packet_version DESC LIMIT 1""",
                    (application_id,),
                ).fetchone()
                if (
                    current is None
                    or current["packet_id"] != row["packet_id"]
                    or current["content_hash"] != expected_packet_hash
                    or current["status"] != "AWAITING_APPROVAL"
                    or current["application_status"] != "AWAITING_APPROVAL"
                    or current["context_hash"] != old_context.context_hash
                ):
                    raise JobOpsError("REVIEW_PACKET_STALE", "The review packet changed before the answers were saved.")
                version = int(current["packet_version"]) + 1
                connection.execute(
                    "UPDATE review_packets SET status='NEEDS_REVISION' WHERE packet_id=? AND status='AWAITING_APPROVAL'",
                    (str(row["packet_id"]),),
                )
                connection.execute(
                    """INSERT INTO review_packets(
                       packet_id,application_id,content_hash,relative_path,status,packet_version,
                       supersedes_packet_id,created_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        updated_packet["packet_id"], application_id, updated_packet["content_hash"],
                        packet_ref["secure_ref"], "AWAITING_APPROVAL", version,
                        row["packet_id"], created_at,
                    ),
                )
                connection.execute(
                    "UPDATE applications SET answers_hash=?,status='AWAITING_APPROVAL',last_safe_state='AWAITING_APPROVAL',updated_at=? WHERE application_id=?",
                    (new_context.answers_hash, created_at, application_id),
                )
                connection.execute(
                    "UPDATE application_bindings SET context_hash=?,context_json=?,updated_at=? WHERE application_id=?",
                    (
                        new_context.context_hash,
                        json.dumps(new_context.as_dict(), ensure_ascii=False, sort_keys=True),
                        created_at,
                        application_id,
                    ),
                )
                # Every resolved field must point at the newly composed bundle.  Keeping
                # prior fields on their superseded secure-ref would leave two active
                # answer bundles and make the ephemeral browser payload ambiguous.
                for item in combined_fields:
                    field_id = stable_id("FLD", application_id, str(item["control_ref"]))
                    field = connection.execute(
                        "SELECT classification,field_hash FROM application_fields WHERE field_id=? AND application_id=?",
                        (field_id, application_id),
                    ).fetchone()
                    if field is None or str(field["classification"]) != item["classification"]:
                        raise JobOpsError("APPLICATION_FIELD_BINDING_INVALID", "A job-specific field changed before it was saved.")
                    revised_field_hash = sha256_bytes(canonical_json({
                        "prior_field_hash": str(field["field_hash"]),
                        "answer_bundle_content_hash": answer_bundle_content_hash,
                        "control_ref": item["control_ref"],
                        "decision": item["decision"],
                    }))
                    record = {
                        "field_id": field_id,
                        "application_id": application_id,
                        "classification": item["classification"],
                        "action": "STOP",
                        "status": "RESOLVED_FOR_APPLICATION",
                        "secure_ref": bundle_ref["secure_ref"] if bundle_ref is not None else None,
                        "redacted_summary": "ENCRYPTED_JOB_SPECIFIC_CONFIRMATION",
                        "field_hash": revised_field_hash,
                    }
                    validate_named("application-field", record, self.schemas)
                    connection.execute(
                        """UPDATE application_fields SET status=?,secure_ref=?,redacted_summary=?,field_hash=?
                           WHERE field_id=? AND application_id=?""",
                        (
                            record["status"], record["secure_ref"], record["redacted_summary"],
                            record["field_hash"], field_id, application_id,
                        ),
                    )
                for control_ref in separate_refs:
                    connection.execute(
                        """UPDATE application_fields SET status='SEPARATE_ACTION_GATED',
                           redacted_summary='NO_FORM_ANSWER_REQUIRED'
                           WHERE field_id=? AND application_id=?""",
                        (stable_id("FLD", application_id, control_ref), application_id),
                    )
                connection.execute(
                    "UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'",
                    (application_id,),
                )
                connection.execute(
                    "UPDATE final_submission_authorizations SET status='INVALIDATED' WHERE application_id=? AND status='AUTHORIZED'",
                    (application_id,),
                )
                connection.execute(
                    "UPDATE external_action_sessions SET status='INVALIDATED' WHERE application_id=? AND status='AUTHORIZED'",
                    (application_id,),
                )
                connection.execute(
                    """UPDATE application_execution_runs SET status='INVALIDATED',updated_at=?
                       WHERE application_id=? AND status IN ('AWAITING_FINAL_AUTHORIZATION','SUBMISSION_STARTED','SUBMITTED')""",
                    (created_at, application_id),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        application_id,
                        "APPLICATION_FIELDS_REBOUND",
                        "AWAITING_APPROVAL",
                        "AWAITING_APPROVAL",
                        json.dumps({
                            "packet_id": updated_packet["packet_id"],
                            "packet_version": version,
                            "field_count": len(resolutions),
                            "acknowledged_non_form_unknown_count": len(non_form_resolutions),
                            "answer_binding_hash": answer_binding_hash,
                        }),
                        created_at,
                    ),
                )

            return {
                "status": "JOB_SPECIFIC_ANSWERS_ENCRYPTED",
                "application_id": application_id,
                "packet_id": updated_packet["packet_id"],
                "packet_version": version,
                "packet_hash": updated_packet["content_hash"],
                "resolved_count": len(resolutions) + len(non_form_resolutions),
                "remaining_unresolved_count": len(new_context.unresolved_stops) + len(new_context.mandatory_unknowns),
                "private_values_emitted": 0,
                "real_external_actions": 0,
                "next_safe_action": "REVIEW_REBOUND_PACKET",
            }
        except Exception as exc:
            cleanup_failure: Exception | None = None
            for reference in reversed(created_references):
                if reference.get("deduplicated") is True:
                    continue
                try:
                    validate_secure_reference(str(reference["secure_ref"]))
                    self.onboarding.delete(str(reference["secure_ref"]), user_confirmed=True)
                except Exception as cleanup_error:  # pragma: no cover - catastrophic storage failure
                    cleanup_failure = cleanup_error
            if cleanup_failure is not None:
                raise JobOpsError(
                    "APPLICATION_FIELD_RESOLUTION_ROLLBACK_FAILED",
                    "Encrypted job-specific answers could not be fully rolled back after a local failure.",
                ) from cleanup_failure
            raise
        finally:
            self._clear_plaintext_inputs(raw_resolutions)
            if prepared_resolutions is not raw_resolutions:
                self._clear_plaintext_inputs(prepared_resolutions)
