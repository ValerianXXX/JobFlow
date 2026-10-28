"""Read models for one application; never infer a submission from preparation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import JobOpsError
from .runtime_paths import runtime_path
from .util import sha256_bytes


def validate_application_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", value):
        raise JobOpsError("APPLICATION_ID_INVALID", "Choose an existing application.")
    return value


def next_action(status: str, stopped_fields: int = 0) -> str:
    if status in {"SUBMISSION_UNKNOWN", "SUBMITTING", "SUBMITTED"}:
        return "VERIFY_WITH_EMPLOYER_NO_RETRY"
    if status in {"CONFIRMED", "FOLLOW_UP", "INTERVIEW"}:
        return "REVIEW_STATUS_MESSAGES"
    if status in {"REJECTED", "OFFER"}:
        return "REVIEW_RECORDED_OUTCOME"
    if status == "CLOSED":
        return "VIEW_ARCHIVED_RECORD"
    if stopped_fields:
        return "RESOLVE_REQUIRED_FIELDS"
    if status == "AWAITING_APPROVAL":
        return "REVIEW_APPLICATION_PACKET"
    if status == "APPROVED":
        return "CONTINUE_USER_PRESENT_SESSION"
    return "REVIEW_PREPARATION_BLOCKER"


def application_workspace(database, project: Path, application_id: str) -> dict:
    application_id = validate_application_id(application_id)
    with database.connect() as connection:
        row = connection.execute(
            """SELECT a.application_id,a.status,a.last_safe_state,a.resume_hash,a.updated_at,
                      j.job_id,j.company,j.title,j.location
               FROM applications a JOIN jobs j ON j.job_id=a.job_id WHERE a.application_id=?""",
            (application_id,),
        ).fetchone()
        if row is None:
            raise JobOpsError(
                "APPLICATION_NOT_FOUND", "The application is no longer available."
            )
        snapshots = connection.execute(
            "SELECT snapshot_id,content_hash,snapshot_path,captured_at FROM jd_snapshots WHERE job_id=? ORDER BY captured_at DESC,snapshot_id",
            (row["job_id"],),
        ).fetchall()
        materials = connection.execute(
            "SELECT material_id,kind,content_hash,created_at FROM materials WHERE application_id=? ORDER BY created_at DESC,material_id LIMIT 100",
            (application_id,),
        ).fetchall()
        packets = connection.execute(
            "SELECT packet_version,content_hash,status,created_at FROM review_packets WHERE application_id=? ORDER BY packet_version DESC LIMIT 20",
            (application_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT event_id,from_state,to_state,created_at FROM events WHERE application_id=? AND to_state IS NOT NULL ORDER BY event_id DESC LIMIT 100",
            (application_id,),
        ).fetchall()
        stopped = connection.execute(
            "SELECT COUNT(*) FROM application_fields WHERE application_id=? AND status='STOP_REQUIRED'",
            (application_id,),
        ).fetchone()[0]
        binding = connection.execute(
            "SELECT context_json FROM application_bindings WHERE application_id=?",
            (application_id,),
        ).fetchone()
    bound_snapshot = None
    if binding:
        try:
            bound_snapshot = json.loads(binding[0]).get("jd_snapshot_hash")
        except (ValueError, AttributeError):
            raise JobOpsError(
                "APPLICATION_BINDING_MISSING", "The application context needs review."
            ) from None
    snapshot_rows = []
    for snapshot in snapshots[:20]:
        item = {
            key: snapshot[key] for key in ("snapshot_id", "content_hash", "captured_at")
        }
        item["bound_to_review"] = snapshot["content_hash"] == bound_snapshot
        item["availability"] = "UNAVAILABLE"
        # A snapshot path is a local locator, never a URL or an arbitrary file capability.
        relative = Path(str(snapshot["snapshot_path"]))
        try:
            if (
                relative.is_absolute()
                or len(relative.parts) < 2
                or relative.parts[0] != "workspace"
            ):
                raise JobOpsError("SNAPSHOT_PATH_INVALID", "Invalid snapshot location.")
            path = runtime_path(
                project, "workspace", *relative.parts[1:], operation="read"
            )
            if path.stat().st_size > 512_000:
                raise JobOpsError(
                    "SNAPSHOT_TOO_LARGE", "The snapshot exceeds the display limit."
                )
            raw = path.read_bytes()
            basis = "RAW_BYTES"
            if sha256_bytes(raw) != snapshot["content_hash"]:
                # Historical text-mode writes translated LF after hashing on
                # Windows. Accept only that transformation, without trimming,
                # rewriting words, or changing the retained original file.
                if (
                    sha256_bytes(raw.replace(b"\r\n", b"\n"))
                    != snapshot["content_hash"]
                ):
                    raise JobOpsError(
                        "SNAPSHOT_HASH_MISMATCH",
                        "The saved job description has changed.",
                    )
                basis = "CANONICAL_LF"
            item.update(
                text=raw.decode("utf-8-sig"),
                availability="HASH_VERIFIED",
                hash_basis=basis,
            )
        except (OSError, UnicodeError, JobOpsError):
            # Metadata remains useful even when old files were removed. Never fetch a replacement.
            pass
        snapshot_rows.append(item)
    return {
        "application_id": application_id,
        "job": {key: row[key] for key in ("company", "title", "location")},
        "status": row["status"],
        "last_safe_state": row["last_safe_state"],
        "updated_at": row["updated_at"],
        "next_action": next_action(row["status"], stopped),
        "required_field_count": stopped,
        "snapshots": snapshot_rows,
        "materials": [
            {
                **dict(item),
                "matches_application_resume": item["content_hash"]
                == row["resume_hash"],
                "delivery_confirmed": False,
            }
            for item in materials
        ],
        "review_versions": [dict(item) for item in packets],
        "timeline": [dict(item) for item in reversed(events)],
        "automatic_retry": False,
        "application_state_changed": False,
    }
