from __future__ import annotations

import re
from typing import Any, Iterable

from .security import validate_secure_reference
from .util import sha256_bytes


FILE_PURPOSES = ("resume", "cover_letter", "portfolio", "attachment")
PUBLIC_LINK_KEYS = ("github", "portfolio", "website")
MAX_APPLICATION_NARRATIVE_CHARACTERS = 4_000
APPLICATION_NARRATIVE_CLASSIFICATION = "application_narrative_review"

_PROTECTED_COVER_LETTER_CONTEXT = (
    "work authorization", "authorized to work", "visa sponsorship", "sponsorship",
    "salary", "compensation", "pay expectation",
    "criminal", "background check", "non-compete", "non compete", "attest",
    "truthfulness", "legal declaration", "declaration", "consent", "privacy agreement",
    "terms and conditions", "certify", "acknowledge",
    "signature", "sign here", "electronic signature",
    "create account", "register account", "password",
    "start date", "available to start", "relocation", "travel",
    "eeo", "self-identification", "self identification", "voluntary disclosure",
    "race", "ethnicity", "gender", "disability", "veteran", "religion",
    "工作授权", "工作资格", "签证担保", "签证赞助", "薪资", "薪酬",
    "背景调查", "犯罪记录", "竞业", "真实性声明", "法律声明", "同意",
    "隐私协议", "条款和条件", "确认", "电子签名", "签名", "账号注册",
    "创建账号", "密码", "入职日期", "到岗", "搬迁", "出差", "自愿披露",
    "平等就业", "种族", "性别", "残障", "退伍军人", "宗教",
)


def _normalize_semantics(*values: object) -> str:
    return "_".join(
        part for part in (
            re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(value or "").casefold()).strip("_")
            for value in values
        ) if part
    )


def _authoritative_visible_semantics(field: dict[str, Any]) -> str:
    # ``display_label`` is the sanitized, hash-bound representation emitted by
    # ats_browser.  The remaining keys support direct unit inputs and older
    # local snapshots without trusting machine id/name attributes.
    for key in ("display_label", "label", "aria_label", "aria-label", "placeholder"):
        value = str(field.get(key, "")).strip()
        if value:
            return value
    # Accessibility descriptions, nearby help and section headings are
    # authoritative only when the control has no direct prompt.  They can veto
    # a misleading machine ``name=cover_letter`` without overriding a real
    # applicant-visible label.
    for key in ("aria_description", "aria-description", "help_text", "section_heading", "adjacent_text"):
        value = str(field.get(key, "")).strip()
        if value:
            return value
    return ""


def cover_letter_semantics_vetoed(field: dict[str, Any]) -> bool:
    """Return whether non-machine context makes Cover Letter use unsafe.

    Applicant-visible labels identify the requested material, but they may also
    combine that request with a legal declaration (for example, ``Cover Letter
    and Legal Consent``).  Every applicant-visible semantic surface therefore
    participates in the veto.  Machine identifiers remain intentionally excluded.
    """

    material = " ".join(
        str(field.get(key, ""))
        for key in (
            "display_label", "label", "aria_label", "aria-label", "placeholder",
            "aria_description", "aria-description", "help_text",
            "section_heading", "adjacent_text",
        )
    )
    normalized = re.sub(r"\s+", " ", material.casefold().replace("_", " ")).strip()
    return any(term in normalized for term in _PROTECTED_COVER_LETTER_CONTEXT)


def _material_answer_key(material: str, *, is_file: bool, is_textarea: bool) -> str | None:
    normalized = _normalize_semantics(material)
    if (is_file or is_textarea) and any(
        token in normalized for token in ("cover_letter", "motivation_letter", "求职信", "动机信")
    ):
        return "cover_letter"
    if is_file and any(token in normalized for token in ("portfolio", "work_sample", "作品集", "工作样本")):
        return "portfolio_file"
    if is_file and any(token in normalized for token in ("resume", "curriculum_vitae", "简历")):
        return "resume"
    if is_file and re.search(r"(?:^|_)cv(?:_|$)", normalized):
        return "resume"
    return None


def _field_text(field: dict[str, Any]) -> str:
    return " ".join(
        str(field.get(key, ""))
        for key in (
            "answer_key", "id", "name", "display_label", "label", "aria_label", "aria-label",
            "placeholder", "control_type", "type",
        )
    ).casefold().replace("_", " ")


