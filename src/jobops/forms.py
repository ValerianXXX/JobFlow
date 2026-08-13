from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Iterable

from .security import normalized_name, validate_secure_reference
from .util import sha256_bytes, write_json


PROVIDERS = ("greenhouse", "lever", "workday")


CLASSIFICATION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("final_submit_stop", ("type=submit", "submit application", "final submit", "提交申请", "最终提交")),
    ("work_authorization_stop", ("work authorization", "authorized to work", "legally eligible", "合法工作授权", "工作授权", "工作资格")),
    ("work_authorization_stop", ("visa sponsorship", "sponsorship", "签证担保", "签证赞助", "是否需要担保")),
    ("compensation_stop", ("salary", "compensation", "pay expectation", "薪资", "薪酬", "期望工资", "期望薪资")),
    ("legal_declaration_stop", ("criminal", "background check", "non-compete", "non compete", "attest", "truthfulness", "背景调查", "犯罪记录", "竞业", "真实性声明", "法律声明")),
    ("signature_stop", ("signature", "sign here", "electronic signature", "电子签名", "签名")),
    ("account_creation_stop", ("create account", "register account", "password", "账号注册", "创建账号", "密码")),
    ("sensitive_review", ("start date", "available to start", "relocation", "travel", "入职日期", "到岗", "搬迁", "出差")),
    ("voluntary_disclosure_stop", ("eeo", "self-identification", "self identification", "voluntary disclosure", "race", "ethnicity", "gender", "disability", "veteran", "religion", "自愿披露", "种族", "性别", "残障", "退伍军人", "宗教")),
)

ORDINARY_TERMS = ("portfolio", "linkedin", "github", "website", "personal site", "作品集", "个人网站")
PRIVATE_TERMS = ("legal name", "full name", "first name", "last name", "email", "phone", "address", "姓名", "邮箱", "电话", "地址")


def _field_material(field: dict[str, Any], page_context: str = "") -> str:
    values = []
    for key in ("label", "id", "name", "type", "autocomplete", "placeholder", "help_text", "aria_label", "aria_description", "section_heading", "adjacent_text"):
        raw = field.get(key, "")
        if isinstance(raw, list):
            raw = " ".join(str(item) for item in raw)
        values.append(f"{key}={raw}")
    options = field.get("options", [])
    values.extend(str(item) for item in options if item is not None)
    values.append(page_context)
    return normalized_name(" ".join(values)).replace("_", " ")


def classify_application_field(field: dict[str, Any], *, page_context: str = "", blocked_categories: Iterable[str] = ()) -> tuple[str, str]:
    material = _field_material(field, page_context)
    if normalized_name(str(field.get("type", ""))) == "file":
        return "file_upload_stop", "File selection is an external upload action and remains blocked."
    if normalized_name(str(field.get("type", ""))) in {"submit", "image"}:
        return "final_submit_stop", "Final submit controls always require the external action gateway."
    section = normalized_name(str(field.get("section_heading", "")) + " " + page_context)
    if any(term in section for term in ("eeo", "self-identification", "self identification", "voluntary", "自愿披露", "平等就业")):
        return "voluntary_disclosure_stop", "The entire EEO/self-identification section is stopped by default."
    for classification, terms in CLASSIFICATION_PATTERNS:
        if any(normalized_name(term) in material for term in terms):
            return classification, f"Matched protected field context for {classification}."
    for category in blocked_categories:
        if normalized_name(str(category).replace("_", " ")) in material:
            return "sensitive_review", "Matched configured protected category."
    if any(term in material for term in ORDINARY_TERMS):
        return "ordinary_fixed", "Recognized non-private fixed field."
    if any(term in material for term in PRIVATE_TERMS):
        return "private_fixed", "Recognized private fixed field; value must resolve from secure-ref."
    return "unknown_stop", "Unrecognized fields fail closed."


