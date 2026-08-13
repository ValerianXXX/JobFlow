from __future__ import annotations

from typing import Any

from .errors import JobOpsError


REQUIRED_REVIEW_FIELDS = {
    "job", "jd_captured_at", "fit", "hard_gaps", "resume_bullets",
    "master_resume_diff", "form_questions", "sensitive_fields",
    "uploads", "external_actions", "source_route", "queue",
}


def build_review_packet(payload: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_REVIEW_FIELDS - set(payload))
    if missing:
        raise JobOpsError("REVIEW_PACKET_INCOMPLETE", "The approval review packet is missing required evidence.", missing=missing)
    job = payload["job"]
    if not isinstance(job, dict) or not all(job.get(key) for key in ("job_id", "company", "title", "official_url")):
        raise JobOpsError("REVIEW_JOB_INCOMPLETE", "The review packet requires a job ID, company, title and official URL.")
    for bullet in payload["resume_bullets"]:
        if not bullet.get("claim_id") or not bullet.get("evidence"):
            raise JobOpsError("REVIEW_BULLET_UNTRACEABLE", "Every resume bullet must include a claim_id and knowledge evidence.")
    for upload in payload["uploads"]:
        if not upload.get("filename") or not str(upload.get("sha256", "")).startswith("sha256:"):
            raise JobOpsError("REVIEW_UPLOAD_UNHASHED", "Every pending upload needs a filename and SHA-256.")
    route = payload["source_route"]
    if route.get("route_kind") not in {"OFFICIAL_DIRECT", "OFFICIAL_TO_APPROVED_ATS"}:
        raise JobOpsError("REVIEW_SOURCE_ROUTE_UNVERIFIED", "Review packet requires a verified official-company application route.")
    queue = payload["queue"]
    if not isinstance(queue.get("pending_limit"), int) or queue["pending_limit"] < 1:
        raise JobOpsError("REVIEW_QUEUE_INVALID", "Review packet requires the active pending-approval limit.")
    return {"schema_version": 1, "status": "AWAITING_APPROVAL", **payload}
