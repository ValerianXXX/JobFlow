from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .errors import JobOpsError
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, iso_utc, load_json, project_root, sha256_bytes, write_json


MAX_CONTINUOUS_JOBS = 1000
JOB_KEYS = {
    "input", "profile_ref", "master_resume_ref", "answer_bank_ref", "route", "form", "research",
    "external_claim_set_ref", "tailoring_manifest_ref", "evidence_bundle_ref", "source_type", "synthetic",
}
REQUIRED_JOB_KEYS = {"profile_ref", "master_resume_ref", "answer_bank_ref", "synthetic"}
MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES = 60 * 1024 * 1024
DEFERRED_EVIDENCE_EXTENSIONS = {
    "jd": {".txt", ".html", ".htm", ".pdf", ".json"},
    "official": {".html", ".htm", ".txt"},
    "form": {".html", ".htm", ".json"},
}
MAX_DEFERRED_ROUTE_BYTES = 1024 * 1024
MAX_DEFERRED_RESEARCH_BYTES = 16 * 1024 * 1024


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
        has_path_input = isinstance(raw.get("input"), str) and bool(raw.get("input"))
        has_evidence_bundle = isinstance(raw.get("evidence_bundle_ref"), str) and bool(raw.get("evidence_bundle_ref"))
        if has_path_input == has_evidence_bundle:
            raise JobOpsError(
                "CONTINUOUS_JOB_SOURCE_INVALID",
                "Every manual-tick job must use exactly one project-local input or one encrypted deferred-evidence bundle.",
                job_index=index,
            )
        if has_evidence_bundle and (
            raw.get("synthetic") is not False or any(raw.get(key) is not None for key in ("route", "form", "research"))
        ):
            raise JobOpsError(
                "CONTINUOUS_JOB_SOURCE_INVALID",
                "An encrypted deferred-evidence bundle is real-profile only and cannot be mixed with project paths.",
                job_index=index,
            )
        if raw.get("synthetic") is False and has_path_input and not all(raw.get(key) for key in ("route", "form", "research")):
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
        if has_evidence_bundle:
            validate_secure_reference(str(raw["evidence_bundle_ref"]))
        identity = (
            str(raw["input"]).replace("\\", "/").casefold()
            if has_path_input else str(raw["evidence_bundle_ref"]).casefold()
        )
        if identity in seen:
            raise JobOpsError("CONTINUOUS_JOB_DUPLICATE", "The same local job input may appear only once per tick.", job_index=index)
        seen.add(identity)
        normalized.append({key: raw[key] for key in sorted(raw)})
    return {"schema_version": 1, "mode": "MANUAL_TICK_ONLY", "jobs": normalized}


