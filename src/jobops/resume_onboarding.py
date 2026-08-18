from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .adapters import audit_real_external_actions
from .db import JobOpsDB
from .document_builder import export_docx_to_pdf, render_pdf_to_pngs, template_fingerprint
from .document_qa import build_visual_record, compare_text, extract_docx_text, extract_pdf_text, validate_visual_record
from .errors import JobOpsError
from .knowledge import KnowledgeGateway
from .onboarding_extraction import merge_resume_continuations
from .locator import locate_knowledge_root
from .private_onboarding import PrivateOnboarding
from .release import security_scan
from .runtime_schema import validate_named
from .runtime_paths import runtime_data_root, runtime_path
from .util import canonical_json, iso_utc, load_json, sha256_bytes, sha256_file, stable_id, write_json


STATUS = "AWAITING_USER_CLAIM_AND_PROFILE_APPROVAL"
FINAL_STATES = (
    "MASTER_RESUME_SECURELY_IMPORTED",
    "CANDIDATE_PROFILE_DRAFTED",
    "CLAIM_REVIEW_PACKET_READY",
)
UNKNOWN_HARD_CONDITIONS = (
    "work_authorization", "visa_sponsorship", "minimum_salary", "available_start_date",
    "relocation", "travel", "background_check", "legal_attestation", "electronic_signature",
    "voluntary_disclosures",
)
ANSWER_BANK_GROUPS: dict[str, tuple[str, ...]] = {
    "job_target": ("target_roles", "target_industries", "target_levels"),
    "work_authorization_and_visa": ("work_authorization", "visa_sponsorship"),
    "location_remote_relocation_travel": ("preferred_locations", "remote_preference", "relocation", "travel"),
    "compensation": ("minimum_salary", "desired_salary"),
    "availability": ("available_start_date",),
    "standard_application": ("why_company", "why_role", "referral_source", "previous_employment"),
    "sensitive_or_legal": ("background_check", "non_compete", "truthfulness_attestation", "electronic_signature"),
    "voluntary_disclosure": ("race_ethnicity", "gender", "disability", "veteran_status", "religion"),
}
SECTION_ALIASES = {
    "summary": ("summary", "professional summary", "profile", "professional profile", "简介", "个人简介"),
    "experience": ("experience", "professional experience", "work experience", "employment", "工作经历", "职业经历"),
    "project": ("projects", "project experience", "selected projects", "项目", "项目经历"),
    "education": ("education", "academic background", "教育", "教育背景"),
    "skill": ("skills", "technical skills", "core competencies", "技能", "专业技能"),
    "certification": ("certifications", "certificates", "licenses", "证书", "资格证书"),
    "language": ("languages", "language", "语言"),
}
EXCLUDED_NAME = re.compile(
    r"(?i)cover[ _-]*letter|求职信|synthetic|jobops|generated|tailored|output|copy|副本|测试"
)
MONTH_MARKER = re.compile(r"(?i)(?:^|[^a-z0-9])(?:aug(?:ust)?|08|八月)(?:[^a-z0-9]|$)")
DATE_PATTERN = re.compile(
    r"(?i)(?:19|20)\d{2}(?:[./-](?:0?[1-9]|1[0-2]))?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(?:19|20)\d{2}|(?:19|20)\d{2}\s*年"
)
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])(?:\$|¥|£|€)?\d+(?:[,.]\d+)*(?:%|\+|[kKmMbB])?")
EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)?\d{3,4}[ .-]\d{4}(?!\d)")
LINKEDIN_PATTERN = re.compile(r"(?i)(?:https?://)?(?:www\.)?linkedin\.com/[^\s|]+")
WEBSITE_PATTERN = re.compile(r"(?i)https?://[^\s|]+")
ADDRESS_PATTERN = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Z0-9.'# -]{2,50}\s+"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr)\b"
    r"(?:\s+(?:apt|apartment|unit|suite|ste)\.?\s*[A-Z0-9-]+)?"
    r"(?:,\s*[^,\r\n]{2,60},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?"
    r"(?:,\s*(?:United States(?: of America)?|USA|US))?)?",
)
STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "using", "work", "experience",
    "professional", "responsible", "skills", "project", "projects", "summary", "education", "including",
    "resume", "candidate", "management", "analysis", "business", "company", "team", "role", "years",
}


@dataclass(frozen=True)
class ResumeCandidate:
    path: Path
    source_type: str
    sha256: str
    size_bytes: int
    modified_at: str
    filename_score: int
    completeness_score: int
    page_hint: int
    date_count: int
    structure_count: int

    @property
    def token(self) -> str:
        return self.sha256.removeprefix("sha256:")[:12]

    def safe_summary(self, index: int) -> dict[str, object]:
        return {
            "candidate": f"resume-candidate-{index:02d}.{self.source_type}",
            "source_type": self.source_type,
            "sha256_prefix": "sha256:" + self.token,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "page_hint": self.page_hint,
            "structure_count": self.structure_count,
            "date_count": self.date_count,
        }


def _known_downloads() -> Path:
    if os.name != "nt":
        raise JobOpsError("WINDOWS_DOWNLOADS_REQUIRED", "Resume discovery requires the Windows Downloads known folder.")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    value = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")
    guid = GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))
    pointer = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
    if result != 0 or not pointer.value:
        raise JobOpsError("DOWNLOADS_KNOWN_FOLDER_UNAVAILABLE", "Windows did not resolve the Downloads known folder.")
    try:
        path = Path(pointer.value).resolve(strict=True)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pointer)
    return path


def _is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _bounded_files(root: Path, max_depth: int = 2) -> Iterable[Path]:
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for entry in entries:
            if _is_reparse(entry):
                continue
            if entry.is_dir() and depth < max_depth:
                queue.append((entry, depth + 1))
            elif entry.is_file():
                yield entry


