from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, project_root, sha256_bytes, sha256_file


_STAGES: tuple[str, ...] = (
    "OFFICIAL_DISCOVERY",
    "ROUTE_BINDING",
    "FORM_ANALYSIS",
    "PRIVATE_VALUE_FREE_PLAN",
    "REVIEW_PACKET",
    "APPROVED_DOM_PREFILL",
    "APPROVED_FILE_ATTACHMENT",
    "EXPLICIT_NONFINAL_NAVIGATION",
    "MULTI_PAGE_RESUME",
    "RESULT_OBSERVATION",
    "MODERN_COMPONENT_REBINDING",
)

_RUNTIME_STAGES: tuple[str, ...] = (
    "APPROVED_DOM_PREFILL",
    "APPROVED_FILE_ATTACHMENT",
    "EXPLICIT_NONFINAL_NAVIGATION",
    "MODERN_COMPONENT_REBINDING",
)

_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "provider": "company",
        "offline_evidence_level": "DIRECT_SNAPSHOT_PASS",
        "evidence_scope": "DIRECT_SITE_AND_BROWSER_RUNTIME",
        "saved_snapshot_modes": ["single_html"],
        "route_shape": "OFFICIAL_DIRECT",
        "dynamic_control_strategy": "opaque_control_ref",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN",
            "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "EXPLICIT_NONFINAL_NAVIGATION",
            "MODERN_COMPONENT_REBINDING",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-official-job-list.html",
            "tests/fixtures/synthetic-teksystems-lwc-form.html",
        ],
        "known_limit_codes": ["NO_PROVIDER_SPECIFIC_MULTI_PAGE_SEQUENCE", "LIVE_SITE_ACCEPTANCE_REQUIRED"],
    },
    {
        "provider": "greenhouse",
        "offline_evidence_level": "SYNTHETIC_VERTICAL_PASS",
        "evidence_scope": "DISCOVERY_TO_PROVIDER_BROWSER_RUNTIME",
        "saved_snapshot_modes": ["single_html", "provider_json"],
        "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN", "REVIEW_PACKET",
            "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "EXPLICIT_NONFINAL_NAVIGATION",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/test_greenhouse_vertical.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-greenhouse-jobs.json",
            "tests/fixtures/synthetic-greenhouse-route.json",
            "tests/fixtures/synthetic-greenhouse-form.html",
            "tests/fixtures/synthetic-greenhouse-continue-form.html",
            "tests/fixtures/synthetic-material-form.html",
        ],
        "known_limit_codes": [
            "PROVIDER_SPECIFIC_MULTI_PAGE_RESUME_NOT_PROVEN",
            "PROVIDER_SPECIFIC_RESULT_OBSERVATION_NOT_PROVEN",
            "LIVE_SITE_ACCEPTANCE_REQUIRED",
        ],
    },
    {
        "provider": "lever",
        "offline_evidence_level": "SYNTHETIC_VERTICAL_PASS",
        "evidence_scope": "DISCOVERY_TO_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        "saved_snapshot_modes": ["single_html", "provider_json"],
        "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN",
            "REVIEW_PACKET", "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "RESULT_OBSERVATION",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/test_lever_vertical.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-lever-postings.json",
            "tests/fixtures/synthetic-lever-route.json",
            "tests/fixtures/synthetic-lever-form.html",
        ],
        "known_limit_codes": [
            "PROVIDER_SPECIFIC_NONFINAL_NAVIGATION_NOT_PROVEN",
            "LIVE_SITE_ACCEPTANCE_REQUIRED",
        ],
    },
    {
        "provider": "workday",
        "offline_evidence_level": "SAVED_SEQUENCE_PASS",
        "evidence_scope": "MULTI_PAGE_SEQUENCE_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        "saved_snapshot_modes": ["single_html", "ordered_html_sequence"],
        "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "logical_field_hash",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN",
            "REVIEW_PACKET", "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT",
            "EXPLICIT_NONFINAL_NAVIGATION", "MULTI_PAGE_RESUME", "RESULT_OBSERVATION",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/test_workday_vertical.py",
            "tests/test_workday_sequence.py",
            "tests/test_real_profile_offline_application.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-workday-sequence.json",
            "tests/fixtures/synthetic-workday-safe-form.html",
        ],
        "known_limit_codes": ["LIVE_SITE_ACCEPTANCE_REQUIRED"],
    },
    {
        "provider": "ashby",
        "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
        "evidence_scope": "DISCOVERY_TO_PROVIDER_BROWSER_RUNTIME",
        "saved_snapshot_modes": ["single_html", "provider_json"],
        "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS",
            "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "EXPLICIT_NONFINAL_NAVIGATION",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/test_ats_provider_contracts.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-ashby-jobs.json",
            "tests/fixtures/synthetic-ashby-form.html",
        ],
        "known_limit_codes": [
            "PROVIDER_SPECIFIC_REVIEW_PACKET_NOT_PROVEN",
            "PROVIDER_SPECIFIC_MULTI_PAGE_RESUME_NOT_PROVEN",
            "PROVIDER_SPECIFIC_RESULT_OBSERVATION_NOT_PROVEN",
            "LIVE_SITE_ACCEPTANCE_REQUIRED",
        ],
    },
    {
        "provider": "smartrecruiters",
        "offline_evidence_level": "SINGLE_SNAPSHOT_PASS",
        "evidence_scope": "DISCOVERY_TO_PROVIDER_BROWSER_RUNTIME",
        "saved_snapshot_modes": ["single_html", "provider_json"],
        "route_shape": "OFFICIAL_TO_APPROVED_ATS",
        "dynamic_control_strategy": "opaque_control_ref",
        "verified_stages": [
            "OFFICIAL_DISCOVERY", "ROUTE_BINDING", "FORM_ANALYSIS",
            "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "EXPLICIT_NONFINAL_NAVIGATION",
        ],
        "evidence_refs": [
            "tests/test_official_discovery.py",
            "tests/test_ats_provider_contracts.py",
            "tests/browser_companion_e2e.cjs",
            "tests/fixtures/synthetic-smartrecruiters-postings.json",
            "tests/fixtures/synthetic-smartrecruiters-form.html",
        ],
        "known_limit_codes": [
            "PROVIDER_SPECIFIC_REVIEW_PACKET_NOT_PROVEN",
            "PROVIDER_SPECIFIC_MULTI_PAGE_RESUME_NOT_PROVEN",
            "PROVIDER_SPECIFIC_RESULT_OBSERVATION_NOT_PROVEN",
            "LIVE_SITE_ACCEPTANCE_REQUIRED",
        ],
    },
)

