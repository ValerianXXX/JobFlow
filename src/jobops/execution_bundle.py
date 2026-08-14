from __future__ import annotations

import json
import re
import secrets
from typing import Any

from .application_execution import validate_application_execution_plan_integrity
from .approvals import ApprovalContext
from .ats_browser import validate_ats_form_snapshot_integrity, validate_browser_action_plan_integrity
from .db import JobOpsDB
from .errors import JobOpsError
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, iso_utc, project_root, sha256_bytes


MAX_EXECUTION_BUNDLE_BYTES = 4 * 1024 * 1024


def _bundle_material(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "bundle_hash"}


def validate_application_execution_bundle(value: dict[str, Any]) -> None:
    validate_named("application-execution-bundle", value, project_root() / "schemas")
    validate_ats_form_snapshot_integrity(value["form_snapshot"])
    validate_browser_action_plan_integrity(value["browser_plan"])
    validate_application_execution_plan_integrity(value["execution_plan"])
    if value["bundle_hash"] != sha256_bytes(canonical_json(_bundle_material(value))):
        raise JobOpsError("EXECUTION_BUNDLE_TAMPERED", "The encrypted execution bundle no longer matches its hash.")
    if (
        value["application_id"] != value["execution_plan"]["application_id"]
        or value["provider"] != value["execution_plan"]["provider"]
        or value["provider"] != value["form_snapshot"]["provider"]
        or value["source_route_hash"] != value["form_snapshot"]["source_route_hash"]
        or value["source_route_hash"] != value["browser_plan"]["source_route_hash"]
        or value["source_route_hash"] != value["execution_plan"]["route_hash"]
        or value["form_snapshot_hash"] != value["form_snapshot"]["form_snapshot_hash"]
        or value["form_snapshot_hash"] != value["browser_plan"]["form_snapshot_hash"]
        or value["form_snapshot_hash"] != value["execution_plan"]["form_snapshot_hash"]
        or value["browser_plan_hash"] != value["browser_plan"]["plan_hash"]
        or value["browser_plan_hash"] != value["execution_plan"]["browser_plan_hash"]
        or value["execution_plan_hash"] != value["execution_plan"]["plan_hash"]
    ):
        raise JobOpsError("EXECUTION_BUNDLE_BINDING_INVALID", "The encrypted execution artifacts do not share one content binding.")

    actions = {str(item["control_ref"]): item for item in value["browser_plan"]["actions"]}
    public_refs: set[str] = set()
    for item in value["public_values"]:
        control_ref = str(item["control_ref"])
        if control_ref in public_refs:
            raise JobOpsError("EXECUTION_BUNDLE_PUBLIC_VALUE_DUPLICATE", "A public form value is repeated in the execution bundle.")
        public_refs.add(control_ref)
        action = actions.get(control_ref)
        if (
            action is None
            or action.get("action") != "PROPOSE_PREFILL"
            or action.get("binding_kind") != "PUBLIC_VALUE_HASH"
            or action.get("binding_ref") != item["value_sha256"]
            or item["value_sha256"] != sha256_bytes(str(item["value"]).encode("utf-8"))
        ):
            raise JobOpsError("EXECUTION_BUNDLE_PUBLIC_VALUE_CHANGED", "A public form value differs from the reviewed browser plan.")
    expected_public_refs = {
        str(item["control_ref"])
        for item in value["browser_plan"]["actions"]
        if item.get("binding_kind") == "PUBLIC_VALUE_HASH"
    }
    if public_refs != expected_public_refs:
        raise JobOpsError("EXECUTION_BUNDLE_PUBLIC_VALUE_INCOMPLETE", "The execution bundle is missing a reviewed public form value.")

    material_keys: set[tuple[str, str]] = set()
    for item in value["material_references"]:
        validate_secure_reference(str(item["secure_ref"]))
        key = (str(item["purpose"]), str(item["sha256"]))
        if key in material_keys:
            raise JobOpsError("EXECUTION_BUNDLE_MATERIAL_DUPLICATE", "An approved upload is repeated in the execution bundle.")
        material_keys.add(key)