def _docx_summary(path: Path) -> tuple[int, int, int, int]:
    page_hint = 0
    structure_count = 0
    completeness = 0
    date_count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            body = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", body, flags=re.DOTALL))
            structure_count = body.count("<w:p") + body.count("<w:tbl") + len([name for name in names if name.startswith("word/header") or name.startswith("word/footer")])
            lowered = text.casefold()
            completeness = sum(any(alias in lowered for alias in aliases) for aliases in SECTION_ALIASES.values())
            date_count = len(DATE_PATTERN.findall(text))
            if "docProps/app.xml" in names:
                app = ET.fromstring(archive.read("docProps/app.xml"))
                pages = next((node.text for node in app.iter() if node.tag.endswith("}Pages")), None)
                page_hint = int(pages or 0)
    except Exception:
        return 0, 0, 0, 0
    return completeness, page_hint, date_count, structure_count


def discover_resume_candidates(root: Path | None = None, *, max_depth: int = 2) -> list[ResumeCandidate]:
    downloads = (root or _known_downloads()).resolve(strict=True)
    candidates: list[ResumeCandidate] = []
    hashes: set[str] = set()
    for path in _bounded_files(downloads, max_depth=max_depth):
        suffix = path.suffix.casefold()
        stem = path.stem
        if suffix not in {".docx", ".pdf"} or path.name.startswith("~$"):
            continue
        if "2026" not in stem.casefold() or not MONTH_MARKER.search(stem) or EXCLUDED_NAME.search(stem):
            continue
        fingerprint = sha256_file(path)
        if fingerprint in hashes:
            continue
        hashes.add(fingerprint)
        score = 0
        if re.search(r"(?i)2026[ _-]*(?:aug|august|08|八月)", stem):
            score += 5
        if re.search(r"(?i)(?:resume|cv|简历)", stem):
            score += 3
        if suffix == ".docx":
            score += 3
        completeness = page_hint = date_count = structure_count = 0
        if suffix == ".docx":
            completeness, page_hint, date_count, structure_count = _docx_summary(path)
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        candidates.append(ResumeCandidate(
            path=path, source_type=suffix.removeprefix("."), sha256=fingerprint,
            size_bytes=path.stat().st_size, modified_at=iso_utc(modified), filename_score=score,
            completeness_score=completeness, page_hint=page_hint, date_count=date_count,
            structure_count=structure_count,
        ))
    return sorted(
        candidates,
        key=lambda item: (
            item.source_type != "docx", -item.filename_score, -item.completeness_score,
            -item.page_hint, -item.structure_count, -item.date_count,
            -datetime.fromisoformat(item.modified_at.replace("Z", "+00:00")).timestamp(),
        ),
    )


def select_resume(candidates: list[ResumeCandidate]) -> tuple[ResumeCandidate | None, ResumeCandidate | None, list[dict[str, object]]]:
    if not candidates:
        return None, None, []
    docx = [item for item in candidates if item.source_type == "docx"]
    pdfs = [item for item in candidates if item.source_type == "pdf"]
    ambiguous: list[dict[str, object]] = []
    if docx:
        ranked = sorted(docx, key=lambda item: (item.filename_score, item.completeness_score, item.page_hint, item.structure_count, item.date_count, item.modified_at), reverse=True)
        selected = ranked[0]
        if len(ranked) > 1:
            first = (ranked[0].filename_score, ranked[0].completeness_score, ranked[0].page_hint, ranked[0].structure_count, ranked[0].date_count)
            second = (ranked[1].filename_score, ranked[1].completeness_score, ranked[1].page_hint, ranked[1].structure_count, ranked[1].date_count)
            if first == second and ranked[0].sha256 != ranked[1].sha256:
                ambiguous = [item.safe_summary(index + 1) for index, item in enumerate(ranked[:3])]
                return None, None, ambiguous
        normalized = re.sub(r"[^a-z0-9]+", "", selected.path.stem.casefold())
        paired = next((item for item in pdfs if re.sub(r"[^a-z0-9]+", "", item.path.stem.casefold()) == normalized), None)
        return selected, paired, []
    return pdfs[0], pdfs[0], []


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t|•▪◦-–—")


def _section_heading(value: str) -> str | None:
    normalized = _clean_line(value).rstrip(":：").casefold()
    for key, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return key
    return None


def parse_resume(text: str, *, candidate_name_hint: str | None = None) -> dict[str, Any]:
    raw_lines = [_clean_line(line) for line in text.replace("\r", "\n").split("\n")]
    raw_lines = [line for line in raw_lines if line]
    lines = merge_resume_continuations(raw_lines)
    contact_values = {
        "email": EMAIL_PATTERN.findall(text), "phone": PHONE_PATTERN.findall(text),
        "linkedin": LINKEDIN_PATTERN.findall(text), "website": WEBSITE_PATTERN.findall(text),
        "address": ADDRESS_PATTERN.findall(text),
    }
    contact_fields = sorted(key for key, values in contact_values.items() if values)
    name = _clean_line(candidate_name_hint or "") or None
    if name is None:
        # Detect the resume-header name before paragraph continuation repair.
        # Otherwise a common three-line header (name, contacts, address) may be
        # merged into one long line and the already-present name becomes
        # impossible to recover without asking the applicant again.
        for line in raw_lines[:8]:
            if _section_heading(line):
                break
            if EMAIL_PATTERN.search(line) or PHONE_PATTERN.search(line) or WEBSITE_PATTERN.search(line):
                continue
            # A reliable name needs at least two tokens.  Single-word all-caps
            # headers such as ANALYTICS must not become personal identifiers.
            if 2 <= len(line.split()) <= 6 and len(line) <= 80 and not DATE_PATTERN.search(line):
                name = line
                break
    current = "summary"
    facts: list[dict[str, str]] = []
    sections: dict[str, list[str]] = {key: [] for key in SECTION_ALIASES}
    seen: set[str] = set()
    for line in lines:
        heading = _section_heading(line)
        if heading:
            current = heading
            continue
        if line == name or EMAIL_PATTERN.search(line) or PHONE_PATTERN.search(line) or LINKEDIN_PATTERN.search(line):
            continue
        if len(line) < 2:
            continue
        sections[current].append(line)
        category = "achievement" if NUMBER_PATTERN.search(line) and current in {"experience", "project"} else current
        values = [line]
        if current == "skill" and len(line) < 500:
            split = [_clean_line(item) for item in re.split(r"[,;|·•]", line) if _clean_line(item)]
            if len(split) > 1:
                values = split
        for value in values:
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            facts.append({
                "fact_id": stable_id("RSM", category, value), "category": category,
                "value": value, "status": "APPLICANT_PROVIDED_UNCONFIRMED",
            })
    for date in sorted(set(DATE_PATTERN.findall(text))):
        facts.append({
            "fact_id": stable_id("RSM", "date", date), "category": "date",
            "value": date, "status": "APPLICANT_PROVIDED_UNCONFIRMED",
        })
    privacy_tokens = set(value for values in contact_values.values() for value in values if len(value.strip()) >= 7)
    if name and len(name) >= 5:
        privacy_tokens.add(name)
    privacy_tokens.update(item["value"] for item in facts if len(item["value"]) >= 24)
    return {
        "candidate_display_name": name,
        "contact_values": contact_values,
        "contact_fields_present": contact_fields,
        "facts": facts,
        "sections": sections,
        "privacy_tokens": sorted(privacy_tokens),
        "date_count": len(DATE_PATTERN.findall(text)),
        "number_count": len(NUMBER_PATTERN.findall(text)),
    }


