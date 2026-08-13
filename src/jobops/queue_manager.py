from __future__ import annotations

import json
from dataclasses import dataclass

from .approvals import ApprovalContext
from .db import JobOpsDB
from .errors import JobOpsError
from .util import iso_utc, stable_id


@dataclass(frozen=True)
class QueueAdmission:
    intake_key: str
    status: str
    reservation_id: str | None
    next_safe_action: str

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class QueueManager:
    def __init__(self, database: JobOpsDB) -> None:
        self.database = database

    @staticmethod
    def _capacity(connection) -> tuple[int, int, int]:
        limit = int(connection.execute("SELECT pending_approval_limit FROM queue_settings WHERE singleton_id=1").fetchone()[0])
        awaiting = int(connection.execute("SELECT COUNT(*) FROM applications WHERE status='AWAITING_APPROVAL'").fetchone()[0])
        reserved = int(connection.execute("SELECT COUNT(*) FROM queue_reservations WHERE status='RESERVED'").fetchone()[0])
        return limit, awaiting, reserved

    def enqueue(self, intake_key: str, *, source_type: str, source_locator: str) -> QueueAdmission:
        if not intake_key or not source_type or not source_locator:
            raise JobOpsError("INTAKE_INVALID", "Intake key, source type and safe source locator are required.")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM intake_queue WHERE intake_key=?", (intake_key,)).fetchone()
            if existing and existing["status"] in {"RESERVED", "ACCEPTED"}:
                return QueueAdmission(intake_key, str(existing["status"]), existing["reservation_id"], "CONTINUE_RESERVED_JOB" if existing["status"] == "RESERVED" else "NONE")
            limit, awaiting, reserved = self._capacity(connection)
            now = iso_utc()
            if awaiting + reserved >= limit:
                connection.execute(
                    """INSERT INTO intake_queue(intake_key,source_type,source_locator,status,reservation_id,created_at,updated_at)
                    VALUES(?,?,?,'DEFERRED',NULL,?,?) ON CONFLICT(intake_key) DO UPDATE SET
                    status='DEFERRED',reservation_id=NULL,updated_at=excluded.updated_at""",
                    (intake_key, source_type, source_locator, now, now),
                )
                return QueueAdmission(intake_key, "DEFERRED", None, "WAIT_FOR_APPROVAL_SLOT")
            oldest = connection.execute(
                "SELECT intake_key FROM intake_queue WHERE status='DEFERRED' ORDER BY created_at,intake_key LIMIT 1"
            ).fetchone()
            if oldest is not None and str(oldest["intake_key"]) != intake_key:
                connection.execute(
                    """INSERT INTO intake_queue(intake_key,source_type,source_locator,status,reservation_id,created_at,updated_at)
                    VALUES(?,?,?,'DEFERRED',NULL,?,?) ON CONFLICT(intake_key) DO UPDATE SET
                    status='DEFERRED',reservation_id=NULL,updated_at=excluded.updated_at""",
                    (intake_key, source_type, source_locator, now, now),
                )
                return QueueAdmission(intake_key, "DEFERRED", None, "WAIT_FOR_OLDER_DEFERRED_INTAKE")
            reservation_id = stable_id("QRS", intake_key)
            connection.execute(
                """INSERT INTO queue_reservations(reservation_id,intake_key,application_id,status,created_at,updated_at)
                VALUES(?,?,NULL,'RESERVED',?,?) ON CONFLICT(intake_key) DO UPDATE SET
                status='RESERVED',updated_at=excluded.updated_at""",
                (reservation_id, intake_key, now, now),
            )
            connection.execute(
                """INSERT INTO intake_queue(intake_key,source_type,source_locator,status,reservation_id,created_at,updated_at)
                VALUES(?,?,?,'RESERVED',?,?,?) ON CONFLICT(intake_key) DO UPDATE SET
                status='RESERVED',reservation_id=excluded.reservation_id,updated_at=excluded.updated_at""",
                (intake_key, source_type, source_locator, reservation_id, now, now),
            )
            return QueueAdmission(intake_key, "RESERVED", reservation_id, "RUN_TO_AWAITING_APPROVAL")

    def reserve_reprocess(self, intake_key: str, application_id: str) -> QueueAdmission:
        """Re-open an accepted intake only from an explicitly safe correction state."""
        allowed = {"NEEDS_USER_INPUT", "MATERIALS_NEEDS_CORRECTION", "SITE_CHANGED", "APPROVAL_EXPIRED"}
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            application = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (application_id,)
            ).fetchone()
            intake = connection.execute(
                "SELECT source_type,source_locator FROM intake_queue WHERE intake_key=?", (intake_key,)
            ).fetchone()
            if application is None or intake is None:
                raise JobOpsError("REPROCESS_TARGET_MISSING", "The accepted intake or application is missing.")
            if application["status"] not in allowed:
                raise JobOpsError(
                    "REPROCESS_STATE_FORBIDDEN",
                    "Only a correction, changed-site, or expired-approval state may be reprocessed.",
                    status=application["status"],
                )
            limit, awaiting, reserved = self._capacity(connection)
            now = iso_utc()
            connection.execute(
                "UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'",
                (application_id,),
            )
            if awaiting + reserved >= limit:
                connection.execute(
                    "UPDATE intake_queue SET status='DEFERRED',reservation_id=NULL,updated_at=? WHERE intake_key=?",
                    (now, intake_key),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        application_id, "REPROCESS_DEFERRED", application["status"], application["status"],
                        json.dumps({"reason": "PENDING_APPROVAL_LIMIT"}), now,
                    ),
                )
                return QueueAdmission(intake_key, "DEFERRED", None, "WAIT_FOR_APPROVAL_SLOT")
            reservation_id = stable_id("QRS", intake_key)
            connection.execute(
                """INSERT INTO queue_reservations(reservation_id,intake_key,application_id,status,created_at,updated_at)
                VALUES(?,?,NULL,'RESERVED',?,?) ON CONFLICT(intake_key) DO UPDATE SET
                application_id=NULL,status='RESERVED',updated_at=excluded.updated_at""",
                (reservation_id, intake_key, now, now),
            )
            connection.execute(
                "UPDATE intake_queue SET status='RESERVED',reservation_id=?,updated_at=? WHERE intake_key=?",
                (reservation_id, now, intake_key),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "REPROCESS_RESERVED", application["status"], application["status"], "{}", now),
            )
            return QueueAdmission(intake_key, "RESERVED", reservation_id, "RUN_TO_AWAITING_APPROVAL")

    def admit_awaiting(
        self,
        reservation_id: str | None,
        context: ApprovalContext,
        *,
        snapshot_relative_path: str,
        job_details: dict[str, object] | None = None,
        secure_profile_ref: str = "secure-ref:SYNTHETIC_PROFILE_001",
        review_packet: dict[str, object] | None = None,
        material_records: list[dict[str, object]] | None = None,
        analysis_record: dict[str, object] | None = None,
        research_records: list[dict[str, object]] | None = None,
        field_records: list[dict[str, object]] | None = None,
        source_route: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not reservation_id:
            raise JobOpsError("QUEUE_RESERVATION_REQUIRED", "Admission to AWAITING_APPROVAL requires a live queue reservation.")
        binding = context.normalized()
        if review_packet is not None and str(review_packet.get("content_hash")) != binding.review_packet_hash:
            raise JobOpsError(
                "REVIEW_PACKET_CONTEXT_MISMATCH",
                "The review packet content hash must match the current approval context.",
            )
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute("SELECT * FROM queue_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if reservation is None:
                raise JobOpsError("QUEUE_RESERVATION_INVALID", "Queue reservation is missing or no longer active.")
            if reservation["status"] == "CONSUMED" and reservation["application_id"] == binding.application_id:
                existing = connection.execute("SELECT status FROM applications WHERE application_id=?", (binding.application_id,)).fetchone()
                if existing and existing["status"] == "AWAITING_APPROVAL":
                    return {"status": "AWAITING_APPROVAL", "application_id": binding.application_id, "reservation_id": reservation_id, "deduplicated": True}
            if reservation["status"] != "RESERVED":
                raise JobOpsError("QUEUE_RESERVATION_INVALID", "Queue reservation is missing or no longer active.")
            limit, awaiting, reserved = self._capacity(connection)
            if awaiting + reserved > limit:
                raise JobOpsError("QUEUE_CAPACITY_INVARIANT_BROKEN", "Awaiting plus reserved slots exceeds the configured limit.")
            now = iso_utc()
            details = job_details or {}
            existing_application = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (binding.application_id,)
            ).fetchone()
            if existing_application is not None and existing_application["status"] not in {
                "NEEDS_USER_INPUT", "MATERIALS_NEEDS_CORRECTION", "SITE_CHANGED", "APPROVAL_EXPIRED"
            }:
                raise JobOpsError(
                    "APPLICATION_REENTRY_FORBIDDEN",
                    "An existing application may re-enter review only from an explicit safe correction state.",
                    status=existing_application["status"],
                )
            connection.execute(
                """INSERT OR IGNORE INTO jobs(job_id,source_type,source_locator,official_url,company,title,location,status,discovered_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    binding.job_id, details.get("source_type", "synthetic"), details.get("source_locator", reservation["intake_key"]),
                    details.get("official_url", binding.canonical_url), details.get("company", "Synthetic Company"),
                    details.get("title", "Synthetic Role"), details.get("location"), "FORM_VALIDATED", now, now,
                ),
            )
            connection.execute("UPDATE jobs SET status='FORM_VALIDATED',updated_at=? WHERE job_id=?", (now, binding.job_id))
            connection.execute(
                "UPDATE jobs SET source_type=?,source_locator=?,official_url=?,company=?,title=?,location=? WHERE job_id=?",
                (
                    details.get("source_type", "synthetic"), details.get("source_locator", reservation["intake_key"]),
                    details.get("official_url", binding.canonical_url), details.get("company", "Synthetic Company"),
                    details.get("title", "Synthetic Role"), details.get("location"), binding.job_id,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO jd_snapshots(snapshot_id,job_id,content_hash,snapshot_path,captured_at) VALUES(?,?,?,?,?)",
                (stable_id("JDS", binding.jd_snapshot_hash), binding.job_id, binding.jd_snapshot_hash, snapshot_relative_path, now),
            )
            resume = next((item for item in binding.uploads if item.purpose == "resume"), binding.uploads[0])
            connection.execute(
                """INSERT INTO applications(application_id,job_id,site,status,resume_hash,answers_hash,dry_run,secure_profile_ref,last_safe_state,updated_at)
                VALUES(?,?,?,?,?,?,1,?,'AWAITING_APPROVAL',?)
                ON CONFLICT(application_id) DO UPDATE SET
                site=excluded.site,status='AWAITING_APPROVAL',resume_hash=excluded.resume_hash,
                answers_hash=excluded.answers_hash,secure_profile_ref=excluded.secure_profile_ref,
                last_safe_state='AWAITING_APPROVAL',updated_at=excluded.updated_at""",
                (binding.application_id, binding.job_id, binding.canonical_url, "AWAITING_APPROVAL", resume.sha256, binding.answers_hash, secure_profile_ref, now),
            )
            connection.execute(
                """INSERT INTO application_bindings(application_id,context_hash,context_json,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(application_id) DO UPDATE SET
                context_hash=excluded.context_hash,context_json=excluded.context_json,updated_at=excluded.updated_at""",
                (binding.application_id, binding.context_hash, json.dumps(binding.as_dict(), ensure_ascii=False, sort_keys=True), now),
            )
            connection.execute(
                "UPDATE queue_reservations SET application_id=?,status='CONSUMED',updated_at=? WHERE reservation_id=? AND status='RESERVED'",
                (binding.application_id, now, reservation_id),
            )
            connection.execute(
                "UPDATE intake_queue SET status='ACCEPTED',updated_at=? WHERE intake_key=?",
                (now, reservation["intake_key"]),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (binding.application_id, "QUEUE_ADMITTED", "FORM_VALIDATED", "AWAITING_APPROVAL", json.dumps({"reservation_id": reservation_id}), now),
            )
            if analysis_record:
                connection.execute(
                    "INSERT OR IGNORE INTO job_analyses(analysis_id,job_id,snapshot_hash,analysis_json,analysis_hash,created_at) VALUES(?,?,?,?,?,?)",
                    (analysis_record["analysis_id"], binding.job_id, binding.jd_snapshot_hash, json.dumps(analysis_record["analysis"], ensure_ascii=False, sort_keys=True), analysis_record["analysis_hash"], now),
                )
            for finding in research_records or []:
                connection.execute(
                    "INSERT OR IGNORE INTO research_findings(finding_id,job_id,snapshot_hash,finding_json,created_at) VALUES(?,?,?,?,?)",
                    (finding["finding_id"], binding.job_id, finding["snapshot_hash"], json.dumps(finding, ensure_ascii=False, sort_keys=True), now),
                )
            for material in material_records or []:
                connection.execute(
                    "INSERT OR IGNORE INTO materials(material_id,application_id,kind,path,content_hash,claim_ids_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (material["material_id"], binding.application_id, material["kind"], material["path"], material["content_hash"], json.dumps(material.get("claim_ids", [])), now),
                )
            for field in field_records or []:
                connection.execute(
                    """INSERT INTO application_fields(field_id,application_id,classification,status,secure_ref,redacted_summary,field_hash,created_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(field_id) DO UPDATE SET
                    classification=excluded.classification,status=excluded.status,secure_ref=excluded.secure_ref,
                    redacted_summary=excluded.redacted_summary,field_hash=excluded.field_hash""",
                    (field["field_id"], binding.application_id, field["classification"], field["status"], field.get("secure_ref"), field.get("redacted_summary"), field["field_hash"], now),
                )
            if source_route:
                connection.execute(
                    """INSERT OR REPLACE INTO source_routes(job_id,company_domain,official_entry_url,current_url,route_kind,guest_mode,account_action,route_json,verified_at,route_hash,ats_tenant,ats_board,ats_job_identity)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding.job_id, source_route["company_domain"], source_route["official_entry_url"], source_route["current_url"],
                        source_route["route_kind"], source_route["guest_mode"], source_route["account_action"],
                        json.dumps(source_route, ensure_ascii=False, sort_keys=True), now, source_route["route_hash"],
                        source_route["ats_tenant"], source_route["ats_board"], source_route["ats_job_identity"],
                    ),
                )
            if review_packet:
                existing_packet = connection.execute(
                    "SELECT application_id,content_hash,packet_version,supersedes_packet_id FROM review_packets WHERE packet_id=?",
                    (review_packet["packet_id"],),
                ).fetchone()
                if existing_packet is not None and str(existing_packet["application_id"]) != binding.application_id:
                    raise JobOpsError(
                        "REVIEW_PACKET_ID_COLLISION",
                        "A review packet identifier is already bound to another application.",
                    )
                if existing_packet is not None and str(existing_packet["content_hash"]) != str(review_packet["content_hash"]):
                    raise JobOpsError(
                        "REVIEW_PACKET_ID_COLLISION",
                        "A review packet identifier cannot be reused for different content.",
                    )
                latest_packet = connection.execute(
                    "SELECT packet_id,packet_version FROM review_packets WHERE application_id=? ORDER BY packet_version DESC LIMIT 1",
                    (binding.application_id,),
                ).fetchone()
                if existing_packet is not None:
                    packet_version = int(existing_packet["packet_version"])
                    supersedes_packet_id = existing_packet["supersedes_packet_id"]
                else:
                    packet_version = int(latest_packet["packet_version"]) + 1 if latest_packet is not None else 1
                    supersedes_packet_id = str(latest_packet["packet_id"]) if latest_packet is not None else None
                connection.execute(
                    "UPDATE review_packets SET status='NEEDS_REVISION' WHERE application_id=? AND status IN ('AWAITING_APPROVAL','APPROVED')",
                    (binding.application_id,),
                )
                connection.execute(
                    """INSERT INTO review_packets(
                    packet_id,application_id,content_hash,relative_path,status,packet_version,supersedes_packet_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(packet_id) DO UPDATE SET
                    content_hash=excluded.content_hash,relative_path=excluded.relative_path,status=excluded.status""",
                    (
                        review_packet["packet_id"], binding.application_id, review_packet["content_hash"],
                        review_packet["secure_ref"], review_packet["status"], packet_version,
                        supersedes_packet_id, now,
                    ),
                )
            pipeline_states = [
                "DISCOVERED", "SNAPSHOTTED", "PARSED", "ELIGIBILITY_CHECKED", "SCORED", "SHORTLISTED",
                "RESEARCHED", "MATERIALS_DRAFTED", "MATERIALS_VALIDATED", "FORM_PREFILLED", "FORM_VALIDATED", "AWAITING_APPROVAL",
            ]
            for prior, target in zip(pipeline_states, pipeline_states[1:]):
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (binding.application_id, "PIPELINE_STEP", prior, target, "{}", now),
                )
        return {"status": "AWAITING_APPROVAL", "application_id": binding.application_id, "reservation_id": reservation_id, "deduplicated": False}

    def release_application(self, application_id: str, *, reason: str) -> dict[str, object]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM applications WHERE application_id=?", (application_id,)).fetchone()
            if row is None or row["status"] != "AWAITING_APPROVAL":
                raise JobOpsError("APPLICATION_NOT_AWAITING_APPROVAL", "Only a pending review packet can release queue capacity.")
            now = iso_utc()
            connection.execute("UPDATE applications SET status='CLOSED',last_safe_state='AWAITING_APPROVAL',updated_at=? WHERE application_id=?", (now, application_id))
            connection.execute(
                "UPDATE review_packets SET status='REJECTED' WHERE application_id=? AND status='AWAITING_APPROVAL'",
                (application_id,),
            )
            connection.execute("UPDATE intake_queue SET status='CLOSED',updated_at=? WHERE reservation_id=(SELECT reservation_id FROM queue_reservations WHERE application_id=?)", (now, application_id))
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "REVIEW_PACKET_REJECTED", "AWAITING_APPROVAL", "CLOSED", json.dumps({"reason": reason}), now),
            )
        return {"status": "CLOSED", "application_id": application_id, "capacity_released": True}

    def request_revision(self, application_id: str, *, reason: str) -> dict[str, object]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (application_id,)
            ).fetchone()
            if row is None or row["status"] not in {"AWAITING_APPROVAL", "APPROVED"}:
                raise JobOpsError(
                    "APPLICATION_NOT_REVISABLE",
                    "Only a pending or approved review packet can be revised.",
                )
            current = str(row["status"])
            now = iso_utc()
            connection.execute(
                "UPDATE applications SET status='MATERIALS_NEEDS_CORRECTION',last_safe_state='MATERIALS_DRAFTED',updated_at=? WHERE application_id=?",
                (now, application_id),
            )
            connection.execute(
                "UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'",
                (application_id,),
            )
            connection.execute(
                "UPDATE review_packets SET status='NEEDS_REVISION' WHERE application_id=? AND status IN ('AWAITING_APPROVAL','APPROVED')",
                (application_id,),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    application_id, "REVISION_REQUESTED", current, "MATERIALS_NEEDS_CORRECTION",
                    json.dumps({"reason": reason}), now,
                ),
            )
        return {
            "status": "MATERIALS_NEEDS_CORRECTION", "application_id": application_id,
            "approval_invalidated": True, "capacity_released": current == "AWAITING_APPROVAL",
        }

    def promote_next_deferred(self) -> QueueAdmission:
        promoted = self.promote_available(maximum=1)
        if promoted:
            return promoted[0]
        status = self.status()
        if status["slots_available"] <= 0:
            return QueueAdmission("", "NO_CAPACITY", None, "WAIT_FOR_APPROVAL_SLOT")
        return QueueAdmission("", "EMPTY", None, "NONE")

    def promote_available(self, *, maximum: int | None = None) -> list[QueueAdmission]:
        if maximum is not None and maximum < 1:
            raise JobOpsError("PROMOTION_LIMIT_INVALID", "Promotion maximum must be positive when provided.")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            results: list[QueueAdmission] = []
            while maximum is None or len(results) < maximum:
                limit, awaiting, reserved = self._capacity(connection)
                if awaiting + reserved >= limit:
                    break
                row = connection.execute(
                    "SELECT * FROM intake_queue WHERE status='DEFERRED' ORDER BY created_at,intake_key LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                reservation_id = stable_id("QRS", str(row["intake_key"]))
                now = iso_utc()
                connection.execute(
                    """INSERT INTO queue_reservations(reservation_id,intake_key,application_id,status,created_at,updated_at)
                    VALUES(?,?,NULL,'RESERVED',?,?) ON CONFLICT(intake_key) DO UPDATE SET
                    status='RESERVED',application_id=NULL,updated_at=excluded.updated_at""",
                    (reservation_id, row["intake_key"], now, now),
                )
                connection.execute(
                    "UPDATE intake_queue SET status='RESERVED',reservation_id=?,updated_at=? WHERE intake_key=?",
                    (reservation_id, now, row["intake_key"]),
                )
                results.append(QueueAdmission(str(row["intake_key"]), "RESERVED", reservation_id, "RUN_TO_AWAITING_APPROVAL"))
            return results

    def status(self) -> dict[str, int]:
        with self.database.connect() as connection:
            limit, awaiting, reserved = self._capacity(connection)
            deferred = int(connection.execute("SELECT COUNT(*) FROM intake_queue WHERE status='DEFERRED'").fetchone()[0])
            closed = int(connection.execute("SELECT COUNT(*) FROM applications WHERE status='CLOSED'").fetchone()[0])
        return {
            "pending_limit": limit, "awaiting_approval": awaiting, "reserved_slots": reserved,
            "deferred_intake": deferred, "closed_applications": closed,
            "slots_available": max(0, limit - awaiting - reserved),
        }