def build_application_execution_bundle(
    *,
    application_id: str,
    form_snapshot: dict[str, Any],
    browser_plan: dict[str, Any],
    execution_plan: dict[str, Any],
    public_values: dict[str, str],
    material_references: list[dict[str, str]],
) -> dict[str, Any]:
    normalized_public: list[dict[str, str]] = []
    for control_ref, raw_value in sorted(public_values.items()):
        value = str(raw_value).strip()
        if not re.fullmatch(r"CTL-[A-F0-9]{12}", str(control_ref)) or not value or len(value) > 4_000:
            raise JobOpsError("EXECUTION_BUNDLE_PUBLIC_VALUE_INVALID", "A reviewed public form value is invalid or too large.")
        normalized_public.append({
            "control_ref": str(control_ref),
            "value": value,
            "value_sha256": sha256_bytes(value.encode("utf-8")),
        })
    normalized_materials = [
        {
            "purpose": str(item.get("purpose", "")),
            "filename": str(item.get("filename", "")),
            "sha256": str(item.get("sha256", "")),
            "secure_ref": str(item.get("secure_ref", "")),
        }
        for item in material_references
    ]
    value: dict[str, Any] = {
        "schema_version": 1,
        "status": "LOCAL_EXECUTION_BUNDLE_READY",
        "application_id": application_id,
        "provider": str(execution_plan.get("provider", "")),
        "source_route_hash": str(execution_plan.get("route_hash", "")),
        "form_snapshot_hash": str(form_snapshot.get("form_snapshot_hash", "")),
        "browser_plan_hash": str(browser_plan.get("plan_hash", "")),
        "execution_plan_hash": str(execution_plan.get("plan_hash", "")),
        "form_snapshot": form_snapshot,
        "browser_plan": browser_plan,
        "execution_plan": execution_plan,
        "public_values": normalized_public,
        "material_references": normalized_materials,
        "bundle_nonce": secrets.token_hex(32),
        "bundle_hash": "",
        "created_at": iso_utc(),
        "real_external_actions": 0,
    }
    value["bundle_hash"] = sha256_bytes(canonical_json(_bundle_material(value)))
    validate_application_execution_bundle(value)
    raw = canonical_json(value)
    if len(raw) > MAX_EXECUTION_BUNDLE_BYTES:
        raise JobOpsError("EXECUTION_BUNDLE_TOO_LARGE", "The encrypted execution bundle exceeds its local storage limit.")
    return value


