from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .errors import JobOpsError
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, iso_utc, load_json, project_root, sha256_bytes, write_json


MAX_CONTINUOUS_JOBS = 1000
JOB_KEYS = {
    "input", "profile_ref", "master_resume_ref", "answer_bank_ref", "route", "form", "research",
    "external_claim_set_ref", "tailoring_manifest_ref", "source_type", "synthetic",
}
REQUIRED_JOB_KEYS = {"input", "profile_ref", "master_resume_ref", "answer_bank_ref", "synthetic"}


def _safe_project_relative_path(value: Any) -> bool:
    if (
        not isinstance(value, str) or not value or len(value) > 512
        or "\x00" in value or ":" in value or any(ord(character) < 32 for character in value)
    ):
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
        if not isinstance(raw.get("synthetic"), bool):
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "Every continuous job must declare whether it is a synthetic fixture.", job_index=index)
        if raw.get("synthetic") is False and not all(raw.get(key) for key in ("route", "form", "research")):
            raise JobOpsError(
                "CONTINUOUS_LOCAL_EVIDENCE_REQUIRED",
                "A real-profile manual tick requires explicit local route, form and research snapshots for every job.",
                job_index=index,
            )
        if raw.get("source_type") not in {None, "txt", "html", "pdf", "snapshot"}:
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "A continuous job has an unsupported source type.", job_index=index)
        path_values = [raw.get(key) for key in ("input", "route", "form", "research") if raw.get(key) is not None]
        if not all(_safe_project_relative_path(item) for item in path_values):
            raise JobOpsError("CONTINUOUS_JOB_INVALID", "Continuous manifest paths must be project-relative.", job_index=index)
        for key in ("profile_ref", "master_resume_ref", "answer_bank_ref"):
            if not isinstance(raw.get(key), str):
                raise JobOpsError("CONTINUOUS_JOB_INVALID", "Continuous private inputs must be opaque secure references.", job_index=index)
            validate_secure_reference(str(raw[key]))
        for key in ("external_claim_set_ref", "tailoring_manifest_ref"):
            if raw.get(key) is not None:
                if not isinstance(raw[key], str):
                    raise JobOpsError("CONTINUOUS_JOB_INVALID", "Optional private bindings must be opaque secure references.", job_index=index)
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