_RUNTIME_EVIDENCE_REFS = [
    "browser-companion/dom.js",
    "browser-companion/service-worker.js",
    "tests/browser_companion_e2e.cjs",
    "tests/browser_companion_manual_navigation_e2e.cjs",
]

_TRANSPORT_OPERATION_SEQUENCE = [
    "read_official_job", "inspect_application_form", "prefill_application_form",
    "upload_materials", "await_user_submit", "verify_receipt",
]


def _stage_support_status(stage: str, *, verified: list[str], verified_status: str) -> str:
    if stage in verified:
        return verified_status
    return "SHARED_RUNTIME_ONLY_PROVIDER_ACCEPTANCE_REQUIRED"


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
    return {**material, "transport_contract_hash": sha256_bytes(canonical_json(material))}


def _safe_evidence_path(relative: str, *, root: Path | None = None) -> Path:
    project = (root or project_root()).resolve()
    candidate = (project / relative).resolve()
    try:
        candidate.relative_to(project)
    except ValueError as exc:
        raise JobOpsError(
            "ATS_CAPABILITY_EVIDENCE_PATH_INVALID",
            "ATS capability evidence must remain inside the project.",
        ) from exc
    if not candidate.is_file():
        raise JobOpsError(
            "ATS_CAPABILITY_EVIDENCE_MISSING",
            "A declared ATS capability evidence file is missing.",
            evidence_ref=relative,
        )
    return candidate


def _evidence_bundle_hash(refs: list[str], *, root: Path | None = None) -> str:
    records = [
        {"evidence_ref": relative, "sha256": sha256_file(_safe_evidence_path(relative, root=root))}
        for relative in sorted(refs)
    ]
    return sha256_bytes(canonical_json(records))


def _contract_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "provider", "offline_evidence_level", "evidence_scope", "saved_snapshot_modes", "route_shape",
        "dynamic_control_strategy", "verified_stages", "unverified_stages", "evidence_refs",
        "evidence_bundle_hash", "known_limit_codes", "guest_first", "account_creation_blocked",
        "upload_blocked", "submit_blocked", "live_site_verified", "user_present_prefill",
        "approved_material_upload", "nonfinal_navigation", "final_submit", "live_compatibility",
        "browser_actions", "network_actions", "real_external_actions", "transport_contract_hash",
        "live_transport_registered", "automatic_retry",
    )}
    return sha256_bytes(canonical_json(material))


def _runtime_evidence_hash(value: dict[str, Any]) -> str:
    material = {key: value[key] for key in (
        "status", "verified_stages", "evidence_refs", "evidence_bundle_hash", "live_site_verified",
        "final_submit", "automatic_retry", "browser_actions", "network_actions", "real_external_actions",
    )}
    return sha256_bytes(canonical_json(material))