def field_answer_key(field: dict[str, Any]) -> str:
    explicit = str(field.get("answer_key", "")).casefold().strip()
    control_type = str(field.get("control_type") or field.get("type") or "").casefold().strip()
    is_file = control_type == "file" or field.get("classification") == "file_upload_stop"
    is_textarea = control_type == "textarea"
    if is_file or is_textarea:
        if cover_letter_semantics_vetoed(field):
            return "unknown"
        # A hash-bound ATS snapshot has already resolved conflicting visible,
        # accessibility, help, and machine semantics.  Never promote its
        # explicit UNKNOWN back to Cover Letter based on display text alone.
        if explicit == "unknown" and field.get("logical_field_hash") and field.get("prompt_hash"):
            return "unknown"
        visible_semantics = _authoritative_visible_semantics(field)
        visible_material_key = _material_answer_key(
            visible_semantics,
            is_file=is_file,
            is_textarea=is_textarea,
        )
        machine_material_key = _material_answer_key(
            _normalize_semantics(explicit, field.get("id"), field.get("name")),
            is_file=is_file,
            is_textarea=is_textarea,
        )
        if is_textarea and field.get("classification") == APPLICATION_NARRATIVE_CLASSIFICATION:
            return "cover_letter"
        if is_textarea and (visible_material_key == "cover_letter" or machine_material_key == "cover_letter"):
            # A generated narrative is available only for the dedicated,
            # protected-context-vetted classification.  A stale/malicious
            # machine key cannot re-open that path.
            return "unknown"
        if visible_material_key:
            return visible_material_key
        if machine_material_key:
            # A visible prompt is authoritative.  An unrelated prompt makes the
            # machine-derived purpose unknown; file inputs remain attachments,
            # while textareas stay outside the generated-narrative path.
            return "unknown" if visible_semantics else machine_material_key
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


def narrative_effective_max_characters(field: dict[str, Any]) -> int | None:
    status = str(field.get("max_length_status") or "ABSENT").strip().upper()
    value = field.get("max_length")
    if status == "ABSENT" and value is None:
        return MAX_APPLICATION_NARRATIVE_CHARACTERS
    if status != "VALID" or isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, MAX_APPLICATION_NARRATIVE_CHARACTERS)


def detect_material_requests(fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    uploads: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    narratives: list[dict[str, Any]] = []
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
        elif (
            field.get("classification") == APPLICATION_NARRATIVE_CLASSIFICATION
            and answer_key == "cover_letter"
            and str(field.get("control_type", "")).casefold() == "textarea"
        ):
            narratives.append({
                "field_id": str(field.get("id", "unknown")),
                "purpose": "cover_letter",
                "required": bool(field.get("required", False)),
                "max_length": field.get("max_length"),
                "max_length_status": str(field.get("max_length_status") or "ABSENT").strip().upper(),
                "effective_max_characters": narrative_effective_max_characters(field),
            })
    return {"uploads": uploads, "public_links": links, "narratives": narratives}


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
    cover_letter: dict[str, Any] | None = None,
    portfolio_file: dict[str, str] | None = None,
    anticipate_later_pages: bool = False,
) -> dict[str, Any]:
    for reference in (master_resume_ref, tailored_docx_ref, tailored_pdf_ref):
        validate_secure_reference(reference)
    requests = detect_material_requests(fields)
    cover_requests = [item for item in requests["uploads"] if item["purpose"] == "cover_letter"]
    cover_requests.extend(requests["narratives"])
    narrative_requests = requests["narratives"]
    narrative_target_count = len(narrative_requests)
    narrative_control_ref: str | None = None
    narrative_max_characters: int | None = None
    if narrative_target_count == 1:
        narrative_control_ref = str(narrative_requests[0]["field_id"])
        narrative_max_characters = narrative_requests[0]["effective_max_characters"]
        narrative_target_status = (
            "BOUND_EXACT_CONTROL" if narrative_max_characters is not None else "INVALID_MAX_LENGTH"
        )
    elif narrative_target_count:
        narrative_target_status = "AMBIGUOUS"
    else:
        narrative_target_status = "NOT_REQUESTED"
    portfolio_requests = [item for item in requests["uploads"] if item["purpose"] == "portfolio"]

    if cover_letter:
        validate_secure_reference(cover_letter["docx_secure_ref"])
        validate_secure_reference(cover_letter["pdf_secure_ref"])
        validate_secure_reference(cover_letter["narrative_secure_ref"])
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

    portfolio_requested = bool(portfolio_requests) or (anticipate_later_pages and portfolio_file is not None)
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
                else "REQUESTED_OPTIONAL" if cover_requests or (anticipate_later_pages and cover_letter)
                else "NOT_REQUESTED"
            ),
            "generation_status": "GENERATED_ON_DEMAND" if cover_letter else "NOT_GENERATED",
            "docx_secure_ref": cover_letter.get("docx_secure_ref") if cover_letter else None,
            "docx_sha256": cover_letter.get("docx_sha256") if cover_letter else None,
            "pdf_secure_ref": cover_letter.get("pdf_secure_ref") if cover_letter else None,
            "pdf_sha256": cover_letter.get("pdf_sha256") if cover_letter else None,
            "narrative_sha256": cover_letter.get("narrative_sha256") if cover_letter else None,
            "narrative_character_count": cover_letter.get("narrative_character_count") if cover_letter else None,
            "narrative_target_status": narrative_target_status,
            "narrative_target_count": narrative_target_count,
            "narrative_control_ref": narrative_control_ref,
            "narrative_max_characters": narrative_max_characters,
        },
        "public_links": links,
        "portfolio_file": {
            "request_status": (
                "REQUESTED_REQUIRED" if any(item["required"] for item in portfolio_requests)
                else "REQUESTED_OPTIONAL" if portfolio_requests or (anticipate_later_pages and portfolio_file)
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
