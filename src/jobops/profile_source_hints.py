"""Bounded applicant-header hints, separate from whole-document privacy detection.

These are editable, unconfirmed inputs, never Claims or legal-name assertions.
Values and their hashes belong only in the encrypted onboarding state.
"""
from __future__ import annotations

import re
from typing import Any

from .candidate_profile import classify_public_urls, parse_mailing_address, split_display_name
from .resume_onboarding import ADDRESS_PATTERN, EMAIL_PATTERN, LINKEDIN_PATTERN, PHONE_PATTERN, WEBSITE_PATTERN
from .util import sha256_bytes

HINT_FIELDS = frozenset({"candidate_display_name", "first_name", "last_name", "email", "phone",
                         "address", "city", "state", "postal_code", "country", "linkedin_url", "github_url", "website_url"})
_PROFILE = re.compile(r"^(?:profile|personal (?:profile|details|information)|contact(?: information| details)?|个人信息|基本信息|联系方式|联系信息)$", re.I)
_BODY = re.compile(r"experience|employment|education|projects?|skills?|certifications?|references?|referees?|summary|languages?|work history|工作经历|职业经历|教育|项目|技能|证书|推荐人|证明人|简介|语言", re.I)
_NAME = re.compile(r"^(?:name|full name|candidate name|姓名|名字|申请人姓名)\s*[:：]\s*(.+)$", re.I)
_LABEL = re.compile(r"(?:e-?mail|contact|phone|telephone|mobile|address|linkedin|github|website|portfolio|url|联系邮箱|电子邮箱|邮箱|电话|手机|通讯地址|地址|个人网站|网址|作品集)\s*[:：]?\s*", re.I)
_ROLE = re.compile(r"\b(?:senior|junior|lead|principal|analyst|engineer|manager|director|consultant|developer|designer|resume|curriculum|vitae|references|referee|employer)\b|申请人资料|个人简历|推荐人", re.I)
_THIRD_PARTY = re.compile(r"\b(?:referee|reference|employer|supervisor|manager|office|company|client)\b|推荐人|证明人|雇主|公司|客户", re.I)


