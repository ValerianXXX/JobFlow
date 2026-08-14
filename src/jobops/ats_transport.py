from __future__ import annotations

from typing import Any

from .ats_capabilities import provider_transport_contract
from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, project_root, sha256_bytes, stable_id


_AUTHORIZATION_BY_ACTION = {
    "read_official_job": "SCOPED_ACTION_SESSION_USE",
    "inspect_application_form": "SCOPED_ACTION_SESSION_USE",
    "prefill_application_form": "SCOPED_ACTION_SESSION_USE",
    "upload_materials": "SCOPED_ACTION_SESSION_USE",
    "submit_application": "FINAL_SUBMISSION_AUTHORIZATION",
    "verify_receipt": "SUBMISSION_ATTEMPT",
}


def _hash(value: object, *, code: str) -> str:
    material = str(value or "")
    if len(material) != 71 or not material.startswith("sha256:"):
        raise JobOpsError(code, "The ATS transport envelope requires a SHA-256 binding.")
    try:
        int(material[7:], 16)
    except ValueError as exc:
        raise JobOpsError(code, "The ATS transport envelope requires a SHA-256 binding.") from exc
    return material


def _material(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in value if key not in {"envelope_id", "envelope_hash"}}


def build_ats_transport_envelope(
    *,
    provider: str,
    action: str,
    application_id: str,
    run_id: str,
    application_context_hash: str,
    source_route_hash: str,
    form_snapshot_hash: str,
    execution_plan_hash: str,
    request_payload_hash: str,
    authorization_kind: str,
    authorization_hash: str,
) -> dict[str, Any]:
    expected_authorization = _AUTHORIZATION_BY_ACTION.get(action)
    if expected_authorization is None:
        raise JobOpsError("ATS_TRANSPORT_ACTION_UNSUPPORTED", "The ATS transport action is unsupported.", action=action)
    if authorization_kind != expected_authorization:
        raise JobOpsError(
            "ATS_TRANSPORT_AUTHORIZATION_MISMATCH",
            "The ATS action is not bound to its required authorization type.",
            action=action,
        )
    contract = provider_transport_contract(provider)
    value = {
        "schema_version": 1,
        "provider": provider,
        "action": action,
        "application_id": application_id,
        "run_id": run_id,
        "application_context_hash": _hash(application_context_hash, code="ATS_TRANSPORT_CONTEXT_HASH_INVALID"),
        "source_route_hash": _hash(source_route_hash, code="ATS_TRANSPORT_ROUTE_HASH_INVALID"),
        "form_snapshot_hash": _hash(form_snapshot_hash, code="ATS_TRANSPORT_FORM_HASH_INVALID"),
        "execution_plan_hash": _hash(execution_plan_hash, code="ATS_TRANSPORT_PLAN_HASH_INVALID"),
        "request_payload_hash": _hash(request_payload_hash, code="ATS_TRANSPORT_PAYLOAD_HASH_INVALID"),
        "authorization_kind": authorization_kind,
        "authorization_hash": _hash(authorization_hash, code="ATS_TRANSPORT_AUTHORIZATION_HASH_INVALID"),
        "transport_contract_hash": contract["transport_contract_hash"],
        "mode": "ISOLATED_FAKE",
        "contains_private_values": False,
        "contains_file_content": False,
        "created_at": iso_utc(),
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    value["envelope_hash"] = sha256_bytes(canonical_json(_material(value)))
    value["envelope_id"] = stable_id("ATE", value["envelope_hash"], application_id, run_id, action)
    validate_ats_transport_envelope(value)
    return value


def validate_ats_transport_envelope(value: dict[str, Any]) -> None:
    validate_named("ats-transport-envelope", value, project_root() / "schemas")
    expected_authorization = _AUTHORIZATION_BY_ACTION.get(str(value.get("action")))
    if value.get("authorization_kind") != expected_authorization:
        raise JobOpsError("ATS_TRANSPORT_AUTHORIZATION_MISMATCH", "The ATS action authorization type is invalid.")
    contract = provider_transport_contract(str(value.get("provider")))
    if value.get("transport_contract_hash") != contract["transport_contract_hash"]:
        raise JobOpsError("ATS_TRANSPORT_CONTRACT_CHANGED", "The provider transport contract changed after planning.")
    if value.get("envelope_hash") != sha256_bytes(canonical_json(_material(value))):
        raise JobOpsError("ATS_TRANSPORT_ENVELOPE_TAMPERED", "The ATS transport envelope hash is invalid.")
    if value.get("envelope_id") != stable_id(
        "ATE", str(value["envelope_hash"]), str(value["application_id"]), str(value["run_id"]), str(value["action"]),
    ):
        raise JobOpsError("ATS_TRANSPORT_ENVELOPE_TAMPERED", "The ATS transport envelope identifier is invalid.")
