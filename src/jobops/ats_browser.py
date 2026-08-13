from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from .errors import JobOpsError
from .forms import classify_application_field
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .sourcing import _canonical_url, _host, host_matches_registered, url_has_sensitive_query
from .util import canonical_json, project_root, sha256_bytes, stable_id


MAX_FORM_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_FORM_CONTROLS = 500
MAX_OPTIONS_PER_CONTROL = 200
MAX_FORM_SEQUENCE_PAGES = 20
MAX_FORM_SEQUENCE_BYTES = 64 * 1024 * 1024
CONTROL_TYPES = {
    "text", "email", "tel", "url", "number", "date", "datetime-local", "radio", "checkbox",
    "select", "textarea", "file", "password", "submit", "button", "image", "other",
}
CAPTCHA_SIGNALS = ("captcha", "recaptcha", "hcaptcha", "cf-turnstile", "challenge-platform")
MFA_SIGNALS = ("one-time code", "verification code", "security code", "two-factor", "2fa", "mfa", "otp", "验证码", "双重验证")
LOGIN_SIGNALS = ("sign in", "log in", "login required", "existing account", "登录", "已有账号")
ACCOUNT_SIGNALS = ("create account", "register account", "set password", "创建账号", "注册账号", "设置密码")


def _compact(value: object, *, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_control_type(tag: str, raw_type: str) -> str:
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    if tag == "button":
        value = raw_type.casefold() or "submit"
        return value if value in {"submit", "button"} else "button"
    value = raw_type.casefold() or "text"
    return value if value in CONTROL_TYPES else "other"


def _suggest_answer_key(field: dict[str, Any]) -> str:
    material = " ".join(
        str(field.get(key, "")) for key in (
            "identifier", "name", "type", "autocomplete", "label", "placeholder", "aria_label", "section_heading",
        )
    ).casefold().replace("-", "_").replace(" ", "_")
    candidates = (
        ("first_name", ("first_name", "given_name", "given-name", "名_")),
        ("last_name", ("last_name", "family_name", "family-name", "姓_")),
        ("full_name", ("full_name", "legal_name", "candidate_name", "姓名", "type_name")),
        ("email", ("email", "邮箱")),
        ("phone", ("phone", "telephone", "mobile", "电话", "手机")),
        ("linkedin", ("linkedin",)),
        ("github", ("github",)),
        ("portfolio", ("portfolio", "作品集")),
        ("website", ("website", "personal_site", "个人网站")),
        ("address", ("address", "street", "地址")),
        ("work_authorization", ("work_authorization", "authorized_to_work", "工作授权", "工作资格")),
        ("salary", ("salary", "compensation", "薪资", "薪酬")),
        ("resume", ("resume", "cv", "简历")),
    )
    for answer_key, signals in candidates:
        if any(signal in material for signal in signals):
            return answer_key
    return "UNKNOWN"


def _infer_step_kind(material: str) -> str:
    value = material.casefold()
    if any(signal in value for signal in ("voluntary self-identification", "self identification", "eeo", "gender", "disability", "veteran", "自愿披露")):
        return "VOLUNTARY_DISCLOSURE"
    if any(signal in value for signal in ("review application", "review and submit", "confirm your application", "检查申请", "确认申请")):
        return "REVIEW"
    if any(signal in value for signal in ("work authorization", "visa sponsorship", "application questions", "salary", "工作授权", "签证", "申请问题")):
        return "APPLICATION_QUESTIONS"
    if any(signal in value for signal in ("work experience", "education", "employment history", "工作经历", "教育经历")):
        return "EXPERIENCE_EDUCATION"
    if any(signal in value for signal in ACCOUNT_SIGNALS + LOGIN_SIGNALS) or "password" in value:
        return "ACCOUNT_OR_LOGIN"
    if any(signal in value for signal in ("my information", "contact information", "full name", "email address", "个人信息", "联系信息")):
        return "MY_INFORMATION"
    return "UNKNOWN"


class _FormHTMLParser(HTMLParser):
    """Extract form semantics while intentionally discarding every entered value."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict[str, Any]] = []
        self.form_actions: list[str] = []
        self.iframes: list[str] = []
        self.ignored_hidden_controls = 0
        self._labels_by_for: dict[str, list[str]] = {}
        self._label_for: str | None = None
        self._label_text: list[str] = []
        self._label_control_indexes: list[int] = []
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._section_heading = ""
        self._active_select: int | None = None
        self._active_textarea: int | None = None
        self._active_button: int | None = None
        self._option_selected = False
        self._suppressed_depth = 0
        self._security_material: list[str] = []

    @property
    def security_material(self) -> str:
        return _compact(" ".join(self._security_material), limit=250_000).casefold()

    def _new_control(self, tag: str, attrs: dict[str, str]) -> int | None:
        raw_type = attrs.get("type", "")
        control_type = _safe_control_type(tag, raw_type)
        if control_type == "hidden" or (tag == "input" and raw_type.casefold() == "hidden"):
            self.ignored_hidden_controls += 1
            return None
        if len(self.controls) >= MAX_FORM_CONTROLS:
            raise JobOpsError("ATS_FORM_TOO_MANY_CONTROLS", "The local form snapshot exceeds the safe control limit.", maximum=MAX_FORM_CONTROLS)
        identifier = _compact(attrs.get("id") or attrs.get("name"), limit=256)
        existing = bool(attrs.get("value") or "checked" in attrs or "selected" in attrs)
        record = {
            "identifier": identifier,
            "name": _compact(attrs.get("name"), limit=256),
            "type": control_type,
            "autocomplete": _compact(attrs.get("autocomplete"), limit=100),
            "placeholder": _compact(attrs.get("placeholder")),
            "aria_label": _compact(attrs.get("aria-label")),
            "aria_description": _compact(attrs.get("aria-description")),
            "help_text": "",
            "section_heading": self._section_heading,
            "label": "",
            "options": [],
            "required": "required" in attrs or attrs.get("aria-required", "").casefold() == "true",
            "existing_value_discarded": existing,
        }
        self.controls.append(record)
        index = len(self.controls) - 1
        if self._label_for is not None:
            self._label_control_indexes.append(index)
        self._security_material.extend(
            value for value in (
                identifier, record["name"], control_type, record["autocomplete"], record["placeholder"],
                record["aria_label"], record["aria_description"], self._section_heading,
                _compact(attrs.get("class")),
            ) if value
        )
        return index

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        values = {key.casefold(): (value or "") for key, value in attrs}
        if lowered in {"script", "style", "noscript", "template"}:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if lowered == "form":
            self.form_actions.append(values.get("action", ""))
        elif lowered == "iframe" and values.get("src"):
            self.iframes.append(values["src"])
        elif lowered in {"h1", "h2", "h3", "legend"}:
            self._heading_tag = lowered
            self._heading_text = []
        elif lowered == "label":
            self._label_for = values.get("for") or ""
            self._label_text = []
            self._label_control_indexes = []
        elif lowered in {"input", "select", "textarea", "button"}:
            index = self._new_control(lowered, values)
            if lowered == "select":
                self._active_select = index
            elif lowered == "textarea":
                self._active_textarea = index
            elif lowered == "button":
                self._active_button = index
        elif lowered == "option" and self._active_select is not None:
            self._option_selected = "selected" in values
            if self._option_selected:
                self.controls[self._active_select]["existing_value_discarded"] = True
        self._security_material.extend(value for key, value in values.items() if key in {"id", "name", "class", "role", "aria-label"} and value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "template"}:
            if self._suppressed_depth:
                self._suppressed_depth -= 1
            return
        if self._suppressed_depth:
            return
        if lowered == self._heading_tag:
            heading = _compact(" ".join(self._heading_text))
            if heading:
                self._section_heading = heading
                self._security_material.append(heading)
            self._heading_tag = None
            self._heading_text = []
        elif lowered == "label" and self._label_for is not None:
            text = _compact(" ".join(self._label_text))
            if text:
                if self._label_for:
                    self._labels_by_for.setdefault(self._label_for, []).append(text)
                for index in self._label_control_indexes:
                    self.controls[index]["label"] = text
                self._security_material.append(text)
            self._label_for = None
            self._label_text = []
            self._label_control_indexes = []
        elif lowered == "select":
            self._active_select = None
        elif lowered == "textarea":
            self._active_textarea = None
        elif lowered == "button":
            self._active_button = None
        elif lowered == "option":
            self._option_selected = False

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        compact = _compact(data)
        if not compact:
            return
        if self._heading_tag is not None:
            self._heading_text.append(compact)
        if self._label_for is not None:
            self._label_text.append(compact)
        if self._active_select is not None and len(self.controls[self._active_select]["options"]) < MAX_OPTIONS_PER_CONTROL:
            self.controls[self._active_select]["options"].append(compact)
        if self._active_textarea is not None:
            self.controls[self._active_textarea]["existing_value_discarded"] = True
        if self._active_button is not None:
            self.controls[self._active_button]["label"] = _compact(
                self.controls[self._active_button]["label"] + " " + compact
            )
        self._security_material.append(compact)

    def finalize(self) -> None:
        for control in self.controls:
            identifier = control["identifier"]
            if not control["label"] and identifier in self._labels_by_for:
                control["label"] = _compact(" ".join(self._labels_by_for[identifier]))


def _provider_host_matches(provider: str, host: str, company_domain: str) -> bool:
    value = _host(host)
    return {
        "company": host_matches_registered(value, company_domain),
        "workday": value.endswith(".myworkdayjobs.com") or value.endswith(".workday.com"),
        "greenhouse": value == "boards.greenhouse.io" or value == "job-boards.greenhouse.io" or value.endswith(".greenhouse.io"),
        "lever": value == "jobs.lever.co" or value.endswith(".lever.co"),
    }.get(provider, False)


def _action_host_status(action: str, current_url: str) -> str:
    if not action:
        return "ABSENT"
    try:
        resolved = _canonical_url(urljoin(current_url, action))
    except (JobOpsError, ValueError):
        return "UNSAFE"
    return "SAME_ORIGIN" if _host(urlparse(resolved).hostname or "") == _host(urlparse(current_url).hostname or "") else "CROSS_ORIGIN_STOP"


def _frame_status(src: str, current_url: str) -> str:
    try:
        resolved = _canonical_url(urljoin(current_url, src))
    except (JobOpsError, ValueError):
        return "UNSAFE_IFRAME"
    return "SAME_ORIGIN_IFRAME" if _host(urlparse(resolved).hostname or "") == _host(urlparse(current_url).hostname or "") else "CROSS_ORIGIN_IFRAME_STOP"


def analyze_local_ats_form(
    snapshot: bytes,
    *,
    route: dict[str, Any],
    blocked_categories: list[str],
) -> dict[str, Any]:
    """Analyze a verified-route local HTML snapshot. No browser or network transport exists here."""
    if not snapshot or len(snapshot) > MAX_FORM_SNAPSHOT_BYTES:
        raise JobOpsError("ATS_FORM_SNAPSHOT_SIZE_INVALID", "The local ATS form snapshot is empty or exceeds the parser limit.", maximum_bytes=MAX_FORM_SNAPSHOT_BYTES)
    validate_named("source-route", route, project_root() / "schemas")
    if route.get("status") not in {"ROUTE_APPROVED", "NEEDS_ACCOUNT_APPROVAL", "NEEDS_USER_INPUT"}:
        raise JobOpsError("ATS_ROUTE_NOT_REVIEWABLE", "The form snapshot does not have a reviewable verified source route.")
    current_url = _canonical_url(str(route["current_url"]))
    if url_has_sensitive_query(current_url):
        raise JobOpsError(
            "ATS_ROUTE_SENSITIVE_QUERY",
            "The routed form URL contains a sensitive query field and cannot enter a local form report.",
        )
    provider = str(route["provider"])
    if not _provider_host_matches(provider, urlparse(current_url).hostname or "", str(route["company_domain"])):
        raise JobOpsError("ATS_PROVIDER_HOST_MISMATCH", "The verified provider does not match the routed form host.")
    try:
        html = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JobOpsError("ATS_FORM_SNAPSHOT_ENCODING_INVALID", "The local form snapshot must be UTF-8.") from exc

    parser = _FormHTMLParser()
    try:
        parser.feed(html)
        parser.close()
        parser.finalize()
    except JobOpsError:
        raise
    except Exception as exc:
        raise JobOpsError("ATS_FORM_SNAPSHOT_INVALID", "The local ATS form snapshot could not be parsed safely.") from exc

    page_hash = sha256_bytes(snapshot)
    safe_fields: list[dict[str, Any]] = []
    classification_counts: dict[str, int] = {}
    for index, raw in enumerate(parser.controls):
        classification, _ = classify_application_field(raw, blocked_categories=blocked_categories)
        if raw["type"] == "file":
            classification = "file_upload_stop"
        reason_code = {
            "ordinary_fixed": "KNOWN_PUBLIC_BINDING_REQUIRED",
            "private_fixed": "SECURE_REFERENCE_REQUIRED",
            "file_upload_stop": "FILE_UPLOAD_EXTERNAL_ACTION",
            "navigation_control_stop": "NAVIGATION_ACTION_REVIEW",
            "final_submit_stop": "FINAL_SUBMIT_EXTERNAL_ACTION",
            "account_creation_stop": "ACCOUNT_CREATION_REQUIRES_APPROVAL",
            "unknown_stop": "UNKNOWN_FIELD_FAIL_CLOSED",
        }.get(classification, "PROTECTED_OR_SENSITIVE_FIELD")
        semantic_material = {
            "identifier": raw["identifier"], "name": raw["name"], "type": raw["type"],
            "label": raw["label"], "section": raw["section_heading"], "options": raw["options"], "index": index,
        }
        prompt_material = {
            "label": raw["label"], "placeholder": raw["placeholder"], "aria_label": raw["aria_label"],
            "section": raw["section_heading"], "options": raw["options"],
        }
        prompt_hash = sha256_bytes(canonical_json(prompt_material))
        answer_key = _suggest_answer_key(raw)
        control_ref = stable_id("CTL", page_hash, canonical_json(semantic_material).decode("utf-8"))
        logical_field_hash = sha256_bytes(canonical_json({
            "provider": provider, "answer_key": answer_key, "classification": classification,
            "control_type": raw["type"], "prompt_hash": prompt_hash,
        }))
        safe_fields.append({
            "control_ref": control_ref,
            "control_type": raw["type"],
            "required": bool(raw["required"]),
            "classification": classification,
            "answer_key": answer_key,
            "logical_field_hash": logical_field_hash,
            "reason_code": reason_code,
            "prompt_hash": prompt_hash,
            "option_count": len(raw["options"]),
            "existing_value_discarded": bool(raw["existing_value_discarded"]),
            "binding_status": "UNBOUND",
            "action": "STOP",
        })
        classification_counts[classification] = classification_counts.get(classification, 0) + 1

    security_material = parser.security_material
    blockers: list[str] = []
    if any(signal in security_material for signal in CAPTCHA_SIGNALS):
        blockers.append("CAPTCHA_STOP")
    if any(signal in security_material for signal in MFA_SIGNALS):
        blockers.append("MFA_STOP")
    if any(signal in security_material for signal in LOGIN_SIGNALS):
        blockers.append("LOGIN_STOP")
    if any(signal in security_material for signal in ACCOUNT_SIGNALS) or classification_counts.get("account_creation_stop", 0):
        blockers.append("ACCOUNT_CREATION_STOP")
    if classification_counts.get("file_upload_stop", 0):
        blockers.append("FILE_UPLOAD_STOP")
    if classification_counts.get("navigation_control_stop", 0):
        blockers.append("NAVIGATION_ACTION_STOP")
    if classification_counts.get("final_submit_stop", 0):
        blockers.append("FINAL_SUBMIT_STOP")

    action_statuses = [_action_host_status(value, current_url) for value in parser.form_actions]
    frame_statuses = [_frame_status(value, current_url) for value in parser.iframes]
    if any(value in {"UNSAFE", "CROSS_ORIGIN_STOP"} for value in action_statuses):
        blockers.append("FORM_ACTION_HOST_STOP")
    if any(value in {"UNSAFE_IFRAME", "CROSS_ORIGIN_IFRAME_STOP"} for value in frame_statuses):
        blockers.append("CROSS_ORIGIN_IFRAME_STOP")
    blockers = sorted(set(blockers))
    step_kind = _infer_step_kind(security_material)

    material = {
        "route_hash": route["route_hash"], "current_url": current_url, "provider": provider,
        "page_content_hash": page_hash, "step_kind": step_kind, "fields": safe_fields, "blockers": blockers,
        "form_action_statuses": action_statuses, "iframe_statuses": frame_statuses,
    }
    form_hash = sha256_bytes(canonical_json(material))
    report = {
        "schema_version": 1,
        "status": "FORM_SNAPSHOT_ANALYZED",
        "source_mode": "LOCAL_SNAPSHOT_ONLY",
        "provider": provider,
        "step_kind": step_kind,
        "canonical_url": current_url,
        "source_route_hash": route["route_hash"],
        "page_content_hash": page_hash,
        "form_snapshot_hash": form_hash,
        "field_count": len(safe_fields),
        "ignored_hidden_control_count": parser.ignored_hidden_controls,
        "classification_counts": classification_counts,
        "fields": safe_fields,
        "blockers": blockers,
        "form_action_statuses": action_statuses,
        "iframe_statuses": frame_statuses,
        "entered_values_retained": False,
        "submit_blocked": True,
        "upload_blocked": True,
        "account_creation_blocked": True,
        "untrusted_page_content_executed": False,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_named("ats-form-snapshot", report, project_root() / "schemas")
    validate_ats_form_snapshot_integrity(report)
    return report


def _form_snapshot_hash(value: dict[str, Any]) -> str:
    material = {
        "route_hash": value["source_route_hash"], "current_url": value["canonical_url"], "provider": value["provider"],
        "page_content_hash": value["page_content_hash"], "step_kind": value["step_kind"], "fields": value["fields"], "blockers": value["blockers"],
        "form_action_statuses": value["form_action_statuses"], "iframe_statuses": value["iframe_statuses"],
    }
    return sha256_bytes(canonical_json(material))


def validate_ats_form_snapshot_integrity(value: dict[str, Any]) -> None:
    validate_named("ats-form-snapshot", value, project_root() / "schemas")
    if value["form_snapshot_hash"] != _form_snapshot_hash(value):
        raise JobOpsError("FORM_SNAPSHOT_INTEGRITY_FAILED", "The local ATS form snapshot hash does not bind its current structure.")


def _browser_plan_hash(value: dict[str, Any]) -> str:
    material = {
        "form_snapshot_hash": value["form_snapshot_hash"], "source_route_hash": value["source_route_hash"],
        "canonical_url": value["canonical_url"], "actions": value["actions"],
    }
    return sha256_bytes(canonical_json(material))


def validate_browser_action_plan_integrity(value: dict[str, Any]) -> None:
    validate_named("browser-action-plan", value, project_root() / "schemas")
    if value["plan_hash"] != _browser_plan_hash(value):
        raise JobOpsError("BROWSER_PLAN_INTEGRITY_FAILED", "The browser action plan hash does not bind its current actions.")
    if len({item["control_ref"] for item in value["actions"]}) != len(value["actions"]):
        raise JobOpsError("BROWSER_PLAN_DUPLICATE_CONTROL", "A browser plan cannot target one control more than once.")
    for item in value["actions"]:
        reference = item["binding_ref"]
        if item["action"] == "PROPOSE_PREFILL" and item["classification"] not in {"ordinary_fixed", "private_fixed"}:
            raise JobOpsError("BROWSER_PLAN_PROTECTED_FIELD", "Protected, unknown and final-submit controls cannot enter a prefill proposal.")
        if item["binding_kind"] == "SECURE_REF" and (not isinstance(reference, str) or not reference.startswith("secure-ref:")):
            raise JobOpsError("BROWSER_PLAN_BINDING_INVALID", "A private field binding must remain an opaque secure reference.")
        if item["binding_kind"] == "PUBLIC_VALUE_HASH" and (not isinstance(reference, str) or not reference.startswith("sha256:")):
            raise JobOpsError("BROWSER_PLAN_BINDING_INVALID", "A public field binding must be represented only by its content hash.")


def build_browser_action_plan(snapshot: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    """Bind opaque values to a safe plan; returned data never includes plaintext values."""
    validate_ats_form_snapshot_integrity(snapshot)
    known_refs = {str(item["control_ref"]) for item in snapshot["fields"]}
    if set(bindings) - known_refs:
        raise JobOpsError("FORM_BINDING_UNKNOWN_CONTROL", "A proposed value targets a control outside the current form snapshot.")
    actions: list[dict[str, Any]] = []
    fillable = 0
    for field in snapshot["fields"]:
        control_ref = str(field["control_ref"])
        classification = str(field["classification"])
        binding = bindings.get(control_ref)
        action = "STOP"
        kind = "NONE"
        binding_ref: str | None = None
        reason_code = str(field["reason_code"])
        if classification == "ordinary_fixed" and isinstance(binding, dict) and binding.get("kind") == "public_value":
            value = str(binding.get("value", ""))
            if value and value not in {"UNKNOWN", "UNANSWERED"}:
                action, kind, binding_ref = "PROPOSE_PREFILL", "PUBLIC_VALUE_HASH", sha256_bytes(value.encode("utf-8"))
                fillable += 1
        elif classification == "private_fixed" and isinstance(binding, dict) and binding.get("kind") == "secure_ref":
            value = str(binding.get("value", ""))
            validate_secure_reference(value)
            action, kind, binding_ref = "PROPOSE_PREFILL", "SECURE_REF", value
            fillable += 1
        actions.append({
            "control_ref": control_ref, "classification": classification, "action": action,
            "binding_kind": kind, "binding_ref": binding_ref, "reason_code": reason_code,
        })
    plan_material = {
        "form_snapshot_hash": snapshot["form_snapshot_hash"], "source_route_hash": snapshot["source_route_hash"],
        "canonical_url": snapshot["canonical_url"], "actions": actions,
    }
    plan = {
        "schema_version": 1,
        "status": "LOCAL_PLAN_READY" if fillable else "NO_FIELDS_BOUND",
        "form_snapshot_hash": snapshot["form_snapshot_hash"],
        "source_route_hash": snapshot["source_route_hash"],
        "canonical_url": snapshot["canonical_url"],
        "plan_hash": sha256_bytes(canonical_json(plan_material)),
        "fillable_count": fillable,
        "stopped_count": len(actions) - fillable,
        "actions": actions,
        "submit_blocked": True,
        "upload_blocked": True,
        "account_creation_blocked": True,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    validate_named("browser-action-plan", plan, project_root() / "schemas")
    validate_browser_action_plan_integrity(plan)
    return plan


def assert_form_snapshot_current(expected: dict[str, Any], current_snapshot: dict[str, Any]) -> None:
    validate_ats_form_snapshot_integrity(expected)
    validate_ats_form_snapshot_integrity(current_snapshot)
    if expected["canonical_url"] != current_snapshot["canonical_url"] or expected["source_route_hash"] != current_snapshot["source_route_hash"]:
        raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The ATS form no longer matches the verified source route.")
    if expected["form_snapshot_hash"] != current_snapshot["form_snapshot_hash"]:
        raise JobOpsError("SITE_CHANGED", "The ATS form changed; rebuild field analysis, review packet and approval before continuing.")


def _form_sequence_hash(value: dict[str, Any]) -> str:
    material = {
        "provider": value["provider"], "canonical_url": value["canonical_url"],
        "source_route_hash": value["source_route_hash"], "steps": value["steps"],
        "unique_field_count": value["unique_field_count"], "duplicate_field_count": value["duplicate_field_count"],
        "blockers": value["blockers"],
    }
    return sha256_bytes(canonical_json(material))


def validate_ats_form_sequence_integrity(value: dict[str, Any]) -> None:
    validate_named("ats-form-sequence", value, project_root() / "schemas")
    if value["sequence_hash"] != _form_sequence_hash(value):
        raise JobOpsError("FORM_SEQUENCE_INTEGRITY_FAILED", "The ATS form sequence hash does not bind its current steps.")
    if len({step["form_snapshot_hash"] for step in value["steps"]}) != len(value["steps"]):
        raise JobOpsError("FORM_SEQUENCE_DUPLICATE_PAGE", "The same ATS form snapshot cannot be counted as two steps.")


def analyze_local_ats_form_sequence(
    snapshots: list[bytes],
    *,
    route: dict[str, Any],
    blocked_categories: list[str],
) -> dict[str, Any]:
    if not snapshots or len(snapshots) > MAX_FORM_SEQUENCE_PAGES:
        raise JobOpsError("ATS_FORM_SEQUENCE_SIZE_INVALID", "A local ATS form sequence must contain 1 to 20 saved pages.")
    if sum(len(item) for item in snapshots) > MAX_FORM_SEQUENCE_BYTES:
        raise JobOpsError("ATS_FORM_SEQUENCE_BYTES_EXCEEDED", "The local ATS form sequence exceeds the total byte limit.")
    reports = [analyze_local_ats_form(item, route=route, blocked_categories=blocked_categories) for item in snapshots]
    logical_fields = [str(field["logical_field_hash"]) for report in reports for field in report["fields"]]
    unique_fields = set(logical_fields)
    blockers = sorted({str(blocker) for report in reports for blocker in report["blockers"]})
    steps = [
        {
            "step_index": index,
            "step_kind": report["step_kind"],
            "form_snapshot_hash": report["form_snapshot_hash"],
            "page_content_hash": report["page_content_hash"],
            "field_count": report["field_count"],
            "blocker_count": len(report["blockers"]),
        }
        for index, report in enumerate(reports, 1)
    ]
    result = {
        "schema_version": 1,
        "status": "LOCAL_FORM_SEQUENCE_ANALYZED",
        "source_mode": "LOCAL_SNAPSHOT_SEQUENCE_ONLY",
        "provider": route["provider"],
        "canonical_url": route["current_url"],
        "source_route_hash": route["route_hash"],
        "step_count": len(steps),
        "steps": steps,
        "unique_field_count": len(unique_fields),
        "duplicate_field_count": len(logical_fields) - len(unique_fields),
        "blockers": blockers,
        "sequence_hash": "",
        "navigation_performed": False,
        "entered_values_retained": False,
        "browser_actions": 0,
        "network_actions": 0,
        "real_external_actions": 0,
    }
    result["sequence_hash"] = _form_sequence_hash(result)
    validate_ats_form_sequence_integrity(result)
    return result
