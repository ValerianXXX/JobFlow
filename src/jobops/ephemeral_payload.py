from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .application_field_resolution import ANSWERABLE_STOP_CLASSES, RESOLUTION_DECISIONS
from .approvals import ApprovalContext
from .ats_browser import validate_ats_form_snapshot_integrity, validate_browser_action_plan_integrity
from .errors import JobOpsError
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, project_root, sha256_bytes


MAX_PRIVATE_JSON_BYTES = 2 * 1024 * 1024
MAX_ASSISTED_MATERIAL_BYTES = 64 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = frozenset({".docx", ".pdf", ".txt"})
ASSISTED_MATERIAL_KINDS = frozenset({
    "generated_resume_docx", "generated_resume_pdf",
    "generated_cover_letter_docx", "generated_cover_letter_pdf",
    "onboarding_source_document", "master_resume_docx", "master_resume_pdf",
})
ASSISTED_FIELD_TYPES = frozenset({
    "text", "email", "tel", "url", "number", "date", "datetime-local", "select", "textarea",
})
PROFILE_FIELD_ALIASES = {
    "full_name": "candidate_display_name",
    "github": "github_url",
    "portfolio": "portfolio_url",
}


def _sha256(value: object, *, code: str) -> str:
    material = str(value or "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", material):
        raise JobOpsError(code, "An ephemeral ATS payload binding requires a valid SHA-256 value.")
    return material


def _field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)) and not isinstance(value, complex):
        normalized = str(value).strip()
        if normalized and normalized not in {"UNKNOWN", "UNANSWERED"}:
            return normalized
    raise JobOpsError(
        "EPHEMERAL_FIELD_VALUE_UNAVAILABLE",
        "The encrypted source does not contain one confirmed scalar value for this form field.",
    )


