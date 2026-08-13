from __future__ import annotations

import re
from typing import Any, Iterable

from .security import validate_secure_reference
from .util import sha256_bytes


FILE_PURPOSES = ("resume", "cover_letter", "portfolio", "attachment")
PUBLIC_LINK_KEYS = ("github", "portfolio", "website")


def _field_text(field: dict[str, Any]) -> str:
    return " ".join(
        str(field.get(key, ""))
        for key in ("answer_key", "id", "name", "label", "control_type", "type")
    ).casefold().replace("_", " ")


def field_answer_key(field: dict[str, Any]) -> str:
    explicit = str(field.get("answer_key", "")).casefold().strip()
    if explicit and explicit != "unknown":
        return explicit
    material = _field_text(field)
    if "github" in material:
        return "github"
    if any(token in material for token in ("cover letter", "motivation letter", "求职信", "动机信")):
        return "cover_letter"
    if any(token in material for token in ("portfolio", "work sample", "作品集", "工作样本")):
        return "portfolio_file" if field.get("classification") == "file_upload_stop" else "portfolio"
    if any(token in material for token in ("resume", "curriculum vitae", " cv ", "简历")):
        return "resume"
    if "website" in material or "personal site" in material or "个人网站" in material:
        return "website"
    return explicit or "unknown"


def detect_material_requests(fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    uploads: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for field in fields:
        answer_key = field_answer_key(field)
        if field.get("classification") == "file_upload_stop":
            purpose = (
                "cover_letter" if answer_key == "cover_letter"
                else "portfolio" if answer_key in {"portfolio", "portfolio_file"}
                else "resume" if answer_key == "resume"
                else "attachment"
            )
            uploads.append({
                "field_id": str(field.get("id", "unknown")),
                "purpose": purpose,
                "required": bool(field.get("required", False)),
            })
        elif field.get("classification") == "ordinary_fixed" and answer_key in PUBLIC_LINK_KEYS:
            links.append({
                "field_id": str(field.get("id", "unknown")),
                "kind": answer_key,
                "required": bool(field.get("required", False)),
            })
    return {"uploads": uploads, "public_links": links}


def build_material_plan(
    *,
    master_resume_ref: str,
    master_resume_sha256: str,
    tailored_docx_ref: str,
    tailored_docx_sha256: str,
    tailored_pdf_ref: str,
    tailored_pdf_sha256: str,
    fields: Iterable[dict[str, Any]],
    public_values: dict[str, Any],
    cover_letter: dict[str, str] | None = None,
    portfolio_file: dict[str, str] | None = None,
) -> dict[str, Any]:
    for reference in (master_resume_ref, tailored_docx_ref, tailored_pdf_ref):
        validate_secure_reference(reference)
    requests = detect_material_requests(fields)
    cover_requests = [item for item in requests["uploads"] if item["purpose"] == "cover_letter"]
    portfolio_requests = [item for item in requests["uploads"] if item["purpose"] == "portfolio"]

    if cover_letter:
        validate_secure_reference(cover_letter["docx_secure_ref"])
        validate_secure_reference(cover_letter["pdf_secure_ref"])
    if portfolio_file:
        validate_secure_reference(portfolio_file["secure_ref"])

    links: list[dict[str, Any]] = []
    for request in requests["public_links"]:
        value = public_values.get(request["kind"])
        bound = isinstance(value, str) and bool(re.fullmatch(r"https://[^\s]{3,2000}", value))
        links.append({
            **request,
            "binding_status": "BOUND_CONFIRMED_PUBLIC_VALUE" if bound else "MISSING_USER_VALUE",
            "value_sha256": sha256_bytes(value.encode("utf-8")) if bound else None,
            "value_exposed_in_packet": False,
        })

    portfolio_requested = bool(portfolio_requests)
    portfolio_bound = portfolio_requested and portfolio_file is not None
    missing_required = any(item["required"] for item in portfolio_requests) and not portfolio_bound
    missing_required = missing_required or any(item["required"] and item["binding_status"] == "MISSING_USER_VALUE" for item in links)
    return {
        "schema_version": 1,
        "status": "NEEDS_USER_MATERIAL" if missing_required else "READY_FOR_REVIEW",
        "resume": {
            "derivation": "TAILORED_COPY_OF_SINGLE_APPROVED_MASTER",
            "master_secure_ref": master_resume_ref,
            "master_sha256": master_resume_sha256,
            "generated_before_application": True,
            "docx_secure_ref": tailored_docx_ref,
            "docx_sha256": tailored_docx_sha256,
            "pdf_secure_ref": tailored_pdf_ref,
            "pdf_sha256": tailored_pdf_sha256,
        },
        "cover_letter": {
            "request_status": (
                "REQUESTED_REQUIRED" if any(item["required"] for item in cover_requests)
                else "REQUESTED_OPTIONAL" if cover_requests
                else "NOT_REQUESTED"
            ),
            "generation_status": "GENERATED_ON_DEMAND" if cover_letter else "NOT_GENERATED",
            "docx_secure_ref": cover_letter.get("docx_secure_ref") if cover_letter else None,
            "docx_sha256": cover_letter.get("docx_sha256") if cover_letter else None,
            "pdf_secure_ref": cover_letter.get("pdf_secure_ref") if cover_letter else None,
            "pdf_sha256": cover_letter.get("pdf_sha256") if cover_letter else None,
        },
        "public_links": links,
        "portfolio_file": {
            "request_status": (
                "REQUESTED_REQUIRED" if any(item["required"] for item in portfolio_requests)
                else "REQUESTED_OPTIONAL" if portfolio_requests
                else "NOT_REQUESTED"
            ),
            "binding_status": "BOUND_SECURE_FILE" if portfolio_bound else ("MISSING_USER_MATERIAL" if portfolio_requested else "NOT_REQUESTED"),
            "secure_ref": portfolio_file.get("secure_ref") if portfolio_bound else None,
            "sha256": portfolio_file.get("sha256") if portfolio_bound else None,
            "safe_filename": portfolio_file.get("safe_filename") if portfolio_bound else None,
        },
        "all_uploads_and_submission_blocked": True,
        "real_external_actions": 0,
    }
