from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, project_root, sha256_bytes


MAX_CONTINUOUS_JOBS = 1000
JOB_KEYS = {
    "input", "profile_ref", "master_resume_ref", "answer_bank_ref", "route", "form", "research",
    "source_type", "synthetic",
}
REQUIRED_JOB_KEYS = {"input", "profile_ref", "master_resume_ref", "answer_bank_ref", "synthetic"}


def _safe_project_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        return False
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def validate_continuous_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "mode", "jobs"}:
        raise JobOpsError("CONTINUOUS_MANIFEST_INVALID", "The manifest must contain only schema_version, mode and jobs.")
    if value.get("schema_version") != 1 or value.get("mode") != "MANUAL_TICK_ONLY":
        raise JobOpsError("CONTINUOUS_MANIFEST_INVALID", "Continuous intake remains manual-tick-only in this build.")
    jobs = value.get("jobs")
    if not isinstance(jobs, list) or not jobs or len(jobs) > MAX_CONTINUOUS_JOBS:
        raise JobOpsError("CONTINUOUS_MANIFEST_INVALID", "The manifest must contain 1 to 1000 local jobs.")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(jobs):
        if not isinstance(raw, dict) or set(raw) - JOB_KEYS or not REQUIRED_JOB_KEYS.issubset(raw):
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "A continuous job contains missing or unrecognized fields.", job_index=index)
        if raw.get("synthetic") is not True:
            raise JobOpsError("CONTINUOUS_REAL_INTAKE_NOT_AUTHORIZED", "Unattended continuous intake is limited to explicit synthetic fixtures.", job_index=index)
        if raw.get("source_type") not in {None, "txt", "html", "pdf", "snapshot"}:
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "A continuous job has an unsupported source type.", job_index=index)
        path_values = [raw.get(key) for key in ("input", "route", "form", "research") if raw.get(key) is not None]
        if not all(_safe_project_relative_path(item) for item in path_values):
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "Continuous manifest paths must be project-relative.", job_index=index)
        for key in ("profile_ref", "master_resume_ref", "answer_bank_ref"):
            if not isinstance(raw.get(key), str):
                raise JobOpsError("CONTINUOUS_JOB_INVALID", "Continuous private inputs must be opaque secure references.", job_index=index)
            validate_secure_reference(str(raw[key]))
        identity = str(raw["input"]).replace("\\", "/").casefold()
        if identity in seen:
            raise JobOpsError("CONTINUOUS_JOB_DUPLICATE", "The same local job input may appear only once per tick.", job_index=index)
        seen.add(identity)
        normalized.append({key: raw[key] for key in sorted(raw)})
    return {"schema_version": 1, "mode": "MANUAL_TICK_ONLY", "jobs": normalized}


def build_continuous_intake_plan(manifest: Any, queue_status: dict[str, int]) -> dict[str, Any]:
    normalized = validate_continuous_manifest(manifest)
    required = {"pending_limit", "awaiting_approval", "reserved_slots", "deferred_intake", "slots_available"}
    if not required.issubset(queue_status):
        raise JobOpsError("CONTINUOUS_QUEUE_STATUS_INVALID", "The queue status is incomplete.")
    available = max(0, int(queue_status["pending_limit"]) - int(queue_status["awaiting_approval"]) - int(queue_status["reserved_slots"]))
    if available != int(queue_status["slots_available"]):
        raise JobOpsError("CONTINUOUS_QUEUE_STATUS_INVALID", "Queue capacity fields are inconsistent.")
    jobs = normalized["jobs"]
    plan_material = {
        "manifest": normalized,
        "pending_limit": int(queue_status["pending_limit"]),
        "awaiting_approval": int(queue_status["awaiting_approval"]),
        "reserved_slots": int(queue_status["reserved_slots"]),
        "existing_deferred": int(queue_status["deferred_intake"]),
    }
    plan = {
        "schema_version": 1,
        "status": "MANUAL_TICK_READY" if available else "PAUSED_AT_PENDING_LIMIT",
        "mode": "MANUAL_TICK_ONLY",
        "plan_hash": sha256_bytes(canonical_json(plan_material)),
        "job_count": len(jobs),
        "pending_limit": int(queue_status["pending_limit"]),
        "awaiting_approval": int(queue_status["awaiting_approval"]),
        "reserved_slots": int(queue_status["reserved_slots"]),
        "existing_deferred": int(queue_status["deferred_intake"]),
        "slots_available": available,
        "jobs_eligible_this_tick": min(available, len(jobs)),
        "jobs_expected_to_defer": max(0, len(jobs) - available),
        "requires_explicit_invocation": True,
        "background_service_started": False,
        "system_tasks_registered": 0,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_named("continuous-intake-plan", plan, project_root() / "schemas")
    return plan