def offline_ats_capabilities() -> dict[str, Any]:
    providers = []
    for definition in _CONTRACTS:
        verified = list(definition["verified_stages"])
        refs = sorted(definition["evidence_refs"])
        item = {
            **definition,
            "verified_stages": verified,
            "unverified_stages": [stage for stage in _STAGES if stage not in verified],
            "evidence_refs": refs,
            "evidence_bundle_hash": _evidence_bundle_hash(refs),
            "known_limit_codes": sorted(definition["known_limit_codes"]),
            "guest_first": True,
            "account_creation_blocked": True,
            "upload_blocked": True,
            "submit_blocked": True,
            "live_site_verified": False,
            "user_present_prefill": _stage_support_status(
                "APPROVED_DOM_PREFILL",
                verified=verified,
                verified_status="PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
            ),
            "approved_material_upload": _stage_support_status(
                "APPROVED_FILE_ATTACHMENT",
                verified=verified,
                verified_status="PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
            ),
            "nonfinal_navigation": _stage_support_status(
                "EXPLICIT_NONFINAL_NAVIGATION",
                verified=verified,
                verified_status="PROVIDER_EVIDENCE_VERIFIED_EXPLICIT_CONTROLS_ONLY",
            ),
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
    runtime_refs = sorted(_RUNTIME_EVIDENCE_REFS)
    runtime = {
        "status": "SYNTHETIC_BROWSER_RUNTIME_PASS",
        "verified_stages": list(_RUNTIME_STAGES),
        "evidence_refs": runtime_refs,
        "evidence_bundle_hash": _evidence_bundle_hash(runtime_refs),
        "live_site_verified": False,
        "final_submit": "USER_ONLY",
        "automatic_retry": False,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    runtime["runtime_evidence_hash"] = _runtime_evidence_hash(runtime)
    report = {
        "schema_version": 3,
        "status": "OFFLINE_ATS_CAPABILITIES",
        "provider_count": len(providers),
        "providers": providers,
        "browser_runtime_evidence": runtime,
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
    definitions = {str(item["provider"]): item for item in _CONTRACTS}
    if {str(item["provider"]) for item in value["providers"]} != set(definitions):
        raise JobOpsError(
            "ATS_CAPABILITY_PROVIDER_SET_INVALID",
            "The ATS capability report must disclose every declared provider exactly once.",
        )
    for item in value["providers"]:
        definition = definitions[str(item["provider"])]
        verified = item["verified_stages"]
        unverified = item["unverified_stages"]
        if set(verified) & set(unverified) or set(verified) | set(unverified) != set(_STAGES):
            raise JobOpsError(
                "ATS_CAPABILITY_STAGE_COVERAGE_INVALID",
                "Verified and unverified ATS stages must form one complete, non-overlapping disclosure.",
                provider=item["provider"],
            )
        expected_disclosure = {
            "offline_evidence_level": definition["offline_evidence_level"],
            "evidence_scope": definition["evidence_scope"],
            "saved_snapshot_modes": definition["saved_snapshot_modes"],
            "route_shape": definition["route_shape"],
            "dynamic_control_strategy": definition["dynamic_control_strategy"],
            "verified_stages": definition["verified_stages"],
            "unverified_stages": [stage for stage in _STAGES if stage not in definition["verified_stages"]],
            "evidence_refs": sorted(definition["evidence_refs"]),
            "known_limit_codes": sorted(definition["known_limit_codes"]),
            "user_present_prefill": _stage_support_status(
                "APPROVED_DOM_PREFILL",
                verified=definition["verified_stages"],
                verified_status="PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
            ),
            "approved_material_upload": _stage_support_status(
                "APPROVED_FILE_ATTACHMENT",
                verified=definition["verified_stages"],
                verified_status="PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
            ),
            "nonfinal_navigation": _stage_support_status(
                "EXPLICIT_NONFINAL_NAVIGATION",
                verified=definition["verified_stages"],
                verified_status="PROVIDER_EVIDENCE_VERIFIED_EXPLICIT_CONTROLS_ONLY",
            ),
        }
        if any(item[key] != expected for key, expected in expected_disclosure.items()):
            raise JobOpsError(
                "ATS_CAPABILITY_SCOPE_DRIFT",
                "An ATS provider disclosure no longer matches its declared evidence scope.",
                provider=item["provider"],
            )
        expected_bundle = _evidence_bundle_hash(item["evidence_refs"])
        if item["evidence_bundle_hash"] != expected_bundle:
            raise JobOpsError(
                "ATS_CAPABILITY_EVIDENCE_INTEGRITY_FAILED",
                "An ATS evidence bundle no longer matches its declared files.",
                provider=item["provider"],
            )
        if item["contract_hash"] != _contract_hash(item):
            raise JobOpsError(
                "ATS_CAPABILITY_INTEGRITY_FAILED",
                "An ATS capability contract hash no longer matches its contents.",
            )
    runtime = value["browser_runtime_evidence"]
    if runtime["verified_stages"] != list(_RUNTIME_STAGES) or runtime["evidence_refs"] != sorted(_RUNTIME_EVIDENCE_REFS):
        raise JobOpsError(
            "ATS_RUNTIME_EVIDENCE_SCOPE_DRIFT",
            "The shared browser-runtime disclosure no longer matches its declared evidence scope.",
        )
    if runtime["evidence_bundle_hash"] != _evidence_bundle_hash(runtime["evidence_refs"]):
        raise JobOpsError(
            "ATS_RUNTIME_EVIDENCE_INTEGRITY_FAILED",
            "The shared browser-runtime evidence no longer matches its declared files.",
        )
    if runtime["runtime_evidence_hash"] != _runtime_evidence_hash(runtime):
        raise JobOpsError(
            "ATS_RUNTIME_EVIDENCE_INTEGRITY_FAILED",
            "The shared browser-runtime evidence hash no longer matches its contents.",
        )