class ApplicationExecutionBundleManager:
    """Load the exact encrypted artifacts that were approved for one application."""

    def __init__(self, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.database = database
        self.onboarding = onboarding

    def load_current(self, application_id: str) -> tuple[dict[str, Any], ApprovalContext, list[str]]:
        if not re.fullmatch(r"APP-[A-F0-9]{12}", application_id):
            raise JobOpsError("APPLICATION_ID_INVALID", "The selected application identifier is invalid.")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT a.status AS application_status,b.context_hash,b.context_json,
                          r.content_hash AS packet_hash,r.relative_path AS packet_ref,
                          m.path AS bundle_ref,m.content_hash AS bundle_content_hash
                   FROM applications a
                   JOIN application_bindings b ON b.application_id=a.application_id
                   JOIN review_packets r ON r.application_id=a.application_id
                   JOIN materials m ON m.application_id=a.application_id AND m.kind='execution_bundle'
                   WHERE a.application_id=?
                   ORDER BY r.packet_version DESC,m.created_at DESC LIMIT 1""",
                (application_id,),
            ).fetchone()
            answer_rows = connection.execute(
                """SELECT DISTINCT secure_ref FROM application_fields
                   WHERE application_id=? AND status='RESOLVED_FOR_APPLICATION' AND secure_ref IS NOT NULL""",
                (application_id,),
            ).fetchall()
        if row is None:
            raise JobOpsError("EXECUTION_BUNDLE_NOT_FOUND", "The application has no encrypted execution bundle.")
        context = ApprovalContext.from_dict(json.loads(str(row["context_json"]))).normalized()
        if context.context_hash != row["context_hash"] or context.review_packet_hash != row["packet_hash"]:
            raise JobOpsError("EXECUTION_BUNDLE_CONTEXT_INVALID", "The current application context is inconsistent.")
        bundle_ref = str(row["bundle_ref"])
        metadata = self.onboarding.reference_metadata(bundle_ref)
        if (
            metadata["status"] != "ACTIVE"
            or metadata["kind"] != "application_execution_bundle"
            or metadata["content_sha256"] != row["bundle_content_hash"]
        ):
            raise JobOpsError("EXECUTION_BUNDLE_REFERENCE_INVALID", "The encrypted execution bundle is unavailable or changed.")
        raw = bytearray(self.onboarding.read_bytes(bundle_ref))
        try:
            if not raw or len(raw) > MAX_EXECUTION_BUNDLE_BYTES:
                raise JobOpsError("EXECUTION_BUNDLE_SIZE_INVALID", "The encrypted execution bundle exceeds its local read limit.")
            if sha256_bytes(bytes(raw)) != row["bundle_content_hash"]:
                raise JobOpsError("EXECUTION_BUNDLE_CONTENT_CHANGED", "The decrypted execution bundle no longer matches its stored hash.")
            try:
                bundle = json.loads(bytes(raw).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise JobOpsError("EXECUTION_BUNDLE_INVALID", "The encrypted execution bundle is not valid JSON.") from exc
        finally:
            raw[:] = b"\0" * len(raw)
        if not isinstance(bundle, dict):
            raise JobOpsError("EXECUTION_BUNDLE_INVALID", "The encrypted execution bundle must be an object.")
        validate_application_execution_bundle(bundle)
        packet_ref = str(row["packet_ref"])
        packet_metadata = self.onboarding.reference_metadata(packet_ref)
        if (
            packet_metadata["status"] != "ACTIVE"
            or packet_metadata["kind"] != "review_packet"
        ):
            raise JobOpsError("REVIEW_PACKET_BINDING_INVALID", "The current encrypted review packet is unavailable or changed.")
        packet_raw = bytearray(self.onboarding.read_bytes(packet_ref))
        try:
            if not packet_raw or len(packet_raw) > MAX_EXECUTION_BUNDLE_BYTES:
                raise JobOpsError("REVIEW_PACKET_SIZE_INVALID", "The encrypted review packet exceeds its local read limit.")
            packet = json.loads(bytes(packet_raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet is not valid JSON.") from exc
        finally:
            packet_raw[:] = b"\0" * len(packet_raw)
        if not isinstance(packet, dict):
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet must be an object.")
        validate_named("review-packet", packet, project_root() / "schemas")
        validate_application_execution_plan_integrity(packet["execution_plan"])
        packet_material = {key: value for key, value in packet.items() if key != "content_hash"}
        if packet.get("content_hash") != row["packet_hash"] or sha256_bytes(canonical_json(packet_material)) != row["packet_hash"]:
            raise JobOpsError("REVIEW_PACKET_HASH_INVALID", "The decrypted review packet no longer matches its stored hash.")
        if (
            bundle["application_id"] != application_id
            or packet.get("execution_bundle_content_hash") != row["bundle_content_hash"]
            or packet.get("execution_plan", {}).get("plan_hash") != bundle["execution_plan_hash"]
            or bundle["source_route_hash"] != context.source_route_hash
            or bundle["form_snapshot_hash"] != context.form_snapshot_hash
        ):
            raise JobOpsError("EXECUTION_BUNDLE_BINDING_INVALID", "The encrypted execution bundle differs from the approved packet.")
        approved_uploads = {(item.purpose, item.filename, item.sha256) for item in context.uploads}
        bundle_uploads = {
            (str(item["purpose"]), str(item["filename"]), str(item["sha256"]))
            for item in bundle["material_references"]
        }
        if approved_uploads != bundle_uploads:
            raise JobOpsError("EXECUTION_BUNDLE_UPLOADS_CHANGED", "The encrypted execution materials differ from the approved uploads.")
        answer_refs = sorted(str(item["secure_ref"]) for item in answer_rows)
        for reference in answer_refs:
            validate_secure_reference(reference)
        return bundle, context, answer_refs