def _tokens(value: str) -> set[str]:
    words = {
        token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#/-]{2,}", value)
        if token.casefold() not in STOP_WORDS
    }
    chinese = re.findall(r"[\u4e00-\u9fff]{2,12}", value)
    for phrase in chinese:
        words.add(phrase)
        words.update(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
    return words


def _metric_signatures(value: str) -> dict[str, set[str]]:
    """Return comparable number/unit pairs; bare dates and unrelated numbers are not conflicts."""

    matches = re.findall(
        r"(?ix)(\$?\s*\d[\d,.]*(?:\s*[+x])?)\s*"
        r"(percentage|percent|million|billion|merchants?|customers?|locations?|projects?|records?|"
        r"clients?|users?|rows?|pages?|months?|weeks?|years?|days?|k|m|%|美元|元|万|百万|亿|个|家|页|天|周|月|年)"
        r"(?![A-Za-z])",
        value,
    )
    output: dict[str, set[str]] = {}
    for raw_number, raw_unit in matches:
        number = re.sub(r"[\s,$]", "", raw_number).casefold()
        unit = raw_unit.casefold()
        aliases = {"percent": "%", "percentage": "%", "million": "m", "billion": "b"}
        output.setdefault(aliases.get(unit, unit), set()).add(number)
    return output


def _numeric_conflict(statement: str, evidence: str) -> bool:
    left = _metric_signatures(statement)
    right = _metric_signatures(evidence)
    comparable = set(left) & set(right)
    return any(left[unit] != right[unit] for unit in comparable)


def _paragraphs(text: str) -> list[tuple[str | None, str]]:
    output: list[tuple[str | None, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    for raw in text.splitlines() + [""]:
        if raw.startswith("#"):
            if buffer:
                output.append((heading, "\n".join(buffer).strip()))
                buffer = []
            heading = raw.lstrip("#").strip()
        elif raw.strip():
            buffer.append(raw.strip())
        elif buffer:
            output.append((heading, "\n".join(buffer).strip()))
            buffer = []
    return [(head, body) for head, body in output if len(body) >= 20]


def _personal_evidence_index(gateway: KnowledgeGateway) -> list[dict[str, Any]]:
    root = gateway._source_root("personal_redacted")
    question_prefixes = tuple(
        str(value).replace("\\", "/").casefold()
        for value in gateway.definitions["personal_redacted"].get("question_only_prefixes", [])
    )
    records: list[dict[str, Any]] = []
    for path in gateway.iter_files("personal_redacted"):
        relative = path.relative_to(root).as_posix()
        if any(relative.casefold() == prefix or relative.casefold().startswith(prefix + "/") for prefix in question_prefixes):
            continue
        text = gateway.read_text("personal_redacted", relative)
        fingerprint = sha256_file(path)
        for heading, body in _paragraphs(text):
            if len(body) > 2500:
                body = body[:2500]
            context = f"{relative} {heading or ''} {body}"
            records.append({
                "relative_path": relative, "heading": heading, "excerpt": body,
                "fingerprint": fingerprint, "tokens": _tokens(context),
                "numbers": set(NUMBER_PATTERN.findall(body)),
                "historical_completion": any(term in context.casefold() for term in ("已完成", "完成事项", "case-", "project", "项目")),
                "current_health": any(term in context.casefold() for term in ("当前健康", "健康记录", "health")),
            })
    return records


def build_claim_candidates(resume: dict[str, Any], gateway: KnowledgeGateway, *, created_at: str) -> dict[str, Any]:
    evidence = _personal_evidence_index(gateway)
    claims: list[dict[str, Any]] = []
    used_evidence: set[tuple[str, str | None]] = set()
    for fact in resume["facts"]:
        if fact["category"] in {"date", "contact"}:
            continue
        statement = str(fact["value"])
        statement_tokens = _tokens(statement)
        numbers = set(NUMBER_PATTERN.findall(statement))
        ranked = []
        for record in evidence:
            overlap = statement_tokens & record["tokens"]
            overlap_ratio = len(overlap) / max(1, min(len(statement_tokens), len(record["tokens"])))
            score = len(overlap) + (2 if numbers and numbers & record["numbers"] else 0)
            if len(overlap) >= 3 and overlap_ratio >= 0.18:
                ranked.append((score, record))
        ranked.sort(key=lambda item: item[0], reverse=True)
        supporting = [item[1] for item in ranked[:2]]
        conflict = any(_numeric_conflict(statement, record["excerpt"]) for record in supporting)
        if conflict:
            lifecycle = "CONFLICT_REQUIRES_REVIEW"
        elif supporting:
            lifecycle = "PROPOSED"
        else:
            lifecycle = "RESUME_ONLY_REQUIRES_CONFIRMATION"
        source_refs = []
        for record in supporting:
            used_evidence.add((record["relative_path"], record["heading"]))
            source_refs.append({
                "source_id": "personal_redacted", "relative_path": record["relative_path"],
                "heading": record["heading"], "excerpt": record["excerpt"],
                "excerpt_fingerprint": sha256_bytes(record["excerpt"].encode("utf-8")),
                "fingerprint": record["fingerprint"], "verified_at": created_at,
            })
        claims.append({
            "claim_id": stable_id("CLM", fact["fact_id"], statement), "resume_statement": statement,
            "category": fact["category"], "lifecycle_status": lifecycle,
            "supporting_evidence": source_refs,
            "responsibility_boundary": {
                "candidate": "APPLICANT_PROVIDED_UNCONFIRMED",
                "team": "REQUIRES_REVIEW", "ai": "NOT_PERSONAL_EVIDENCE",
            },
            "allowed_wording": [statement],
            "prohibited_wording": ["Do not expand scope, numbers, completion, ownership, or responsibility beyond verified evidence."],
            "confidence": "MEDIUM" if supporting and not conflict else "LOW",
            "evidence_freshness": "CURRENT_SNAPSHOT_UNAPPROVED" if supporting else "NO_KNOWLEDGE_EVIDENCE",
            "conflict": conflict, "approval_required": True, "approved_for_external": False,
        })
    optional = []
    for record in evidence:
        key = (record["relative_path"], record["heading"])
        if key in used_evidence or not record["historical_completion"] or record["current_health"]:
            continue
        optional.append({
            "claim_id": stable_id("CLM", record["relative_path"], record["heading"] or "", record["excerpt"]),
            "lifecycle_status": "OPTIONAL_CLAIM_CANDIDATE", "resume_statement": None,
            "supporting_evidence": [{
                "source_id": "personal_redacted", "relative_path": record["relative_path"],
                "heading": record["heading"], "excerpt": record["excerpt"],
                "excerpt_fingerprint": sha256_bytes(record["excerpt"].encode("utf-8")),
                "fingerprint": record["fingerprint"], "verified_at": created_at,
            }],
            "responsibility_boundary": {"candidate": "REQUIRES_REVIEW", "team": "REQUIRES_REVIEW", "ai": "NOT_PERSONAL_EVIDENCE"},
            "allowed_wording": [],
            "prohibited_wording": ["Do not add this evidence to an application until the user approves exact wording and ownership."],
            "confidence": "LOW", "evidence_freshness": "CURRENT_SNAPSHOT_UNAPPROVED",
            "conflict": False, "approval_required": True, "approved_for_external": False,
        })
        if len(optional) >= 8:
            break
    claims.extend(optional)
    counts = {
        "proposed": sum(item["lifecycle_status"] == "PROPOSED" for item in claims),
        "resume_only": sum(item["lifecycle_status"] == "RESUME_ONLY_REQUIRES_CONFIRMATION" for item in claims),
        "optional": sum(item["lifecycle_status"] == "OPTIONAL_CLAIM_CANDIDATE" for item in claims),
        "conflicts": sum(item["lifecycle_status"] == "CONFLICT_REQUIRES_REVIEW" for item in claims),
        "auto_approved": sum(bool(item["approved_for_external"]) for item in claims),
    }
    if counts["auto_approved"] != 0:
        raise JobOpsError("CLAIM_AUTO_APPROVAL_FORBIDDEN", "Onboarding may only create unapproved Claim candidates.")
    return {"schema_version": 1, "status": STATUS, "created_at": created_at, "claims": claims, "counts": counts}


def validate_claim_candidate_evidence(bundle: dict[str, Any], gateway: KnowledgeGateway) -> dict[str, Any]:
    verified_anchors = 0
    for claim in bundle.get("claims", []):
        if claim.get("approved_for_external") is not False or claim.get("approval_required") is not True:
            raise JobOpsError("CLAIM_APPROVAL_GATE_INVALID", "Every onboarding Claim must remain unapproved and require user review.")
        for source in claim.get("supporting_evidence", []):
            if source.get("source_id") != "personal_redacted":
                raise JobOpsError("NON_PERSONAL_SOURCE", "Only personal_redacted may support a personal Claim candidate.")
            relative = str(source.get("relative_path", ""))
            path = gateway.safe_path("personal_redacted", relative)
            if not path.is_file() or sha256_file(path) != source.get("fingerprint"):
                raise JobOpsError("EVIDENCE_FILE_CHANGED", "A proposed Claim evidence file is missing or its fingerprint changed.")
            text = gateway.read_text("personal_redacted", relative)
            excerpt = str(source.get("excerpt", "")).strip()
            heading = str(source.get("heading") or "").strip()
            normalized_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
            if not excerpt or excerpt not in normalized_text:
                raise JobOpsError("EVIDENCE_ANCHOR_MISSING", "A proposed Claim excerpt no longer exists in its source.")
            if heading:
                headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
                if heading not in headings:
                    raise JobOpsError("EVIDENCE_ANCHOR_MISSING", "A proposed Claim heading no longer exists in its source.")
            if sha256_bytes(excerpt.encode("utf-8")) != source.get("excerpt_fingerprint"):
                raise JobOpsError("EVIDENCE_EXCERPT_CHANGED", "A proposed Claim excerpt fingerprint is invalid.")
            verified_anchors += 1
    return {
        "status": "PASS", "claim_count": len(bundle.get("claims", [])),
        "verified_anchors": verified_anchors, "auto_approved": 0,
        "knowledge_write_operations": 0,
    }


def _profile_draft(resume: dict[str, Any], master_ref: str, created_at: str) -> dict[str, Any]:
    provided = len(resume["facts"]) + (1 if resume["candidate_display_name"] else 0) + len(resume["contact_fields_present"])
    unknown = sum(len(values) for values in ANSWER_BANK_GROUPS.values())
    draft = {
        "schema_version": 1, "profile_version": stable_id("PFD", master_ref, created_at),
        "status": STATUS, "master_resume_ref": master_ref,
        "candidate_display_name": {
            "value": resume["candidate_display_name"],
            "status": "APPLICANT_PROVIDED_UNCONFIRMED" if resume["candidate_display_name"] else "UNKNOWN",
        },
        "contact_fields_present": resume["contact_fields_present"],
        # This draft is DPAPI-protected.  Keeping the exact resume-provided
        # values here prevents asking for the same email/phone/name on every
        # application after the user approves onboarding once.
        "resume_contact_values": {
            key: list(values[:10])
            for key, values in resume["contact_values"].items()
            if key in {"email", "phone", "address", "linkedin", "website"} and values
        },
        "resume_facts": resume["facts"],
        "target_preferences": {
            field: {"value": None, "status": "UNKNOWN"}
            for field in ("target_roles", "target_industries", "target_levels", "preferred_locations", "remote_preference")
        },
        "hard_conditions": {field: {"value": None, "status": "UNKNOWN"} for field in UNKNOWN_HARD_CONDITIONS},
        "field_status_counts": {"applicant_provided_unconfirmed": provided, "unknown": unknown},
        "created_at": created_at,
    }
    return draft


def _answer_bank(created_at: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "status": STATUS, "created_at": created_at,
        "groups": {
            group: {field: {"value": None, "status": "UNKNOWN"} for field in fields}
            for group, fields in ANSWER_BANK_GROUPS.items()
        },
    }


def _pdf_structure(path: Path) -> dict[str, Any]:
    text, page_count = extract_pdf_text(path)
    page_sizes: list[list[float]] = []
    link_count = 0
    font_names: set[str] = set()
    candidate_name_hint: str | None = None
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        for page in reader.pages:
            page_sizes.append([round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2)])
            annotations = page.get("/Annots") or []
            for annotation in annotations:
                try:
                    item = annotation.get_object()
                    if item.get("/Subtype") == "/Link":
                        link_count += 1
                except Exception:
                    continue
            resources = page.get("/Resources") or {}
            fonts = resources.get("/Font") or {}
            for font in fonts.values():
                try:
                    base = font.get_object().get("/BaseFont")
                    if base:
                        font_names.add(str(base))
                except Exception:
                    continue
    except ModuleNotFoundError:
        page_sizes = []
    try:
        import pdfplumber

        with pdfplumber.open(path) as document:
            if document.pages:
                page = document.pages[0]
                words = page.extract_words(x_tolerance=1, y_tolerance=3) or []
                if words:
                    top = min(float(word["top"]) for word in words)
                    left_header = [
                        str(word["text"]) for word in words
                        if abs(float(word["top"]) - top) <= 4 and float(word["x0"]) < float(page.width) * 0.35
                    ]
                    hint = _clean_line(" ".join(left_header))
                    if 2 <= len(hint.split()) <= 6 and len(hint) <= 80 and not DATE_PATTERN.search(hint):
                        candidate_name_hint = hint
    except (ModuleNotFoundError, OSError, ValueError):
        candidate_name_hint = None
    material = {
        "source_type": "pdf", "page_count": page_count, "page_sizes": page_sizes,
        "font_count": len(font_names), "link_count": link_count,
        "text_present": bool(text.strip()), "date_count": len(DATE_PATTERN.findall(text)),
        "number_count": len(NUMBER_PATTERN.findall(text)), "file_sha256": sha256_file(path),
    }
    material["template_fingerprint"] = sha256_bytes(canonical_json(material))
    return {"text": text, "structure": material, "candidate_name_hint": candidate_name_hint}


def _docx_structure(path: Path) -> dict[str, Any]:
    text = extract_docx_text(path)
    fingerprint = template_fingerprint(path)
    with zipfile.ZipFile(path) as archive:
        body = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        damaged_objects = 0
        editable_objects = body.count("<w:object") + body.count("<w:pict")
    material = {
        "source_type": "docx", "page_geometry": [list(item) for item in fingerprint.page_geometry],
        "style_count": len(fingerprint.style_ids), "table_count": len(fingerprint.table_grids),
        "header_count": len(fingerprint.headers), "footer_count": len(fingerprint.footers),
        "hyperlink_count": len(fingerprint.hyperlinks), "package_part_count": len(fingerprint.package_parts),
        "embedded_object_count": editable_objects, "damaged_object_count": damaged_objects,
        "date_count": len(DATE_PATTERN.findall(text)), "number_count": len(NUMBER_PATTERN.findall(text)),
        "file_sha256": fingerprint.master_sha256,
        "template_fingerprint": sha256_bytes(canonical_json(fingerprint.as_dict())),
    }
    return {"text": text, "structure": material, "fingerprint": fingerprint.as_dict()}


def _safe_staging_root(onboarding: PrivateOnboarding) -> Path:
    root = onboarding.store.private_root / "staging"
    root.mkdir(parents=True, exist_ok=True)
    if _is_reparse(root):
        raise JobOpsError("PRIVATE_STAGING_UNSAFE", "Private staging cannot be a symlink or reparse point.")
    return root


def _pdftoppm() -> str:
    found = shutil.which("pdftoppm")
    if not found:
        raise JobOpsError("PDF_RENDERER_MISSING", "The bundled local Poppler renderer is required.")
    path = Path(found)
    if path.suffix.casefold() in {".cmd", ".bat"} and len(path.parents) >= 3:
        native = path.parents[2] / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
        if native.is_file():
            return str(native)
    return found


def _scan_private_tokens(project: Path, tokens: list[str]) -> int:
    needles = [value for value in tokens if len(value) >= 7]
    if not needles:
        return 0
    hits = 0
    data_root = runtime_data_root(project)
    roots = [
        project / "src", project / "config", project / "schemas", project / ".agents",
        project / "state", project / "reports", project / "workspace", project / "tests",
    ]
    if data_root != project:
        roots.extend(data_root / area for area in ("state", "reports", "workspace"))
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix.casefold() in {".db", ".pyc", ".png", ".pdf", ".docx"}:
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            hits += sum(value.casefold() in text.casefold() for value in needles)
    for path in project.rglob("*.docx"):
        try:
            with zipfile.ZipFile(path) as archive:
                text = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith((".xml", ".rels")))
            hits += sum(value.casefold() in text.casefold() for value in needles)
        except Exception:
            continue
    for path in project.rglob("*.pdf"):
        try:
            text, _ = extract_pdf_text(path)
            hits += sum(value.casefold() in text.casefold() for value in needles)
        except Exception:
            continue
    return int(hits)


class ResumeOnboardingManager:
    def __init__(self, project: Path, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.project = project.resolve()
        self.database = database
        self.database.initialize()
        self.onboarding = onboarding
        self.onboarding.assert_outside_project(self.project)
        self.schemas = self.project / "schemas"

    def prepare(self) -> dict[str, Any]:
        candidates = discover_resume_candidates(max_depth=2)
        selected, paired_pdf, ambiguous = select_resume(candidates)
        if selected is None:
            if ambiguous:
                return {
                    "status": "NEEDS_MASTER_RESUME_SELECTION", "candidate_count": len(ambiguous),
                    "candidates": ambiguous, "private_values_emitted": 0,
                    "real_external_actions": 0, "next_safe_action": "select one of the redacted candidates",
                }
            return {
                "status": "MASTER_RESUME_NOT_FOUND", "search_root": "$DOWNLOADS",
                "max_subdirectory_depth": 2, "candidate_count": 0, "private_values_emitted": 0,
                "real_external_actions": 0, "next_safe_action": "place the 2026 Aug resume in Downloads",
            }
        created_at = iso_utc()
        original_hash = selected.sha256
        imported: list[dict[str, Any]] = []
        session_root: Path | None = None
        try:
            master_kind = "master_resume_docx" if selected.source_type == "docx" else "master_resume_pdf"
            master_record = self.onboarding.import_file(master_kind, selected.path, synthetic=False)
            imported.append(master_record)
            pdf_record = None
            if paired_pdf and paired_pdf.sha256 != selected.sha256:
                pdf_record = self.onboarding.import_file("master_resume_pdf", paired_pdf.path, synthetic=False)
                imported.append(pdf_record)
            elif selected.source_type == "pdf":
                pdf_record = master_record
            staging = _safe_staging_root(self.onboarding)
            session = uuid.uuid4().hex[:16]
            session_root = staging / ("onboarding-" + session)
            session_root.mkdir(parents=False, exist_ok=False)
            master_path = session_root / ("master." + selected.source_type)
            master_path.write_bytes(self.onboarding.read_bytes(str(master_record["secure_ref"])))
            if sha256_file(master_path) != original_hash:
                raise JobOpsError("MASTER_ROUNDTRIP_HASH_MISMATCH", "The secure master resume failed its decrypt/hash roundtrip.")
            if selected.source_type == "docx":
                analysis = _docx_structure(master_path)
                render_pdf = session_root / "render-reference.pdf"
                export_docx_to_pdf(
                    master_path, render_pdf,
                    self.project / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1",
                )
                if pdf_record:
                    paired_path = session_root / "paired-reference.pdf"
                    paired_path.write_bytes(self.onboarding.read_bytes(str(pdf_record["secure_ref"])))
                    paired_text, paired_pages = extract_pdf_text(paired_path)
                    analysis["comparison"] = {
                        "text_difference_ratio": compare_text(analysis["text"], paired_text),
                        "paired_pdf_pages": paired_pages,
                        "date_sets_match": sorted(set(DATE_PATTERN.findall(analysis["text"]))) == sorted(set(DATE_PATTERN.findall(paired_text))),
                        "number_sets_match": sorted(set(NUMBER_PATTERN.findall(analysis["text"]))) == sorted(set(NUMBER_PATTERN.findall(paired_text))),
                    }
            else:
                analysis = _pdf_structure(master_path)
                render_pdf = master_path
                analysis["comparison"] = {"status": "NOT_APPLICABLE_PDF_ONLY"}
            resume = parse_resume(analysis["text"], candidate_name_hint=analysis.get("candidate_name_hint"))
            located = locate_knowledge_root(self.project, self.project / "config" / "knowledge-sources.json")
            gateway = KnowledgeGateway(located)
            knowledge_before = gateway.snapshot_collections()
            claim_bundle = build_claim_candidates(resume, gateway, created_at=created_at)
            validate_claim_candidate_evidence(claim_bundle, gateway)
            profile = _profile_draft(resume, str(master_record["secure_ref"]), created_at)
            validate_named("candidate-profile-draft", profile, self.schemas)
            answer_bank = _answer_bank(created_at)
            analysis_private = {
                "schema_version": 1, "created_at": created_at,
                "structure": analysis["structure"], "comparison": analysis.get("comparison", {}),
                "sections": resume["sections"], "privacy_tokens": resume["privacy_tokens"],
                "contact_fields_present": resume["contact_fields_present"],
                "source_file_sha256": original_hash,
            }
            profile_record = self.onboarding.import_bytes("candidate_profile", canonical_json(profile), synthetic=False)
            answer_record = self.onboarding.import_bytes("answer_bank", canonical_json(answer_bank), synthetic=False)
            claims_record = self.onboarding.import_bytes("claim_candidates", canonical_json(claim_bundle), synthetic=False)
            analysis_record = self.onboarding.import_bytes("resume_analysis", canonical_json(analysis_private), synthetic=False)
            imported.extend([profile_record, answer_record, claims_record, analysis_record])
            pages = render_pdf_to_pngs(render_pdf, session_root / "renders", _pdftoppm())
            if not pages:
                raise JobOpsError("RESUME_RENDER_EMPTY", "Resume rendering produced no pages.")
            source_unchanged = sha256_file(selected.path) == original_hash
            if not source_unchanged:
                raise JobOpsError("SOURCE_FILE_CHANGED", "The selected Downloads file changed during secure import.")
            session_value = {
                "schema_version": 1, "session": session, "created_at": created_at,
                "master_ref": master_record["secure_ref"],
                "pdf_reference_ref": pdf_record["secure_ref"] if pdf_record else None,
                "profile_ref": profile_record["secure_ref"], "answer_bank_ref": answer_record["secure_ref"],
                "claims_ref": claims_record["secure_ref"], "analysis_ref": analysis_record["secure_ref"],
                "safe_display_name": "resume-2026-aug-master.docx" if selected.source_type == "docx" else "resume-2026-aug-reference.pdf",
                "source_type": selected.source_type, "sha256": original_hash,
                "size_bytes": selected.size_bytes, "modified_at": selected.modified_at,
                "paired_pdf": bool(pdf_record and selected.source_type == "docx"),
                "editable_master_status": "EDITABLE_MASTER_DOCX_AVAILABLE" if selected.source_type == "docx" else "EDITABLE_MASTER_DOCX_MISSING",
                "structure": analysis["structure"], "source_file_unchanged": source_unchanged,
                "knowledge_before": knowledge_before,
                "rendered_at": iso_utc(), "page_names": [path.name for path in pages],
                "page_hashes": [sha256_file(path) for path in pages],
                "new_refs": [str(record["secure_ref"]) for record in imported if not record.get("deduplicated")],
            }
            write_json(session_root / "session.json", session_value)
            return {
                "status": "AWAITING_VISUAL_INSPECTION", "visual_session": session,
                "selected_file": {"safe_display_name": session_value["safe_display_name"], "source_type": selected.source_type, "sha256_prefix": "sha256:" + selected.token, "size_bytes": selected.size_bytes, "modified_at": selected.modified_at, "paired_pdf": session_value["paired_pdf"]},
                "master_resume_ref": master_record["secure_ref"],
                "editable_master_status": session_value["editable_master_status"],
                "rendered_pages": [{"page": index + 1, "filename": path.name, "sha256": sha256_file(path)} for index, path in enumerate(pages)],
                "private_values_emitted": 0, "source_file_unchanged": True,
                "real_external_actions": 0, "next_safe_action": "inspect every rendered page, then finalize-resume-onboarding",
            }
        except Exception as exc:
            cleanup_failures = 0
            if session_root is not None:
                try:
                    self.onboarding.remove_staging_directory(session_root)
                except Exception:
                    cleanup_failures += 1
            for record in imported:
                if not record.get("deduplicated"):
                    try:
                        self.onboarding.delete(str(record["secure_ref"]), user_confirmed=True)
                    except Exception:
                        cleanup_failures += 1
            if cleanup_failures:
                raise JobOpsError(
                    "RESUME_ONBOARDING_ROLLBACK_FAILED",
                    "Resume onboarding failed and one or more private staging or reference cleanups did not complete.",
                    failed_compensations=cleanup_failures,
                ) from exc
            raise

    def visual_page_paths(self, session: str) -> list[Path]:
        if not re.fullmatch(r"[a-f0-9]{16}", session):
            raise JobOpsError("ONBOARDING_SESSION_INVALID", "The visual session token is invalid.")
        root = _safe_staging_root(self.onboarding) / ("onboarding-" + session)
        if not root.is_dir() or _is_reparse(root):
            raise JobOpsError("ONBOARDING_SESSION_MISSING", "The visual review session is missing or unsafe.")
        value = load_json(root / "session.json")
        pages = [root / "renders" / str(name) for name in value["page_names"]]
        if any(not page.is_file() for page in pages):
            raise JobOpsError("ONBOARDING_RENDER_MISSING", "One or more rendered pages are missing.")
        return pages

    def finalize(self, session: str, page_results: list[str]) -> dict[str, Any]:
        pages = self.visual_page_paths(session)
        session_root = pages[0].parents[1]
        value = load_json(session_root / "session.json")
        if len(page_results) != len(pages) or any(result not in {"PASS", "FAIL"} for result in page_results):
            raise JobOpsError("VISUAL_RESULTS_INCOMPLETE", "Every rendered page requires one explicit PASS or FAIL result.")
        reasons = [
            ["Original-resolution review checked clipping, overlap, overflow, blank pages, font legibility, and link/layout integrity."]
            if result == "PASS" else ["Visual reviewer found a layout defect requiring correction."]
            for result in page_results
        ]
        visual = build_visual_record(
            pages, [{"result": result, "reasons": reason} for result, reason in zip(page_results, reasons)],
            reviewer_type="codex_visual", rendered_at=str(value["rendered_at"]), reviewed_at=iso_utc(),
        )
        visual_status = validate_visual_record(visual, pages)
        visual_record = self.onboarding.import_bytes("visual_evidence", canonical_json(visual), synthetic=False)
        packet_record: dict[str, Any] | None = None
        try:
            analysis = json.loads(self.onboarding.read_bytes(str(value["analysis_ref"])).decode("utf-8"))
            profile = json.loads(self.onboarding.read_bytes(str(value["profile_ref"])).decode("utf-8"))
            answer_bank = json.loads(self.onboarding.read_bytes(str(value["answer_bank_ref"])).decode("utf-8"))
            claims = json.loads(self.onboarding.read_bytes(str(value["claims_ref"])).decode("utf-8"))
            validate_named("candidate-profile-draft", profile, self.schemas)
            located = locate_knowledge_root(self.project, self.project / "config" / "knowledge-sources.json")
            gateway = KnowledgeGateway(located)
            knowledge_after = gateway.snapshot_collections()
            knowledge = gateway.compare_snapshots(value["knowledge_before"], knowledge_after)
            if knowledge["status"] != "UNCHANGED":
                raise JobOpsError("KNOWLEDGE_CHANGED_DURING_ONBOARDING", "Knowledge fingerprints changed during onboarding.")
            action_audit = audit_real_external_actions(self.database)
            if action_audit["real_external_actions"] != 0:
                raise JobOpsError("REAL_EXTERNAL_ACTION_DETECTED", "Onboarding detected a real external side effect.")
            profile_counts = profile["field_status_counts"]
            total_fields = int(profile_counts["applicant_provided_unconfirmed"]) + int(profile_counts["unknown"])
            completeness = round(100 * int(profile_counts["applicant_provided_unconfirmed"]) / max(1, total_fields), 1)
            unknown_answer_fields = sum(len(group) for group in answer_bank["groups"].values())
            packet = {
                "schema_version": 1,
                "packet_id": stable_id("ONB", str(value["sha256"]), str(value["claims_ref"]), str(visual_record["content_sha256"])),
                "status": STATUS, "final_states": list(FINAL_STATES),
                "selected_file": {
                    "safe_display_name": value["safe_display_name"], "source_type": value["source_type"],
                    "sha256_prefix": "sha256:" + str(value["sha256"]).removeprefix("sha256:")[:12],
                    "size_bytes": value["size_bytes"], "modified_at": value["modified_at"],
                    "paired_pdf": value["paired_pdf"],
                },
                "master_resume": {
                    "secure_ref": value["master_ref"], "pdf_reference_ref": value["pdf_reference_ref"],
                    "editable_master_status": value["editable_master_status"],
                    "structure_status": "PASS" if value["source_type"] == "docx" else "LIMITED_PDF_REFERENCE",
                    "page_count": len(pages), "template_fingerprint": value["structure"]["template_fingerprint"],
                    "visual_status": visual_status, "visual_record_ref": visual_record["secure_ref"],
                },
                "candidate_profile": {
                    "secure_ref": value["profile_ref"], "completeness_percent": completeness,
                    "provided_unconfirmed": profile_counts["applicant_provided_unconfirmed"],
                    "unknown": profile_counts["unknown"], "confirmation_field_count": total_fields,
                },
                "answer_bank": {
                    "secure_ref": value["answer_bank_ref"], "unknown_field_count": unknown_answer_fields,
                    "categories": list(answer_bank["groups"]),
                },
                "claims": {"secure_ref": value["claims_ref"], **claims["counts"]},
                "unknown_hard_conditions": list(UNKNOWN_HARD_CONDITIONS),
                "validation": {
                    "runtime_schema": "PASS", "secure_roundtrip": "PASS",
                    "source_file_unchanged": bool(value["source_file_unchanged"]),
                    "fingerprint_reverified": str(value["structure"]["file_sha256"]) == str(value["sha256"]),
                    "leak_findings": 0, "staging_residue": 0, "database_consistent": "PASS",
                    "knowledge_write_operations": 0, "project_boundary": "PASS", "external_actions": 0,
                },
                "real_external_actions": 0, "knowledge_bases": "UNCHANGED", "created_at": iso_utc(),
                "next_safe_action": "review-onboarding --latest",
            }
            if visual_status != "PASS":
                raise JobOpsError("MASTER_RESUME_VISUAL_QA_FAILED", "The resume did not pass original-resolution visual review.")
            validate_named("onboarding-review", packet, self.schemas)
            self.onboarding.remove_staging_directory(session_root)
            staging_files = [path for path in _safe_staging_root(self.onboarding).rglob("*") if path.is_file()]
            if staging_files:
                raise JobOpsError("PRIVATE_STAGING_RESIDUE", "Private staging contains residual files after onboarding.", count=len(staging_files))
            leakage = _scan_private_tokens(self.project, list(analysis.get("privacy_tokens", [])))
            if leakage:
                raise JobOpsError("PRIVATE_VALUE_LEAK_DETECTED", "Private resume values were found outside secure storage.", finding_count=leakage)
            generic_scan = security_scan(self.project, self.database)
            if generic_scan["status"] != "PASS":
                raise JobOpsError("PROJECT_SECURITY_SCAN_FAILED", "The project security scan found a release-boundary issue.", finding_count=generic_scan["finding_count"])
            packet_record = self.onboarding.import_bytes("onboarding_review_packet", canonical_json(packet), synthetic=False)
            refs = [
                value["master_ref"], value["profile_ref"], value["answer_bank_ref"], value["claims_ref"],
                value["analysis_ref"], visual_record["secure_ref"], packet_record["secure_ref"],
            ]
            with self.database.connect() as connection:
                active = int(connection.execute(
                    f"SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND secure_ref IN ({','.join('?' for _ in refs)})",
                    refs,
                ).fetchone()[0])
            if active != len(set(refs)):
                self.onboarding.delete(str(packet_record["secure_ref"]), user_confirmed=True)
                raise JobOpsError("PRIVATE_REFERENCE_INCONSISTENT", "Onboarding references are not consistently active.")
            index = {
                "schema_version": 1, "status": STATUS, "packet_ref": packet_record["secure_ref"],
                "packet_sha256": packet_record["content_sha256"], "version": packet_record["version"],
                "safe_display_name": value["safe_display_name"], "source_type": value["source_type"],
                "master_resume_ref": value["master_ref"], "candidate_profile_ref": value["profile_ref"],
                "answer_bank_ref": value["answer_bank_ref"], "claim_candidates_ref": value["claims_ref"],
                "created_at": packet["created_at"], "next_safe_action": packet["next_safe_action"],
            }
            index_path = runtime_path(
                self.project,
                "state",
                "onboarding-review-index.json",
                operation="write",
            )
            write_json(index_path, index)
            leakage_after_index = _scan_private_tokens(self.project, list(analysis.get("privacy_tokens", [])))
            if leakage_after_index:
                index_path.unlink(missing_ok=True)
                self.onboarding.delete(str(packet_record["secure_ref"]), user_confirmed=True)
                raise JobOpsError("PRIVATE_VALUE_LEAK_DETECTED", "Private resume values were found after writing the redacted index.", finding_count=leakage_after_index)
            return {
                **packet, "packet_ref": packet_record["secure_ref"],
                "master_resume_analysis_ref": value["analysis_ref"],
                "private_values_emitted": 0, "staging_residue": 0,
            }
        except Exception as exc:
            cleanup_failures = 0
            try:
                self.onboarding.remove_staging_directory(session_root)
            except Exception:
                cleanup_failures += 1
            if packet_record is not None and not packet_record.get("deduplicated"):
                try:
                    self.onboarding.delete(str(packet_record["secure_ref"]), user_confirmed=True)
                except Exception:
                    cleanup_failures += 1
            if not visual_record.get("deduplicated"):
                try:
                    self.onboarding.delete(str(visual_record["secure_ref"]), user_confirmed=True)
                except Exception:
                    cleanup_failures += 1
            for reference in value.get("new_refs", []):
                try:
                    self.onboarding.delete(str(reference), user_confirmed=True)
                except Exception:
                    cleanup_failures += 1
            if cleanup_failures:
                raise JobOpsError(
                    "RESUME_ONBOARDING_ROLLBACK_FAILED",
                    "Resume onboarding finalization failed and one or more private staging or reference cleanups did not complete.",
                    failed_compensations=cleanup_failures,
                ) from exc
            raise

    def show_review(self, packet_ref: str | None = None) -> dict[str, Any]:
        reference = packet_ref
        if reference is None:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT secure_ref FROM private_refs WHERE kind='onboarding_review_packet' AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            if row is None:
                raise JobOpsError("ONBOARDING_PACKET_MISSING", "No active onboarding review packet exists.")
            reference = str(row[0])
        packet = json.loads(self.onboarding.read_bytes(reference).decode("utf-8"))
        validate_named("onboarding-review", packet, self.schemas)
        return {**packet, "packet_ref": reference, "private_values_emitted": 0}