def map_fields(fields: Iterable[dict[str, Any]], known_answers: dict[str, str], blocked_categories: list[str], *, page_context: str = "") -> dict[str, object]:
    mapped: list[dict[str, Any]] = []
    stopped: list[str] = []
    unknown: list[str] = []
    for field in fields:
        label = str(field.get("label", ""))
        field_id = str(field.get("id") or field.get("name") or "unknown_field")
        classification, reason = classify_application_field(field, page_context=page_context, blocked_categories=blocked_categories)
        answer = known_answers.get(field_id)
        record: dict[str, Any] = {"id": field_id, "label": label, "classification": classification, "reason": reason}
        if classification == "ordinary_fixed" and answer not in (None, "", "UNKNOWN"):
            record.update({"gate": "PREFILL_ALLOWED", "action": "PREFILL", "value": answer})
        elif classification == "private_fixed" and answer and str(answer).startswith("secure-ref:"):
            validate_secure_reference(str(answer))
            record.update({"gate": "PREFILL_ALLOWED", "action": "PREFILL_FROM_SECURE_STORE", "secure_ref": answer, "redacted_summary": "PRIVATE_VALUE_PRESENT"})
        else:
            record.update({
                "gate": "STOP_REQUIRED", "action": "STOP", "status": "STOPPED",
                "secure_ref": answer if answer and str(answer).startswith("secure-ref:") else None,
                "redacted_summary": "VALUE_WITHHELD" if answer not in (None, "", "UNKNOWN", "UNANSWERED") else "UNANSWERED",
            })
            stopped.append(field_id)
            if classification == "unknown_stop":
                unknown.append(field_id)
        mapped.append(record)
    return {"fields": mapped, "sensitive_fields": stopped, "unknown_fields": unknown, "submit_blocked": True}


def build_mock_ats_site(output_dir: Path, provider: str, fields: list[dict[str, Any]]) -> dict[str, object]:
    provider = provider.casefold()
    if provider not in PROVIDERS:
        raise ValueError(provider)
    output_dir.mkdir(parents=True, exist_ok=True)
    form_fields = []
    for field in fields:
        field_id = html.escape(str(field["id"]), quote=True)
        label = html.escape(str(field["label"]))
        field_type = str(field.get("type", "text"))
        classification, _ = classify_application_field(field)
        form_fields.append(f'<section><label for="{field_id}">{label}</label><input id="{field_id}" name="{field_id}" type="{field_type}" data-classification="{classification}"></section>')
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>JobOps {provider.title()} Local Simulation</title>
<style>body{{font-family:Arial,sans-serif;background:#f5f7fa;color:#172033;margin:0}}main{{max-width:760px;margin:32px auto;background:#fff;padding:36px;border:1px solid #dbe1ea;border-radius:12px}}h1{{margin:0 0 8px;color:#12335b}}.notice{{background:#eef4fb;border-left:4px solid #285f9b;padding:12px;margin:18px 0}}label{{font-weight:600;display:block;margin-top:16px}}input{{width:100%;box-sizing:border-box;margin-top:6px;padding:10px;border:1px solid #aeb9c9;border-radius:6px}}button{{margin-top:24px;padding:12px 18px;background:#777;color:#fff;border:0;border-radius:6px}}small{{display:block;color:#657086;margin-top:10px}}</style></head>
<body><main data-provider="{provider}" data-local-simulation="true"><h1>{provider.title()} application simulation</h1><p>Strategy Analyst - Example Analytics Lab</p><div class="notice">Local synthetic page. No network request or real application is possible.</div><form>{''.join(form_fields)}<button id="submit" type="button" disabled aria-disabled="true" data-classification="final_submit_stop">Submit blocked in dry-run</button><small id="gate-status">Final submission gate: BLOCKED</small></form></main></body></html>"""
    path = output_dir / f"{provider}.html"
    path.write_text(document, encoding="utf-8")
    manifest = {"provider": provider, "path": path.name, "sha256": sha256_bytes(document.encode("utf-8")), "network_actions": 0, "submit_blocked": True}
    write_json(output_dir / f"{provider}.json", manifest)
    return manifest
