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
        "saved_snapshot_modes": ["single_html"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
    },
    {
        "provider": "lever", "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
        "saved_snapshot_modes": ["single_html"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
    },
    {
        "provider": "workday", "offline_evidence_level": "SAVED_SEQUENCE_PASS",
        "saved_snapshot_modes": ["single_html", "ordered_html_sequence"], "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "logical_field_hash",
    },
)


def _contract_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "provider", "offline_evidence_level", "saved_snapshot_modes", "route_shape", "dynamic_control_strategy",
        "guest_first", "account_creation_blocked", "upload_blocked", "submit_blocked", "live_site_verified",
        "browser_actions", "network_actions", "real_external_actions",
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
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
        }
        item["contract_hash"] = _contract_hash(item)
        providers.append(item)
    report = {
        "schema_version": 1,
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