def run_continuous_intake_tick(
    manifest: Any,
    *,
    queue_status: Callable[[], dict[str, int]],
    prepare_job: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run one explicit local batch and return only redacted operational evidence.

    The caller supplies the local preparation function.  No background service,
    browser transport, scheduler, or network adapter is available through this
    function.  A failed job does not prevent later jobs from being considered.
    """

    normalized = validate_continuous_manifest(manifest)
    plan = build_continuous_intake_plan(normalized, queue_status())
    results: list[dict[str, Any]] = []
    counts = {"prepared": 0, "deduplicated": 0, "deferred": 0, "failed": 0}
    for ordinal, item in enumerate(normalized["jobs"], 1):
        safe = {
            "ordinal": ordinal,
            "source_type": str(item.get("source_type") or "txt"),
            "source_mode": "SYNTHETIC_FIXTURE" if item["synthetic"] else "SAVED_LOCAL_EVIDENCE",
            "application_id": None,
            "error_code": None,
            "real_external_actions": 0,
        }
        try:
            raw = prepare_job(dict(item))
            if not isinstance(raw, dict) or int(raw.get("real_external_actions", 0)) != 0:
                raise JobOpsError(
                    "CONTINUOUS_EXTERNAL_ACTION_FORBIDDEN",
                    "A manual local tick may not report or perform a real external action.",
                )
            application_id = raw.get("application_id")
            if isinstance(application_id, str) and re.fullmatch(r"APP-[A-F0-9]{12}", application_id):
                safe["application_id"] = application_id
            if raw.get("status") == "DEFERRED":
                safe.update({"status": "DEFERRED_CAPACITY", "next_safe_action": "REVIEW_PENDING_APPLICATIONS"})
                counts["deferred"] += 1
            elif raw.get("deduplicated") is True:
                safe.update({"status": "ALREADY_TRACKED", "next_safe_action": "REVIEW_CURRENT_APPLICATION_STATE"})
                counts["deduplicated"] += 1
            elif raw.get("status") == "AWAITING_APPROVAL":
                safe.update({"status": "PREPARED", "next_safe_action": "REVIEW_APPLICATION_PACKET"})
                counts["prepared"] += 1
            else:
                safe.update({"status": "ALREADY_TRACKED", "next_safe_action": "REVIEW_CURRENT_APPLICATION_STATE"})
                counts["deduplicated"] += 1
        except JobOpsError as exc:
            safe.update({
                "status": "LOCAL_ERROR", "error_code": exc.code,
                "next_safe_action": "FIX_LOCAL_EVIDENCE_AND_RETRY_MANUAL_TICK",
            })
            counts["failed"] += 1
        except Exception:
            safe.update({
                "status": "LOCAL_ERROR", "error_code": "LOCAL_PREPARATION_FAILED",
                "next_safe_action": "FIX_LOCAL_EVIDENCE_AND_RETRY_MANUAL_TICK",
            })
            counts["failed"] += 1
        results.append(safe)

    final_queue = queue_status()
    status = (
        "COMPLETED_WITH_LOCAL_ERRORS" if counts["failed"]
        else "PAUSED_AT_PENDING_LIMIT" if counts["deferred"]
        else "MANUAL_TICK_COMPLETE"
    )
    outcome = {
        "schema_version": 1,
        "status": status,
        "mode": "MANUAL_TICK_ONLY",
        "plan_hash": plan["plan_hash"],
        "job_count": len(results),
        "prepared_count": counts["prepared"],
        "deduplicated_count": counts["deduplicated"],
        "deferred_count": counts["deferred"],
        "failed_count": counts["failed"],
        "results": results,
        "queue": {
            key: int(final_queue[key])
            for key in ("pending_limit", "awaiting_approval", "reserved_slots", "deferred_intake", "slots_available")
        },
        "requires_explicit_invocation": True,
        "background_service_started": False,
        "system_tasks_registered": 0,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_named("continuous-intake-result", outcome, project_root() / "schemas")
    return outcome


class ContinuousIntakeDescriptorStore:
    """Persist only opaque references and project-relative local evidence paths.

    Descriptor files live beside the local database under ``state`` (or beside a
    test database), never in tracked source.  They contain no applicant values,
    document text, URLs, or external session material.
    """

    def __init__(self, database: Any, schema_root: Path | None = None) -> None:
        self.root = Path(database.path).parent / "continuous-intake"
        self.schemas = schema_root or project_root() / "schemas"

    @staticmethod
    def _digest(intake_key: str) -> str:
        match = re.fullmatch(r"sha256:([a-f0-9]{64})", intake_key)
        if not match:
            raise JobOpsError("CONTINUOUS_INTAKE_KEY_INVALID", "A saved continuation must bind to one JD content hash.")
        return str(match.group(1))

    def _path(self, intake_key: str) -> Path:
        return self.root / f"{self._digest(intake_key)}.json"

    def remember(self, intake_key: str, job: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_continuous_manifest({
            "schema_version": 1, "mode": "MANUAL_TICK_ONLY", "jobs": [job],
        })["jobs"][0]
        descriptor = {
            "schema_version": 1,
            "status": "READY_FOR_MANUAL_CONTINUATION",
            "intake_key": intake_key,
            "job": normalized,
            "created_at": iso_utc(),
            "real_external_actions": 0,
        }
        descriptor["descriptor_hash"] = sha256_bytes(canonical_json(descriptor))
        validate_named("continuous-intake-descriptor", descriptor, self.schemas)
        write_json(self._path(intake_key), descriptor)
        return {
            "status": descriptor["status"], "intake_key": intake_key,
            "descriptor_hash": descriptor["descriptor_hash"], "real_external_actions": 0,
        }

    def load(self, intake_key: str) -> dict[str, Any] | None:
        path = self._path(intake_key)
        if not path.is_file():
            return None
        try:
            descriptor = load_json(path)
        except (OSError, ValueError) as exc:
            raise JobOpsError("CONTINUOUS_DESCRIPTOR_INVALID", "A saved local continuation descriptor is unreadable.") from exc
        validate_named("continuous-intake-descriptor", descriptor, self.schemas)
        expected = sha256_bytes(canonical_json({key: value for key, value in descriptor.items() if key != "descriptor_hash"}))
        if descriptor["descriptor_hash"] != expected or descriptor["intake_key"] != intake_key:
            raise JobOpsError("CONTINUOUS_DESCRIPTOR_CHANGED", "A saved local continuation descriptor changed after it was recorded.")
        job = validate_continuous_manifest({
            "schema_version": 1, "mode": "MANUAL_TICK_ONLY", "jobs": [descriptor["job"]],
        })["jobs"][0]
        return job

    def forget(self, intake_key: str) -> bool:
        try:
            self._path(intake_key).unlink(missing_ok=True)
            return True
        except OSError:
            return False


def continue_recorded_intake(
    *,
    project: Path,
    database: Any,
    onboarding: Any,
    maximum: int = MAX_CONTINUOUS_JOBS,
) -> dict[str, Any]:
    """Fill newly available queue capacity from previously saved local descriptors."""

    if not isinstance(maximum, int) or not 1 <= maximum <= MAX_CONTINUOUS_JOBS:
        raise JobOpsError("CONTINUOUS_PROMOTION_LIMIT_INVALID", "The local continuation limit is invalid.")
    from .orchestrator import JobOpsOrchestrator
    from .queue_manager import QueueManager
    from .security import assert_project_io_path

    manager = QueueManager(database)
    store = ContinuousIntakeDescriptorStore(database, project / "schemas")
    orchestrator = JobOpsOrchestrator(project, database, onboarding)
    results: list[dict[str, Any]] = []
    first_promotion: dict[str, Any] | None = None
    descriptor_missing = False
    descriptor_cleanup_failure_count = 0

    for _ in range(maximum):
        admission = manager.promote_next_deferred()
        if first_promotion is None:
            first_promotion = admission.as_dict()
        if admission.status != "RESERVED":
            break
        try:
            job = store.load(admission.intake_key)
        except JobOpsError as exc:
            manager.release_reservation(admission.reservation_id, reason=exc.code)
            if not store.forget(admission.intake_key):
                descriptor_cleanup_failure_count += 1
            results.append({
                "ordinal": len(results) + 1, "source_type": "snapshot",
                "source_mode": "SAVED_LOCAL_EVIDENCE", "status": "LOCAL_ERROR",
                "application_id": None, "error_code": exc.code,
                "next_safe_action": "FIX_LOCAL_EVIDENCE_AND_RETRY_MANUAL_TICK",
                "real_external_actions": 0,
            })
            continue
        if job is None:
            descriptor_missing = True
            break
        fixtures = project / "tests" / "fixtures"

        def local_path(value: Any) -> Path:
            path = Path(str(value))
            return assert_project_io_path(path if path.is_absolute() else project / path, project, operation="read")

        def prepare(item: dict[str, Any]) -> dict[str, Any]:
            return orchestrator.run_to_awaiting(
                local_path(item["input"]),
                profile_ref=item["profile_ref"], master_resume_ref=item["master_resume_ref"],
                answer_bank_ref=item["answer_bank_ref"],
                external_claim_set_ref=item.get("external_claim_set_ref"),
                tailoring_manifest_ref=item.get("tailoring_manifest_ref"),
                route_fixture=local_path(item.get("route", fixtures / "synthetic-forward-route.json")),
                form_fixture=local_path(item.get("form", fixtures / "synthetic-forward-form.json")),
                research_fixture=local_path(item.get("research", fixtures / "synthetic-research.html")),
                source_type=item.get("source_type"), synthetic=bool(item["synthetic"]),
            )

        tick = run_continuous_intake_tick(
            {"schema_version": 1, "mode": "MANUAL_TICK_ONLY", "jobs": [job]},
            queue_status=manager.status,
            prepare_job=prepare,
        )
        row = dict(tick["results"][0])
        row["ordinal"] = len(results) + 1
        results.append(row)
        if row["status"] != "DEFERRED_CAPACITY" and not store.forget(admission.intake_key):
            descriptor_cleanup_failure_count += 1
        if row["status"] not in {"LOCAL_ERROR"}:
            break

    return {
        "status": (
            "RESERVED_REQUIRES_ORIGINAL_MANIFEST" if descriptor_missing
            else "LOCAL_CONTINUATION_PROCESSED" if results
            else "NO_RECORDED_LOCAL_CONTINUATION"
        ),
        "initial_promotion": first_promotion or {
            "intake_key": "", "status": "EMPTY", "reservation_id": None, "next_safe_action": "NONE",
        },
        "processed_count": len(results),
        "prepared_count": sum(item["status"] == "PREPARED" for item in results),
        "failed_count": sum(item["status"] == "LOCAL_ERROR" for item in results),
        "descriptor_cleanup_failure_count": descriptor_cleanup_failure_count,
        "results": results,
        "queue": manager.status(),
        "background_service_started": False,
        "system_tasks_registered": 0,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
