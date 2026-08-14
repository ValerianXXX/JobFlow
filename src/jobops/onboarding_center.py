from __future__ import annotations

import io
import heapq
import json
import re
import threading
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from . import UI_PROTOCOL_VERSION, __version__
from .adapters import audit_real_external_actions
from .approvals import ApprovalContext, issue_approval
from .ats_capabilities import offline_ats_capabilities
from .browser_assist import BrowserAssistManager
from .application_readiness import build_application_readiness
from .application_execution import validate_application_execution_plan_integrity
from .application_field_resolution import (
    ApplicationFieldResolutionManager,
    field_resolution_summary,
)
from .ai_runtime import (
    ALLOWED_CATEGORIES,
    AI_QUALITY_CONTRACT,
    ENTITY_CATEGORIES,
    MAX_AI_INPUT_CHARS,
    AIAnalysisEngine,
    configured_ai_engine,
)
from .ai_connections import AIConnectionManager
from .db import JobOpsDB
from .continuous_intake import (
    ContinuousIntakeDescriptorStore,
    build_deferred_evidence_bundle,
    continue_recorded_intake,
)
from .document_builder import discover_template_slots, inspect_docx_text_blocks, template_fingerprint
from .document_qa import extract_pdf_text
from .errors import JobOpsError
from .external_claims import (
    ALLOWED_EXTERNAL_USES,
    build_external_claim_set,
    claim_review_hash,
    validate_external_claim_set_integrity,
)
from .external_actions import ExternalActionGateway, ExternalActionPolicy
from .external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from .execution_controller import IsolatedApplicationExecutionController
from .official_discovery import MAX_SNAPSHOT_BYTES, discover_official_jobs
from .orchestrator import JobOpsOrchestrator, MAX_JD_SOURCE_BYTES, _read_jd
from .onboarding_catalog import FIELD_BY_ID, FIELD_IDS, REQUIRED_FIELD_IDS, STATUS_OPTIONS, USE_POLICIES, empty_answers, public_catalog
from .private_onboarding import PrivateOnboarding
from .queue_manager import QueueManager
from .resume_tailoring import (
    build_resume_tailoring_manifest,
    build_tailoring_proposal,
    validate_resume_tailoring_manifest_integrity,
)
from .runtime_schema import validate_named
from .security import assert_no_plaintext_secret
from .source_quality import document_quality_rank, document_text_preflight, safe_ai_failure_category
from .sourcing import _canonical_url, _host, _provider_and_tenant, host_matches_registered, registrable_domain
from .util import canonical_json, iso_utc, load_json, sha256_bytes, sha256_file, stable_id, write_json


IN_PROGRESS = "IN_PROGRESS"
COMPLETE = "ONBOARDING_COMPLETE"
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_RETAINED_SOURCE_BYTES = 64 * 1024 * 1024
LARGE_EXPORT_THRESHOLD_BYTES = 200 * 1024 * 1024
MAX_LARGE_EXPORT_BYTES = 8 * 1024 * 1024 * 1024
MAX_DERIVED_TEXT_CHARS = 12_000_000
MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024
MAX_LARGE_ZIP_MEMBERS = 100_000
MAX_LARGE_CONVERSATIONS_BYTES = 8 * 1024 * 1024 * 1024
MAX_CHATGPT_MEMBER_COMPRESSION_RATIO = 1_000
MAX_CHATGPT_CONVERSATION_CHARS = 64 * 1024 * 1024
MAX_CHATGPT_FRAGMENT_CANDIDATES = 600
MAX_CHATGPT_AI_SELECTION_CHARS = MAX_AI_INPUT_CHARS
MAX_DOCX_MEMBERS = 10_000
MAX_DOCX_XML_BYTES = 64 * 1024 * 1024
MAX_DOCX_XML_COMPRESSION_RATIO = 200
MAX_JSON_NODES = 250_000
MAX_JSON_DEPTH = 100
MAX_ONBOARDING_PDF_PAGES = 500
MAX_REVIEW_PACKET_BYTES = 2 * 1024 * 1024
MAX_OFFLINE_APPLICATION_BUNDLE_BYTES = MAX_JD_SOURCE_BYTES + MAX_SNAPSHOT_BYTES + 16 * 1024 * 1024 + 64 * 1024
ALLOWED_SOURCE_TYPES = {"resume", "project_case", "supporting_material", "portfolio", "ai_summary", "chatgpt_export"}
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt", ".md", ".json", ".zip"}
AI_SOURCE_TYPES = {"ai_summary", "chatgpt_export"}
STRICT_AI_ANALYSIS_MODE = "AI_CORE_ENTITY_ANALYSIS"
HARD_CONTINUITY_FIELDS = {
    "work_authorization", "visa_sponsorship", "preferred_locations", "remote_preference",
    "relocation", "travel", "minimum_salary", "available_start_date",
}
EXPLICIT_HARD_FIELDS = {
    "work_authorization", "visa_sponsorship", "preferred_locations", "remote_preference",
    "minimum_salary", "available_start_date",
}
AMBIGUOUS_HARD_VALUES = {"UNKNOWN", "UNSURE", ""}
ALWAYS_CONFIRM_FIELDS = {"background_check", "non_compete", "truthfulness_attestation", "electronic_signature"}
VOLUNTARY_FIELDS = {"race_ethnicity", "gender", "disability", "veteran_status", "religion"}


def _ai_analysis_is_complete(summary: Any) -> bool:
    if not isinstance(summary, dict):
        return False
    try:
        chunks = int(summary.get("ai_chunks", 0))
        input_characters = int(summary.get("ai_input_characters", -1))
        covered_characters = int(summary.get("ai_covered_characters", -2))
    except (TypeError, ValueError):
        return False
    return (
        summary.get("analysis_mode") == STRICT_AI_ANALYSIS_MODE
        and summary.get("quality_contract") == AI_QUALITY_CONTRACT
        and summary.get("ai_input_truncated") is False
        and chunks >= 1
        and input_characters > 0
        and covered_characters == input_characters
    )


def _public_ai_analysis(summary: Any) -> dict[str, Any]:
    value = summary if isinstance(summary, dict) else {}
    fields = {
        key: value[key]
        for key in (
            "ai_chunks", "ai_chunking_applied", "ai_input_characters",
            "ai_covered_characters", "ai_input_truncated", "ai_repair_count",
            "quality_contract", "quality_gate_version",
            "archive_scan_complete", "user_fragments_scanned", "readable_user_fragments",
            "safe_fragments_considered", "ai_selected_fragments", "ai_omitted_fragments",
            "ai_selection_bounded", "ai_selection_mode", "ai_selection_character_limit",
            "ai_selected_characters",
            "document_quality",
        )
        if key in value
    }
    fields["analysis_complete"] = _ai_analysis_is_complete(value)
    return fields


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "target_roles": ("target role", "target roles", "target function", "目标岗位", "目标职位", "求职方向"),
    "target_industries": ("target industry", "target industries", "目标行业"),
    "target_levels": ("target level", "seniority", "目标级别", "职级"),
    "work_authorization": ("work authorization", "authorized to work", "工作授权", "工作许可"),
    "visa_sponsorship": ("visa sponsorship", "sponsorship", "签证担保", "签证赞助"),
    "preferred_locations": ("preferred location", "preferred locations", "location preference", "期望地点", "工作地点"),
    "remote_preference": ("remote preference", "work arrangement", "远程偏好", "办公方式"),
    "relocation": ("relocation", "relocate", "搬迁"),
    "travel": ("travel", "business travel", "出差"),
    "minimum_salary": ("minimum salary", "salary floor", "最低薪资", "最低工资"),
    "desired_salary": ("desired salary", "salary expectation", "期望薪资"),
    "available_start_date": ("available start date", "start date", "notice period", "入职时间", "通知期"),
    "why_company": ("why company", "company motivation", "公司动机", "为什么公司"),
    "why_role": ("why role", "role motivation", "岗位动机", "为什么岗位"),
    "referral_source": ("referral source", "application source", "申请来源", "推荐来源"),
    "previous_employment": ("previous employment", "worked here before", "曾任职", "过去任职"),
    "background_check": ("background check", "背景调查"),
    "non_compete": ("non-compete", "non compete", "竞业限制", "竞业"),
    "truthfulness_attestation": ("truthfulness attestation", "accuracy declaration", "真实性声明"),
    "electronic_signature": ("electronic signature", "e-signature", "电子签名"),
    "race_ethnicity": ("race", "ethnicity", "种族", "族裔"),
    "gender": ("gender", "sex", "性别"),
    "disability": ("disability", "残障", "残疾"),
    "veteran_status": ("veteran status", "veteran", "退伍军人"),
    "religion": ("religion", "宗教"),
}


def _clean_text(value: str, *, limit: int = 20_000) -> str:
    value = value.replace("\x00", " ")
    value = re.sub(r"[\t ]+", " ", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()[:limit]


def _comparison_tokens(value: str) -> set[str]:
    tokens = {
        token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", value)
        if token.casefold() not in {"the", "and", "for", "with", "from", "present", "project", "resume"}
    }
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,10}", value):
        tokens.add(phrase)
        tokens.update(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
    return tokens


def _metric_values(value: str) -> list[str]:
    values = re.findall(
        r"(?ix)(?<![A-Za-z0-9])\$?\d[\d,.]*(?:\s*(?:percentage|percent|million|billion|merchants?|"
        r"customers?|locations?|projects?|records?|clients?|users?|rows?|pages?|months?|weeks?|years?|days?|"
        r"k|m|%|\+|x|美元|元|万|百万|亿|个|家|页|天|周|月|年)(?![A-Za-z]))?",
        value,
    )
    output: list[str] = []
    for item in values:
        cleaned = " ".join(item.split())
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output[:10]


def _metric_signatures(value: str) -> dict[str, set[str]]:
    matches = re.findall(
        r"(?ix)(\$?\s*\d[\d,.]*(?:\s*[+x])?)\s*"
        r"(percentage|percent|million|billion|merchants?|customers?|locations?|projects?|records?|"
        r"clients?|users?|rows?|pages?|months?|weeks?|years?|days?|k|m|%|美元|元|万|百万|亿|个|家|页|天|周|月|年)"
        r"(?![A-Za-z])",
        value,
    )
    aliases = {
        "percent": "%", "percentage": "%", "million": "m", "billion": "b",
        "merchants": "merchant", "customers": "customer", "locations": "location",
        "projects": "project", "records": "record", "clients": "client", "users": "user",
        "rows": "row", "pages": "page", "months": "month", "weeks": "week",
        "years": "year", "days": "day",
    }
    output: dict[str, set[str]] = {}
    for raw_number, raw_unit in matches:
        number = re.sub(r"[\s,$]", "", raw_number).casefold()
        unit = aliases.get(raw_unit.casefold(), raw_unit.casefold())
        output.setdefault(unit, set()).add(number)
    return output


def _metric_differences(statement: str, evidence: str) -> list[dict[str, Any]]:
    left, right = _metric_signatures(statement), _metric_signatures(evidence)
    output: list[dict[str, Any]] = []
    for unit in sorted(set(left) & set(right)):
        if left[unit] == right[unit]:
            continue
        output.append({
            "dimension": unit,
            "resume_value": ", ".join(sorted(left[unit])),
            "evidence_value": ", ".join(sorted(right[unit])),
        })
    return output


def _evidence_preview(statement: str, excerpt: str) -> dict[str, Any]:
    statement_tokens = _comparison_tokens(statement)
    statement_metrics = _metric_values(statement)
    ranked: list[tuple[int, str]] = []
    for raw in excerpt.splitlines():
        if re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|){2,}", raw):
            continue
        line = re.sub(r"https?://\S+", "", raw)
        line = re.sub(r"[`#*_]+", " ", line)
        line = " · ".join(part.strip() for part in line.split("|") if part.strip())
        line = " ".join(line.split())
        if len(line) < 12:
            continue
        common = statement_tokens & _comparison_tokens(line)
        differences = _metric_differences(statement, line)
        score = len(common) * 3 + len(differences) * 6
        if len(common) >= 2 and differences:
            ranked.append((score, line[:360]))
    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    summary_parts: list[str] = []
    for _, line in ranked:
        if line not in summary_parts:
            summary_parts.append(line)
        if len(summary_parts) == 2:
            break
    summary = " … ".join(summary_parts)[:500]
    differences = _metric_differences(statement, summary) if summary else []
    return {
        "summary": summary,
        "relevant": bool(summary_parts and ranked[0][0] >= 12 and differences),
        "resume_metrics": statement_metrics,
        "evidence_metrics": _metric_values(summary),
        "differences": differences,
    }


def _is_ai_qualified_claim(item: dict[str, Any]) -> bool:
    return (
        item.get("ai_validated") is True
        and item.get("analysis_mode") == STRICT_AI_ANALYSIS_MODE
        and str(item.get("category", "")) in ALLOWED_CATEGORIES
    )


def _claim_signature(statement: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", statement.casefold())


def _complete_claim_text(statement: str) -> bool:
    return 20 <= len(statement) <= 2_000 and statement[-1:] in ".?!。！？" and "|" not in statement


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；|\n]", value) if item.strip()]


def _string_values(value: Any) -> Iterable[str]:
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise JobOpsError(
                "ONBOARDING_JSON_COMPLEXITY_LIMIT",
                "The JSON source exceeds the bounded node or nesting-depth limit.",
                maximum_nodes=MAX_JSON_NODES,
                maximum_depth=MAX_JSON_DEPTH,
            )
        if isinstance(current, str):
            yield current
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in reversed(current))
        elif isinstance(current, dict):
            stack.extend((item, depth + 1) for item in reversed(tuple(current.values())))


def _chatgpt_user_fragments(value: Any) -> Iterable[str]:
    """Yield applicant-authored content, not titles, metadata, or assistant output."""

    conversations = value if isinstance(value, list) else [value]
    found_official = False
    for conversation in conversations:
        if not isinstance(conversation, dict) or not isinstance(conversation.get("mapping"), dict):
            continue
        for node in conversation["mapping"].values():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            if not isinstance(author, dict) or author.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, dict):
                continue
            found_official = True
            for fragment in _string_values(content.get("parts", [])):
                yield fragment
    if found_official:
        return
    # Compatibility for curated/simplified exports: only explicit content fields
    # are considered.  Titles and arbitrary metadata are intentionally ignored.
    for conversation in conversations:
        if isinstance(conversation, dict) and "content" in conversation:
            yield from _string_values(conversation["content"])


def _iter_json_array(stream: Any) -> Iterable[Any]:
    """Incrementally decode a top-level JSON array without loading the export."""

    decoder = json.JSONDecoder()
    reader = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="strict", newline="")
    buffer = ""
    position = 0
    started = False
    expecting_value = True
    eof = False

    def fill() -> bool:
        nonlocal buffer, position, eof
        if eof:
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        chunk = reader.read(1024 * 1024)
        if chunk:
            buffer += chunk
            return True
        eof = True
        return False

    while True:
        while position >= len(buffer):
            if not fill():
                if started:
                    raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json ended before its top-level array was complete.")
                raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json is empty.")
        while position < len(buffer) and buffer[position].isspace():
            position += 1
            if position >= len(buffer) and not fill():
                raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json ended unexpectedly.")
        if not started:
            if buffer[position] != "[":
                raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json must contain a top-level array.")
            position += 1
            started = True
            continue
        if not expecting_value:
            if buffer[position] == ",":
                position += 1
                expecting_value = True
                continue
            if buffer[position] == "]":
                return
            raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json contains an invalid array separator.")
        if buffer[position] == "]":
            return
        while True:
            try:
                value, end = decoder.raw_decode(buffer, position)
                break
            except RecursionError as exc:
                raise JobOpsError(
                    "CHATGPT_EXPORT_CONVERSATION_TOO_COMPLEX",
                    "One conversation exceeds the bounded JSON nesting limit.",
                ) from exc
            except json.JSONDecodeError as exc:
                pending = len(buffer) - position
                if pending > MAX_CHATGPT_CONVERSATION_CHARS:
                    raise JobOpsError(
                        "CHATGPT_EXPORT_CONVERSATION_TOO_LARGE",
                        "One conversation is too large for bounded local processing.",
                    ) from exc
                if not fill():
                    raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json is not valid UTF-8 JSON.") from exc
        position = end
        expecting_value = False
        yield value


