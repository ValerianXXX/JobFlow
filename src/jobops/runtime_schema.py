from __future__ import annotations

import re
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
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            _fail(path, "Array items must be unique.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{index}]", root=root)
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            _fail(path, "String is shorter than allowed.")
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
        expected_provider = {"greenhouse_json": "greenhouse", "lever_json": "lever"}.get(provider_format)
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
    if name == "material-plan":
        cover = value.get("cover_letter", {})
        cover_requested = cover.get("request_status") != "NOT_REQUESTED"
        cover_generated = cover.get("generation_status") == "GENERATED_ON_DEMAND"
        cover_values = [
            cover.get("docx_secure_ref"), cover.get("docx_sha256"),
            cover.get("pdf_secure_ref"), cover.get("pdf_sha256"),
        ]
        if cover_requested != cover_generated or cover_generated != all(cover_values):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Cover Letter generation must exactly follow the detected form request.")
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
        ready = not value.get("blockers")
        if (value.get("status") == "PUBLIC_RELEASE_READY") != ready:
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "Release readiness status must match blocker presence.")
        if ready and not all((value.get("worktree_clean"), value.get("version_consistent"), value.get("independent_qa_fresh"))):
            raise JobOpsError("SCHEMA_SEMANTIC_CONFLICT", "A ready release must satisfy every final boolean gate.")


def validate_named(name: str, value: dict[str, Any], schema_dir: Path) -> dict[str, Any]:
    path = schema_dir / f"{name}.schema.json"
    if not path.is_file():
        raise JobOpsError("SCHEMA_NOT_FOUND", "The named runtime schema is missing.", schema=name)
    schema = load_json(path)
    validate_schema(value, schema)
    semantic_validate(name, value)
    return value