class IsolatedEphemeralPayloadProbe:
    """Consume a synthetic private payload without a browser, network, or upload."""

    kind = "fake"

    def consume(self, payload: memoryview, staged_files: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            decoded = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("EPHEMERAL_PAYLOAD_INVALID", "The isolated private payload is invalid.") from exc
        fields = decoded.get("fields") if isinstance(decoded, dict) else None
        if not isinstance(fields, list):
            raise JobOpsError("EPHEMERAL_PAYLOAD_INVALID", "The isolated private payload has no field list.")
        field_bindings = []
        for item in fields:
            if not isinstance(item, dict) or set(item) != {"control_ref", "value"}:
                raise JobOpsError("EPHEMERAL_PAYLOAD_INVALID", "An isolated private field binding is invalid.")
            field_bindings.append({
                "control_ref": str(item["control_ref"]),
                "value_sha256": sha256_bytes(str(item["value"]).encode("utf-8")),
            })
        material_bindings = []
        for item in staged_files:
            path = item.get("path")
            if not isinstance(path, Path) or not path.is_file():
                raise JobOpsError("EPHEMERAL_MATERIAL_UNAVAILABLE", "A staged synthetic material is unavailable.")
            material_bindings.append({
                "purpose": str(item["purpose"]),
                "sha256": sha256_bytes(path.read_bytes()),
            })
        return {
            "status": "FAKE_EPHEMERAL_PAYLOAD_CONSUMED",
            "payload_sha256": sha256_bytes(bytes(payload)),
            "field_count": len(field_bindings),
            "file_count": len(material_bindings),
            "field_binding_hash": sha256_bytes(canonical_json(sorted(field_bindings, key=lambda item: item["control_ref"]))),
            "material_binding_hash": sha256_bytes(canonical_json(sorted(material_bindings, key=lambda item: (item["purpose"], item["sha256"])))),
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
        }


class EphemeralATSPayloadBroker:
    """Materialize approved bindings only inside a bounded private lease."""

    def __init__(self, onboarding: PrivateOnboarding, *, isolated_test_mode: bool = False) -> None:
        self.onboarding = onboarding
        self.isolated_test_mode = isolated_test_mode
        self.schemas = project_root() / "schemas"

    def _private_json(
        self,
        reference: str,
        *,
        allowed_kinds: set[str],
        require_synthetic: bool = True,
    ) -> tuple[dict[str, Any], str]:
        validate_secure_reference(reference)
        metadata = self.onboarding.reference_metadata(reference)
        if (
            metadata["status"] != "ACTIVE"
            or metadata["kind"] not in allowed_kinds
            or (require_synthetic and metadata["synthetic"] is not True)
        ):
            raise JobOpsError(
                "EPHEMERAL_PRIVATE_REFERENCE_INVALID",
                "The private payload lease accepts only active references of the expected kind.",
            )
        raw = bytearray(self.onboarding.read_bytes(reference))
        try:
            if not raw or len(raw) > MAX_PRIVATE_JSON_BYTES:
                raise JobOpsError("EPHEMERAL_PRIVATE_JSON_SIZE_INVALID", "The encrypted private JSON exceeds the bounded lease limit.")
            try:
                value = json.loads(bytes(raw).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise JobOpsError("EPHEMERAL_PRIVATE_JSON_INVALID", "The encrypted private source is not valid JSON.") from exc
            if not isinstance(value, dict):
                raise JobOpsError("EPHEMERAL_PRIVATE_JSON_INVALID", "The encrypted private source must contain an object.")
            return value, str(metadata["kind"])
        finally:
            raw[:] = b"\0" * len(raw)

    def _resolve_private_field(self, reference: str, answer_key: str, *, require_synthetic: bool = True) -> str:
        value, kind = self._private_json(
            reference,
            allowed_kinds={"candidate_profile", "answer_bank"},
            require_synthetic=require_synthetic,
        )
        if kind == "candidate_profile":
            key = PROFILE_FIELD_ALIASES.get(answer_key, answer_key)
            return _field_value(value.get(key))
        answers = value.get("answers")
        if not isinstance(answers, dict):
            return _field_value(value.get(answer_key))
        if not isinstance(answers.get(answer_key), dict):
            raise JobOpsError("EPHEMERAL_FIELD_VALUE_UNAVAILABLE", "The encrypted Answer Bank has no matching field.")
        item = answers[answer_key]
        if item.get("status") != "CONFIRMED" or item.get("use_policy") in {
            "confirm_each_application", "do_not_store", "prefer_not_to_answer",
        }:
            raise JobOpsError(
                "EPHEMERAL_FIELD_CONFIRMATION_REQUIRED",
                "This encrypted answer is missing, undisclosed, or still requires per-application confirmation.",
            )
        return _field_value(item.get("value"))

    def _resolve_application_answers(
        self,
        *,
        context: ApprovalContext,
        form_snapshot: dict[str, Any],
        browser_plan: dict[str, Any],
        references: list[str],
        require_synthetic: bool = True,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
        if not references:
            return [], [], 0
        if len(references) != 1:
            raise JobOpsError(
                "EPHEMERAL_ANSWER_BUNDLE_COUNT_INVALID",
                "The current application must bind exactly one complete job-specific answer bundle.",
            )
        if context.unresolved_stops or context.mandatory_unknowns:
            raise JobOpsError(
                "EPHEMERAL_APPLICATION_FIELDS_UNRESOLVED",
                "Job-specific answers must be fully confirmed before an ephemeral payload can be formed.",
            )
        reference = references[0]
        metadata = self.onboarding.reference_metadata(reference)
        bundle, kind = self._private_json(
            reference,
            allowed_kinds={"application_answer_bundle"},
            require_synthetic=require_synthetic,
        )
        if kind != "application_answer_bundle" or metadata["content_sha256"] is None:
            raise JobOpsError("EPHEMERAL_ANSWER_BUNDLE_INVALID", "The encrypted job-specific answer bundle is invalid.")
        if bundle.get("application_id") != context.application_id:
            raise JobOpsError("EPHEMERAL_ANSWER_APPLICATION_MISMATCH", "The encrypted job-specific answers belong to another application.")
        prior_answers_hash = _sha256(bundle.get("prior_answers_hash"), code="EPHEMERAL_PRIOR_ANSWERS_HASH_INVALID")
        fields = bundle.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
            raise JobOpsError("EPHEMERAL_ANSWER_BUNDLE_INVALID", "The encrypted job-specific answer list is invalid.")
        fields_by_ref = {str(item["control_ref"]): item for item in form_snapshot["fields"]}
        actions_by_ref = {str(item["control_ref"]): item for item in browser_plan["actions"]}
        resolved: list[dict[str, str]] = []
        decisions: list[dict[str, str]] = []
        skipped = 0
        seen: set[str] = set()
        for item in fields:
            if not isinstance(item, dict):
                raise JobOpsError("EPHEMERAL_ANSWER_BUNDLE_INVALID", "A job-specific answer entry is invalid.")
            control_ref = str(item.get("control_ref", ""))
            classification = str(item.get("classification", ""))
            answer_key = str(item.get("answer_key", ""))
            decision = str(item.get("decision", ""))
            field = fields_by_ref.get(control_ref)
            action = actions_by_ref.get(control_ref)
            if (
                not re.fullmatch(r"CTL-[A-F0-9]{12}", control_ref)
                or control_ref in seen
                or classification not in ANSWERABLE_STOP_CLASSES
                or decision not in RESOLUTION_DECISIONS
                or field is None
                or action is None
                or field.get("classification") != classification
                or field.get("answer_key") != answer_key
                or action.get("action") != "STOP"
                or action.get("binding_kind") != "NONE"
            ):
                raise JobOpsError("EPHEMERAL_ANSWER_FORM_BINDING_CHANGED", "A confirmed job-specific answer no longer matches the reviewed form.")
            seen.add(control_ref)
            decisions.append({"control_ref": control_ref, "decision": decision})
            if decision == "CONFIRMED_VALUE":
                resolved.append({"control_ref": control_ref, "value": _field_value(item.get("value"))})
            else:
                if bool(field.get("required", False)) or item.get("value") is not None:
                    raise JobOpsError("EPHEMERAL_ANSWER_DECISION_INVALID", "A required field cannot be skipped and a skip decision cannot carry a value.")
                skipped += 1
        expected_answers_hash = sha256_bytes(canonical_json({
            "prior_answers_hash": prior_answers_hash,
            "answer_bundle_content_hash": str(metadata["content_sha256"]),
            "fields": sorted(decisions, key=lambda item: item["control_ref"]),
        }))
        if expected_answers_hash != context.answers_hash:
            raise JobOpsError("EPHEMERAL_ANSWER_BINDING_CHANGED", "The encrypted job-specific answers differ from the approved application context.")
        expected_bindings = [
            {"control_ref": item["control_ref"], "value_sha256": sha256_bytes(item["value"].encode("utf-8"))}
            for item in resolved
        ]
        return resolved, expected_bindings, skipped

    @staticmethod
    def _safe_suffix(filename: str) -> str:
        if not re.fullmatch(r"[^\\/:*?\"<>|]{1,160}", filename) or Path(filename).name != filename:
            raise JobOpsError("EPHEMERAL_UPLOAD_NAME_INVALID", "An approved upload name is not a safe display filename.")
        suffix = Path(filename).suffix.casefold()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise JobOpsError("EPHEMERAL_UPLOAD_FORMAT_INVALID", "The isolated payload proof accepts DOCX, PDF, or TXT materials only.")
        return suffix

    def materialize_assisted_payload(
        self,
        *,
        context: ApprovalContext,
        form_snapshot: dict[str, Any],
        browser_plan: dict[str, Any],
        public_values: dict[str, str],
        material_references: dict[str, str],
        application_answer_bundle_references: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve one approved user-present payload without writing plaintext to disk."""

        normalized = context.normalized()
        validate_ats_form_snapshot_integrity(form_snapshot)
        validate_browser_action_plan_integrity(browser_plan)
        if (
            browser_plan.get("form_snapshot_hash") != normalized.form_snapshot_hash
            or form_snapshot.get("form_snapshot_hash") != normalized.form_snapshot_hash
        ):
            raise JobOpsError("SITE_CHANGED", "The assisted payload lease does not match the approved form snapshot.")
        if (
            browser_plan.get("source_route_hash") != normalized.source_route_hash
            or form_snapshot.get("source_route_hash") != normalized.source_route_hash
        ):
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The assisted payload lease does not match the approved source route.")
        if set(public_values) - {str(item["control_ref"]) for item in browser_plan["actions"]}:
            raise JobOpsError("EPHEMERAL_PUBLIC_BINDING_EXTRA", "A public value targets a control outside the approved browser plan.")

        answer_references = list(application_answer_bundle_references or [])
        resolved_fields, expected_field_bindings, skipped_optional_count = self._resolve_application_answers(
            context=normalized,
            form_snapshot=form_snapshot,
            browser_plan=browser_plan,
            references=answer_references,
            require_synthetic=False,
        )
        fields_by_ref = {str(item["control_ref"]): item for item in form_snapshot["fields"]}
        for item in resolved_fields:
            field = fields_by_ref.get(str(item["control_ref"]))
            if field is None or str(field.get("control_type")) not in ASSISTED_FIELD_TYPES:
                raise JobOpsError(
                    "BROWSER_CONTROL_TYPE_UNSUPPORTED",
                    "A confirmed application answer uses a control type the browser companion cannot fill safely.",
                    control_type=str(field.get("control_type")) if field else "missing",
                )
        for action in browser_plan["actions"]:
            control_ref = str(action["control_ref"])
            if action["action"] != "PROPOSE_PREFILL":
                if control_ref in public_values:
                    raise JobOpsError("EPHEMERAL_STOP_FIELD_VALUE_FORBIDDEN", "A stopped form control cannot receive a value.")
                continue
            field = fields_by_ref.get(control_ref)
            if field is None or field.get("classification") != action.get("classification"):
                raise JobOpsError("EPHEMERAL_FORM_BINDING_CHANGED", "A planned form control no longer matches its reviewed field.")
            if str(field.get("control_type")) not in ASSISTED_FIELD_TYPES:
                raise JobOpsError(
                    "BROWSER_CONTROL_TYPE_UNSUPPORTED",
                    "This approved field uses a control type the browser companion cannot fill safely.",
                    control_type=str(field.get("control_type")),
                )
            answer_key = str(field.get("answer_key") or "")
            if action["binding_kind"] == "SECURE_REF":
                resolved = self._resolve_private_field(
                    str(action["binding_ref"]), answer_key, require_synthetic=False,
                )
            elif action["binding_kind"] == "PUBLIC_VALUE_HASH":
                if control_ref not in public_values:
                    raise JobOpsError("EPHEMERAL_PUBLIC_VALUE_MISSING", "A hash-bound public value must be supplied again for this lease.")
                resolved = _field_value(public_values[control_ref])
                if sha256_bytes(resolved.encode("utf-8")) != action["binding_ref"]:
                    raise JobOpsError("EPHEMERAL_PUBLIC_VALUE_CHANGED", "A public field value changed after review.")
            else:
                raise JobOpsError("EPHEMERAL_FIELD_BINDING_INVALID", "A proposed prefill has no approved value binding.")
            resolved_fields.append({"control_ref": control_ref, "value": resolved})
            expected_field_bindings.append({
                "control_ref": control_ref,
                "value_sha256": sha256_bytes(resolved.encode("utf-8")),
            })

        if len({str(item["control_ref"]) for item in resolved_fields}) != len(resolved_fields):
            raise JobOpsError("EPHEMERAL_FIELD_BINDING_DUPLICATE", "An approved form field was resolved more than once.")

        expected_uploads = [
            (_sha256(item.sha256, code="EPHEMERAL_UPLOAD_HASH_INVALID"), item)
            for item in normalized.uploads
        ]
        normalized_material_refs = {
            _sha256(key, code="EPHEMERAL_UPLOAD_HASH_INVALID"): str(value)
            for key, value in material_references.items()
        }
        if set(normalized_material_refs) != {item[0] for item in expected_uploads}:
            raise JobOpsError("EPHEMERAL_UPLOAD_BINDINGS_INCOMPLETE", "Every approved upload must map to exactly one secure reference.")
        files: list[dict[str, str]] = []
        expected_material_bindings: list[dict[str, str]] = []
        for upload_hash, upload in sorted(
            expected_uploads, key=lambda item: (item[1].purpose, item[1].filename, item[0]),
        ):
            reference = normalized_material_refs[upload_hash]
            validate_secure_reference(reference)
            metadata = self.onboarding.reference_metadata(reference)
            if (
                metadata["status"] != "ACTIVE"
                or metadata["kind"] not in ASSISTED_MATERIAL_KINDS
                or metadata["content_sha256"] != upload_hash
            ):
                raise JobOpsError("EPHEMERAL_UPLOAD_REFERENCE_INVALID", "An approved upload does not match its active encrypted reference.")
            self._safe_suffix(upload.filename)
            files.append({
                "purpose": upload.purpose,
                "filename": upload.filename,
                "sha256": upload_hash,
                "secure_ref": reference,
            })
            expected_material_bindings.append({"purpose": upload.purpose, "sha256": upload_hash})

        field_bindings = sorted(expected_field_bindings, key=lambda item: item["control_ref"])
        material_bindings = sorted(expected_material_bindings, key=lambda item: (item["purpose"], item["sha256"]))
        return {
            "fields": sorted(resolved_fields, key=lambda item: item["control_ref"]),
            "files": files,
            "field_binding_hash": sha256_bytes(canonical_json(field_bindings)),
            "material_binding_hash": sha256_bytes(canonical_json(material_bindings)),
            "field_count": len(resolved_fields),
            "file_count": len(files),
            "application_answer_bundle_count": len(answer_references),
            "skipped_optional_field_count": skipped_optional_count,
        }

    def read_assisted_material(
        self,
        *,
        reference: str,
        expected_sha256: str,
        filename: str,
    ) -> bytearray:
        """Decrypt one approved material into memory for a one-use loopback stream."""

        validate_secure_reference(reference)
        expected = _sha256(expected_sha256, code="EPHEMERAL_UPLOAD_HASH_INVALID")
        self._safe_suffix(filename)
        metadata = self.onboarding.reference_metadata(reference)
        if (
            metadata["status"] != "ACTIVE"
            or metadata["kind"] not in ASSISTED_MATERIAL_KINDS
            or metadata["content_sha256"] != expected
        ):
            raise JobOpsError("EPHEMERAL_UPLOAD_REFERENCE_INVALID", "The approved upload reference is unavailable or changed.")
        raw = bytearray(self.onboarding.read_bytes(reference))
        if not raw or len(raw) > MAX_ASSISTED_MATERIAL_BYTES:
            raw[:] = b"\0" * len(raw)
            raise JobOpsError("EPHEMERAL_UPLOAD_SIZE_INVALID", "The approved upload exceeds the browser companion file limit.")
        if sha256_bytes(bytes(raw)) != expected:
            raw[:] = b"\0" * len(raw)
            raise JobOpsError("EPHEMERAL_UPLOAD_CONTENT_CHANGED", "The decrypted upload changed after approval.")
        return raw

    def run_isolated_probe(
        self,
        *,
        context: ApprovalContext,
        form_snapshot: dict[str, Any],
        browser_plan: dict[str, Any],
        public_values: dict[str, str],
        material_references: dict[str, str],
        application_answer_bundle_references: list[str] | None = None,
        probe: IsolatedEphemeralPayloadProbe | None = None,
    ) -> dict[str, Any]:
        if not self.isolated_test_mode:
            raise JobOpsError(
                "PHASE_NOT_AUTHORIZED",
                "Private ATS payload materialization remains unavailable outside the isolated local proof.",
            )
        adapter = probe or IsolatedEphemeralPayloadProbe()
        if type(adapter) is not IsolatedEphemeralPayloadProbe or adapter.kind != "fake":
            raise JobOpsError("EPHEMERAL_ADAPTER_FORBIDDEN", "Only the built-in isolated payload probe is permitted in this build.")
        normalized = context.normalized()
        validate_ats_form_snapshot_integrity(form_snapshot)
        validate_browser_action_plan_integrity(browser_plan)
        if (
            browser_plan.get("form_snapshot_hash") != normalized.form_snapshot_hash
            or form_snapshot.get("form_snapshot_hash") != normalized.form_snapshot_hash
        ):
            raise JobOpsError("SITE_CHANGED", "The private payload lease does not match the approved form snapshot.")
        if (
            browser_plan.get("source_route_hash") != normalized.source_route_hash
            or form_snapshot.get("source_route_hash") != normalized.source_route_hash
        ):
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The private payload lease does not match the approved source route.")
        if set(public_values) - {str(item["control_ref"]) for item in browser_plan["actions"]}:
            raise JobOpsError("EPHEMERAL_PUBLIC_BINDING_EXTRA", "A public value targets a control outside the approved browser plan.")

        answer_references = list(application_answer_bundle_references or [])
        resolved_fields, expected_field_bindings, skipped_optional_count = self._resolve_application_answers(
            context=normalized,
            form_snapshot=form_snapshot,
            browser_plan=browser_plan,
            references=answer_references,
            require_synthetic=True,
        )
        confirmed_stop_count = len(resolved_fields)
        fields_by_ref = {str(item["control_ref"]): item for item in form_snapshot["fields"]}
        for action in browser_plan["actions"]:
            control_ref = str(action["control_ref"])
            if action["action"] != "PROPOSE_PREFILL":
                if control_ref in public_values:
                    raise JobOpsError("EPHEMERAL_STOP_FIELD_VALUE_FORBIDDEN", "A stopped form control cannot receive a value.")
                continue
            field = fields_by_ref.get(control_ref)
            if field is None or field.get("classification") != action.get("classification"):
                raise JobOpsError("EPHEMERAL_FORM_BINDING_CHANGED", "A planned form control no longer matches its reviewed field.")
            answer_key = str(field.get("answer_key") or "")
            if action["binding_kind"] == "SECURE_REF":
                resolved = self._resolve_private_field(str(action["binding_ref"]), answer_key)
            elif action["binding_kind"] == "PUBLIC_VALUE_HASH":
                if control_ref not in public_values:
                    raise JobOpsError("EPHEMERAL_PUBLIC_VALUE_MISSING", "A hash-bound public value must be supplied again for this lease.")
                resolved = _field_value(public_values[control_ref])
                if sha256_bytes(resolved.encode("utf-8")) != action["binding_ref"]:
                    raise JobOpsError("EPHEMERAL_PUBLIC_VALUE_CHANGED", "A public field value changed after the browser plan was reviewed.")
            else:
                raise JobOpsError("EPHEMERAL_FIELD_BINDING_INVALID", "A proposed prefill has no approved value binding.")
            resolved_fields.append({"control_ref": control_ref, "value": resolved})
            expected_field_bindings.append({"control_ref": control_ref, "value_sha256": sha256_bytes(resolved.encode("utf-8"))})

        expected_uploads = [
            (_sha256(item.sha256, code="EPHEMERAL_UPLOAD_HASH_INVALID"), item)
            for item in normalized.uploads
        ]
        expected_upload_hashes = {upload_hash for upload_hash, _ in expected_uploads}
        normalized_material_refs = {_sha256(key, code="EPHEMERAL_UPLOAD_HASH_INVALID"): str(value) for key, value in material_references.items()}
        if set(normalized_material_refs) != expected_upload_hashes:
            raise JobOpsError("EPHEMERAL_UPLOAD_BINDINGS_INCOMPLETE", "Every approved upload hash must map to exactly one secure reference.")

        staged_paths: list[Path] = []
        payload = bytearray()
        result: dict[str, Any]
        with self.onboarding.staging_directory() as staging:
            staged_files: list[dict[str, Any]] = []
            expected_material_bindings: list[dict[str, str]] = []
            for index, (upload_hash, upload) in enumerate(
                sorted(expected_uploads, key=lambda item: (item[1].purpose, item[1].filename, item[0])), 1,
            ):
                reference = normalized_material_refs[upload_hash]
                validate_secure_reference(reference)
                metadata = self.onboarding.reference_metadata(reference)
                if (
                    metadata["status"] != "ACTIVE"
                    or metadata["synthetic"] is not True
                    or metadata["kind"] not in {
                        "generated_resume_docx", "generated_resume_pdf",
                        "generated_cover_letter_docx", "generated_cover_letter_pdf",
                        "onboarding_source_document",
                    }
                    or metadata["content_sha256"] != upload_hash
                ):
                    raise JobOpsError("EPHEMERAL_UPLOAD_REFERENCE_INVALID", "An approved upload does not match its active synthetic secure reference.")
                suffix = self._safe_suffix(upload.filename)
                target = staging / f"material-{index:02d}{suffix}"
                raw = bytearray(self.onboarding.read_bytes(reference))
                try:
                    if sha256_bytes(bytes(raw)) != upload_hash:
                        raise JobOpsError("EPHEMERAL_UPLOAD_CONTENT_CHANGED", "A decrypted upload changed after review.")
                    with target.open("xb") as handle:
                        try:
                            os.chmod(target, 0o600)
                        except OSError:
                            pass
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    raw[:] = b"\0" * len(raw)
                staged_paths.append(target)
                staged_files.append({"purpose": upload.purpose, "path": target})
                expected_material_bindings.append({"purpose": upload.purpose, "sha256": upload_hash})

            payload.extend(canonical_json({
                "schema_version": 1,
                "application_id": normalized.application_id,
                "fields": sorted(resolved_fields, key=lambda item: item["control_ref"]),
            }))
            try:
                result = adapter.consume(memoryview(payload), staged_files)
            finally:
                payload[:] = b"\0" * len(payload)

            expected_field_hash = sha256_bytes(canonical_json(sorted(expected_field_bindings, key=lambda item: item["control_ref"])))
            expected_material_hash = sha256_bytes(canonical_json(sorted(expected_material_bindings, key=lambda item: (item["purpose"], item["sha256"]))))
            if (
                set(result) != {
                    "status", "payload_sha256", "field_count", "file_count", "field_binding_hash",
                    "material_binding_hash", "browser_actions", "network_actions", "real_external_actions",
                }
                or result.get("status") != "FAKE_EPHEMERAL_PAYLOAD_CONSUMED"
                or result.get("field_count") != len(resolved_fields)
                or result.get("file_count") != len(staged_files)
                or result.get("field_binding_hash") != expected_field_hash
                or result.get("material_binding_hash") != expected_material_hash
                or any(result.get(key) != 0 for key in ("browser_actions", "network_actions", "real_external_actions"))
            ):
                raise JobOpsError("EPHEMERAL_PROBE_EVIDENCE_INVALID", "The isolated payload probe returned unsafe or inconsistent evidence.")

        if any(path.exists() for path in staged_paths):
            raise JobOpsError("EPHEMERAL_STAGING_CLEANUP_FAILED", "An isolated private material remained after the payload lease.")
        evidence = {
            "schema_version": 1,
            "status": "ISOLATED_EPHEMERAL_PAYLOAD_VALIDATED",
            "application_id": normalized.application_id,
            "application_context_hash": normalized.context_hash,
            "browser_plan_hash": str(browser_plan["plan_hash"]),
            "form_snapshot_hash": normalized.form_snapshot_hash,
            "field_count": int(result["field_count"]),
            "file_count": int(result["file_count"]),
            "field_binding_hash": str(result["field_binding_hash"]),
            "material_binding_hash": str(result["material_binding_hash"]),
            "application_answer_bundle_count": len(answer_references),
            "confirmed_stop_field_count": confirmed_stop_count,
            "skipped_optional_field_count": skipped_optional_count,
            "synthetic_only": True,
            "production_activation": False,
            "temporary_files_removed": True,
            "private_values_emitted": 0,
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
        }
        validate_named("ephemeral-ats-payload-evidence", evidence, self.schemas)
        return evidence
