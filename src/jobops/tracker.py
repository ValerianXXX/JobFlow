from __future__ import annotations

from typing import Any

from .db import JobOpsDB
from .security import validate_secure_reference
from .util import iso_utc


def upsert_application(
    database: JobOpsDB,
    *,
    application_id: str,
    job_id: str,
    site: str,
    status: str,
    resume_hash: str,
    answers_hash: str,
    secure_profile_ref: str | None,
) -> None:
    if status in {"AWAITING_APPROVAL", "APPROVED", "SUBMITTING", "SUBMITTED", "CONFIRMED"}:
        from .errors import JobOpsError
        raise JobOpsError("EXTERNAL_GATEWAY_REQUIRED", "Protected review and external-action states cannot be written through the generic tracker.", target=status)
    validate_secure_reference(secure_profile_ref)
    with database.connect() as connection:
        existing = connection.execute("SELECT application_id FROM applications WHERE job_id=?", (job_id,)).fetchone()
        stable_id = str(existing[0]) if existing else application_id
        connection.execute(
            """INSERT INTO applications(application_id,job_id,site,status,resume_hash,answers_hash,dry_run,secure_profile_ref,last_safe_state,updated_at)
            VALUES(?,?,?,?,?,?,1,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET site=excluded.site,status=excluded.status,
            resume_hash=excluded.resume_hash,answers_hash=excluded.answers_hash,
            secure_profile_ref=excluded.secure_profile_ref,updated_at=excluded.updated_at""",
            (stable_id, job_id, site, status, resume_hash, answers_hash, secure_profile_ref, status, iso_utc()),
        )


def schedule_reminder(database: JobOpsDB, *, reminder_id: str, application_id: str, kind: str, due_at: str) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO reminders(reminder_id,application_id,kind,due_at,status) VALUES(?,?,?,?,?)",
            (reminder_id, application_id, kind, due_at, "PENDING"),
        )