def build_deferred_evidence_bundle(
    *,
    files: dict[str, tuple[str, bytes]],
    route_json: bytes,
    research_text: bytes,
) -> bytes:
    if set(files) != {"jd", "official", "form"}:
        raise JobOpsError("CONTINUOUS_EVIDENCE_FILES_INVALID", "Deferred evidence requires one JD, official page and application form.")
    if (
        not isinstance(route_json, bytes) or not route_json or len(route_json) > MAX_DEFERRED_ROUTE_BYTES
        or not isinstance(research_text, bytes) or not research_text or len(research_text) > MAX_DEFERRED_RESEARCH_BYTES
    ):
        raise JobOpsError("CONTINUOUS_EVIDENCE_FILES_INVALID", "Deferred route or research evidence has an invalid size.")
    try:
        route_value = json.loads(route_json.decode("utf-8"))
        research_text.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobOpsError("CONTINUOUS_EVIDENCE_FILES_INVALID", "Deferred route and research evidence must use the internal UTF-8 format.") from exc
    if not isinstance(route_value, dict):
        raise JobOpsError("CONTINUOUS_EVIDENCE_FILES_INVALID", "Deferred route evidence must be a JSON object.")
    entries: dict[str, bytes] = {"route.json": route_json, "research.txt": research_text}
    manifest_files: list[dict[str, Any]] = []
    for key in ("jd", "official", "form"):
        extension, content = files[key]
        normalized_extension = str(extension).casefold()
        if (
            normalized_extension not in DEFERRED_EVIDENCE_EXTENSIONS[key]
            or not isinstance(content, bytes) or not content
        ):
            raise JobOpsError("CONTINUOUS_EVIDENCE_FILES_INVALID", "A deferred evidence file has an invalid extension or empty content.")
        name = key + normalized_extension
        entries[name] = content
        manifest_files.append({
            "key": key, "name": name, "extension": normalized_extension,
            "size": len(content), "sha256": sha256_bytes(content),
        })
    manifest = {
        "schema_version": 1, "files": manifest_files,
        "route_sha256": sha256_bytes(route_json), "research_sha256": sha256_bytes(research_text),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", canonical_json(manifest))
        for name, content in entries.items():
            archive.writestr(name, content)
    value = buffer.getvalue()
    if len(value) > MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES:
        raise JobOpsError(
            "CONTINUOUS_EVIDENCE_BUNDLE_TOO_LARGE",
            "The deferred local evidence is too large to retain safely; retry after a review slot is available.",
            maximum_bytes=MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES,
        )
    return value


def extract_deferred_evidence_bundle(value: bytes, staging: Path) -> dict[str, Path]:
    if not isinstance(value, bytes) or not value or len(value) > MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES:
        raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "The encrypted deferred evidence has an invalid size.")
    try:
        with zipfile.ZipFile(io.BytesIO(value), "r") as archive:
            infos = archive.infolist()
            if (
                len(infos) != 6
                or sum(info.file_size for info in infos) > MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES
                or any(
                    info.compress_type != zipfile.ZIP_STORED or info.is_dir()
                    or info.file_size < 1 or info.compress_size != info.file_size
                    for info in infos
                )
            ):
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence must use the fixed local archive format.")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or "manifest.json" not in names or "route.json" not in names or "research.txt" not in names:
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence entries are incomplete or duplicated.")
            manifest_raw = archive.read("manifest.json")
            if len(manifest_raw) > 64 * 1024:
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence metadata exceeds the safe limit.")
            manifest = json.loads(manifest_raw.decode("utf-8"))
            if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "files", "route_sha256", "research_sha256"} or manifest.get("schema_version") != 1:
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence metadata is invalid.")
            file_records = manifest.get("files")
            if not isinstance(file_records, list) or len(file_records) != 3:
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence must describe exactly three source files.")
            expected_names = {"manifest.json", "route.json", "research.txt"}
            paths: dict[str, Path] = {}
            seen_keys: set[str] = set()
            for record in file_records:
                if not isinstance(record, dict) or set(record) != {"key", "name", "extension", "size", "sha256"}:
                    raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "A deferred evidence file descriptor is invalid.")
                key, name, extension = record["key"], record["name"], record["extension"]
                if (
                    not isinstance(key, str) or not isinstance(name, str) or not isinstance(extension, str)
                    or key not in DEFERRED_EVIDENCE_EXTENSIONS or key in seen_keys
                    or extension not in DEFERRED_EVIDENCE_EXTENSIONS[key]
                    or name != key + extension or not re.fullmatch(r"[a-z]+\.[a-z0-9]{1,12}", name)
                    or type(record["size"]) is not int or not 1 <= record["size"] <= MAX_DEFERRED_EVIDENCE_BUNDLE_BYTES
                    or not isinstance(record["sha256"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", record["sha256"])
                    or name not in names
                ):
                    raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "A deferred evidence filename is invalid.")
                seen_keys.add(key)
                content = archive.read(name)
                if record["size"] != len(content) or record["sha256"] != sha256_bytes(content):
                    raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_CHANGED", "A deferred evidence file failed its content binding.")
                target = staging / name
                target.write_bytes(content)
                paths[key] = target
                expected_names.add(name)
            if seen_keys != {"jd", "official", "form"} or set(names) != expected_names:
                raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred evidence contains an unexpected entry.")
            for key, name in (("route", "route.json"), ("research", "research.txt")):
                content = archive.read(name)
                maximum = MAX_DEFERRED_ROUTE_BYTES if key == "route" else MAX_DEFERRED_RESEARCH_BYTES
                if (
                    not 1 <= len(content) <= maximum
                    or not isinstance(manifest[f"{key}_sha256"], str)
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest[f"{key}_sha256"])
                    or manifest[f"{key}_sha256"] != sha256_bytes(content)
                ):
                    raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_CHANGED", "Deferred route or research evidence changed.")
                if key == "route" and not isinstance(json.loads(content.decode("utf-8")), dict):
                    raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "Deferred route evidence must be a JSON object.")
                if key == "research":
                    content.decode("utf-8")
                target = staging / name
                target.write_bytes(content)
                paths[key] = target
            return paths
    except JobOpsError:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobOpsError("CONTINUOUS_EVIDENCE_BUNDLE_INVALID", "The encrypted deferred evidence could not be validated.") from exc


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
    document text, URLs, or external session material. UI-deferred evidence is
    represented only by a DPAPI-backed opaque reference.
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
            evidence_reference = item.get("evidence_bundle_ref")
            if evidence_reference is not None:
                metadata = onboarding.reference_metadata(str(evidence_reference))
                if (
                    metadata.get("kind") != "continuous_evidence_bundle"
                    or metadata.get("status") != "ACTIVE"
                    or metadata.get("synthetic") is not False
                ):
                    raise JobOpsError(
                        "CONTINUOUS_EVIDENCE_REFERENCE_INVALID",
                        "The saved deferred evidence is not an active real-profile bundle.",
                    )
                try:
                    bundle = onboarding.read_bytes(str(evidence_reference))
                    with onboarding.staging_directory() as staging:
                        extracted = extract_deferred_evidence_bundle(bundle, staging)
                        # Once exact local evidence is available in the controlled
                        # one-use directory, delete its queued ciphertext before
                        # generating application materials. This makes cleanup a
                        # prerequisite rather than a best-effort afterthought.
                        onboarding.delete(str(evidence_reference), user_confirmed=True)
                        return orchestrator.run_to_awaiting(
                            extracted["jd"],
                            profile_ref=item["profile_ref"], master_resume_ref=item["master_resume_ref"],
                            answer_bank_ref=item["answer_bank_ref"],
                            external_claim_set_ref=item.get("external_claim_set_ref"),
                            tailoring_manifest_ref=item.get("tailoring_manifest_ref"),
                            route_fixture=extracted["route"], form_fixture=extracted["form"],
                            research_fixture=extracted["research"], official_snapshot_fixture=extracted["official"],
                            source_type=item.get("source_type"), synthetic=False,
                        )
                except Exception as source_error:
                    try:
                        if onboarding.reference_metadata(str(evidence_reference)).get("status") == "ACTIVE":
                            onboarding.delete(str(evidence_reference), user_confirmed=True)
                    except Exception as cleanup_error:
                        raise JobOpsError(
                            "CONTINUOUS_EVIDENCE_CLEANUP_FAILED",
                            "Deferred evidence could not be removed after local continuation stopped.",
                        ) from cleanup_error
                    raise source_error
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
        if row["status"] == "LOCAL_ERROR":
            # Errors can occur before the orchestrator receives the promoted
            # reservation (for example, a missing saved path or an invalid
            # encrypted bundle). Release is idempotent when the orchestrator
            # already performed its own rollback.
            manager.release_reservation(
                admission.reservation_id,
                reason=str(row.get("error_code") or "LOCAL_CONTINUATION_FAILED"),
            )
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
