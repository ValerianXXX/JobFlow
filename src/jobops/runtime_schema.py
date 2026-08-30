from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import JobOpsError
from .util import load_json, parse_iso


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _fail(path: str, message: str, **details: object) -> None:
    raise JobOpsError("SCHEMA_VALIDATION_FAILED", message, path=path or "$", **details)


_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_APPLICATION_WHEEL_PROVENANCE_FIELDS = {
    "format",
    "source_commit",
    "source_git_tree_oid",
    "source_build_tree_sha256",
    "source_archive_sha256",
    "build_lock_sha256",
    "build_recipe_sha256",
    "pass_a_wheel_sha256",
    "pass_b_wheel_sha256",
    "reproducible",
}
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "conin$",
    "conout$",
    "clock$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_RUNTIME_WHEEL_TAGS = {
    "cp313-cp313-win_amd64",
    "cp311-abi3-win_amd64",
    "py3-none-any",
    "py3-none-win_amd64",
}


def _validate_runtime_relative_path(value: object) -> str:
    """Mirror the PS5.1 bootstrap/verifier Windows path contract exactly."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 768
        or value != unicodedata.normalize("NFC", value)
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or value.endswith("/")
    ):
        raise JobOpsError("RUNTIME_CLOSURE_PATH_INVALID", "A runtime closure path is unsafe on Windows.")
    parts = value.split("/")
    for part in parts:
        if (
            not part
            or len(part) > 255
            or part in {".", ".."}
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            or any(ord(character) < 32 or ord(character) > 126 or character in '\"<>|?*' for character in part)
        ):
            raise JobOpsError("RUNTIME_CLOSURE_PATH_INVALID", "A runtime closure path is unsafe on Windows.")
    return value


def _require_explicit_utc(value: object, field: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z",
        value,
    ):
        raise JobOpsError("UPDATE_TIMESTAMP_INVALID", "Update timestamps must use explicit UTC Z notation.", field=field)


def validate_application_wheel_provenance(
    provenance: object,
    *,
    application_wheel_sha256: object,
    source_commit: object,
    build_lock_sha256: object | None = None,
) -> dict[str, Any]:
    """Fail closed unless one reproducible source-to-wheel chain is self-consistent."""
    if not isinstance(provenance, dict) or set(provenance) != _APPLICATION_WHEEL_PROVENANCE_FIELDS:
        raise JobOpsError(
            "APPLICATION_WHEEL_PROVENANCE_INVALID",
            "Application wheel provenance has an incomplete or unrecognized shape.",
        )
    if provenance.get("format") != "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1":
        raise JobOpsError("APPLICATION_WHEEL_PROVENANCE_INVALID", "Application wheel provenance format is unsupported.")
    if provenance.get("reproducible") is not True:
        raise JobOpsError("APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH", "Application wheel rebuilds did not match.")
    if not _COMMIT_PATTERN.fullmatch(str(provenance.get("source_commit", ""))) or not _COMMIT_PATTERN.fullmatch(
        str(provenance.get("source_git_tree_oid", ""))
    ):
        raise JobOpsError("APPLICATION_WHEEL_PROVENANCE_INVALID", "Application wheel source identity is invalid.")
    for field in (
        "source_build_tree_sha256",
        "source_archive_sha256",
        "build_lock_sha256",
        "build_recipe_sha256",
        "pass_a_wheel_sha256",
        "pass_b_wheel_sha256",
    ):
        if not _SHA256_PATTERN.fullmatch(str(provenance.get(field, ""))):
            raise JobOpsError(
                "APPLICATION_WHEEL_PROVENANCE_INVALID",
                "Application wheel provenance contains an invalid digest.",
                field=field,
            )
    if provenance.get("source_commit") != source_commit:
        raise JobOpsError("APPLICATION_WHEEL_SOURCE_COMMIT_MISMATCH", "Application wheel provenance binds a different source commit.")
    expected_wheel = str(application_wheel_sha256)
    if not _SHA256_PATTERN.fullmatch(expected_wheel) or any(
        provenance.get(field) != expected_wheel
        for field in ("pass_a_wheel_sha256", "pass_b_wheel_sha256")
    ):
        raise JobOpsError("APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH", "Application wheel digests do not agree.")
    if build_lock_sha256 is not None and provenance.get("build_lock_sha256") != build_lock_sha256:
        raise JobOpsError("APPLICATION_WHEEL_BUILD_LOCK_MISMATCH", "Application wheel provenance binds a different build lock.")
    return provenance


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$", *, root: dict[str, Any] | None = None) -> None:
    root = root or schema
    if "$ref" in schema:
        reference = str(schema["$ref"])
        if not reference.startswith("#/"):
            _fail(path, "Only local schema references are supported.")
        resolved: Any = root
        for part in reference[2:].split("/"):
            resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
        validate_schema(value, resolved, path, root=root)
        return
    expected = schema.get("type")
    if expected is not None:
        accepted = [expected] if isinstance(expected, str) else expected
        if not any(_matches_type(value, item) for item in accepted):
            _fail(path, "Value has the wrong JSON type.", expected=accepted, actual=type(value).__name__)
    if "const" in schema and value != schema["const"]:
        _fail(path, "Value does not match the required constant.", expected=schema["const"])
    if "enum" in schema and value not in schema["enum"]:
        _fail(path, "Value is outside the allowed enum.", allowed=schema["enum"], actual=value)
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            _fail(path, "Object is missing required properties.", missing=missing)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                _fail(path, "Object contains unrecognized properties.", extras=extras)
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], f"{path}.{key}", root=root)
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], f"{path}.{key}", root=root)
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            _fail(path, "Array contains too few items.")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            _fail(path, "Array contains too many items.")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            _fail(path, "Array items must be unique.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{index}]", root=root)
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            _fail(path, "String is shorter than allowed.")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            _fail(path, "String is longer than allowed.")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            _fail(path, "String does not match the required pattern.", pattern=schema["pattern"])
        if schema.get("format") == "date-time":
            try:
                parse_iso(value)
            except Exception as exc:
                _fail(path, "String is not a valid timezone-aware date-time.", error=type(exc).__name__)
        if schema.get("format") == "uri":
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                _fail(path, "String is not an absolute URI.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, "Number is below the minimum.", minimum=schema["minimum"])
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, "Number exceeds the maximum.", maximum=schema["maximum"])


def semantic_validate(name: str, value: dict[str, Any]) -> None:
    if name == "claim":
        if parse_iso(value["last_verified_at"]) >= parse_iso(value["expires_at"]):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Claim expiry must follow verification.")
        if bool(value.get("approved_for_external")) != (value.get("lifecycle_status") == "approved"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Claim approval flag and lifecycle status must agree.")
    if name == "approval":
        if parse_iso(value["issued_at"]) >= parse_iso(value["expires_at"]):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Approval expires_at must be later than issued_at.")
        if value.get("status") == "CONSUMED" and not value.get("consumed_at"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Consumed approval requires consumed_at.")
    if name == "final-submission-authorization":
        if parse_iso(value["issued_at"]) >= parse_iso(value["expires_at"]):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Final submission authorization must expire after issuance.")
        if value.get("status") == "CONSUMED" and not value.get("consumed_at"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Consumed final submission authorization requires consumed_at.")
    if name == "application-execution-checkpoint":
        expected = {
            1: {("PLAN_VALIDATED", "PASS")},
            2: {("FRESHNESS_BOUND", "PASS")},
            3: {("PREFILL_PROPOSAL_VALIDATED", "PASS")},
            4: {("SCOPED_ACTIONS_VALIDATED", "CONSUMED")},
            5: {("AWAITING_FINAL_AUTHORIZATION", "AWAITING_USER")},
            6: {("FINAL_AUTHORIZATION_CONSUMED", "CONSUMED")},
            7: {("FAKE_SUBMISSION_RECORDED", "RECORDED")},
            8: {("RECEIPT_VERIFIED", "CONFIRMED"), ("SUBMISSION_UNKNOWN", "UNKNOWN")},
        }
        pair = (value.get("phase"), value.get("status"))
        recovery_pair = (
            value.get("sequence") in {6, 7, 8}
            and value.get("phase") == "INTERRUPTION_RECONCILED"
            and value.get("status") in {"CONFIRMED", "UNKNOWN"}
        )
        if pair not in expected.get(value.get("sequence"), set()) and not recovery_pair:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Execution checkpoint phase and status do not match its fixed sequence.")
    if name == "ats-transport-envelope":
        expected_authorization = {
            "read_official_job": "SCOPED_ACTION_SESSION_USE",
            "inspect_application_form": "SCOPED_ACTION_SESSION_USE",
            "prefill_application_form": "SCOPED_ACTION_SESSION_USE",
            "upload_materials": "SCOPED_ACTION_SESSION_USE",
            "submit_application": "FINAL_SUBMISSION_AUTHORIZATION",
            "verify_receipt": "SUBMISSION_ATTEMPT",
        }.get(value.get("action"))
        if value.get("authorization_kind") != expected_authorization:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS transport action and authorization type do not match.")
    if name == "external-action-session":
        if parse_iso(value["issued_at"]) >= parse_iso(value["expires_at"]):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "External action session must expire after issuance.")
        if value.get("allowed_actions") != sorted(value.get("allowed_actions", [])):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "External action session scopes must be canonical and sorted.")
        if value.get("status") == "REVOKED" and not value.get("revoked_at"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "A revoked action session requires revoked_at.")
        if value.get("status") != "REVOKED" and value.get("revoked_at"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Only a revoked action session may contain revoked_at.")
    if name == "source-route":
        history = value.get("navigation_history", [])
        if not history or history[-1] != value.get("current_url"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Route history must end at current_url.")
        from .sourcing import source_route_hash
        if value.get("route_hash") != source_route_hash(value):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Source route hash does not bind the current route content.")
    if name == "jd-snapshot" and value.get("source_format") == "page_snapshot" and not value.get("source_url"):
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "A saved page snapshot must retain its source URL.")
    if name == "fit-result":
        if value.get("eligibility_status") == "INELIGIBLE" and value.get("recommendation") != "DO_NOT_APPLY":
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Hard ineligibility must override the aggregate Fit score.")
        if value.get("eligibility_status") == "NEEDS_USER_INPUT" and value.get("recommendation") == "RECOMMEND":
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Unknown hard conditions cannot produce an unconditional recommendation.")
    if name == "queue-reservation" and value.get("pending_limit", 0) < 1:
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Queue reservation limit must be positive.")
    if name == "queue-reservation" and value.get("pending_count", 0) + value.get("reserved_count", 0) > value.get("pending_limit", 0):
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Pending plus reserved capacity cannot exceed the configured limit.")
    if name == "knowledge-evidence" and parse_iso(value["last_verified_at"]) >= parse_iso(value["expires_at"]):
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Knowledge evidence must expire after it was verified.")
    if name == "requirement":
        threshold = value.get("threshold")
        if value.get("logic") == "AT_LEAST" and (threshold is None or threshold > len(value.get("items", []))):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "AT_LEAST requirements need a reachable positive threshold.")
        if value.get("logic") != "AT_LEAST" and threshold is not None:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Only AT_LEAST requirements may carry a threshold.")
    if name == "research-finding":
        if value.get("official") and not str(value.get("source_type", "")).startswith("official_"):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Official research evidence needs an official source type.")
        if value.get("claim") not in value.get("evidence_excerpt", "") and value.get("evidence_excerpt") not in value.get("claim", ""):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "The finding must be directly supported by its saved excerpt.")
    if name == "application-field":
        stop_class = str(value.get("classification", "")).endswith("_stop") or value.get("classification") in {"sensitive_review"}
        if stop_class and value.get("action") != "STOP":
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Sensitive, unknown, and final-submit fields must stop.")
    if name == "recovery-event" and value.get("blocked_state") == "SUBMISSION_UNKNOWN" and value.get("decision") not in {"NO_AUTO_RETRY", "CLOSE"}:
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "SUBMISSION_UNKNOWN cannot resume or retry automatically.")
    if name == "site-policy" and value.get("real_actions_enabled") is not False:
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Real site actions remain disabled in this build.")
    if name == "application" and value.get("status") in {"SUBMITTING", "SUBMITTED", "CONFIRMED"} and value.get("dry_run") is not True:
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Application records retain the dry-run constraint in this schema version.")
    if name == "receipt" and value.get("verified") is not True:
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Only verified evidence may be persisted as a receipt.")
    if name == "official-discovery":
        candidates = value.get("candidates", [])
        if value.get("candidate_count") != len(candidates):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Official discovery candidate_count must match the candidate list.")
        if any(item.get("snapshot_hash") != value.get("snapshot_hash") for item in candidates):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Every discovered candidate must retain the same local snapshot hash.")
        provider_format = str(value.get("source_format", ""))
        expected_provider = {
            "greenhouse_json": "greenhouse",
            "lever_json": "lever",
            "ashby_json": "ashby",
            "smartrecruiters_json": "smartrecruiters",
        }.get(provider_format)
        if expected_provider and any(
            item.get("provider") != expected_provider or item.get("evidence_kind") != "provider_json"
            for item in candidates
        ):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Saved ATS JSON candidates must match the declared provider and retain provider_json evidence.",
            )
        if not expected_provider and any(item.get("evidence_kind") == "provider_json" for item in candidates):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "HTML and saved-page discovery cannot claim provider_json evidence.",
            )
    if name == "external-claim-set":
        from .external_claims import validate_external_claim_set_integrity
        validate_external_claim_set_integrity(value)
    if name == "application-readiness":
        ready = value.get("status") == "READY_FOR_OFFLINE_APPLICATION_PREPARATION"
        capabilities = value.get("capabilities", {})
        if ready != (not value.get("blockers")):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Application readiness and blocker state disagree.")
        for key in (
            "offline_application_preparation", "tailored_resume_generation",
            "on_demand_cover_letter_generation", "review_packet_generation",
        ):
            if bool(capabilities.get(key)) != ready:
                raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Application readiness capability flags disagree.")
    if name == "resume-tailoring-manifest":
        from .resume_tailoring import validate_resume_tailoring_manifest_integrity
        validate_resume_tailoring_manifest_integrity(value)
    if name == "ats-form-snapshot":
        if value.get("field_count") != len(value.get("fields", [])):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS form field_count must match the field list.")
        counts: dict[str, int] = {}
        for field in value.get("fields", []):
            classification = str(field.get("classification"))
            counts[classification] = counts.get(classification, 0) + 1
        if value.get("classification_counts") != counts:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS form classification counts do not match the field list.")
    if name == "browser-action-plan":
        actions = value.get("actions", [])
        fillable = sum(item.get("action") == "PROPOSE_PREFILL" for item in actions)
        if value.get("fillable_count") != fillable or value.get("stopped_count") != len(actions) - fillable:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Browser action plan counts do not match its actions.")
        for item in actions:
            is_fill = item.get("action") == "PROPOSE_PREFILL"
            if is_fill != (item.get("binding_kind") != "NONE" and item.get("binding_ref") is not None):
                raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Only bound controls may be proposed for prefill.")
    if name == "ats-vertical-evidence":
        if value.get("fields_discovered") != value.get("fields_proposed", 0) + value.get("fields_stopped", 0):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS vertical field counts must cover every discovered control.")
    if name == "ats-form-sequence":
        steps = value.get("steps", [])
        if value.get("step_count") != len(steps) or [item.get("step_index") for item in steps] != list(range(1, len(steps) + 1)):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS form sequence steps must be complete and monotonically numbered.")
    if name == "ats-capability-report" and value.get("provider_count") != len(value.get("providers", [])):
        raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "ATS capability provider_count must match the provider list.")
    if name == "continuous-intake-plan":
        if value.get("job_count") != value.get("jobs_eligible_this_tick", 0) + value.get("jobs_expected_to_defer", 0):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Continuous plan must account for every job in the manual tick.")
        expected = "MANUAL_TICK_READY" if value.get("slots_available", 0) else "PAUSED_AT_PENDING_LIMIT"
        if value.get("status") != expected:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Continuous plan status must match available queue capacity.")
    if name == "continuous-intake-result":
        results = value.get("results", [])
        expected_ordinals = list(range(1, len(results) + 1))
        if value.get("job_count") != len(results) or [item.get("ordinal") for item in results] != expected_ordinals:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Continuous result rows must account for every job in order.")
        expected_counts = {
            "prepared_count": sum(item.get("status") == "PREPARED" for item in results),
            "deduplicated_count": sum(item.get("status") == "ALREADY_TRACKED" for item in results),
            "deferred_count": sum(item.get("status") == "DEFERRED_CAPACITY" for item in results),
            "failed_count": sum(item.get("status") == "LOCAL_ERROR" for item in results),
        }
        if any(value.get(key) != count for key, count in expected_counts.items()):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Continuous result counters must match the redacted rows.")
        expected_status = (
            "COMPLETED_WITH_LOCAL_ERRORS" if expected_counts["failed_count"]
            else "PAUSED_AT_PENDING_LIMIT" if expected_counts["deferred_count"]
            else "MANUAL_TICK_COMPLETE"
        )
        if value.get("status") != expected_status:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Continuous result status must match its job outcomes.")
        for item in results:
            failed = item.get("status") == "LOCAL_ERROR"
            if failed != bool(item.get("error_code")):
                raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Only failed local jobs may expose an error code.")
    if name == "material-plan":
        cover = value.get("cover_letter", {})
        cover_requested = cover.get("request_status") != "NOT_REQUESTED"
        cover_generated = cover.get("generation_status") == "GENERATED_ON_DEMAND"
        cover_values = [
            cover.get("docx_secure_ref"), cover.get("docx_sha256"),
            cover.get("pdf_secure_ref"), cover.get("pdf_sha256"), cover.get("narrative_sha256"),
            cover.get("narrative_character_count"),
        ]
        if cover_requested != cover_generated or cover_generated != all(cover_values):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Cover Letter generation must exactly follow the detected form request.")
        target_status = cover.get("narrative_target_status")
        target_count = cover.get("narrative_target_count")
        target_ref = cover.get("narrative_control_ref")
        target_max = cover.get("narrative_max_characters")
        target_shape_valid = (
            (target_status == "NOT_REQUESTED" and target_count == 0 and target_ref is None and target_max is None)
            or (target_status == "AMBIGUOUS" and isinstance(target_count, int) and target_count > 1 and target_ref is None and target_max is None)
            or (target_status == "INVALID_MAX_LENGTH" and target_count == 1 and bool(target_ref) and target_max is None)
            or (
                target_status == "BOUND_EXACT_CONTROL"
                and target_count == 1
                and bool(target_ref)
                and isinstance(target_max, int)
                and not isinstance(target_max, bool)
                and 1 <= target_max <= 4_000
            )
        )
        if not target_shape_valid:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "The application narrative target must bind exactly one eligible textarea or fail closed.")
        for link in value.get("public_links", []):
            bound = link.get("binding_status") == "BOUND_CONFIRMED_PUBLIC_VALUE"
            if bound != bool(link.get("value_sha256")):
                raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "A public-link binding must contain only its confirmed value hash.")
        portfolio = value.get("portfolio_file", {})
        portfolio_requested = portfolio.get("request_status") != "NOT_REQUESTED"
        portfolio_bound = portfolio.get("binding_status") == "BOUND_SECURE_FILE"
        portfolio_values = [portfolio.get("secure_ref"), portfolio.get("sha256"), portfolio.get("safe_filename")]
        if portfolio_bound != all(portfolio_values):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "A bound portfolio file must include one complete secure binding.")
        if not portfolio_requested and portfolio.get("binding_status") != "NOT_REQUESTED":
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "An unrequested portfolio file must not be attached.")
        required_missing = any(
            item.get("required") and item.get("binding_status") == "MISSING_USER_VALUE"
            for item in value.get("public_links", [])
        ) or (
            portfolio.get("request_status") == "REQUESTED_REQUIRED" and not portfolio_bound
        )
        expected_status = "NEEDS_USER_MATERIAL" if required_missing else "READY_FOR_REVIEW"
        if value.get("status") != expected_status:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "The material-plan status must match its required missing bindings.")
    if name == "application-execution-plan":
        from .application_execution import validate_application_execution_plan_integrity
        validate_application_execution_plan_integrity(value)
    if name == "release-readiness":
        blockers = value.get("blockers")
        status = value.get("status")
        ready = value.get("public_release_ready")
        runtime_status = value.get("runtime_closure_status")
        attestation_status = value.get("release_attestation_status")
        clean_status = value.get("clean_windows_evidence_status")
        failure_code = value.get("release_attestation_failure_code")
        manual_clean = value.get("manual_release_gates", {}).get(
            "clean_windows_profile"
        )
        if not isinstance(blockers, list):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Public release blockers must be a validated list.",
            )
        if status == "PUBLIC_RELEASE_READY":
            if (
                ready is not True
                or blockers
                or runtime_status != "ATTESTED"
                or attestation_status != "PASS"
                or clean_status != "PASS"
                or failure_code is not None
                or manual_clean != "PASS"
            ):
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "Public release readiness requires an empty blocker set and the complete verified evidence chain.",
                )
        elif status == "PUBLIC_RELEASE_BLOCKED":
            if ready is not False or not blockers:
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "A blocked public release must expose at least one stable blocker.",
                )
        else:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The public release readiness status is invalid.",
            )
        if runtime_status == "UNATTESTED":
            if "RELEASE_RUNTIME_CLOSURE_UNATTESTED" not in blockers:
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "An unattested runtime closure must block public release.",
                )
        elif runtime_status != "ATTESTED":
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The runtime closure attestation status is invalid.",
            )
        expected_attestation_blocker = {
            "MISSING": "RELEASE_ATTESTATION_MISSING",
            "INVALID": "RELEASE_ATTESTATION_INVALID",
        }.get(attestation_status)
        if expected_attestation_blocker is not None and expected_attestation_blocker not in blockers:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Missing or invalid signed release evidence must block public release.",
            )
        if (attestation_status == "PASS") != (runtime_status == "ATTESTED"):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Only a fully verified signed release chain can attest the runtime closure.",
            )
        expected_clean = {
            "PASS": "PASS",
            "MISSING": "PENDING",
            "INVALID": "INVALID",
            "NOT_CHECKED": "PENDING",
        }.get(clean_status)
        if expected_clean is None or manual_clean != expected_clean:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The compatibility clean-Windows gate must derive from canonical external evidence.",
            )
        clean_blocker = {
            "MISSING": "CLEAN_WINDOWS_EVIDENCE_MISSING",
            "INVALID": "CLEAN_WINDOWS_EVIDENCE_INVALID",
        }.get(clean_status)
        if clean_blocker is not None and clean_blocker not in blockers:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Missing or invalid clean-Windows evidence must block public release.",
            )
        if clean_status == "NOT_CHECKED" and attestation_status == "PASS":
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Clean-Windows evidence may be unchecked only while the signed release chain is unavailable.",
            )
        if (attestation_status != "PASS" or clean_status != "PASS") and not isinstance(
            failure_code, str
        ):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "An incomplete release evidence chain must expose a stable redacted failure code.",
            )
    if name == "update-manifest-v2":
        from .util import canonical_json, sha256_bytes

        release = value.get("release", {})
        asset = value.get("asset", {})
        predecessor = value.get("predecessor", {})
        closure = value.get("runtime_closure", {})
        attestation = value.get("publisher_attestation", {})
        policy = value.get("policy", {})
        version = str(release.get("version", ""))
        for timestamp_field, timestamp_value in (
            ("issued_at_utc", value.get("issued_at_utc")),
            ("publisher_attestation.issued_at_utc", attestation.get("issued_at_utc")),
            ("publisher_attestation.evidence_expires_at_utc", attestation.get("evidence_expires_at_utc")),
        ):
            _require_explicit_utc(timestamp_value, timestamp_field)
        version_tuple = tuple(int(part) for part in version.split("."))
        expected_name = f"JobFlow-v{version}-windows-x64-complete.zip"
        expected_prefix = f"JobFlow-v{version}-windows-x64/"
        if asset.get("name") != expected_name or asset.get("archive_prefix") != expected_prefix:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The update asset name and archive prefix must bind the declared release version.",
            )
        if predecessor.get("maximum_version_exclusive") != version:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The predecessor upper bound must be the declared release version.",
            )
        if tuple(int(part) for part in str(predecessor.get("minimum_version", "")).split(".")) >= version_tuple:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The predecessor minimum must be lower than the declared release version.",
            )
        legacy_predecessors = value.get("legacy_v1_predecessors")
        if legacy_predecessors is not None:
            if not 1 <= len(legacy_predecessors) <= 64:
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "The signed legacy-v1 predecessor authorization set must contain 1 to 64 identities.",
                )
            identities: list[tuple[tuple[int, int, int], str, str]] = []
            directories: set[str] = set()
            for item in legacy_predecessors:
                legacy_version = str(item.get("version", ""))
                source_sha256 = str(item.get("source_sha256", ""))
                version_directory = str(item.get("version_directory", ""))
                expected_directory = f"v{legacy_version}-{source_sha256[:12]}"
                if version_directory != expected_directory:
                    raise JobOpsError(
                        "SCHEMA_SEMANTIC_CONFLICT",
                        "A legacy-v1 predecessor directory must bind its exact version and source digest.",
                    )
                identity = (
                    tuple(int(part) for part in legacy_version.split(".")),
                    source_sha256,
                    version_directory,
                )
                if identity[0] >= version_tuple:
                    raise JobOpsError(
                        "SCHEMA_SEMANTIC_CONFLICT",
                        "A legacy-v1 predecessor must be older than the declared release version.",
                    )
                if identity in identities or version_directory in directories:
                    raise JobOpsError(
                        "SCHEMA_SEMANTIC_CONFLICT",
                        "The signed legacy-v1 predecessor authorization set is ambiguous or duplicated.",
                    )
                identities.append(identity)
                directories.add(version_directory)
            if identities != sorted(identities):
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "Legacy-v1 predecessor identities must be in canonical version and digest order.",
                )
        for field in ("minimum_updater_version", "minimum_bootstrap_version"):
            minimum = tuple(int(part) for part in str(policy.get(field, "")).split("."))
            if minimum > version_tuple:
                raise JobOpsError(
                    "SCHEMA_SEMANTIC_CONFLICT",
                    "Update policy cannot require a bootstrap or updater newer than the declared release.",
                )
        if (
            closure.get("source_commit") != release.get("source_commit")
            or closure.get("platform") != release.get("platform")
            or closure.get("source_payload_sha256") != asset.get("sha256")
        ):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The structural runtime closure must bind the release commit, platform, and source payload.",
            )
        closure_build_inputs = closure.get("build_inputs", {})
        validate_application_wheel_provenance(
            closure_build_inputs.get("application_wheel_provenance"),
            application_wheel_sha256=closure_build_inputs.get("application_wheel_sha256"),
            source_commit=release.get("source_commit"),
        )
        attested_bindings = {
            "runtime_closure_manifest_sha256": closure.get("manifest_sha256"),
            "runtime_tree_sha256": closure.get("tree_sha256"),
            "build_inputs_sha256": sha256_bytes(canonical_json(closure.get("build_inputs", {}))),
            "source_commit": closure.get("source_commit"),
            "source_payload_sha256": closure.get("source_payload_sha256"),
            "file_count": closure.get("file_count"),
            "total_bytes": closure.get("total_bytes"),
            "policy_sha256": sha256_bytes(canonical_json(policy)),
        }
        if any(attestation.get(key) != expected for key, expected in attested_bindings.items()):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The external publisher attestation must bind the structural closure, source payload, counts, and policy.",
            )
        attested_at = parse_iso(str(attestation.get("issued_at_utc")))
        manifest_issued_at = parse_iso(str(value.get("issued_at_utc")))
        evidence_expires_at = parse_iso(str(attestation.get("evidence_expires_at_utc")))
        if attested_at > manifest_issued_at:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The publisher attestation cannot postdate the signed update manifest.",
            )
        if manifest_issued_at >= evidence_expires_at:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The signed update manifest must be issued before its publisher evidence expires.",
            )
    if name == "installed-pointer-v2":
        version = str(value.get("version", ""))
        source_payload = str(value.get("source_payload_sha256", ""))
        digest = source_payload.removeprefix("sha256:")
        expected_directory = f"v{version}-{digest[:12]}"
        if value.get("version_directory") != expected_directory:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The installed pointer directory must bind the version and source payload digest.",
            )
        bootstrap = tuple(int(part) for part in str(value.get("bootstrap_version", "")).split("."))
        installed = tuple(int(part) for part in version.split("."))
        if bootstrap > installed:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The installed pointer cannot claim a bootstrap newer than its installed release.",
            )
    if name == "runtime-closure":
        python = value.get("python", {})
        build_inputs = value.get("build_inputs", {})
        layout = value.get("layout", {})
        protected = value.get("protected_builder", {})
        smoke = value.get("offline_smoke_tests", {})
        version = str(python.get("version", ""))
        compact = "313"
        expected_artifact = "python-3.13.15-embed-amd64.zip"
        expected_pth = "runtime/python313._pth"
        records = value.get("files", [])
        paths = [_validate_runtime_relative_path(item.get("path")) for item in records if isinstance(item, dict)]
        for layout_field in ("python", "python_pth", "application_root"):
            _validate_runtime_relative_path(layout.get(layout_field))
        if (
            version != "3.13.15"
            or python.get("artifact_name") != expected_artifact
            or layout.get("python") != "runtime/python.exe"
            or layout.get("python_pth") != expected_pth
            or layout.get("application_root") != "app"
            or layout.get("module") != "jobops.cli"
        ):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "The embedded Python artifact, version and isolated path file must agree.",
            )
        def _runtime_path_sort_key(path: str) -> tuple[str, ...]:
            # Runtime paths are already restricted to ASCII.  Uppercasing
            # matches the Windows producer's OrdinalIgnoreCase ordering,
            # including punctuation relative to letters.
            return tuple(part.upper() for part in path.split("/"))

        if paths != sorted(paths, key=_runtime_path_sort_key) or len({path.casefold() for path in paths}) != len(paths):
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "Runtime closure file records must be unique and canonically sorted.",
            )
        if value.get("file_count") != len(records):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Runtime closure file_count must match its records.")
        if value.get("total_bytes") != sum(
            item.get("size", 0) for item in records if isinstance(item, dict)
        ):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Runtime closure total_bytes must match its records.")
        from .util import canonical_json, sha256_bytes

        if value.get("tree_sha256") != sha256_bytes(canonical_json(records)):
            raise JobOpsError(
                "RUNTIME_CLOSURE_DIGEST_MISMATCH",
                "Runtime closure tree_sha256 must bind the canonical file records.",
            )
        required_paths = {
            ".jobops-root",
            "runtime/python.exe",
            f"runtime/python{compact}.dll",
            expected_pth,
            f"runtime/python{compact}.zip",
            "app/jobops/__init__.py",
            "app/jobops/cli.py",
            "app/jobops/runtime_health.py",
            "config/windows-cp313-build.lock",
            "config/windows-cp313-runtime.lock",
        }
        if not required_paths.issubset(set(paths)):
            raise JobOpsError("RUNTIME_CLOSURE_LAYOUT_MISSING", "Runtime closure required files are missing.")
        wheels = build_inputs.get("wheels", [])
        wheel_names: set[str] = set()
        for wheel in wheels:
            name = str(wheel.get("name", ""))
            version_value = str(wheel.get("version", ""))
            tag = str(wheel.get("tag", ""))
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                or not re.fullmatch(r"[A-Za-z0-9_.+-]+", version_value)
                or tag not in _RUNTIME_WHEEL_TAGS
                or name.casefold() in wheel_names
            ):
                raise JobOpsError("RUNTIME_CLOSURE_WHEEL_INVALID", "Runtime wheel metadata is unsafe or unsupported.")
            wheel_names.add(name.casefold())
        if value.get("status") != "BUILT_UNATTESTED":
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "A local structural runtime closure cannot self-assert trusted provenance.",
            )
        if python.get("sigstore_verified") is not False or protected.get("outer_signature_ready") is not False:
            raise JobOpsError(
                "SCHEMA_SEMANTIC_CONFLICT",
                "A local structural runtime closure cannot self-assert external publisher verification.",
            )
        if (
            smoke.get("import_passed") is not True
            or smoke.get("schema_passed") is not True
            or smoke.get("external_actions") != 0
            or protected.get("deterministic_rebuild_match") is not True
        ):
            raise JobOpsError(
                "RUNTIME_CLOSURE_EVIDENCE_INVALID",
                "Runtime closure smoke and deterministic rebuild evidence must be successful.",
            )
        validate_application_wheel_provenance(
            build_inputs.get("application_wheel_provenance"),
            application_wheel_sha256=build_inputs.get("application_wheel_sha256"),
            source_commit=value.get("source_commit"),
        )


def validate_named(name: str, value: dict[str, Any], schema_dir: Path) -> dict[str, Any]:
    path = schema_dir / f"{name}.schema.json"
    if not path.is_file():
        raise JobOpsError("SCHEMA_NOT_FOUND", "The named runtime schema is missing.", schema=name)
    schema = load_json(path)
    validate_schema(value, schema)
    semantic_validate(name, value)
    return value