def collect(text: str, descriptor: dict[str, Any], source_id: str) -> tuple[dict, dict, dict]:
    """Use only a bounded initial header. Never reopen identity scope in a body.

    Unsupported formats remain visible in the saved transcript for manual entry.
    References and other contacts remain in the separate privacy-token inventory.
    """
    if descriptor.get("status") != "AVAILABLE":
        return {}, {}, {"status": "SOURCE_REVIEW_UNAVAILABLE", "suggested_fields": [], "ambiguous_fields": []}
    lines = text.splitlines()
    header: list[tuple[int, str]] = []
    for number, raw in enumerate(lines[:80], 1):
        line = re.sub(r"\s+", " ", raw).strip(" \t•·-*#")
        if not line:
            continue
        heading = line.rstrip(":：").strip()
        if _PROFILE.fullmatch(heading):
            continue
        contact_row = any(pattern.search(line) for pattern in (EMAIL_PATTERN, PHONE_PATTERN, WEBSITE_PATTERN, LINKEDIN_PATTERN, ADDRESS_PATTERN))
        if _BODY.search(heading) and not _NAME.match(line) and (not contact_row or _BODY.match(heading)):
            break
        header.append((number, line))
        if len(header) >= 16:
            break

    offers: dict[str, list[tuple[str, int, str]]] = {}
    def offer(field: str, value: str, line: int, basis: str) -> None:
        if field in HINT_FIELDS and value and len(value) <= 2_000:
            offers.setdefault(field, []).append((value, line, basis))

    names: list[tuple[str, int, str]] = []
    contact_lines: set[int] = set()
    for number, line in header:
        match = _NAME.fullmatch(line)
        if match:
            value = match.group(1).strip()
            if 0 < len(value) <= 160 and not any(pattern.search(value) for pattern in (EMAIL_PATTERN, PHONE_PATTERN, WEBSITE_PATTERN)):
                names.append((value, number, "EXPLICIT_LABEL"))
            continue
        prefix = EMAIL_PATTERN.split(line)[0] if EMAIL_PATTERN.search(line) else line.split("https://")[0]
        if _THIRD_PARTY.search(prefix):
            continue
        matches = [(field, match.group()) for field, pattern in (
            ("email", EMAIL_PATTERN), ("phone", PHONE_PATTERN), ("address", ADDRESS_PATTERN),
            ("url", WEBSITE_PATTERN), ("url", LINKEDIN_PATTERN),
        ) for match in pattern.finditer(line)]
        # A contact row consists of labels, contact values and separators. Do not
        # lift an address from a descriptive sentence or employer annotation.
        remainder = line
        for _, value in sorted(matches, key=lambda item: len(item[1]), reverse=True):
            remainder = remainder.replace(value, " ")
        remainder = _LABEL.sub("", remainder).strip(" \t|/,;·•:：()（）")
        if remainder or not matches:
            continue
        contact_lines.add(number)
        for field, value in matches:
            if field == "phone" and not 10 <= sum(char.isdigit() for char in value) <= 15:
                continue
            if field == "address":
                for part, content in parse_mailing_address(value).items():
                    offer(part, content, number, "HEADER_CONTACT")
            elif field == "url":
                if value.startswith("http://"):
                    continue
                for part, content in classify_public_urls([value]).items():
                    offer(part, content, number, "HEADER_CONTACT")
            else:
                offer(field, value, number, "HEADER_CONTACT")

    if not names and header:
        number, value = header[0]
        if (len(value) <= 80 and not _ROLE.search(value) and split_display_name(value)
                and any(number < contact <= number + 3 for contact in contact_lines)):
            names.append((value, number, "HEADER_NAME"))
    for value, number, basis in names:
        offer("candidate_display_name", value, number, basis)
    # A single display value may preserve familiar Latin name parts as editable
    # hints. Single-token, mixed-script/numeric and ambiguous names stay whole.
    unique_names = {value.casefold() for value, _, _ in names}
    if len(unique_names) == 1:
        value, number, basis = names[0]
        for part, content in split_display_name(value).items():
            offer(part, content, number, basis)

    hints, evidence, ambiguous = {}, {}, []
    for field, proposals in offers.items():
        if len({value.casefold() for value, _, _ in proposals}) != 1:
            ambiguous.append(field)
            continue
        value, number, basis = proposals[0]
        hints[field] = value
        evidence[field] = {"source_id": source_id, "source_sha256": descriptor["source_sha256"],
                           "text_sha256": descriptor["text_sha256"], "line_start": number, "line_end": number,
                           "basis": basis, "value_sha256": sha256_bytes(value.encode("utf-8"))}
    return hints, evidence, {"status": "UNCONFIRMED_HEADER_HINTS", "suggested_fields": sorted(hints), "ambiguous_fields": sorted(ambiguous)}


def public_origins(state: dict) -> dict:
    """Expose coordinates only when the current answer still matches its origin."""
    sources = {item["source_id"]: item for item in state.get("sources", [])}
    sources.update({item["source_id"]: item["metadata"] for item in state.get("pending_sources", [])})
    result = {}
    for field, origin in state.get("profile_hint_sources", {}).items():
        if not isinstance(origin, dict):
            continue
        answer = state.get("answers", {}).get(field, {})
        value = answer.get("value")
        if (field not in HINT_FIELDS or not isinstance(value, str) or answer.get("status") != "CONFIRMED"
                or sha256_bytes(value.encode("utf-8")) != origin.get("value_sha256")):
            continue
        source = sources.get(origin.get("source_id"), {})
        descriptor = source.get("analysis_text_review", {})
        status = "AVAILABLE" if (descriptor.get("status") == "AVAILABLE"
                 and descriptor.get("source_sha256") == origin.get("source_sha256")
                 and descriptor.get("text_sha256") == origin.get("text_sha256")) else "SOURCE_CHANGED"
        result[field] = {key: origin[key] for key in ("source_id", "source_sha256", "text_sha256", "line_start", "line_end", "basis")}
        result[field]["status"] = status
    return result