def _chatgpt_fragment_priority(value: str) -> int:
    lowered = value.casefold()
    indicators = (
        " i ", " my ", " we ", "worked", "built", "led ", "managed", "intern", "university",
        "project", "experience", "skill", "resume", "我", "我的", "我们", "负责", "实习", "项目",
        "工作", "经历", "教育", "毕业", "技能", "证书", "简历", "成果",
    )
    prompt_markers = ("please ", "could you", "can you", "帮我", "请帮", "请写", "生成一", "翻译")
    score = min(12, max(1, len(value) // 350))
    score += 5 * sum(marker in f" {lowered} " for marker in indicators)
    score += 3 if re.search(r"\b(?:19|20)\d{2}\b|\d[\d,.]*\s*(?:%|\+|x|万|百万|亿)", value) else 0
    score -= 3 * sum(marker in lowered for marker in prompt_markers)
    return score


def _select_chatgpt_fragments(values: Iterable[Any]) -> tuple[str, int, dict[str, Any]]:
    """Keep a representative, high-signal sample while scanning the full export."""

    selected: list[tuple[int, str, int, str]] = []
    excluded = 0
    ordinal = 0
    readable = 0
    for value in values:
        for fragment in _chatgpt_user_fragments(value):
            ordinal += 1
            # A whole user message becomes one provenance line. This prevents page
            # wrapping inside long prompts from creating false line citations.
            cleaned = " ".join(_clean_text(fragment, limit=30_000).split())
            if not cleaned:
                continue
            readable += 1
            try:
                assert_no_plaintext_secret(cleaned)
            except Exception:
                excluded += 1
                continue
            tie_breaker = sha256_bytes(f"{ordinal}:{cleaned}".encode("utf-8"))
            item = (_chatgpt_fragment_priority(cleaned), tie_breaker, ordinal, cleaned)
            if len(selected) < MAX_CHATGPT_FRAGMENT_CANDIDATES:
                heapq.heappush(selected, item)
            elif item[:2] > selected[0][:2]:
                heapq.heapreplace(selected, item)

    packed: list[tuple[int, str]] = []
    used = 0
    for _, _, item_ordinal, cleaned in sorted(selected, key=lambda item: (item[0], item[1]), reverse=True):
        available = MAX_CHATGPT_AI_SELECTION_CHARS - used
        if available < 40:
            break
        bounded = cleaned[:available]
        if len(bounded) < 20:
            continue
        packed.append((item_ordinal, bounded))
        used += len(bounded) + 1
    packed.sort(key=lambda item: item[0])
    safe_fragments = max(0, readable - excluded)
    selected_text = "\n".join(value for _, value in packed)
    omitted = max(0, safe_fragments - len(packed))
    return selected_text, excluded, {
        "archive_scan_complete": True,
        "user_fragments_scanned": ordinal,
        "readable_user_fragments": readable,
        "safe_fragments_considered": safe_fragments,
        "ai_selected_fragments": len(packed),
        "ai_omitted_fragments": omitted,
        "ai_selection_bounded": omitted > 0,
        "ai_selection_mode": "HIGH_SIGNAL_BOUNDED" if omitted > 0 else "ALL_SAFE_USER_MESSAGES",
        "ai_selection_character_limit": MAX_CHATGPT_AI_SELECTION_CHARS,
        "ai_selected_characters": len(selected_text),
    }


def extract_key_value_suggestions(text: str, *, source_id: str, source_status: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        line = _clean_text(raw_line, limit=2_000)
        if not line or len(line) > 2_000:
            continue
        match = re.match(r"^\s*[-*•]?\s*([^:：]{2,80})\s*[:：]\s*(.{1,1500})$", line)
        if not match:
            continue
        label = match.group(1).strip().casefold()
        value = match.group(2).strip()
        for field_id, aliases in FIELD_ALIASES.items():
            if label in {alias.casefold() for alias in aliases}:
                normalized: Any = _split_values(value) if FIELD_BY_ID[field_id]["input_type"] == "tags" else value
                key = (field_id, json.dumps(normalized, ensure_ascii=False, sort_keys=True))
                if key not in seen:
                    suggestions.append({
                        "suggestion_id": stable_id("SGG", source_id, field_id, key[1]),
                        "field_id": field_id, "value": normalized, "source_id": source_id,
                        "source_status": source_status, "accepted": False,
                    })
                    seen.add(key)
                break
    return suggestions


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise JobOpsError("ONBOARDING_DOCUMENT_TOO_LARGE", "The DOCX contains too many archive entries.")
            documents = [item for item in members if item.filename == "word/document.xml" and not item.is_dir()]
            if len(documents) != 1:
                raise JobOpsError(
                    "ONBOARDING_DOCUMENT_AMBIGUOUS",
                    "The DOCX must contain exactly one Word main-document XML part.",
                )
            document = documents[0]
            if document.file_size < 1 or document.file_size > MAX_DOCX_XML_BYTES:
                raise JobOpsError("ONBOARDING_DOCUMENT_TOO_LARGE", "The DOCX main document exceeds the safe extraction limit.")
            if document.flag_bits & 0x1:
                raise JobOpsError("ONBOARDING_DOCUMENT_ENCRYPTED", "Password-protected DOCX files are not supported.")
            if int(document.file_size) / max(1, int(document.compress_size)) > MAX_DOCX_XML_COMPRESSION_RATIO:
                raise JobOpsError(
                    "ONBOARDING_DOCUMENT_COMPRESSION_UNSAFE",
                    "The DOCX main document has an unsafe compression ratio.",
                )
            with archive.open(document, "r") as stream:
                body = stream.read(MAX_DOCX_XML_BYTES + 1)
            if len(body) != int(document.file_size) or len(body) > MAX_DOCX_XML_BYTES:
                raise JobOpsError("ONBOARDING_DOCUMENT_TOO_LARGE", "The DOCX main document did not match its bounded size.")
    except JobOpsError:
        raise
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        raise JobOpsError("ONBOARDING_DOCUMENT_INVALID", "The uploaded DOCX is not a readable Word document.") from exc
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise JobOpsError("ONBOARDING_DOCUMENT_INVALID", "The uploaded DOCX XML is damaged.") from exc
    lines: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.endswith("}p"):
            text = "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
            if text.strip():
                lines.append(text.strip())
    return "\n".join(lines)


def _chatgpt_archive_text(archive: zipfile.ZipFile, *, large_mode: bool) -> tuple[str, int, dict[str, Any]]:
    members = archive.infolist()
    member_limit = MAX_LARGE_ZIP_MEMBERS if large_mode else MAX_ZIP_MEMBERS
    if len(members) > member_limit:
        raise JobOpsError("CHATGPT_EXPORT_TOO_LARGE", "The ChatGPT export contains too many archive entries.")
    if not large_mode and sum(int(item.file_size) for item in members) > MAX_ZIP_UNCOMPRESSED:
        raise JobOpsError("CHATGPT_EXPORT_TOO_LARGE", "The uncompressed ChatGPT export exceeds the standard local safety limit.")
    candidates = [item for item in members if Path(item.filename).name.casefold() == "conversations.json" and not item.is_dir()]
    if not candidates:
        raise JobOpsError("CHATGPT_EXPORT_CONVERSATIONS_MISSING", "The official export does not contain conversations.json.")
    if len(candidates) != 1:
        raise JobOpsError("CHATGPT_EXPORT_CONVERSATIONS_AMBIGUOUS", "The export contains more than one conversations.json file.")
    candidate = candidates[0]
    candidate_limit = MAX_LARGE_CONVERSATIONS_BYTES if large_mode else MAX_ZIP_UNCOMPRESSED
    if candidate.file_size < 1 or candidate.file_size > candidate_limit:
        raise JobOpsError("CHATGPT_EXPORT_TOO_LARGE", "conversations.json exceeds the selected local safety limit.")
    compressed_size = max(1, int(candidate.compress_size))
    if int(candidate.file_size) / compressed_size > MAX_CHATGPT_MEMBER_COMPRESSION_RATIO:
        raise JobOpsError("CHATGPT_EXPORT_COMPRESSION_UNSAFE", "conversations.json has an unsafe compression ratio.")
    if candidate.flag_bits & 0x1:
        raise JobOpsError("CHATGPT_EXPORT_ENCRYPTED", "Encrypted ZIP exports are not supported.")
    try:
        with archive.open(candidate, "r") as stream:
            return _select_chatgpt_fragments(_iter_json_array(stream))
    except (UnicodeDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise JobOpsError("CHATGPT_EXPORT_INVALID", "conversations.json could not be safely streamed as UTF-8 JSON.") from exc


def _chatgpt_export_text(data: bytes) -> tuple[str, int, dict[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _chatgpt_archive_text(archive, large_mode=False)
    except zipfile.BadZipFile as exc:
        raise JobOpsError("CHATGPT_EXPORT_INVALID", "The uploaded ChatGPT export is not a valid ZIP archive.") from exc


def _chatgpt_export_text_path(path: Path) -> tuple[str, int, dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as archive:
            return _chatgpt_archive_text(archive, large_mode=True)
    except zipfile.BadZipFile as exc:
        raise JobOpsError("CHATGPT_EXPORT_INVALID", "The selected large ChatGPT export is not a valid ZIP archive.") from exc


def _json_text(data: bytes) -> str:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except RecursionError as exc:
        raise JobOpsError(
            "ONBOARDING_JSON_COMPLEXITY_LIMIT",
            "The uploaded JSON exceeds the bounded nesting-depth limit.",
            maximum_depth=MAX_JSON_DEPTH,
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobOpsError("ONBOARDING_JSON_INVALID", "The uploaded JSON is not valid UTF-8 JSON.") from exc
    parts: list[str] = []
    used = 0
    for item in _string_values(value):
        cleaned = _clean_text(item, limit=30_000)
        if not cleaned:
            continue
        added = len(cleaned) + (1 if parts else 0)
        if used + added > MAX_DERIVED_TEXT_CHARS:
            raise JobOpsError(
                "ONBOARDING_DERIVED_TEXT_TOO_LARGE",
                "The readable source exceeds the bounded complete-analysis limit; no partial source was imported.",
                maximum_characters=MAX_DERIVED_TEXT_CHARS,
            )
        parts.append(cleaned)
        used += added
    return "\n".join(parts)


class OnboardingCenterService:
    def __init__(
        self,
        project: Path,
        database: JobOpsDB,
        onboarding: PrivateOnboarding,
        ai_engine: AIAnalysisEngine | None = None,
        ai_connections: AIConnectionManager | None = None,
    ) -> None:
        self.project = project.resolve(strict=True)
        self.database = database
        self.database.initialize()
        self.execution_reconciliation = IsolatedApplicationExecutionController(
            self.database
        ).reconcile_interrupted_runs()
        self.onboarding = onboarding
        self.onboarding.assert_outside_project(self.project)
        self.browser_assist = BrowserAssistManager(self.project, self.database, self.onboarding)
        initial_engine = ai_engine or configured_ai_engine()
        self.ai_connections = ai_connections or AIConnectionManager(
            self.onboarding.store.private_root.parent / "ai-connection.json",
            initial_engine=initial_engine,
        )
        self.ai_engine = self.ai_connections.current_engine
        self.schemas = self.project / "schemas"
        self.index_path = self.project / "state" / "onboarding-center-index.json"
        self._lock = threading.RLock()

    def _latest_ref(self, kind: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT secure_ref FROM private_refs WHERE kind=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return str(row[0]) if row else None

    def _secure_content_hash(self, reference: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT content_sha256,status FROM private_refs WHERE secure_ref=?", (reference,)
            ).fetchone()
        if row is None or str(row["status"]) != "ACTIVE":
            raise JobOpsError("SECURE_REFERENCE_MISSING", "An encrypted application source is not active.")
        return str(row["content_sha256"])

    def _master_resume_descriptor(self, state: dict[str, Any]) -> dict[str, Any] | None:
        current = state.get("master_resume")
        if isinstance(current, dict) and current.get("secure_ref"):
            reference = str(current["secure_ref"])
            content_hash = self._secure_content_hash(reference)
            if content_hash != current.get("sha256"):
                raise JobOpsError("MASTER_RESUME_HASH_INVALID", "The encrypted Master Resume binding has changed.")
            return deepcopy(current)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT secure_ref,kind,content_sha256 FROM private_refs
                   WHERE kind IN ('master_resume_docx','master_resume_pdf') AND status='ACTIVE'
                   ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        editable = str(row["kind"]) == "master_resume_docx"
        return {
            "secure_ref": str(row["secure_ref"]), "sha256": str(row["content_sha256"]),
            "extension": ".docx" if editable else ".pdf", "source_id": None,
            "editable_docx": editable, "template_fingerprint": None,
            "template_slots": [], "designated_at": None,
        }

    def _designate_master_resume(self, state: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any] | None:
        metadata = pending.get("metadata") if isinstance(pending.get("metadata"), dict) else {}
        extension = str(metadata.get("extension", "")).casefold()
        if metadata.get("category") != "resume" or not metadata.get("raw_retained") or extension not in {".docx", ".pdf"}:
            return None
        reference = str(metadata.get("secure_ref", ""))
        content_hash = self._secure_content_hash(reference)
        if content_hash != metadata.get("sha256"):
            raise JobOpsError("MASTER_RESUME_HASH_INVALID", "The retained resume hash does not match its encrypted source.")
        fingerprint_hash = content_hash
        slots: list[str] = []
        if extension == ".docx":
            with self.onboarding.staging_directory() as staging:
                local_copy = staging / "master-resume.docx"
                local_copy.write_bytes(self.onboarding.read_bytes(reference))
                fingerprint = template_fingerprint(local_copy)
                if fingerprint.master_sha256 != content_hash:
                    raise JobOpsError("MASTER_RESUME_HASH_INVALID", "The decrypted Master Resume failed its hash check.")
                fingerprint_hash = sha256_bytes(canonical_json(fingerprint.as_dict()))
                slots = discover_template_slots(local_copy)
        descriptor = {
            "secure_ref": reference, "sha256": content_hash, "extension": extension,
            "source_id": str(metadata.get("source_id") or pending.get("source_id") or ""),
            "editable_docx": extension == ".docx", "template_fingerprint": fingerprint_hash,
            "template_slots": slots, "designated_at": iso_utc(),
        }
        state["master_resume"] = descriptor
        return descriptor

    def _claim_approval_context(
        self, state_ref: str, state: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None, str | None]:
        master = self._master_resume_descriptor(state)
        ui_claims = [
            item for item in self._claims_for_ui(state)
            if item.get("decision") == "CONFIRMED" and item.get("deleted") is not True
        ]
        if master is None or not ui_claims:
            return master, [], None, self._latest_ref("candidate_profile")
        review_hash = claim_review_hash(ui_claims, str(master["sha256"]))
        profile_ref = self._latest_ref("candidate_profile")
        base = {
            str(item.get("claim_id")): item for item in self._claim_bundle().get("claims", [])
            if item.get("claim_id")
        }
        material = {
            str(item.get("claim_id")): item for item in state.get("material_claims", [])
            if item.get("claim_id")
        }

        def binding(kind: str, reference: str) -> dict[str, str]:
            return {"kind": kind, "secure_ref": reference, "content_sha256": self._secure_content_hash(reference)}

        def source_bindings(claim_id: str, seen: set[str] | None = None) -> list[dict[str, str]]:
            visited = set(seen or ())
            if claim_id in visited:
                raise JobOpsError("EXTERNAL_CLAIM_PROVENANCE_CYCLE", "A derived Claim contains a provenance cycle.")
            visited.add(claim_id)
            if claim_id in base:
                return [binding("MASTER_RESUME", str(master["secure_ref"]))]
            item = material.get(claim_id)
            if item is None:
                raise JobOpsError("EXTERNAL_CLAIM_SOURCE_MISSING", "A confirmed Claim has no encrypted source.")
            direct_ref = str(item.get("source_ref") or "")
            if direct_ref:
                return [binding("UPLOADED_MATERIAL", direct_ref)]
            output: list[dict[str, str]] = []
            for parent in item.get("provenance_claim_ids", []):
                output.extend(source_bindings(str(parent), visited))
            if not output:
                raise JobOpsError("EXTERNAL_CLAIM_SOURCE_MISSING", "A derived Claim has no encrypted source ancestry.")
            return list({canonical_json(value).decode("utf-8"): value for value in output}.values())

        claims: list[dict[str, Any]] = []
        for item in ui_claims:
            claim_id = str(item["claim_id"])
            original = base.get(claim_id, {})
            claims.append({
                **item,
                "responsibility_boundary": original.get("responsibility_boundary"),
                "source_bindings": source_bindings(claim_id),
            })
        return master, claims, review_hash, profile_ref

    def _external_claim_status(
        self, state_ref: str, state: dict[str, Any], review_hash: str | None, master: dict[str, Any] | None,
    ) -> dict[str, Any]:
        reference = self._latest_ref("external_claim_set")
        if not reference or not review_hash or master is None:
            return {"current": False, "claim_count": 0, "allowed_uses": []}
        try:
            value = self._read_json_ref(reference)
            validate_named("external-claim-set", value, self.schemas)
            validate_external_claim_set_integrity(value)
        except JobOpsError:
            return {"current": False, "claim_count": 0, "allowed_uses": []}
        current = (
            value.get("onboarding_state_ref") == state_ref
            and value.get("review_hash") == review_hash
            and (value.get("master_resume") or {}).get("sha256") == master.get("sha256")
            and value.get("profile_ref") == self._latest_ref("candidate_profile")
        )
        return {
            "current": bool(current),
            "claim_count": int(value.get("claim_count", 0)) if current else 0,
            "allowed_uses": list(value.get("allowed_uses", [])) if current else [],
            "content_hash": str(value.get("content_hash")) if current else None,
        }

    def _tailoring_manifest_status(
        self, state_ref: str, master: dict[str, Any] | None,
    ) -> dict[str, Any]:
        reference = self._latest_ref("resume_tailoring_manifest")
        if not reference or master is None:
            return {"current": False, "block_count": 0, "content_hash": None}
        try:
            value = self._read_json_ref(reference)
            validate_named("resume-tailoring-manifest", value, self.schemas)
            validate_resume_tailoring_manifest_integrity(value)
        except JobOpsError:
            return {"current": False, "block_count": 0, "content_hash": None}
        current = (
            value.get("onboarding_state_ref") == state_ref
            and value.get("master_resume_ref") == master.get("secure_ref")
            and value.get("master_resume_sha256") == master.get("sha256")
            and value.get("template_fingerprint") == master.get("template_fingerprint")
        )
        return {
            "current": bool(current),
            "block_count": int(value.get("block_count", 0)) if current else 0,
            "content_hash": str(value.get("content_hash")) if current else None,
        }

    def _tailoring_claims(self, state: dict[str, Any], master: dict[str, Any]) -> list[dict[str, Any]]:
        material = {
            str(item.get("claim_id")): item for item in state.get("material_claims", [])
            if item.get("claim_id")
        }
        output: list[dict[str, Any]] = []
        for item in self._claims_for_ui(state):
            if item.get("decision") != "CONFIRMED" or item.get("deleted") is True:
                continue
            original = material.get(str(item.get("claim_id")), {})
            output.append({
                **item,
                "source_id": original.get("source_id", item.get("source_id")),
                "provenance": deepcopy(original.get("provenance")),
            })
        return output

    def _build_tailoring_proposal(
        self, state_ref: str, state: dict[str, Any], master: dict[str, Any],
    ) -> dict[str, Any]:
        if state.get("status") != COMPLETE:
            raise JobOpsError("ONBOARDING_INCOMPLETE", "Complete onboarding before approving safe resume-editing positions.")
        if not master.get("editable_docx"):
            raise JobOpsError("EDITABLE_MASTER_DOCX_MISSING", "Upload an editable DOCX Master Resume first.")
        with self.onboarding.staging_directory() as staging:
            local_copy = staging / "master-resume.docx"
            local_copy.write_bytes(self.onboarding.read_bytes(str(master["secure_ref"])))
            if sha256_file(local_copy) != master.get("sha256"):
                raise JobOpsError("MASTER_RESUME_HASH_INVALID", "The decrypted Master Resume failed its hash check.")
            blocks = inspect_docx_text_blocks(local_copy)
        return build_tailoring_proposal(
            onboarding_state_ref=state_ref,
            master_resume=master,
            blocks=blocks,
            claims=self._tailoring_claims(state, master),
        )

    def _read_json_ref(self, reference: str) -> dict[str, Any]:
        try:
            value = json.loads(self.onboarding.read_bytes(reference).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("ONBOARDING_PRIVATE_STATE_INVALID", "Encrypted onboarding state is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("ONBOARDING_PRIVATE_STATE_INVALID", "Encrypted onboarding state must be an object.")
        return value

    def _claim_bundle(self) -> dict[str, Any]:
        reference = self._latest_ref("claim_candidates")
        if not reference:
            return {"claims": [], "counts": {}}
        return self._read_json_ref(reference)

    def _base_profile(self) -> dict[str, Any]:
        reference = self._latest_ref("candidate_profile")
        return self._read_json_ref(reference) if reference else {}

    def _default_state(self) -> dict[str, Any]:
        claims = [item for item in self._claim_bundle().get("claims", []) if _is_ai_qualified_claim(item)]
        claim_decisions = {str(item.get("claim_id")): "PENDING" for item in claims if item.get("claim_id")}
        conflict_resolutions = {
            str(item.get("claim_id")): {"status": "PENDING", "resolution": None, "manual_value": None}
            for item in claims if item.get("lifecycle_status") == "CONFLICT_REQUIRES_REVIEW"
        }
        answer_ref = self._latest_ref("answer_bank")
        answers = empty_answers()
        if answer_ref:
            existing = self._read_json_ref(answer_ref)
            if int(existing.get("schema_version", 0)) == 2 and isinstance(existing.get("answers"), dict):
                for field_id in FIELD_IDS:
                    if isinstance(existing["answers"].get(field_id), dict):
                        answers[field_id] = existing["answers"][field_id]
        now = iso_utc()
        return {
            "schema_version": 3, "status": IN_PROGRESS, "locale": "zh", "answers": answers,
            "sources": [], "pending_sources": [], "suggestions": [], "material_claims": [],
            "claim_overrides": {},
            "claim_decisions": claim_decisions, "conflict_resolutions": conflict_resolutions,
            "strict_ai_claims": True,
            "profile_review": "PENDING", "revision_number": 1, "previous_state_ref": None,
            "answer_bank_ref": answer_ref, "created_at": now, "updated_at": now, "completed_at": None,
        }

    def _normalized_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Backfill encrypted v1 state without mutating its historical ciphertext."""

        state["schema_version"] = 3
        state["strict_ai_claims"] = True
        state.setdefault("pending_sources", [])
        state.setdefault("claim_overrides", {})
        state.setdefault("master_resume", None)
        state.setdefault("revision_number", 1)
        state.setdefault("previous_state_ref", None)
        state.setdefault("answer_bank_ref", self._latest_ref("answer_bank"))
        for item in state.setdefault("material_claims", []):
            item.setdefault("deleted", False)
            item.setdefault("provenance_claim_ids", [])
        return state

    @staticmethod
    def _assert_editable(state: dict[str, Any]) -> None:
        if state.get("status") == COMPLETE:
            raise JobOpsError(
                "ONBOARDING_REVISION_REQUIRED",
                "Completed onboarding is an immutable snapshot. Start a new revision before editing.",
            )

    @_synchronized
    def ensure_state(self) -> tuple[str, dict[str, Any]]:
        reference = self._latest_ref("onboarding_center_state")
        if reference:
            return reference, self._normalized_state(self._read_json_ref(reference))
        state = self._default_state()
        record = self.onboarding.import_bytes("onboarding_center_state", canonical_json(state), synthetic=False)
        reference = str(record["secure_ref"])
        try:
            self._write_index(reference, state)
        except Exception as exc:
            try:
                self.onboarding.delete(reference, user_confirmed=True)
            except Exception as rollback_error:
                raise JobOpsError(
                    "ONBOARDING_INITIAL_STATE_ROLLBACK_FAILED",
                    "The initial encrypted onboarding state could not be removed after its redacted index failed.",
                ) from rollback_error
            raise JobOpsError(
                "ONBOARDING_INITIAL_INDEX_WRITE_FAILED",
                "The initial redacted onboarding index failed, so its encrypted state was removed.",
            ) from exc
        return reference, state

    def _completion(self, answers: dict[str, Any]) -> dict[str, Any]:
        resolved = sum(answers[field_id].get("status") != "UNKNOWN" for field_id in REQUIRED_FIELD_IDS)
        total = len(REQUIRED_FIELD_IDS)
        remaining_fields = [field_id for field_id in REQUIRED_FIELD_IDS if answers[field_id].get("status") == "UNKNOWN"]
        return {
            "total": total, "resolved": resolved, "remaining": total - resolved,
            "remaining_fields": remaining_fields, "percent": round(100 * resolved / total, 1),
        }

    def _write_index(self, state_ref: str, state: dict[str, Any], *, completion_ref: str | None = None) -> None:
        answers = state.get("answers", {})
        completion = self._completion(answers)
        review = self._review_counts(state)
        value = {
            "schema_version": 1, "status": state.get("status", IN_PROGRESS), "state_ref": state_ref,
            "completion_ref": completion_ref, "locale": state.get("locale", "zh"),
            "answers": completion, "sources": len(state.get("sources", [])),
            "claims": {"total": review["claims_total"], "reviewed": review["claims_reviewed"]},
            "conflicts": {"total": review["conflicts_total"], "resolved": review["conflicts_resolved"]},
            "updated_at": state.get("updated_at"), "real_external_actions": 0,
        }
        write_json(self.index_path, value)

    def _save_state(
        self,
        reference: str,
        state: dict[str, Any],
        *,
        completion_ref: str | None = None,
    ) -> dict[str, object]:
        previous = self.onboarding.read_bytes(reference)
        state["updated_at"] = iso_utc()
        result = self.onboarding.rotate(reference, canonical_json(state))
        try:
            self._write_index(reference, state, completion_ref=completion_ref)
        except Exception as exc:
            try:
                self.onboarding.rotate(reference, previous)
            except Exception as rollback_error:
                raise JobOpsError(
                    "ONBOARDING_STATE_INDEX_ROLLBACK_FAILED",
                    "The encrypted onboarding state could not be restored after its redacted index failed to update.",
                ) from rollback_error
            raise JobOpsError(
                "ONBOARDING_STATE_INDEX_WRITE_FAILED",
                "The redacted onboarding index failed to update, so the encrypted state was restored.",
            ) from exc
        return result

    def _rollback_private_writes(
        self,
        created_references: list[str],
        *,
        rotated_reference: str | None = None,
        previous_value: bytes | None = None,
    ) -> None:
        failures = 0
        if rotated_reference and previous_value is not None:
            try:
                self.onboarding.rotate(rotated_reference, previous_value)
            except Exception:
                failures += 1
        for created_reference in reversed(created_references):
            try:
                self.onboarding.delete(created_reference, user_confirmed=True)
            except Exception:
                failures += 1
        if failures:
            raise JobOpsError(
                "ONBOARDING_PRIVATE_ROLLBACK_FAILED",
                "A multi-reference private update failed and one or more compensating writes did not complete.",
                failed_compensations=failures,
            )

    def _claims_for_ui(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        decisions = state.get("claim_decisions", {})
        overrides = state.get("claim_overrides", {})
        editable = state.get("status") != COMPLETE
        output = []
        for item in self._claim_bundle().get("claims", []):
            if not _is_ai_qualified_claim(item):
                continue
            claim_id = str(item.get("claim_id"))
            override = overrides.get(claim_id, {}) if isinstance(overrides.get(claim_id), dict) else {}
            evidence = item.get("supporting_evidence", []) if isinstance(item.get("supporting_evidence"), list) else []
            fallback = next((entry.get("excerpt") for entry in evidence if isinstance(entry, dict) and entry.get("excerpt")), None)
            output.append({
                "claim_id": claim_id, "category": override.get("category", item.get("category", "optional")),
                "statement": override.get("statement", item.get("resume_statement") or fallback), "lifecycle_status": item.get("lifecycle_status"),
                "confidence": item.get("confidence"), "conflict": bool(item.get("conflict")),
                "conflict_id": claim_id if item.get("conflict") else None,
                "evidence_count": len(evidence), "decision": decisions.get(claim_id, "PENDING"),
                "deleted": bool(override.get("deleted", False)), "editable": editable,
                "source_kind": "master_resume_or_personal_evidence", "provenance_claim_ids": [],
                "entity_id": item.get("entity_id"), "entity": item.get("entity"),
                "claim_kind": item.get("claim_kind"),
            })
        output.extend({
            "claim_id": item["claim_id"], "category": item["category"],
            "statement": item.get("statement"), "lifecycle_status": "AI_FILTERED_REQUIRES_CONFIRMATION",
            "confidence": item.get("confidence", "LOW"), "conflict": False, "evidence_count": 1,
            "decision": item.get("decision", "PENDING"), "deleted": bool(item.get("deleted", False)),
            "editable": editable, "source_kind": "ai_filtered_uploaded_material",
            "source_id": item.get("source_id"), "provenance_claim_ids": item.get("provenance_claim_ids", []),
            "entity_id": item.get("entity_id"), "entity": item.get("entity"),
            "claim_kind": item.get("claim_kind"),
        } for item in state.get("material_claims", []) if _is_ai_qualified_claim(item))
        return output

    def _conflicts_for_ui(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        claims = {
            str(item.get("claim_id")): item
            for item in self._claim_bundle().get("claims", [])
            if item.get("claim_id")
        }
        ui_claims = {item["claim_id"]: item for item in self._claims_for_ui(state)}
        sources = {str(item.get("source_id")): item for item in state.get("sources", [])}
        output: list[dict[str, Any]] = []
        for conflict_id, resolution in state.get("conflict_resolutions", {}).items():
            base = {
                "conflict_id": str(conflict_id),
                "status": resolution.get("status", "PENDING"),
                "resolution": resolution.get("resolution"),
                "manual_value": resolution.get("manual_value"),
            }
            field_id = resolution.get("field_id")
            if field_id in FIELD_BY_ID:
                candidates = []
                for suggestion in state.get("suggestions", []):
                    if suggestion.get("ai_validated") is not True:
                        continue
                    if suggestion.get("field_id") != field_id:
                        continue
                    source = sources.get(str(suggestion.get("source_id")), {})
                    candidates.append({
                        "value": deepcopy(suggestion.get("value")),
                        "source_id": suggestion.get("source_id"),
                        "safe_source_name": source.get("safe_display_name", "encrypted-source"),
                        "source_status": suggestion.get("source_status"),
                    })
                if len({json.dumps(item["value"], ensure_ascii=False, sort_keys=True) for item in candidates}) < 2:
                    continue
                output.append({
                    **base, "kind": "FIELD_VALUE_CONFLICT", "field_id": field_id,
                    "group": FIELD_BY_ID[field_id]["group"], "reason": "MULTIPLE_SOURCE_VALUES",
                    "candidates": candidates,
                })
                continue
            claim = claims.get(str(conflict_id), {})
            ui_claim = ui_claims.get(str(conflict_id))
            if not ui_claim or not _is_ai_qualified_claim(claim):
                continue
            evidence = []
            for item in claim.get("supporting_evidence", []):
                if not isinstance(item, dict):
                    continue
                preview = _evidence_preview(
                    str(ui_claims.get(str(conflict_id), {}).get("statement", claim.get("resume_statement")) or ""),
                    str(item.get("excerpt") or ""),
                )
                if preview["relevant"] and preview["differences"]:
                    evidence.append({
                        "source_id": item.get("source_id"), "heading": item.get("heading"),
                        **preview,
                    })
            if not evidence:
                continue
            first_difference = evidence[0]["differences"][0]
            output.append({
                **base, "kind": "CLAIM_EVIDENCE_CONFLICT", "claim_id": str(conflict_id),
                "category": ui_claim["category"], "reason": "COMPARABLE_VALUE_MISMATCH",
                "resume_statement": ui_claim.get("statement", claim.get("resume_statement")),
                "entity": ui_claim.get("entity"), "claim_kind": ui_claim.get("claim_kind"),
                "difference": first_difference, "evidence_candidates": evidence,
            })
        return output

    def _pipeline_dashboard(self, state: dict[str, Any]) -> dict[str, Any]:
        queue = QueueManager(self.database).status()
        actions = audit_real_external_actions(self.database)
        action_control = ExternalActionSessionManager(
            self.database, ExternalActionSessionPolicy.production_disabled()
        ).control_state()
        with self.database.connect() as connection:
            status_rows = connection.execute(
                "SELECT status,COUNT(*) AS total FROM applications GROUP BY status ORDER BY status"
            ).fetchall()
            pending_rows = connection.execute(
                """SELECT a.application_id,a.job_id,a.status,a.updated_at,
                          j.company,j.title,j.location,j.official_url,
                          r.packet_id,r.packet_version,r.status AS packet_status,r.content_hash
                   FROM applications a
                   JOIN jobs j ON j.job_id=a.job_id
                   LEFT JOIN review_packets r ON r.packet_id=(
                       SELECT rp.packet_id FROM review_packets rp
                       WHERE rp.application_id=a.application_id
                         AND rp.status IN ('AWAITING_APPROVAL','APPROVED')
                       ORDER BY rp.packet_version DESC LIMIT 1
                   )
                   WHERE a.status='AWAITING_APPROVAL'
                   ORDER BY a.updated_at,a.application_id
                   LIMIT 100"""
            ).fetchall()
            deferred_rows = connection.execute(
                """SELECT intake_key,source_type,created_at,updated_at
                   FROM intake_queue WHERE status='DEFERRED'
                   ORDER BY created_at,intake_key LIMIT 100"""
            ).fetchall()
            recent_rows = connection.execute(
                """SELECT a.application_id,a.status,a.updated_at,j.company,j.title,j.location,
                          r.packet_id,r.packet_version,r.status AS packet_status,
                          (SELECT expires_at FROM approvals p WHERE p.application_id=a.application_id
                           ORDER BY p.issued_at DESC LIMIT 1) AS approval_expires_at
                   FROM applications a
                   JOIN jobs j ON j.job_id=a.job_id
                   LEFT JOIN review_packets r ON r.packet_id=(
                       SELECT rp.packet_id FROM review_packets rp
                       WHERE rp.application_id=a.application_id
                       ORDER BY rp.packet_version DESC LIMIT 1
                   )
                   WHERE a.status<>'AWAITING_APPROVAL'
                   ORDER BY a.updated_at DESC,a.application_id DESC LIMIT 30"""
            ).fetchall()
            execution_rows = connection.execute(
                """SELECT r.run_id,r.application_id,r.status,r.checkpoint_sequence,r.updated_at,
                          a.status AS application_status,j.company,j.title,j.location,
                          c.phase AS last_phase,c.status AS checkpoint_status
                   FROM application_execution_runs r
                   JOIN applications a ON a.application_id=r.application_id
                   JOIN jobs j ON j.job_id=a.job_id
                   LEFT JOIN application_execution_checkpoints c ON c.checkpoint_id=(
                       SELECT ec.checkpoint_id FROM application_execution_checkpoints ec
                       WHERE ec.run_id=r.run_id ORDER BY ec.sequence DESC LIMIT 1
                   )
                   ORDER BY r.updated_at DESC,r.run_id DESC LIMIT 30"""
            ).fetchall()
        applications = [{
            "application_id": str(row["application_id"]),
            "job_id": str(row["job_id"]),
            "status": str(row["status"]),
            "updated_at": str(row["updated_at"]),
            "company": str(row["company"]),
            "title": str(row["title"]),
            "location": str(row["location"]) if row["location"] is not None else None,
            "official_url": str(row["official_url"]) if row["official_url"] is not None else None,
            "packet_id": str(row["packet_id"]) if row["packet_id"] is not None else None,
            "packet_version": int(row["packet_version"]) if row["packet_version"] is not None else None,
            "packet_status": str(row["packet_status"]) if row["packet_status"] is not None else "MISSING",
            "packet_hash_prefix": str(row["content_hash"])[:15] if row["content_hash"] is not None else None,
        } for row in pending_rows]
        deferred = [{
            "safe_intake_id": sha256_bytes(str(row["intake_key"]).encode("utf-8"))[:15],
            "source_type": str(row["source_type"]),
            "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
            "status": "DEFERRED",
        } for row in deferred_rows]
        recent = [{
            "application_id": str(row["application_id"]), "status": str(row["status"]),
            "updated_at": str(row["updated_at"]), "company": str(row["company"]),
            "title": str(row["title"]),
            "location": str(row["location"]) if row["location"] is not None else None,
            "packet_id": str(row["packet_id"]) if row["packet_id"] is not None else None,
            "packet_version": int(row["packet_version"]) if row["packet_version"] is not None else None,
            "packet_status": str(row["packet_status"]) if row["packet_status"] is not None else "MISSING",
            "approval_expires_at": str(row["approval_expires_at"]) if row["approval_expires_at"] is not None else None,
        } for row in recent_rows]
        execution_runs = []
        for row in execution_rows:
            run_status = str(row["status"])
            application_status = str(row["application_status"])
            interrupted = (
                run_status in {"SUBMISSION_STARTED", "SUBMITTED"}
                or (
                    run_status == "AWAITING_FINAL_AUTHORIZATION"
                    and application_status in {"SUBMITTING", "SUBMITTED", "SUBMISSION_UNKNOWN", "CONFIRMED"}
                )
            )
            display_status = "INTERRUPTED_RECONCILIATION_REQUIRED" if interrupted else run_status
            next_safe_action = {
                "AWAITING_FINAL_AUTHORIZATION": "USER_FINAL_CONFIRMATION_REQUIRED",
                "CONFIRMED": "NONE",
                "SUBMISSION_UNKNOWN": "MANUAL_EXTERNAL_VERIFICATION_REQUIRED",
                "INVALIDATED": "REBUILD_REVIEW_PACKET",
                "INTERRUPTED_RECONCILIATION_REQUIRED": "RESTART_RECONCILIATION_REQUIRED",
            }.get(display_status, "MANUAL_EXTERNAL_VERIFICATION_REQUIRED")
            execution_runs.append({
                "run_id": str(row["run_id"]),
                "application_id": str(row["application_id"]),
                "company": str(row["company"]),
                "title": str(row["title"]),
                "location": str(row["location"]) if row["location"] is not None else None,
                "status": display_status,
                "application_status": application_status,
                "checkpoint_sequence": int(row["checkpoint_sequence"]),
                "last_phase": str(row["last_phase"]) if row["last_phase"] is not None else "MISSING",
                "checkpoint_status": str(row["checkpoint_status"]) if row["checkpoint_status"] is not None else "MISSING",
                "updated_at": str(row["updated_at"]),
                "automatic_retry": False,
                "next_safe_action": next_safe_action,
            })
        browser_assist = self.browser_assist.public_status()
        return {
            "status": "LOCAL_PIPELINE_READY",
            "onboarding_status": str(state.get("status", IN_PROGRESS)),
            "queue": queue,
            "application_status_counts": {str(row["status"]): int(row["total"]) for row in status_rows},
            "pending_applications": applications,
            "deferred_intake": deferred, "recent_applications": recent,
            "execution_runs": execution_runs,
            "execution_status_counts": {
                status: sum(1 for item in execution_runs if item["status"] == status)
                for status in sorted({item["status"] for item in execution_runs})
            },
            "startup_execution_reconciliation": dict(self.execution_reconciliation),
            "safety": {
                "network_mode": "LOCAL_OFFLINE_PLUS_USER_PRESENT_BROWSER_ASSIST",
                "real_website_accesses": int(browser_assist["real_website_inspections"]),
                "external_action_attempts": int(actions["attempt_count"]),
                "real_external_actions": int(actions["real_external_actions"]),
                "knowledge_write_operations": 0,
                "external_action_control_enabled": bool(action_control["enabled"]),
                "external_action_control_mode": str(action_control["mode"]),
                "submit_capability": False,
                "automatic_retry": False,
            },
            "generated_at": iso_utc(),
        }

    @_synchronized
    def disable_external_actions(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("user_confirmed") is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "The emergency stop requires an explicit user confirmation.")
        stopped = self.browser_assist.stop(user_confirmed=True)
        actions = audit_real_external_actions(self.database)
        return {
            "status": "EXTERNAL_ACTIONS_DISABLED",
            "browser_assist_status": stopped["status"],
            "revoked_assists": int(stopped["revoked_assists"]),
            "submission_unknown_assists": int(stopped["submission_unknown_assists"]),
            "real_external_actions": int(actions["real_external_actions"]),
            "automatic_retry": False,
        }

    @_synchronized
    def start_browser_assist(self, payload: dict[str, Any]) -> dict[str, Any]:
        application_id = str(payload.get("application_id", "")).strip()
        if not re.fullmatch(r"APP-[A-F0-9]{12}", application_id):
            raise JobOpsError("APPLICATION_ID_INVALID", "Choose one approved application for browser assistance.")
        displayed = self.review_packet(application_id)
        if displayed["status"] != "APPROVED" or displayed["application_status"] != "APPROVED":
            raise JobOpsError(
                "APPLICATION_NOT_APPROVED",
                "Review and approve the current packet before starting live prefill and upload.",
            )
        source_route = displayed["packet"].get("source_route")
        if not isinstance(source_route, dict):
            raise JobOpsError("SOURCE_ROUTE_MISSING", "The approved review packet has no verified company route.")
        return self.browser_assist.start(
            application_id=application_id,
            source_route=source_route,
            user_confirmed=payload.get("user_confirmed") is True,
        )

    @_synchronized
    def resolve_browser_assist_unknown(self, payload: dict[str, Any]) -> dict[str, Any]:
        submitted = payload.get("submitted")
        if not isinstance(submitted, bool):
            raise JobOpsError("SUBMISSION_RESULT_INVALID", "Confirm whether the application was submitted successfully.")
        return self.browser_assist.resolve_unknown(
            application_id=str(payload.get("application_id", "")).strip(),
            submitted=submitted,
            user_confirmed=payload.get("user_confirmed") is True,
        )

    def prepare_synthetic_execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise JobOpsError(
            "SYNTHETIC_DEMO_ONLY",
            "The isolated execution rehearsal exists only in the temporary synthetic demo.",
        )

    def complete_synthetic_execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise JobOpsError(
            "SYNTHETIC_DEMO_ONLY",
            "The isolated execution rehearsal exists only in the temporary synthetic demo.",
        )

    @_synchronized
    def set_queue_limit(self, payload: dict[str, Any]) -> dict[str, Any]:
        limit = payload.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise JobOpsError("PENDING_LIMIT_INVALID", "The pending approval limit must be a whole number.")
        self.database.set_pending_limit(limit)
        return {
            "status": "QUEUE_LIMIT_UPDATED", "queue": QueueManager(self.database).status(),
            "real_external_actions": 0,
        }

    @_synchronized
    def discover_official_jobs(
        self,
        snapshot: bytes,
        *,
        official_entry_url: str,
        company_domain: str,
        source_format: str,
    ) -> dict[str, Any]:
        if len(snapshot) > MAX_SNAPSHOT_BYTES:
            raise JobOpsError(
                "OFFICIAL_SNAPSHOT_SIZE_INVALID",
                "The local official-careers snapshot exceeds the offline parser limit.",
            )
        policy = load_json(self.project / "config" / "policy.json")
        report = discover_official_jobs(
            snapshot,
            official_entry_url=official_entry_url,
            company_domain=company_domain,
            approved_ats_hosts=policy["approved_ats_hosts"],
            source_format=source_format,
        )
        validate_named("official-discovery", report, self.schemas)
        return {
            **report,
            "snapshot_persisted": False,
            "candidate_queue_mutations": 0,
            "next_safe_action": "review candidates; obtain separate authorization before any live freshness check",
        }

    @_synchronized
    def review_packet(self, application_id: str) -> dict[str, Any]:
        application_id = str(application_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", application_id):
            raise JobOpsError("APPLICATION_ID_INVALID", "The selected application identifier is invalid.")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.packet_id,r.packet_version,r.content_hash,r.relative_path,r.status,r.created_at,
                          a.status AS application_status,j.company,j.title,j.location,
                          b.context_hash,b.context_json
                   FROM review_packets r
                   JOIN applications a ON a.application_id=r.application_id
                   JOIN jobs j ON j.job_id=a.job_id
                   JOIN application_bindings b ON b.application_id=r.application_id
                   WHERE r.application_id=?
                    ORDER BY r.packet_version DESC LIMIT 1""",
                (application_id,),
            ).fetchone()
            stopped_fields = int(connection.execute(
                "SELECT COUNT(*) FROM application_fields WHERE application_id=? AND status='STOP_REQUIRED'",
                (application_id,),
            ).fetchone()[0])
        if row is None:
            raise JobOpsError("REVIEW_PACKET_NOT_FOUND", "The selected review packet does not exist.")
        raw = self.onboarding.read_bytes(str(row["relative_path"]))
        if len(raw) > MAX_REVIEW_PACKET_BYTES:
            raise JobOpsError("REVIEW_PACKET_SIZE_INVALID", "The encrypted review packet exceeds the local display limit.")
        try:
            packet = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet is not valid JSON.") from exc
        if not isinstance(packet, dict):
            raise JobOpsError("REVIEW_PACKET_INVALID", "The encrypted review packet must be an object.")
        validate_named("review-packet", packet, self.schemas)
        validate_application_execution_plan_integrity(packet["execution_plan"])
        if packet.get("application_id") != application_id or packet.get("packet_id") != row["packet_id"]:
            raise JobOpsError("REVIEW_PACKET_BINDING_INVALID", "The review packet is not bound to the selected application.")
        if packet.get("content_hash") != row["content_hash"]:
            raise JobOpsError("REVIEW_PACKET_HASH_INVALID", "The review packet hash does not match the active queue record.")
        context = ApprovalContext.from_dict(json.loads(str(row["context_json"])))
        if context.context_hash != row["context_hash"] or context.review_packet_hash != packet["content_hash"]:
            raise JobOpsError("APPLICATION_BINDING_MISSING", "The review packet approval binding is inconsistent.")
        return {
            "status": str(row["status"]), "application_status": str(row["application_status"]),
            "application_id": application_id, "packet_id": str(row["packet_id"]),
            "packet_version": int(row["packet_version"]),
            "created_at": str(row["created_at"]), "stopped_fields": stopped_fields,
            "job_summary": {
                "company": str(row["company"]), "title": str(row["title"]),
                "location": str(row["location"]) if row["location"] is not None else None,
            },
            "packet": packet, "private_transport": "LOCAL_SESSION_ONLY",
            "field_resolution": field_resolution_summary(packet, context),
            "private_values_persisted_to_project": 0, "real_external_actions": 0,
        }

    @_synchronized
    def resolve_application_fields(self, payload: dict[str, Any]) -> dict[str, Any]:
        application_id = str(payload.get("application_id", "")).strip()
        expected_hash = str(payload.get("expected_packet_hash", "")).strip()
        outcome = ApplicationFieldResolutionManager(
            self.database, self.onboarding,
        ).resolve(
            application_id=application_id,
            expected_packet_hash=expected_hash,
            raw_resolutions=payload.get("resolutions"),
            user_confirmed=payload.get("user_confirmed") is True,
        )
        return {
            **outcome,
            "phase5_authorization": "PER_APPLICATION_USER_PRESENT_REQUIRED",
            "real_external_actions": 0,
        }

    @_synchronized
    def decide_review_packet(self, payload: dict[str, Any]) -> dict[str, Any]:
        application_id = str(payload.get("application_id", "")).strip()
        decision = str(payload.get("decision", "")).strip().upper()
        expected_hash = str(payload.get("expected_packet_hash", "")).strip()
        if payload.get("user_confirmed") is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "The review decision requires explicit confirmation.")
        if decision not in {"APPROVE", "REVISE", "REJECT"}:
            raise JobOpsError("REVIEW_DECISION_INVALID", "Choose approve, revise, or reject.")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_hash):
            raise JobOpsError("REVIEW_PACKET_HASH_INVALID", "The selected review packet hash is invalid.")

        displayed = self.review_packet(application_id)
        if displayed["packet"]["content_hash"] != expected_hash:
            raise JobOpsError("REVIEW_PACKET_STALE", "The review packet changed after it was displayed. Review the current packet again.")

        manager = QueueManager(self.database)
        if decision == "APPROVE":
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT context_json FROM application_bindings WHERE application_id=?", (application_id,)
                ).fetchone()
            if row is None:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "The current approval binding is missing.")
            context = ApprovalContext.from_dict(json.loads(row["context_json"]))
            if context.unresolved_stops or context.mandatory_unknowns:
                raise JobOpsError(
                    "APPLICATION_FIELDS_UNRESOLVED",
                    "Confirm every highlighted job-specific question before approving this packet.",
                    unresolved_count=len(context.unresolved_stops) + len(context.mandatory_unknowns),
                )
            approval = issue_approval(context=context, user_confirmed=True)
            outcome = ExternalActionGateway(
                self.database, ExternalActionPolicy.production_disabled()
            ).persist_approval(approval, context)
        elif decision == "REVISE":
            outcome = manager.request_revision(application_id, reason="USER_REQUESTED_REVIEW_PACKET_REVISION")
        else:
            outcome = manager.release_application(application_id, reason="USER_REJECTED_REVIEW_PACKET")

        continuation = continue_recorded_intake(
            project=self.project, database=self.database, onboarding=self.onboarding,
        )
        promoted = continuation["initial_promotion"]
        return {
            "status": str(outcome["status"]), "decision": decision,
            "application_id": application_id, "promoted": promoted,
            "continued_intake": continuation,
            "queue": manager.status(), "phase5_authorization": "PER_APPLICATION_USER_PRESENT_REQUIRED",
            "real_external_actions": 0,
            "next_safe_action": (
                "AWAIT_SEPARATE_EXTERNAL_ACTION_AUTHORIZATION"
                if decision == "APPROVE" else
                "REBUILD_OFFLINE_REVIEW_PACKET" if decision == "REVISE" else
                str(promoted.get("next_safe_action", "NONE"))
            ),
        }

    @_synchronized
    def bootstrap(self) -> dict[str, Any]:
        reference, state = self.ensure_state()
        completion = self._completion(state["answers"])
        claims = self._claims_for_ui(state)
        conflicts = self._conflicts_for_ui(state)
        master, approval_claims, review_hash, _ = self._claim_approval_context(reference, state)
        external_claims = self._external_claim_status(reference, state, review_hash, master)
        tailoring_manifest = self._tailoring_manifest_status(reference, master)
        queue_status = QueueManager(self.database).status()
        readiness = build_application_readiness(
            onboarding_status=str(state.get("status", IN_PROGRESS)),
            ai_ready=bool(self.ai_engine.ready and self.ai_engine.public_status().get("status") == "READY"),
            master_resume=master,
            confirmed_claim_count=len(approval_claims),
            claim_review_hash=review_hash,
            external_claim_status=external_claims,
            queue=queue_status,
            tailoring_manifest_status=tailoring_manifest,
        )
        validate_named("application-readiness", readiness, self.schemas)
        all_base = self._claim_bundle().get("claims", [])
        all_material = state.get("material_claims", [])
        raw_claim_count = len(all_base) + len(all_material)
        active_suggestions = [item for item in state.get("suggestions", []) if item.get("ai_validated") is True]
        dashboard = self._pipeline_dashboard(state)
        return {
            "build": {
                "product": "JobFlow", "version": __version__,
                "ui_protocol": UI_PROTOCOL_VERSION,
            },
            "dashboard": dashboard,
            "browser_assist": self.browser_assist.public_status(),
            "application_readiness": readiness,
            "external_claim_approval": {
                "available": bool(
                    state.get("status") == COMPLETE and master is not None and approval_claims and review_hash
                ),
                "current": bool(external_claims["current"]),
                "confirmed_claim_count": len(approval_claims),
                "review_hash": review_hash,
                "allowed_uses": list(ALLOWED_EXTERNAL_USES),
                "real_external_actions": 0,
            },
            "tailoring_manifest": {
                "available": bool(
                    state.get("status") == COMPLETE and master is not None
                    and master.get("editable_docx") and not master.get("template_slots")
                ),
                "current": bool(tailoring_manifest["current"]),
                "block_count": int(tailoring_manifest["block_count"]),
                "real_external_actions": 0,
            },
            "ats_capabilities": offline_ats_capabilities(),
            "status": state.get("status", IN_PROGRESS), "locale": state.get("locale", "zh"),
            "revision_number": int(state.get("revision_number", 1)),
            "can_start_revision": state.get("status") == COMPLETE,
            "catalog": public_catalog(), "answers": deepcopy(state["answers"]), "completion": completion,
            "sources": [
                {
                    **{
                        key: item[key]
                        for key in (
                            "source_id", "category", "extension", "safe_display_name", "source_status",
                            "suggestion_count", "fact_count", "raw_retained", "analysis_mode", "imported_at",
                        )
                        if key in item
                    },
                    **_public_ai_analysis(item.get("extraction_summary")),
                }
                for item in state.get("sources", [])
            ],
            "pending_sources": deepcopy(state.get("pending_sources", [])),
            "suggestions": deepcopy(active_suggestions), "claims": claims,
            "conflicts": conflicts, "profile_review": state.get("profile_review", "PENDING"),
            "state_ref": reference, "privacy": {"storage": "WINDOWS_DPAPI", "network": "LOCALHOST_ONLY", "plaintext_project_files": 0},
            "ai_engine": self.ai_engine.public_status(),
            "ai_connection": self.ai_connections.public_state(),
            "claim_quality": {
                "strict_ai_only": True,
                "active_ai_claims": len(claims),
                "quarantined_legacy_claims": max(0, raw_claim_count - len(claims)),
                "suppressed_invalid_conflicts": max(0, len(state.get("conflict_resolutions", {})) - len(conflicts)),
            },
            "real_external_actions": int(dashboard["safety"]["real_external_actions"]),
            "knowledge_write_operations": 0,
        }

    @_synchronized
    def connect_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode", "")).strip()
        self.ai_engine = self.ai_connections.connect(mode)
        return {
            "status": "AI_CONNECTED",
            "ai_engine": self.ai_engine.public_status(),
            "ai_connection": self.ai_connections.public_state(),
            "private_values_emitted": 0,
            "credentials_read": 0,
            "credentials_stored": 0,
            "real_external_actions": 0,
        }

    @_synchronized
    def prepare_offline_application_bundle(
        self,
        *,
        metadata: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Create one encrypted review packet from explicit local snapshots only."""

        if set(files) != {"jd", "official", "form"}:
            raise JobOpsError(
                "APPLICATION_BUNDLE_FILES_INVALID",
                "Choose one saved JD, official job-page snapshot, and application-form snapshot.",
            )
        allowed_extensions = {
            "jd": {".txt", ".html", ".htm", ".pdf", ".json"},
            "official": {".html", ".htm", ".txt"},
            "form": {".html", ".htm", ".json"},
        }
        size_limits = {"jd": MAX_JD_SOURCE_BYTES, "official": MAX_SNAPSHOT_BYTES, "form": 16 * 1024 * 1024}
        for key, (extension, value) in files.items():
            if extension.casefold() not in allowed_extensions[key] or not value or len(value) > size_limits[key]:
                raise JobOpsError("APPLICATION_BUNDLE_FILE_INVALID", "One selected local application file has an unsupported format or size.", part=key)
        total = sum(len(value) for _, value in files.values())
        if not total or total > MAX_OFFLINE_APPLICATION_BUNDLE_BYTES:
            raise JobOpsError("APPLICATION_BUNDLE_SIZE_INVALID", "The selected local application bundle exceeds the safe limit.")
        official_url = _canonical_url(str(metadata.get("official_url", "")))
        application_url = _canonical_url(str(metadata.get("application_url", "")))
        excerpt = re.sub(r"\s+", " ", str(metadata.get("evidence_excerpt", ""))).strip()
        if not 12 <= len(excerpt) <= 2_000:
            raise JobOpsError("APPLICATION_RESEARCH_EXCERPT_INVALID", "Paste one exact 12–2000 character excerpt from the saved official page.")
        guest_value = metadata.get("guest_available")
        if guest_value not in {True, False, None}:
            raise JobOpsError("APPLICATION_GUEST_STATUS_INVALID", "Guest availability must be yes, no, or unknown.")
        company_domain = registrable_domain(_host(urlparse(official_url).hostname or ""))
        navigation = [official_url] if application_url == official_url else [official_url, application_url]
        route: dict[str, Any] = {
            "official_entry_url": official_url, "current_url": application_url,
            "navigation_history": navigation, "guest_available": guest_value,
            "official_snapshot": "ONE_TIME_LOCAL_SNAPSHOT",
            "research": {
                "title": str(metadata.get("research_title") or "Official company information")[:500],
                "url": official_url, "source_type": "official_company",
                "published_at": metadata.get("published_at") or None,
                "accessed_at": iso_utc(), "official": True, "evidence_excerpt": excerpt,
            },
        }
        current_host = _host(urlparse(application_url).hostname or "")
        if not host_matches_registered(current_host, company_domain):
            provider, tenant, board, identity = _provider_and_tenant(current_host, application_url)
            route["tenant_binding"] = {
                "provider": provider, "company_registrable_domain": company_domain,
                "ats_host": current_host, "tenant": tenant, "board": board, "job_identity": identity,
            }
        deferred_evidence_retained = False
        with self.onboarding.staging_directory() as staging:
            paths: dict[str, Path] = {}
            for name, (extension, value) in files.items():
                target = staging / (name + extension.casefold())
                target.write_bytes(value)
                paths[name] = target
            route_path = staging / "route.json"
            route_path.write_bytes(canonical_json(route))
            research_text, _, _ = _read_jd(paths["official"], files["official"][0].lstrip("."))
            normalized_research = re.sub(r"\s+", " ", research_text).strip()
            if excerpt not in normalized_research:
                raise JobOpsError(
                    "APPLICATION_RESEARCH_EXCERPT_MISSING",
                    "The pasted company excerpt was not found in the selected saved official page.",
                )
            research_path = staging / "research.txt"
            research_path.write_text(normalized_research, encoding="utf-8")
            orchestrator = JobOpsOrchestrator(self.project, self.database, self.onboarding)
            result = orchestrator.run_to_awaiting(
                paths["jd"], profile_ref=None, master_resume_ref=None, answer_bank_ref=None,
                route_fixture=route_path, form_fixture=paths["form"],
                research_fixture=research_path, official_snapshot_fixture=paths["official"],
                synthetic=False,
            )
            if result.get("status") == "DEFERRED":
                intake_key = str(result.get("intake_key", ""))
                descriptor_store = ContinuousIntakeDescriptorStore(self.database, self.project / "schemas")
                existing = descriptor_store.load(intake_key)
                if existing is not None and existing.get("evidence_bundle_ref"):
                    try:
                        existing_metadata = self.onboarding.reference_metadata(
                            str(existing["evidence_bundle_ref"])
                        )
                        if (
                            existing_metadata.get("kind") != "continuous_evidence_bundle"
                            or existing_metadata.get("status") != "ACTIVE"
                            or existing_metadata.get("synthetic") is not False
                        ):
                            existing = None
                    except JobOpsError:
                        existing = None
                    if existing is None:
                        descriptor_store.forget(intake_key)
                if existing is not None:
                    deferred_evidence_retained = True
                else:
                    evidence_record: dict[str, object] | None = None
                    try:
                        evidence_bundle = build_deferred_evidence_bundle(
                            files=files,
                            route_json=canonical_json(route),
                            research_text=normalized_research.encode("utf-8"),
                        )
                        evidence_record = self.onboarding.import_bytes(
                            "continuous_evidence_bundle", evidence_bundle, synthetic=False,
                        )
                        references = orchestrator.current_real_application_references()
                        source_type = files["jd"][0].lstrip(".").casefold()
                        if source_type == "htm":
                            source_type = "html"
                        elif source_type == "json":
                            source_type = "snapshot"
                        descriptor_store.remember(intake_key, {
                            **references,
                            "evidence_bundle_ref": str(evidence_record["secure_ref"]),
                            "source_type": source_type,
                            "synthetic": False,
                        })
                        deferred_evidence_retained = True
                    except Exception as retention_error:
                        try:
                            if evidence_record is not None:
                                self.onboarding.delete(str(evidence_record["secure_ref"]), user_confirmed=True)
                            QueueManager(self.database).rollback_unretained_deferred(
                                intake_key,
                                reason=(
                                    retention_error.code
                                    if isinstance(retention_error, JobOpsError)
                                    else "DEFERRED_EVIDENCE_RETENTION_FAILED"
                                ),
                            )
                        except Exception as rollback_error:
                            raise JobOpsError(
                                "DEFERRED_EVIDENCE_ROLLBACK_FAILED",
                                "Deferred evidence could not be retained and its local admission rollback did not complete.",
                            ) from rollback_error
                        raise retention_error
        return {
            "status": str(result["status"]), "application_id": result.get("application_id"),
            "review_packet_id": result.get("review_packet_id"), "queue": result.get("queue"),
            "deferred_evidence_retained": deferred_evidence_retained,
            "real_external_actions": 0, "network_actions": 0,
            "next_safe_action": "OPEN_LOCAL_REVIEW_PACKET" if result.get("application_id") else result.get("next_safe_action"),
        }

    def close(self) -> None:
        try:
            self.browser_assist.close()
        finally:
            self.ai_connections.close()

    @staticmethod
    def _validate_answer(field_id: str, value: Any) -> dict[str, Any]:
        if field_id not in FIELD_BY_ID or not isinstance(value, dict):
            raise JobOpsError("ONBOARDING_ANSWER_INVALID", "The onboarding answer payload contains an unknown field.")
        status = str(value.get("status", "UNKNOWN"))
        policy = str(value.get("use_policy", FIELD_BY_ID[field_id]["default_policy"]))
        if status not in STATUS_OPTIONS or policy not in USE_POLICIES:
            raise JobOpsError("ONBOARDING_ANSWER_INVALID", "An onboarding answer uses an unsupported status or policy.")
        answer = value.get("value")
        if isinstance(answer, list):
            if len(answer) > 50 or any(not isinstance(item, str) or len(item) > 500 for item in answer):
                raise JobOpsError("ONBOARDING_ANSWER_INVALID", "A multi-value onboarding answer is too large.")
            answer = [item.strip() for item in answer if item.strip()]
        elif isinstance(answer, str):
            if len(answer) > 5_000:
                raise JobOpsError("ONBOARDING_ANSWER_INVALID", "An onboarding answer exceeds the local size limit.")
            answer = answer.strip()
        elif answer is not None:
            raise JobOpsError("ONBOARDING_ANSWER_INVALID", "An onboarding answer must be text, a text list, or null.")
        if FIELD_BY_ID[field_id]["input_type"] == "tags" and isinstance(answer, str):
            answer = _split_values(answer)
        if status == "CONFIRMED" and answer in (None, "", []):
            raise JobOpsError("ONBOARDING_ANSWER_REQUIRED", "A confirmed onboarding answer needs a value.", field_id=field_id)
        if field_id in {"github_url", "portfolio_url"} and status == "CONFIRMED":
            if not isinstance(answer, str) or not re.fullmatch(r"https://[^\s]{3,2000}", answer):
                raise JobOpsError(
                    "ONBOARDING_PUBLIC_URL_INVALID",
                    "A public profile link must be a complete HTTPS URL.",
                    field_id=field_id,
                )
        if status in {"PREFER_NOT_TO_ANSWER", "NOT_APPLICABLE", "UNKNOWN"}:
            answer = None
        if field_id in ALWAYS_CONFIRM_FIELDS:
            policy = "confirm_each_application"
        if status == "PREFER_NOT_TO_ANSWER":
            policy = "prefer_not_to_answer"
        if policy == "do_not_store":
            answer = None
            status = "PREFER_NOT_TO_ANSWER"
        return {
            "value": answer, "status": status, "source": "APPLICANT_CONFIRMED" if status != "UNKNOWN" else "UNKNOWN",
            "use_policy": policy, "updated_at": iso_utc(),
        }

    @_synchronized
    def save_answers(self, payload: dict[str, Any]) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        locale = payload.get("locale")
        if locale in {"zh", "en"}:
            state["locale"] = locale
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise JobOpsError("ONBOARDING_ANSWERS_INVALID", "The save request must contain an answers object.")
        for field_id, value in answers.items():
            state["answers"][field_id] = self._validate_answer(field_id, value)
        answer_bank = {
            "schema_version": 2, "status": IN_PROGRESS, "locale": state["locale"],
            "answers": state["answers"], "completion": self._completion(state["answers"]), "updated_at": iso_utc(),
        }
        validate_named("onboarding-answer-bank", answer_bank, self.schemas)
        answer_ref = state.get("answer_bank_ref")
        previous_answer: bytes | None = None
        created_references: list[str] = []
        rotated_reference: str | None = None
        try:
            if answer_ref:
                previous_answer = self.onboarding.read_bytes(str(answer_ref))
                self.onboarding.rotate(str(answer_ref), canonical_json(answer_bank))
                rotated_reference = str(answer_ref)
            else:
                record = self.onboarding.import_bytes("answer_bank", canonical_json(answer_bank), synthetic=False)
                answer_ref = str(record["secure_ref"])
                created_references.append(answer_ref)
                state["answer_bank_ref"] = answer_ref
            self._save_state(reference, state)
        except Exception as exc:
            if isinstance(exc, JobOpsError) and exc.code in {
                "ONBOARDING_STATE_INDEX_ROLLBACK_FAILED", "PRIVATE_ROTATION_ROLLBACK_FAILED",
            }:
                raise JobOpsError(
                    "ONBOARDING_ANSWER_SAVE_ROLLBACK_FAILED",
                    "The Answer Bank save stopped with an indeterminate encrypted-state commit; references were retained for repair.",
                ) from exc
            try:
                self._rollback_private_writes(
                    created_references,
                    rotated_reference=rotated_reference,
                    previous_value=previous_answer,
                )
            except Exception as rollback_error:
                raise JobOpsError(
                    "ONBOARDING_ANSWER_SAVE_ROLLBACK_FAILED",
                    "The Answer Bank save failed and its prior encrypted version could not be fully restored.",
                ) from rollback_error
            raise JobOpsError(
                "ONBOARDING_ANSWER_SAVE_FAILED",
                "The Answer Bank save did not commit, so all encrypted references were restored.",
            ) from exc
        return {"status": "SAVED", "completion": answer_bank["completion"], "private_values_emitted": 0, "real_external_actions": 0}

    @_synchronized
    def accept_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        suggestion = next((item for item in state.get("suggestions", []) if item.get("suggestion_id") == suggestion_id), None)
        if suggestion is None:
            raise JobOpsError("ONBOARDING_SUGGESTION_MISSING", "The selected suggestion no longer exists.")
        field_id = str(suggestion["field_id"])
        state["answers"][field_id] = {
            "value": suggestion["value"], "status": "CONFIRMED", "source": "APPLICANT_CONFIRMED",
            "use_policy": FIELD_BY_ID[field_id]["default_policy"], "updated_at": iso_utc(),
        }
        suggestion["accepted"] = True
        self._save_state(reference, state)
        return {"status": "SUGGESTION_ACCEPTED", "field_id": field_id, "private_values_emitted": 0}

    def _extract_text(self, data: bytes, extension: str, source_type: str) -> tuple[str, int, dict[str, Any]]:
        if source_type == "chatgpt_export":
            if extension != ".zip":
                raise JobOpsError("CHATGPT_EXPORT_FORMAT_INVALID", "The official ChatGPT export must be uploaded as ZIP.")
            return _chatgpt_export_text(data)
        if extension == ".docx":
            return _docx_text(data), 0, {"document_page_count": None}
        if extension == ".pdf":
            with self.onboarding.staging_directory() as staging:
                target = staging / "source.pdf"
                target.write_bytes(data)
                # PDF generators vary widely: a spatial extraction can preserve
                # columns, while a logical extraction is often better for AI
                # grounding.  Compare both locally and keep only the safer text.
                # No extracted content or original path enters the diagnostics.
                attempts: list[tuple[str, str, int, dict[str, Any]]] = []
                errors: list[JobOpsError] = []
                for mode, layout in (("LOGICAL_READING_ORDER", False), ("SPATIAL_LAYOUT", True)):
                    try:
                        candidate_text, page_count = extract_pdf_text(
                            target,
                            layout=layout,
                            page_limit=MAX_ONBOARDING_PDF_PAGES,
                            character_limit=MAX_DERIVED_TEXT_CHARS,
                        )
                    except JobOpsError as exc:
                        errors.append(exc)
                        continue
                    report = document_text_preflight(
                        candidate_text,
                        extension=extension,
                        page_count=page_count,
                    )
                    attempts.append((mode, candidate_text, page_count, report))
                if not attempts:
                    raise errors[0] if errors else JobOpsError(
                        "ONBOARDING_PDF_EXTRACTION_FAILED",
                        "The PDF could not be read by the bounded local extractors.",
                    )
                selected = max(attempts, key=lambda item: document_quality_rank(item[3]))
                mode, text, page_count, report = selected
                return text, 0, {
                    "document_page_count": page_count,
                    "pdf_extraction_strategy": mode,
                    "pdf_extraction_candidates_compared": len(attempts),
                    "document_quality": report,
                }
        if extension == ".json":
            return _json_text(data), 0, {}
        try:
            return data.decode("utf-8-sig"), 0, {}
        except UnicodeDecodeError as exc:
            raise JobOpsError("ONBOARDING_TEXT_ENCODING_INVALID", "Text onboarding sources must be UTF-8 compatible.") from exc

    def _analyze_prepared_text(
        self,
        *,
        source_type: str,
        extension: str,
        source_hash: str,
        text: str,
        excluded_secret_fragments: int,
        raw_data: bytes | None,
        intake_mode: str,
        source_selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.ai_engine.ready:
            raise JobOpsError(
                "AI_ENGINE_REQUIRED",
                "AI analysis is required before any extracted content may enter Claim review. No source content was imported.",
            )
        source_id = stable_id("SRC", source_type, source_hash)
        text = _clean_text(text, limit=MAX_DERIVED_TEXT_CHARS + 1)
        if len(text) > MAX_DERIVED_TEXT_CHARS:
            raise JobOpsError(
                "ONBOARDING_DERIVED_TEXT_TOO_LARGE",
                "The readable source exceeds the bounded complete-analysis limit; no partial source was imported.",
                maximum_characters=MAX_DERIVED_TEXT_CHARS,
            )
        if not text:
            raise JobOpsError("ONBOARDING_SOURCE_TEXT_EMPTY", "The source did not contain readable text for AI analysis.")
        source_status = "AI_FILTERED_REQUIRES_CONFIRMATION"
        suggestions: list[dict[str, Any]] = []
        raw_retained = source_type != "chatgpt_export"
        if source_type != "chatgpt_export":
            assert_no_plaintext_secret(text)
        document_quality = (source_selection or {}).get("document_quality")
        try:
            candidates, extraction_summary = self.ai_engine.analyze_document(
                text, source_id=source_id, source_type=source_type,
            )
        except JobOpsError as exc:
            if exc.code.startswith("AI_"):
                raise JobOpsError(
                    exc.code,
                    exc.message,
                    **{
                        **exc.details,
                        "failure_category": safe_ai_failure_category(exc.code, exc.details),
                        "document_quality": document_quality,
                    },
                ) from exc
            raise
        if not _ai_analysis_is_complete(extraction_summary):
            raise JobOpsError(
                "AI_ANALYSIS_INCOMPLETE",
                "The AI did not prove complete coverage of the bounded source, so no partial result was imported.",
                analysis_mode=extraction_summary.get("analysis_mode"),
                quality_contract=extraction_summary.get("quality_contract"),
                ai_chunks=extraction_summary.get("ai_chunks"),
                ai_input_characters=extraction_summary.get("ai_input_characters"),
                ai_covered_characters=extraction_summary.get("ai_covered_characters"),
                ai_input_truncated=extraction_summary.get("ai_input_truncated"),
            )
        if source_type == "chatgpt_export":
            derived = {
                "schema_version": 2, "source_id": source_id, "source_type": source_type,
                "source_sha256": source_hash, "analysis_mode": STRICT_AI_ANALYSIS_MODE,
                "candidate_count": len(candidates), "suggestions": [],
                "excluded_secret_fragments": excluded_secret_fragments, "raw_retained": False,
                "intake_mode": intake_mode,
                "source_selection": source_selection or {},
            }
            stored = self.onboarding.import_bytes("onboarding_ai_derived", canonical_json(derived), synthetic=False)
        else:
            if raw_data is None:
                raise JobOpsError("ONBOARDING_SOURCE_DATA_MISSING", "The retained source content is unavailable.")
            kind = "onboarding_ai_derived" if source_type == "ai_summary" else "onboarding_source_document"
            stored = self.onboarding.import_bytes(kind, raw_data, synthetic=False)
        extraction_summary = {
            "raw_lines": len(text.splitlines()),
            "reconstructed_blocks": extraction_summary.get("ai_entities", 0),
            "filtered_noise_lines": 0,
            "claim_candidates": len(candidates),
            "non_claim_blocks": 0,
            "intake_mode": intake_mode,
            **(source_selection or {}),
            **extraction_summary,
        }
        return {
            "source_id": source_id, "source_type": source_type, "extension": extension,
            "source_hash": source_hash, "source_status": source_status, "suggestions": suggestions,
            "candidates": candidates, "extraction_summary": extraction_summary,
            "raw_retained": raw_retained, "excluded_secret_fragments": excluded_secret_fragments,
            "secure_ref": str(stored["secure_ref"]),
        }

    def _prepare_source(self, source_type: str, extension: str, data: bytes) -> dict[str, Any]:
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise JobOpsError("ONBOARDING_SOURCE_TYPE_INVALID", "The onboarding source type is not supported.")
        extension = extension.casefold().strip()
        if not extension.startswith("."):
            extension = "." + extension
        if extension not in ALLOWED_EXTENSIONS:
            raise JobOpsError("ONBOARDING_SOURCE_FORMAT_INVALID", "The onboarding source format is not supported.")
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise JobOpsError("ONBOARDING_SOURCE_SIZE_INVALID", "The onboarding source is empty or exceeds the local size limit.")
        if source_type != "chatgpt_export" and len(data) > MAX_RETAINED_SOURCE_BYTES:
            raise JobOpsError(
                "ONBOARDING_SOURCE_SIZE_INVALID",
                "Retained resume, project, supporting and AI-summary sources may not exceed 64 MiB.",
            )
        if source_type == "chatgpt_export" and len(data) > LARGE_EXPORT_THRESHOLD_BYTES:
            raise JobOpsError(
                "CHATGPT_EXPORT_LIGHTNING_REQUIRED",
                "ChatGPT exports over 200 MB must use the streaming large-file option.",
                threshold_bytes=LARGE_EXPORT_THRESHOLD_BYTES,
            )
        source_hash = sha256_bytes(data)
        text, excluded_secret_fragments, source_selection = self._extract_text(data, extension, source_type)
        if source_type != "chatgpt_export":
            source_selection = dict(source_selection or {})
            quality = source_selection.get("document_quality") or document_text_preflight(
                text, extension=extension, page_count=source_selection.get("document_page_count"),
            )
            source_selection["document_quality"] = quality
            if quality["status"] == "FAIL":
                raise JobOpsError(
                    "ONBOARDING_DOCUMENT_QUALITY_FAILED",
                    "The local document extraction quality is too low for grounded AI analysis. Nothing was imported.",
                    document_quality=quality,
                )
        return self._analyze_prepared_text(
            source_type=source_type,
            extension=extension,
            source_hash=source_hash,
            text=text,
            excluded_secret_fragments=excluded_secret_fragments,
            raw_data=data,
            intake_mode="STANDARD_MEMORY",
            source_selection=source_selection,
        )

    def _prepare_large_chatgpt_export(
        self,
        path: Path,
        *,
        extension: str,
        source_hash: str,
        upload_size: int,
    ) -> dict[str, Any]:
        extension = extension.casefold().strip()
        if extension != ".zip":
            raise JobOpsError("CHATGPT_EXPORT_FORMAT_INVALID", "The large ChatGPT export must be a ZIP file.")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash):
            raise JobOpsError("ONBOARDING_SOURCE_HASH_INVALID", "The streamed upload hash is invalid.")
        if not 1 <= int(upload_size) <= MAX_LARGE_EXPORT_BYTES:
            raise JobOpsError("ONBOARDING_SOURCE_SIZE_INVALID", "The large ChatGPT export exceeds the local safety limit.")
        resolved = path.resolve(strict=True)
        staging_root = (self.onboarding.store.private_root / "staging").resolve(strict=True)
        if staging_root not in resolved.parents or resolved.stat().st_size != int(upload_size):
            raise JobOpsError("ONBOARDING_STAGING_BOUNDARY_INVALID", "The streamed upload left its controlled private staging boundary.")
        text, excluded_secret_fragments, source_selection = _chatgpt_export_text_path(resolved)
        if resolved.stat().st_size != int(upload_size):
            raise JobOpsError("ONBOARDING_STAGING_FILE_CHANGED", "The streamed upload changed during analysis.")
        return self._analyze_prepared_text(
            source_type="chatgpt_export",
            extension=extension,
            source_hash=source_hash,
            text=text,
            excluded_secret_fragments=excluded_secret_fragments,
            raw_data=None,
            intake_mode="LIGHTNING_STREAM",
            source_selection=source_selection,
        )

    @staticmethod
    def _source_metadata(prepared: dict[str, Any], *, ordinal: int) -> dict[str, Any]:
        return {
            "source_id": prepared["source_id"], "category": prepared["source_type"],
            "extension": prepared["extension"],
            "safe_display_name": f"{prepared['source_type']}-{ordinal:02d}{prepared['extension'] if prepared['source_type'] != 'chatgpt_export' else '.derived'}",
            "secure_ref": prepared["secure_ref"], "sha256": prepared["source_hash"],
            "source_status": prepared["source_status"], "suggestion_count": len(prepared["suggestions"]),
            "fact_count": len(prepared["candidates"]), "raw_retained": prepared["raw_retained"],
            "excluded_secret_fragments": prepared["excluded_secret_fragments"],
            "extraction_summary": prepared["extraction_summary"],
            "analysis_mode": prepared["extraction_summary"].get("analysis_mode", "NOT_APPLICABLE"),
            "imported_at": iso_utc(),
        }

    def _commit_pending_source(self, state: dict[str, Any], pending: dict[str, Any], selections: list[dict[str, Any]] | None) -> dict[str, int]:
        source_id = str(pending["source_id"])
        if not _ai_analysis_is_complete(pending.get("extraction_summary")):
            raise JobOpsError(
                "AI_ANALYSIS_REQUIRED",
                "Only content that passed strict AI entity analysis with complete source coverage can enter Claim review.",
            )
        by_id = {str(item["candidate_id"]): item for item in pending.get("candidates", [])}
        selected: list[dict[str, Any]] = []
        if selections is None:
            selections = [
                {"candidate_id": item["candidate_id"], "selected": bool(item.get("selected", False)), "statement": item["statement"], "category": item["category"]}
                for item in by_id.values()
            ]
        for choice in selections:
            if not isinstance(choice, dict) or str(choice.get("candidate_id")) not in by_id:
                raise JobOpsError("SOURCE_PREVIEW_SELECTION_INVALID", "A source preview selection is invalid.")
            if choice.get("selected") is not True:
                continue
            original = by_id[str(choice["candidate_id"])]
            statement = _clean_text(str(choice.get("statement", original["statement"])), limit=2_000)
            category = _clean_text(str(choice.get("category", original["category"])), limit=80).casefold()
            if not _complete_claim_text(statement) or category not in ALLOWED_CATEGORIES:
                raise JobOpsError("SOURCE_PREVIEW_SELECTION_INVALID", "Selected Claim text or category is invalid.")
            if original.get("entity") and category not in ENTITY_CATEGORIES:
                raise JobOpsError("SOURCE_PREVIEW_SELECTION_INVALID", "An experience Claim must remain work, internship, education, or project.")
            if not original.get("entity") and category in ENTITY_CATEGORIES:
                raise JobOpsError("SOURCE_PREVIEW_SELECTION_INVALID", "A standalone Claim cannot be converted into an ungrounded experience entity.")
            selected_item = {**original, "statement": statement, "category": category}
            if original.get("entity") and category != original["entity"].get("entity_type"):
                revised_entity = {**original["entity"], "entity_type": category}
                identity = "|".join(str(revised_entity.get(key) or "").casefold() for key in ("entity_type", "organization", "role", "start_date", "end_date"))
                revised_entity["entity_fingerprint"] = stable_id("ENTKEY", identity)
                revised_entity["entity_id"] = stable_id("ENT", source_id, identity)
                selected_item["entity"] = revised_entity
                selected_item["entity_id"] = revised_entity["entity_id"]
                selected_item["applicant_category_override"] = True
            selected.append(selected_item)
        if pending.get("replace_existing"):
            state["material_claims"] = [item for item in state.get("material_claims", []) if item.get("source_id") != source_id]
            state["suggestions"] = [item for item in state.get("suggestions", []) if item.get("source_id") != source_id]
        existing = next((item for item in state["sources"] if item["source_id"] == source_id), None)
        if existing is None:
            state["sources"].append(deepcopy(pending["metadata"]))
        else:
            existing.update(deepcopy(pending["metadata"]))
        known_claims = {item["claim_id"] for item in state.get("material_claims", [])}
        active_by_signature: dict[tuple[str, str, str], dict[str, Any]] = {}
        for claim in state.get("material_claims", []):
            if not _is_ai_qualified_claim(claim) or claim.get("deleted"):
                continue
            entity_fingerprint = str((claim.get("entity") or {}).get("entity_fingerprint") or "")
            active_by_signature[(claim["category"], entity_fingerprint, _claim_signature(str(claim.get("statement", ""))))] = claim
        duplicates = 0
        for item in selected:
            entity_fingerprint = str((item.get("entity") or {}).get("entity_fingerprint") or "")
            signature = (item["category"], entity_fingerprint, _claim_signature(item["statement"]))
            duplicate = active_by_signature.get(signature)
            if duplicate is not None:
                source_ids = duplicate.setdefault("additional_source_ids", [])
                if source_id not in source_ids and source_id != duplicate.get("source_id"):
                    source_ids.append(source_id)
                duplicates += 1
                continue
            claim_id = stable_id("CLM", source_id, item["statement"])
            if claim_id in known_claims:
                duplicates += 1
                continue
            claim = {
                "claim_id": claim_id, "category": item["category"], "statement": item["statement"],
                "source_ref": pending["metadata"]["secure_ref"], "source_id": source_id,
                "source_candidate_id": item["candidate_id"], "provenance": item.get("provenance"),
                "provenance_claim_ids": [], "decision": "PENDING", "approved_for_external": False,
                "deleted": False, "ai_validated": True, "analysis_mode": STRICT_AI_ANALYSIS_MODE,
                "confidence": item.get("confidence", "LOW"), "claim_kind": item.get("claim_kind"),
                "entity_id": item.get("entity_id"), "entity": deepcopy(item.get("entity")),
                "applicant_category_override": bool(item.get("applicant_category_override")),
            }
            state["material_claims"].append(claim)
            known_claims.add(claim_id)
            active_by_signature[signature] = claim
        known_suggestions = {item["suggestion_id"] for item in state.get("suggestions", [])}
        state["suggestions"].extend(item for item in pending.get("suggestions", []) if item["suggestion_id"] not in known_suggestions)
        state["profile_review"] = "PENDING"
        self._refresh_field_conflicts(state)
        return {
            "selected_claims": len(selected) - duplicates,
            "duplicate_claims_merged": duplicates,
            "suggestions": len(pending.get("suggestions", [])),
        }

    @_synchronized
    def preview_source(self, source_type: str, extension: str, data: bytes) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        prepared = self._prepare_source(source_type, extension, data)
        return self._save_source_preview(reference, state, prepared)

    @_synchronized
    def preview_large_chatgpt_export(
        self,
        path: Path,
        *,
        extension: str,
        source_hash: str,
        upload_size: int,
    ) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        prepared = self._prepare_large_chatgpt_export(
            path,
            extension=extension,
            source_hash=source_hash,
            upload_size=upload_size,
        )
        return self._save_source_preview(reference, state, prepared)

    def _save_source_preview(
        self,
        reference: str,
        state: dict[str, Any],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self._source_metadata(prepared, ordinal=len(state["sources"]) + 1)
        pending = {
            "source_id": prepared["source_id"], "metadata": metadata,
            "candidates": prepared["candidates"], "suggestions": prepared["suggestions"],
            "extraction_summary": prepared["extraction_summary"], "replace_existing": False,
        }
        state["pending_sources"] = [item for item in state.get("pending_sources", []) if item.get("source_id") != prepared["source_id"]]
        state["pending_sources"].append(pending)
        prepared_reference = str(prepared["secure_ref"])
        try:
            self._save_state(reference, state)
        except Exception as exc:
            if isinstance(exc, JobOpsError) and exc.code in {
                "ONBOARDING_STATE_INDEX_ROLLBACK_FAILED", "PRIVATE_ROTATION_ROLLBACK_FAILED",
            }:
                raise JobOpsError(
                    "SOURCE_PREVIEW_ROLLBACK_FAILED",
                    "The source preview stopped with an indeterminate encrypted-state commit; its private reference was retained for repair.",
                ) from exc
            try:
                self._rollback_private_writes([prepared_reference])
            except Exception as rollback_error:
                raise JobOpsError(
                    "SOURCE_PREVIEW_ROLLBACK_FAILED",
                    "The source preview failed and its partial encrypted reference could not be removed.",
                ) from rollback_error
            raise JobOpsError(
                "SOURCE_PREVIEW_SAVE_FAILED",
                "The source preview did not commit, so its new encrypted reference was removed.",
            ) from exc
        return {
            "status": "SOURCE_PREVIEW_READY", "source_id": prepared["source_id"],
            "safe_display_name": metadata["safe_display_name"],
            "candidate_count": len(prepared["candidates"]), "suggestion_count": len(prepared["suggestions"]),
            "extraction_summary": prepared["extraction_summary"], "private_values_emitted": 0,
            "real_external_actions": 0,
        }

    @_synchronized
    def commit_source(self, source_id: str, selections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        pending = next((item for item in state.get("pending_sources", []) if item.get("source_id") == source_id), None)
        if pending is None:
            raise JobOpsError("SOURCE_PREVIEW_MISSING", "The selected source preview no longer exists.")
        counts = self._commit_pending_source(state, pending, selections)
        master = self._designate_master_resume(state, pending)
        state["pending_sources"] = [item for item in state.get("pending_sources", []) if item.get("source_id") != source_id]
        self._save_state(reference, state)
        return {
            "status": "SOURCE_SECURELY_IMPORTED", "source_id": source_id, **counts,
            "master_resume_designated": master is not None,
            "editable_master_docx": bool(master and master.get("editable_docx")),
            "private_values_emitted": 0, "real_external_actions": 0,
        }

    @_synchronized
    def discard_source_preview(self, source_id: str) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        pending = next((item for item in state.get("pending_sources", []) if item.get("source_id") == source_id), None)
        if pending is None:
            raise JobOpsError("SOURCE_PREVIEW_MISSING", "The selected source preview no longer exists.")
        previous_state = deepcopy(state)
        state["pending_sources"] = [item for item in state.get("pending_sources", []) if item.get("source_id") != source_id]
        self._save_state(reference, state)
        if not pending.get("replace_existing") and not any(item.get("source_id") == source_id for item in state.get("sources", [])):
            try:
                self.onboarding.delete(str(pending["metadata"]["secure_ref"]), user_confirmed=True)
            except Exception as exc:
                try:
                    self._save_state(reference, previous_state)
                except Exception as rollback_error:
                    raise JobOpsError(
                        "SOURCE_PREVIEW_DISCARD_ROLLBACK_FAILED",
                        "The preview could not be discarded and its encrypted state could not be restored.",
                    ) from rollback_error
                raise JobOpsError(
                    "SOURCE_PREVIEW_PRIVATE_DELETE_FAILED",
                    "The private preview could not be deleted, so the preview was restored for a safe retry.",
                ) from exc
        return {"status": "SOURCE_PREVIEW_DISCARDED", "source_id": source_id, "real_external_actions": 0}

    @_synchronized
    def delete_source(self, source_id: str, *, user_confirmed: bool) -> dict[str, Any]:
        if not user_confirmed:
            raise JobOpsError("SOURCE_DELETE_CONFIRMATION_REQUIRED", "Deleting an imported source requires explicit confirmation.")
        reference, state = self.ensure_state()
        self._assert_editable(state)
        source = next((item for item in state.get("sources", []) if item.get("source_id") == source_id), None)
        if source is None:
            raise JobOpsError("SOURCE_MISSING", "The selected imported source no longer exists.")
        previous_state = deepcopy(state)
        remaining_sources = {
            str(item.get("source_id")): item for item in state.get("sources", [])
            if item.get("source_id") != source_id
        }
        direct_claim_ids: set[str] = set()
        retained_shared_claims = 0
        for item in state.get("material_claims", []):
            alternatives = [
                str(value) for value in item.get("additional_source_ids", [])
                if str(value) != source_id and str(value) in remaining_sources
            ]
            if item.get("source_id") == source_id:
                if alternatives:
                    replacement = alternatives.pop(0)
                    item["source_id"] = replacement
                    item["source_ref"] = remaining_sources[replacement].get("secure_ref")
                    item["additional_source_ids"] = alternatives
                    retained_shared_claims += 1
                elif item.get("claim_id"):
                    direct_claim_ids.add(str(item["claim_id"]))
            elif source_id in item.get("additional_source_ids", []):
                item["additional_source_ids"] = alternatives
        removed_claim_ids = set(direct_claim_ids)
        changed = True
        while changed:
            changed = False
            for item in state.get("material_claims", []):
                claim_id = str(item.get("claim_id", ""))
                parents = {str(value) for value in item.get("provenance_claim_ids", [])}
                if claim_id and claim_id not in removed_claim_ids and parents & removed_claim_ids:
                    removed_claim_ids.add(claim_id)
                    changed = True
        before_suggestions = len(state.get("suggestions", []))
        state["sources"] = [item for item in state.get("sources", []) if item.get("source_id") != source_id]
        state["pending_sources"] = [item for item in state.get("pending_sources", []) if item.get("source_id") != source_id]
        state["material_claims"] = [
            item for item in state.get("material_claims", []) if str(item.get("claim_id")) not in removed_claim_ids
        ]
        state["suggestions"] = [item for item in state.get("suggestions", []) if item.get("source_id") != source_id]
        state["profile_review"] = "PENDING"
        if isinstance(state.get("master_resume"), dict) and state["master_resume"].get("source_id") == source_id:
            state["master_resume"] = None
        self._refresh_field_conflicts(state)
        self._save_state(reference, state)
        secure_ref = str(source.get("secure_ref", ""))
        ciphertext_deleted = False
        if secure_ref and not any(item.get("secure_ref") == secure_ref for item in state.get("sources", [])):
            try:
                self.onboarding.delete(secure_ref, user_confirmed=True)
                ciphertext_deleted = True
            except Exception as exc:
                try:
                    self._save_state(reference, previous_state)
                except Exception as rollback_error:
                    raise JobOpsError(
                        "SOURCE_DELETE_ROLLBACK_FAILED",
                        "The source could not be deleted and its encrypted onboarding state could not be restored.",
                    ) from rollback_error
                raise JobOpsError(
                    "SOURCE_PRIVATE_DELETE_FAILED",
                    "The private source could not be deleted, so the source and its review state were restored for a safe retry.",
                ) from exc
        return {
            "status": "SOURCE_DELETED",
            "source_id": source_id,
            "removed_claims": len(removed_claim_ids),
            "retained_shared_claims": retained_shared_claims,
            "removed_suggestions": before_suggestions - len(state.get("suggestions", [])),
            "private_ciphertext_deleted": ciphertext_deleted,
            "secure_erase_claimed": False,
            "real_external_actions": 0,
        }

    @_synchronized
    def reprocess_source(self, source_id: str) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        source = next((item for item in state.get("sources", []) if item.get("source_id") == source_id), None)
        if source is None or not source.get("raw_retained"):
            raise JobOpsError("SOURCE_REPROCESS_UNAVAILABLE", "This source cannot be reprocessed from retained local content.")
        data = self.onboarding.read_bytes(str(source["secure_ref"]))
        extension = str(source.get("extension") or Path(str(source.get("safe_display_name", ""))).suffix)
        prepared = self._prepare_source(str(source["category"]), extension, data)
        duplicate_ref = str(prepared.get("secure_ref", ""))
        if duplicate_ref and duplicate_ref != str(source.get("secure_ref", "")):
            self.onboarding.delete(duplicate_ref, user_confirmed=True)
        prepared["secure_ref"] = str(source["secure_ref"])
        pending = {
            "source_id": source_id, "metadata": {
                **deepcopy(source), "fact_count": len(prepared["candidates"]),
                "extraction_summary": prepared["extraction_summary"],
                "analysis_mode": prepared["extraction_summary"].get("analysis_mode"),
                "source_status": prepared["source_status"],
            },
            "candidates": prepared["candidates"], "suggestions": prepared["suggestions"],
            "extraction_summary": prepared["extraction_summary"], "replace_existing": True,
        }
        state["pending_sources"] = [item for item in state.get("pending_sources", []) if item.get("source_id") != source_id]
        state["pending_sources"].append(pending)
        self._save_state(reference, state)
        return {
            "status": "SOURCE_PREVIEW_READY",
            "source_id": source_id,
            "candidate_count": len(prepared["candidates"]),
            "ai_repair_attempted": bool(prepared["extraction_summary"].get("ai_repair_attempted")),
            "private_values_emitted": 0,
            "real_external_actions": 0,
        }

    @_synchronized
    def import_source(self, source_type: str, extension: str, data: bytes) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        prepared = self._prepare_source(source_type, extension, data)
        existing_source = next((item for item in state["sources"] if item["source_id"] == prepared["source_id"]), None)
        metadata = self._source_metadata(prepared, ordinal=len(state["sources"]) + 1)
        pending = {
            "source_id": prepared["source_id"], "metadata": metadata,
            "candidates": prepared["candidates"], "suggestions": prepared["suggestions"],
            "extraction_summary": prepared["extraction_summary"], "replace_existing": False,
        }
        counts = self._commit_pending_source(state, pending, [
            {"candidate_id": item["candidate_id"], "selected": True, "statement": item["statement"], "category": item["category"]}
            for item in prepared["candidates"]
        ])
        master = self._designate_master_resume(state, pending)
        prepared_reference = str(prepared["secure_ref"])
        try:
            self._save_state(reference, state)
        except Exception as exc:
            if isinstance(exc, JobOpsError) and exc.code in {
                "ONBOARDING_STATE_INDEX_ROLLBACK_FAILED", "PRIVATE_ROTATION_ROLLBACK_FAILED",
            }:
                raise JobOpsError(
                    "SOURCE_IMPORT_ROLLBACK_FAILED",
                    "The source import stopped with an indeterminate encrypted-state commit; its private reference was retained for repair.",
                ) from exc
            try:
                self._rollback_private_writes([prepared_reference])
            except Exception as rollback_error:
                raise JobOpsError(
                    "SOURCE_IMPORT_ROLLBACK_FAILED",
                    "The source import failed and its partial encrypted reference could not be removed.",
                ) from rollback_error
            raise JobOpsError(
                "SOURCE_IMPORT_SAVE_FAILED",
                "The source import did not commit, so its new encrypted reference was removed.",
            ) from exc
        return {
            "status": "SOURCE_SECURELY_IMPORTED", "source_id": prepared["source_id"], "source_type": source_type,
            "safe_display_name": existing_source["safe_display_name"] if existing_source else metadata["safe_display_name"],
            "source_status": prepared["source_status"], "suggestion_count": len(prepared["suggestions"]), "fact_count": len(prepared["candidates"]),
            "raw_retained": prepared["raw_retained"], "excluded_secret_fragments": prepared["excluded_secret_fragments"],
            "master_resume_designated": master is not None,
            "editable_master_docx": bool(master and master.get("editable_docx")),
            **counts,
            "private_values_emitted": 0, "real_external_actions": 0,
        }

    @_synchronized
    def approve_external_claims(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("user_confirmed") is not True:
            raise JobOpsError(
                "EXTERNAL_CLAIM_CONFIRMATION_REQUIRED",
                "Using confirmed Claim wording in resumes and application materials requires explicit approval.",
            )
        expected_review_hash = str(payload.get("expected_review_hash", ""))
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_review_hash):
            raise JobOpsError("EXTERNAL_CLAIM_REVIEW_HASH_INVALID", "The external Claim review binding is invalid.")
        uses = payload.get("allowed_uses")
        if not isinstance(uses, list) or not all(isinstance(item, str) for item in uses):
            raise JobOpsError("EXTERNAL_CLAIM_USES_INVALID", "External Claim uses must be a list.")
        state_ref, state = self.ensure_state()
        if state.get("status") != COMPLETE:
            raise JobOpsError("ONBOARDING_INCOMPLETE", "Complete onboarding before approving Claims for materials.")
        master, claims, review_hash, profile_ref = self._claim_approval_context(state_ref, state)
        if master is None:
            raise JobOpsError("MASTER_RESUME_MISSING", "A secure Master Resume is required before external Claim approval.")
        if not claims:
            raise JobOpsError("CONFIRMED_CLAIMS_MISSING", "Confirm at least one Claim before external use approval.")
        if profile_ref is None:
            raise JobOpsError("CANDIDATE_PROFILE_MISSING", "The completed encrypted Candidate Profile is missing.")
        if review_hash != expected_review_hash:
            raise JobOpsError("EXTERNAL_CLAIM_REVIEW_STALE", "The Claim review changed; review the current wording again.")
        current = self._external_claim_status(state_ref, state, review_hash, master)
        normalized_uses = sorted(set(uses))
        if current.get("current") and current.get("allowed_uses") == normalized_uses:
            return {
                "status": "EXTERNAL_CLAIMS_ALREADY_APPROVED", "changed": False,
                "claim_count": int(current["claim_count"]), "content_hash": current.get("content_hash"),
                "allowed_uses": normalized_uses, "real_external_actions": 0,
            }
        value = build_external_claim_set(
            onboarding_state_ref=state_ref,
            profile_ref=profile_ref,
            master_resume=master,
            claims=claims,
            allowed_uses=normalized_uses,
            expected_review_hash=expected_review_hash,
        )
        validate_named("external-claim-set", value, self.schemas)
        validate_external_claim_set_integrity(value)
        record = self.onboarding.import_bytes("external_claim_set", canonical_json(value), synthetic=False)
        return {
            "status": "EXTERNAL_CLAIMS_APPROVED", "changed": not bool(record.get("deduplicated")),
            "claim_count": value["claim_count"], "content_hash": value["content_hash"],
            "allowed_uses": value["allowed_uses"], "claim_set_ref": record["secure_ref"],
            "private_values_emitted": 0, "real_external_actions": 0,
        }

    @_synchronized
    def tailoring_manifest_proposal(self) -> dict[str, Any]:
        state_ref, state = self.ensure_state()
        master = self._master_resume_descriptor(state)
        if master is None:
            raise JobOpsError("MASTER_RESUME_MISSING", "Upload a Master Resume before preparing safe editing positions.")
        proposal = self._build_tailoring_proposal(state_ref, state, master)
        return {
            **proposal,
            "source_text_exposed_to_local_session_only": True,
            "private_values_persisted": 0,
            "real_external_actions": 0,
        }

    @_synchronized
    def approve_tailoring_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("user_confirmed") is not True:
            raise JobOpsError("TAILORING_CONFIRMATION_REQUIRED", "Safe resume-editing positions require explicit approval.")
        expected = str(payload.get("expected_proposal_hash", ""))
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
            raise JobOpsError("TAILORING_PROPOSAL_HASH_INVALID", "The tailoring proposal binding is invalid.")
        selections = payload.get("selections")
        if not isinstance(selections, list) or not all(isinstance(item, dict) for item in selections):
            raise JobOpsError("TAILORING_SELECTION_INVALID", "Tailoring selections must be a list of approved positions.")
        state_ref, state = self.ensure_state()
        master = self._master_resume_descriptor(state)
        if master is None:
            raise JobOpsError("MASTER_RESUME_MISSING", "The encrypted Master Resume is missing.")
        proposal = self._build_tailoring_proposal(state_ref, state, master)
        value = build_resume_tailoring_manifest(
            onboarding_state_ref=state_ref,
            master_resume=master,
            proposal=proposal,
            selections=selections,
            expected_proposal_hash=expected,
            user_confirmed=True,
        )
        validate_named("resume-tailoring-manifest", value, self.schemas)
        validate_resume_tailoring_manifest_integrity(value)
        record = self.onboarding.import_bytes("resume_tailoring_manifest", canonical_json(value), synthetic=False)
        return {
            "status": "TAILORING_MANIFEST_APPROVED", "changed": not bool(record.get("deduplicated")),
            "block_count": value["block_count"], "content_hash": value["content_hash"],
            "manifest_ref": record["secure_ref"], "private_values_emitted": 0,
            "real_external_actions": 0,
        }

    @staticmethod
    def _refresh_field_conflicts(state: dict[str, Any]) -> None:
        state["conflict_resolutions"] = {
            key: value for key, value in state.get("conflict_resolutions", {}).items()
            if not value.get("field_id")
        }
        by_field: dict[str, set[str]] = {}
        for item in state.get("suggestions", []):
            if item.get("ai_validated") is not True:
                continue
            encoded = json.dumps(item.get("value"), ensure_ascii=False, sort_keys=True)
            by_field.setdefault(str(item["field_id"]), set()).add(encoded)
        for field_id, values in by_field.items():
            if len(values) < 2:
                continue
            conflict_id = stable_id("CFL", "field", field_id)
            state.setdefault("conflict_resolutions", {}).setdefault(conflict_id, {
                "status": "PENDING", "resolution": None, "manual_value": None, "field_id": field_id,
            })

    @_synchronized
    def save_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        profile_review = payload.get("profile_review")
        if profile_review in {"PENDING", "CONFIRMED"}:
            state["profile_review"] = profile_review
        edits = payload.get("claim_edits", {})
        if not isinstance(edits, dict):
            raise JobOpsError("CLAIM_EDIT_INVALID", "Claim edits must be an object.")
        base_ids = {
            str(item.get("claim_id")) for item in self._claim_bundle().get("claims", [])
            if item.get("claim_id") and _is_ai_qualified_claim(item)
        }
        material = {item["claim_id"]: item for item in state.get("material_claims", []) if _is_ai_qualified_claim(item)}
        for claim_id, edit in edits.items():
            if claim_id not in base_ids and claim_id not in material or not isinstance(edit, dict):
                raise JobOpsError("CLAIM_EDIT_INVALID", "A Claim edit references an unknown Claim.")
            statement = _clean_text(str(edit.get("statement", "")), limit=2_000)
            category = _clean_text(str(edit.get("category", "")), limit=80)
            deleted = edit.get("deleted") is True
            if not deleted and (not _complete_claim_text(statement) or category not in ALLOWED_CATEGORIES):
                raise JobOpsError("CLAIM_EDIT_INVALID", "An active Claim needs text and a category.")
            if claim_id in material:
                entity = material[claim_id].get("entity")
                if entity and category not in ENTITY_CATEGORIES:
                    raise JobOpsError("CLAIM_EDIT_INVALID", "An experience Claim must remain work, internship, education, or project.")
                if not entity and category in ENTITY_CATEGORIES:
                    raise JobOpsError("CLAIM_EDIT_INVALID", "A standalone Claim cannot become an ungrounded experience entity.")
                if entity and category != entity.get("entity_type"):
                    identity = "|".join(str(entity.get(key) or "").casefold() for key in ("organization", "role", "start_date", "end_date"))
                    entity.update({
                        "entity_type": category,
                        "entity_fingerprint": stable_id("ENTKEY", category, identity),
                        "entity_id": stable_id("ENT", str(material[claim_id].get("source_id")), category, identity),
                    })
                    material[claim_id]["entity_id"] = entity["entity_id"]
                    material[claim_id]["applicant_category_override"] = True
                material[claim_id].update({"statement": statement, "category": category, "deleted": deleted})
                material[claim_id]["approved_for_external"] = False
            else:
                state.setdefault("claim_overrides", {})[claim_id] = {
                    "statement": statement, "category": category, "deleted": deleted, "updated_at": iso_utc(),
                }
            if deleted:
                if claim_id in state.get("claim_decisions", {}):
                    state["claim_decisions"][claim_id] = "REJECTED"
                elif claim_id in material:
                    material[claim_id]["decision"] = "REJECTED"
                if claim_id in state.get("conflict_resolutions", {}):
                    state["conflict_resolutions"][claim_id].update({"status": "RESOLVED", "resolution": "EXCLUDE", "manual_value": None})
        decisions = payload.get("claim_decisions", {})
        if not isinstance(decisions, dict):
            raise JobOpsError("CLAIM_REVIEW_INVALID", "Claim decisions must be an object.")
        known = set(state.get("claim_decisions", {}))
        for claim_id, decision in decisions.items():
            if decision not in {"PENDING", "CONFIRMED", "REJECTED"}:
                raise JobOpsError("CLAIM_REVIEW_INVALID", "Claim review decisions must be pending, confirmed, or rejected.")
            if claim_id in known:
                state["claim_decisions"][claim_id] = decision
            elif claim_id in material:
                material[claim_id]["decision"] = decision
            else:
                raise JobOpsError("CLAIM_REVIEW_INVALID", "A Claim decision references an unknown Claim.")
        resolutions = payload.get("conflict_resolutions", {})
        if not isinstance(resolutions, dict):
            raise JobOpsError("CONFLICT_REVIEW_INVALID", "Conflict resolutions must be an object.")
        for conflict_id, resolution in resolutions.items():
            if conflict_id not in state.get("conflict_resolutions", {}) or not isinstance(resolution, dict):
                raise JobOpsError("CONFLICT_REVIEW_INVALID", "A conflict resolution references an unknown conflict.")
            action = str(resolution.get("resolution", ""))
            if action not in {"USE_RESUME", "USE_EVIDENCE", "USE_DIRECT_ANSWER", "EXCLUDE"}:
                raise JobOpsError("CONFLICT_REVIEW_INVALID", "The selected conflict resolution is unsupported.")
            manual = resolution.get("manual_value")
            if manual is not None and (not isinstance(manual, str) or len(manual) > 5_000):
                raise JobOpsError("CONFLICT_REVIEW_INVALID", "A manual conflict value is invalid.")
            state["conflict_resolutions"][conflict_id] = {
                **state["conflict_resolutions"][conflict_id], "status": "RESOLVED",
                "resolution": action, "manual_value": manual.strip() if isinstance(manual, str) else None,
            }
        self._save_state(reference, state)
        return {"status": "REVIEW_SAVED", **self._review_counts(state), "private_values_emitted": 0}

    def _mark_claim_deleted(self, state: dict[str, Any], claim_id: str) -> None:
        material = next((item for item in state.get("material_claims", []) if item.get("claim_id") == claim_id), None)
        if material is not None:
            material["deleted"] = True
            material["decision"] = "REJECTED"
        elif claim_id in state.get("claim_decisions", {}):
            current = state.setdefault("claim_overrides", {}).setdefault(claim_id, {})
            current["deleted"] = True
            state["claim_decisions"][claim_id] = "REJECTED"
        else:
            raise JobOpsError("CLAIM_TRANSFORM_INVALID", "A Claim transformation references an unknown Claim.")
        if claim_id in state.get("conflict_resolutions", {}):
            state["conflict_resolutions"][claim_id].update({"status": "RESOLVED", "resolution": "EXCLUDE", "manual_value": None})

    @_synchronized
    def transform_claims(self, payload: dict[str, Any]) -> dict[str, Any]:
        reference, state = self.ensure_state()
        self._assert_editable(state)
        action = str(payload.get("action", ""))
        claim_ids = payload.get("claim_ids", [])
        if not isinstance(claim_ids, list) or not all(isinstance(item, str) for item in claim_ids):
            raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Claim IDs must be a list.")
        ui_claims = {item["claim_id"]: item for item in self._claims_for_ui(state) if not item.get("deleted")}
        if any(claim_id not in ui_claims for claim_id in claim_ids):
            raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Only active Claims can be transformed.")
        created: list[str] = []
        if action == "MERGE":
            if not 2 <= len(claim_ids) <= 20:
                raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Merge requires between two and twenty Claims.")
            statement = _clean_text(str(payload.get("statement", "")), limit=2_000)
            category = _clean_text(str(payload.get("category", ui_claims[claim_ids[0]]["category"])), limit=80)
            source_categories = {ui_claims[claim_id]["category"] for claim_id in claim_ids}
            entity_fingerprints = {
                str((ui_claims[claim_id].get("entity") or {}).get("entity_fingerprint") or "")
                for claim_id in claim_ids
            }
            if not _complete_claim_text(statement) or category not in ALLOWED_CATEGORIES or source_categories != {category}:
                raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Merged Claim text or category is invalid.")
            if category in {"work", "internship", "education", "project"} and len(entity_fingerprints) != 1:
                raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Experience Claims can only be merged inside the same real-world entity.")
            for claim_id in claim_ids:
                self._mark_claim_deleted(state, claim_id)
            claim_id = stable_id("CLM", "MERGED", *claim_ids, statement)
            state["material_claims"].append({
                "claim_id": claim_id, "category": category, "statement": statement,
                "source_ref": None, "source_id": "derived-merge", "provenance_claim_ids": claim_ids,
                "decision": "PENDING", "approved_for_external": False, "deleted": False,
                "ai_validated": True, "analysis_mode": STRICT_AI_ANALYSIS_MODE,
                "confidence": "LOW", "claim_kind": "summary",
                "entity_id": ui_claims[claim_ids[0]].get("entity_id"),
                "entity": deepcopy(ui_claims[claim_ids[0]].get("entity")),
            })
            created.append(claim_id)
        elif action == "SPLIT":
            statements = payload.get("statements", [])
            if len(claim_ids) != 1 or not isinstance(statements, list) or not 2 <= len(statements) <= 10:
                raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Split requires one Claim and two to ten resulting statements.")
            category = _clean_text(str(payload.get("category", ui_claims[claim_ids[0]]["category"])), limit=80)
            cleaned = [_clean_text(str(item), limit=2_000) for item in statements]
            if category != ui_claims[claim_ids[0]]["category"] or category not in ALLOWED_CATEGORIES or any(not _complete_claim_text(item) for item in cleaned):
                raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Every split Claim needs complete text and a category.")
            self._mark_claim_deleted(state, claim_ids[0])
            for index, statement in enumerate(cleaned, start=1):
                claim_id = stable_id("CLM", "SPLIT", claim_ids[0], str(index), statement)
                state["material_claims"].append({
                    "claim_id": claim_id, "category": category, "statement": statement,
                    "source_ref": None, "source_id": "derived-split", "provenance_claim_ids": claim_ids,
                    "decision": "PENDING", "approved_for_external": False, "deleted": False,
                    "ai_validated": True, "analysis_mode": STRICT_AI_ANALYSIS_MODE,
                    "confidence": "LOW", "claim_kind": ui_claims[claim_ids[0]].get("claim_kind"),
                    "entity_id": ui_claims[claim_ids[0]].get("entity_id"),
                    "entity": deepcopy(ui_claims[claim_ids[0]].get("entity")),
                })
                created.append(claim_id)
        else:
            raise JobOpsError("CLAIM_TRANSFORM_INVALID", "Unsupported Claim transformation.")
        state["profile_review"] = "PENDING"
        self._save_state(reference, state)
        return {"status": "CLAIMS_TRANSFORMED", "action": action, "created_claim_ids": created, "private_values_emitted": 0}

    @_synchronized
    def start_revision(self) -> dict[str, Any]:
        previous_ref, state = self.ensure_state()
        if state.get("status") != COMPLETE:
            return {
                "status": "ONBOARDING_ALREADY_EDITABLE",
                "revision_number": int(state.get("revision_number", 1)),
                "state_ref": previous_ref,
                "changed": False,
                "real_external_actions": 0,
            }
        now = iso_utc()
        revision = deepcopy(state)
        revision.update({
            "schema_version": 3, "status": IN_PROGRESS, "revision_number": int(state.get("revision_number", 1)) + 1,
            "previous_state_ref": previous_ref, "answer_bank_ref": None, "profile_review": "PENDING",
            "pending_sources": [], "created_at": now, "updated_at": now, "completed_at": None,
        })
        answer_bank = {
            "schema_version": 2, "status": IN_PROGRESS, "locale": revision["locale"], "answers": revision["answers"],
            "completion": self._completion(revision["answers"]), "updated_at": now,
        }
        validate_named("onboarding-answer-bank", answer_bank, self.schemas)
        created_references: list[str] = []
        try:
            answer_record = self.onboarding.import_bytes("answer_bank", canonical_json(answer_bank), synthetic=False)
            answer_reference = str(answer_record["secure_ref"])
            created_references.append(answer_reference)
            revision["answer_bank_ref"] = answer_reference
            record = self.onboarding.import_bytes("onboarding_center_state", canonical_json(revision), synthetic=False)
            reference = str(record["secure_ref"])
            created_references.append(reference)
            self._write_index(reference, revision)
        except Exception as exc:
            try:
                self._rollback_private_writes(created_references)
            except Exception as rollback_error:
                raise JobOpsError(
                    "ONBOARDING_REVISION_ROLLBACK_FAILED",
                    "The new onboarding revision failed and its partial encrypted references could not be fully removed.",
                ) from rollback_error
            raise JobOpsError(
                "ONBOARDING_REVISION_WRITE_FAILED",
                "The new onboarding revision did not commit, so its partial encrypted references were removed.",
            ) from exc
        return {
            "status": "ONBOARDING_REVISION_STARTED", "revision_number": revision["revision_number"],
            "state_ref": reference, "previous_state_ref": previous_ref, "changed": True,
            "real_external_actions": 0,
        }

    @_synchronized
    def redacted_status(self) -> dict[str, Any]:
        state_ref, state = self.ensure_state()
        completion = self._completion(state["answers"])
        review = self._review_counts(state)
        return {
            "status": state.get("status", IN_PROGRESS), "state_ref": state_ref,
            "supported_locales": ["zh", "en"], "current_locale": state.get("locale", "zh"),
            "answers": completion, "sources": len(state.get("sources", [])), **review,
            "profile_review": state.get("profile_review", "PENDING"),
            "private_values_emitted": 0, "real_external_actions": 0,
            "next_safe_action": "onboarding-center" if state.get("status") != COMPLETE else "start offline job intake",
        }

    def _review_counts(self, state: dict[str, Any]) -> dict[str, int]:
        claims = self._claims_for_ui(state)
        decisions = [item.get("decision") for item in claims if not item.get("deleted")]
        conflicts = self._conflicts_for_ui(state)
        return {
            "claims_total": len(decisions), "claims_reviewed": sum(value in {"CONFIRMED", "REJECTED"} for value in decisions),
            "conflicts_total": len(conflicts), "conflicts_resolved": sum(item.get("status") == "RESOLVED" for item in conflicts),
        }

    @staticmethod
    def _answer_value(answers: dict[str, Any], field_id: str, default: Any = None) -> Any:
        item = answers[field_id]
        return item.get("value") if item.get("status") == "CONFIRMED" else default

    def _build_profile(self, state: dict[str, Any], profile_ref: str) -> dict[str, Any]:
        base = self._base_profile()
        confirmed_claims = [item for item in self._claims_for_ui(state) if item.get("decision") == "CONFIRMED" and not item.get("deleted")]
        skills = [str(item["statement"]) for item in confirmed_claims if item.get("category") == "skill"]
        languages = [str(item["statement"]) for item in confirmed_claims if item.get("category") == "language"]
        certifications = [str(item["statement"]) for item in confirmed_claims if item.get("category") == "certification"]
        education = " | ".join(str(item["statement"]) for item in confirmed_claims if item.get("category") == "education") or None
        years = [
            int(value)
            for item in confirmed_claims if item.get("category") in {"work", "internship"}
            for value in re.findall(r"\b(20\d{2})\b", " ".join(str((item.get("entity") or {}).get(key) or "") for key in ("start_date", "end_date")))
        ]
        years_experience: int | str = max(0, datetime.now(timezone.utc).year - min(years)) if years else "UNKNOWN"
        name_item = base.get("candidate_display_name")
        display_name = name_item.get("value") if isinstance(name_item, dict) else base.get("candidate_display_name")
        answers = state["answers"]
        authorization = self._answer_value(answers, "work_authorization", "UNKNOWN")
        if authorization == "UNSURE":
            authorization = "UNKNOWN"
        minimum = self._answer_value(answers, "minimum_salary", 0)
        if isinstance(minimum, str):
            match = re.search(r"\d[\d,]*(?:\.\d+)?", minimum)
            minimum = float(match.group(0).replace(",", "")) if match else 0
        profile = {
            "profile_ref": profile_ref, "profile_version": stable_id("PRF", state["updated_at"], profile_ref),
            "candidate_display_name": str(display_name or "UNKNOWN"),
            "target_functions": list(self._answer_value(answers, "target_roles", [])),
            "target_industries": list(self._answer_value(answers, "target_industries", [])),
            "target_levels": list(self._answer_value(answers, "target_levels", [])),
            "locations": list(self._answer_value(answers, "preferred_locations", [])),
            "remote_preference": str(self._answer_value(answers, "remote_preference", "UNKNOWN")),
            "minimum_salary": minimum, "work_authorization": authorization,
            "skills": skills, "languages": languages, "certifications": certifications, "education": education,
            "years_experience": years_experience,
        }
        github_url = self._answer_value(answers, "github_url")
        portfolio_url = self._answer_value(answers, "portfolio_url")
        if github_url:
            profile["github_url"] = str(github_url)
        if portfolio_url:
            profile["portfolio_url"] = str(portfolio_url)
        portfolio_sources = [
            item for item in state.get("sources", [])
            if item.get("category") == "portfolio" and item.get("raw_retained")
        ]
        if portfolio_sources:
            selected_portfolio = portfolio_sources[-1]
            profile.update({
                "portfolio_file_ref": str(selected_portfolio["secure_ref"]),
                "portfolio_file_sha256": str(selected_portfolio["sha256"]),
                "portfolio_file_display_name": str(selected_portfolio["safe_display_name"]),
            })
        validate_named("candidate-profile", profile, self.schemas)
        return profile

    @_synchronized
    def complete(self, *, user_confirmed: bool) -> dict[str, Any]:
        if not user_confirmed:
            raise JobOpsError("ONBOARDING_CONFIRMATION_REQUIRED", "Final onboarding completion requires explicit user confirmation.")
        state_ref, state = self.ensure_state()
        if state.get("status") == COMPLETE:
            raise JobOpsError("ONBOARDING_ALREADY_COMPLETE", "This onboarding packet is already complete.")
        completion = self._completion(state["answers"])
        if completion["remaining"]:
            raise JobOpsError(
                "ONBOARDING_ANSWERS_INCOMPLETE",
                "Every onboarding field needs an answer, not-applicable choice, or disclosure preference.",
                remaining=completion["remaining"], fields=completion["remaining_fields"],
            )
        invalid_hard = [field_id for field_id in HARD_CONTINUITY_FIELDS if state["answers"][field_id]["status"] not in {"CONFIRMED", "NOT_APPLICABLE"}]
        invalid_hard.extend(
            field_id for field_id in EXPLICIT_HARD_FIELDS
            if state["answers"][field_id]["status"] != "CONFIRMED"
            or state["answers"][field_id].get("value") in (None, [], "")
        )
        invalid_hard.extend(
            field_id for field_id in HARD_CONTINUITY_FIELDS
            if isinstance(state["answers"][field_id].get("value"), str)
            and state["answers"][field_id]["value"].upper() in AMBIGUOUS_HARD_VALUES
        )
        invalid_hard = sorted(set(invalid_hard))
        if invalid_hard:
            raise JobOpsError("ONBOARDING_HARD_CONDITIONS_UNRESOLVED", "Hard continuity fields cannot remain undisclosed, not applicable, or ambiguous.", fields=invalid_hard)
        if state.get("profile_review") != "CONFIRMED":
            raise JobOpsError("PROFILE_REVIEW_REQUIRED", "The Candidate Profile review has not been confirmed.")
        if state.get("pending_sources"):
            raise JobOpsError("SOURCE_PREVIEW_PENDING", "Every uploaded source preview must be confirmed or discarded before completion.")
        sources_requiring_ai = [
            str(item.get("source_id")) for item in state.get("sources", [])
            if not _ai_analysis_is_complete(item.get("extraction_summary"))
        ]
        if sources_requiring_ai:
            raise JobOpsError(
                "SOURCE_AI_REANALYSIS_REQUIRED",
                "Every retained source must be re-analyzed by the configured AI engine or deleted before onboarding completes.",
                source_count=len(sources_requiring_ai),
            )
        review = self._review_counts(state)
        if review["claims_reviewed"] != review["claims_total"]:
            raise JobOpsError("CLAIM_REVIEW_INCOMPLETE", "Every Claim candidate must be confirmed or rejected before onboarding completes.")
        if review["conflicts_resolved"] != review["conflicts_total"]:
            raise JobOpsError("CONFLICT_REVIEW_INCOMPLETE", "Every detected conflict must be resolved before onboarding completes.")
        for field_id in ALWAYS_CONFIRM_FIELDS:
            if state["answers"][field_id]["use_policy"] != "confirm_each_application":
                raise JobOpsError("LEGAL_CONFIRMATION_POLICY_INVALID", "Legal and signature answers must remain gated per application.")
        actions = audit_real_external_actions(self.database)
        if actions["real_external_actions"] != 0:
            raise JobOpsError("REAL_EXTERNAL_ACTION_DETECTED", "Onboarding completion detected an external side effect.")
        answer_ref = state.get("answer_bank_ref")
        if not answer_ref:
            raise JobOpsError("ANSWER_BANK_MISSING", "The encrypted Answer Bank is missing.")
        # Create a new immutable profile version.  Never rewrite the profile referenced by
        # a previous review packet: historical evidence bindings must remain reproducible.
        answer_bank = {
            "schema_version": 2, "status": COMPLETE, "locale": state["locale"], "answers": state["answers"],
            "completion": completion, "updated_at": iso_utc(),
        }
        validate_named("onboarding-answer-bank", answer_bank, self.schemas)
        active_claims = self._claims_for_ui(state)
        active_ids = {item["claim_id"] for item in active_claims}
        active_material = [item for item in state.get("material_claims", []) if item.get("claim_id") in active_ids]
        active_conflict_ids = {item["conflict_id"] for item in self._conflicts_for_ui(state)}
        claim_approvals = {
            "schema_version": 1, "status": COMPLETE, "confirmed_at": iso_utc(),
            "decisions": {key: value for key, value in state["claim_decisions"].items() if key in active_ids},
            "material_decisions": {item["claim_id"]: item["decision"] for item in active_material},
            "claim_overrides": {key: value for key, value in state.get("claim_overrides", {}).items() if key in active_ids},
            "material_claims": active_material,
            "conflict_resolutions": {
                key: value for key, value in state["conflict_resolutions"].items() if key in active_conflict_ids
            },
            "quarantined_legacy_claims": (
                len(self._claim_bundle().get("claims", [])) + len(state.get("material_claims", [])) - len(active_claims)
            ),
            "approved_for_external": False,
            "note": "User review is complete; registry promotion remains separately evidence-gated.",
        }
        imported_materials = sum(item["category"] not in AI_SOURCE_TYPES for item in state["sources"])
        existing_master = bool(self._latest_ref("master_resume_docx") or self._latest_ref("master_resume_pdf"))
        if existing_master and not any(item["category"] == "resume" for item in state["sources"]):
            imported_materials += 1
        source_counts = {
            "resume_or_material": imported_materials,
            "ai": sum(item["category"] in AI_SOURCE_TYPES for item in state["sources"]),
            "direct_answers": sum(item["source"] == "APPLICANT_CONFIRMED" for item in state["answers"].values()),
        }
        previous_answer = self.onboarding.read_bytes(str(answer_ref))
        created_references: list[str] = []
        answer_rotated = False
        try:
            profile = self._build_profile(state, "secure-ref:IMPORT_PENDING")
            profile_record = self.onboarding.import_bytes("candidate_profile", canonical_json(profile), synthetic=False)
            profile_ref = str(profile_record["secure_ref"])
            created_references.append(profile_ref)
            profile["profile_ref"] = profile_ref
            validate_named("candidate-profile", profile, self.schemas)
            self.onboarding.rotate(profile_ref, canonical_json(profile))
            self.onboarding.rotate(str(answer_ref), canonical_json(answer_bank))
            answer_rotated = True
            approval_record = self.onboarding.import_bytes("claim_approvals", canonical_json(claim_approvals), synthetic=False)
            approval_ref = str(approval_record["secure_ref"])
            created_references.append(approval_ref)
            packet = {
                "schema_version": 1, "status": COMPLETE, "profile_ref": profile_ref,
                "answer_bank_ref": answer_ref, "claim_approvals_ref": approval_ref,
                "counts": {
                    "answers_resolved": completion["resolved"], "answers_total": completion["total"],
                    "claims_reviewed": review["claims_reviewed"], "claims_total": review["claims_total"],
                    "conflicts_resolved": review["conflicts_resolved"], "conflicts_total": review["conflicts_total"],
                },
                "sources": source_counts, "locale": state["locale"], "completed_at": iso_utc(),
                "real_external_actions": 0, "knowledge_write_operations": 0,
            }
            validate_named("onboarding-completion", packet, self.schemas)
            completion_record = self.onboarding.import_bytes(
                "onboarding_completion_packet", canonical_json(packet), synthetic=False,
            )
            completion_ref = str(completion_record["secure_ref"])
            created_references.append(completion_ref)
            state["status"] = COMPLETE
            state["completed_at"] = packet["completed_at"]
            self._save_state(state_ref, state, completion_ref=completion_ref)
        except Exception as exc:
            if isinstance(exc, JobOpsError) and exc.code in {
                "ONBOARDING_STATE_INDEX_ROLLBACK_FAILED", "PRIVATE_ROTATION_ROLLBACK_FAILED",
            }:
                raise JobOpsError(
                    "ONBOARDING_COMPLETION_ROLLBACK_FAILED",
                    "Onboarding completion stopped with an indeterminate encrypted-state commit; references were retained for repair.",
                ) from exc
            try:
                self._rollback_private_writes(
                    created_references,
                    rotated_reference=str(answer_ref) if answer_rotated else None,
                    previous_value=previous_answer if answer_rotated else None,
                )
            except Exception as rollback_error:
                raise JobOpsError(
                    "ONBOARDING_COMPLETION_ROLLBACK_FAILED",
                    "Onboarding completion failed and its partial encrypted references could not be fully restored.",
                ) from rollback_error
            raise JobOpsError(
                "ONBOARDING_COMPLETION_WRITE_FAILED",
                "Onboarding completion did not commit, so all prior encrypted versions were restored.",
            ) from exc
        return {
            **packet, "completion_ref": completion_ref,
            "private_values_emitted": 0, "staging_residue": 0,
            "next_safe_action": "start offline job intake; real external actions remain disabled",
        }
