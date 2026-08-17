from __future__ import annotations

from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, project_root, sha256_bytes


_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "provider": "company", "offline_evidence_level": "DIRECT_SNAPSHOT_PASS",
        "saved_snapshot_modes": ["single_html"], "route_shape": "OFFICIAL_DIRECT",
        "dynamic_control_strategy": "opaque_control_ref",
    },
    {
        "provider": "greenhouse", "offline_evidence_level": "SYNTHETIC_VERTICAL_PASS",
        "saved_snapshot_modes": ["single_html", "provider_json"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
    },
    {
        "provider": "lever", "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
        "saved_snapshot_modes": ["single_html", "provider_json"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
    },
    {
        "provider": "workday", "offline_evidence_level": "SAVED_SEQUENCE_PASS",
        "saved_snapshot_modes": ["single_html", "ordered_html_sequence"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "logical_field_hash",
    },
)

_TRANSPORT_OPERATION_SEQUENCE = [
    "read_official_job", "inspect_application_form", "prefill_application_form",
    "upload_materials", "await_user_submit", "verify_receipt",
]


def provider_transport_contract(provider: str) -> dict[str, Any]:
    if provider not in {item["provider"] for item in _CONTRACTS}:
        raise JobOpsError("ATS_PROVIDER_UNSUPPORTED", "The ATS provider has no transport contract.", provider=provider)
    material = {
        "provider": provider,
        "contract_version": 2,
        "operation_sequence": list(_TRANSPORT_OPERATION_SEQUENCE),
        "guest_first": True,
        "account_creation": "STOP_REQUIRES_SEPARATE_USER_DECISION",
        "final_submit_gate": "USER_ONLY_NO_TOOL_CAPABILITY",
        "submit_capability": False,
        "receipt_policy": "OBSERVE_OR_ASK_USER_NO_AUTOMATIC_RETRY",
        "automatic_retry": False,
        "private_values_persisted": False,
        "file_content_in_envelope": False,
        "live_transport_registered": False,
    }
    return {
        **material,
        "transport_contract_hash": sha256_bytes(canonical_json(material)),
    }


def _contract_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "provider", "offline_evidence_level", "saved_snapshot_modes", "route_shape", "dynamic_control_strategy",
        "guest_first", "account_creation_blocked", "upload_blocked", "submit_blocked", "live_site_verified",
        "user_present_prefill", "approved_material_upload", "nonfinal_navigation", "final_submit",
        "live_compatibility",
        "browser_actions", "network_actions", "real_external_actions",
        "transport_contract_hash", "live_transport_registered", "automatic_retry",
    )}
    return sha256_bytes(canonical_json(material))


def offline_ats_capabilities() -> dict[str, Any]:
    providers = []
    for definition in _CONTRACTS:
        item = {
            **definition,
            "guest_first": True,
            "account_creation_blocked": True,
            "upload_blocked": True,
            "submit_blocked": True,
            "live_site_verified": False,
            "user_present_prefill": "SUPPORTED_WITH_RUNTIME_REVALIDATION",
            "approved_material_upload": "SUPPORTED_WITH_RUNTIME_REVALIDATION",
            "nonfinal_navigation": "SUPPORTED_EXPLICIT_CONTROLS_ONLY",
            "final_submit": "USER_ONLY",
            "live_compatibility": "NOT_UNIVERSALLY_VERIFIED",
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
            "transport_contract_hash": provider_transport_contract(str(definition["provider"]))["transport_contract_hash"],
            "live_transport_registered": False,
            "automatic_retry": False,
        }
        item["contract_hash"] = _contract_hash(item)
        providers.append(item)
    report = {
        "schema_version": 2,
        "status": "OFFLINE_ATS_CAPABILITIES",
        "provider_count": len(providers),
        "providers": providers,
        "live_site_accessed": False,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_ats_capability_integrity(report)
    return report


def validate_ats_capability_integrity(value: dict[str, Any]) -> None:
    validate_named("ats-capability-report", value, project_root() / "schemas")
    if value["provider_count"] != len(value["providers"]):
        raise JobOpsError("ATS_CAPABILITY_COUNT_INVALID", "ATS provider count does not match the capability list.")
    if len({item["provider"] for item in value["providers"]}) != len(value["providers"]):
        raise JobOpsError("ATS_CAPABILITY_PROVIDER_DUPLICATE", "Each ATS provider may appear only once.")
    if any(item["contract_hash"] != _contract_hash(item) for item in value["providers"]):
        raise JobOpsError("ATS_CAPABILITY_INTEGRITY_FAILED", "An ATS capability contract hash no longer matches its contents.")
