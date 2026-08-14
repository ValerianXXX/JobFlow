from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import unicodedata
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .util import stable_id


AI_PROTOCOL_VERSION = 2
AI_QUALITY_CONTRACT = "ENTITY_DEDUPED_LINE_ANCHORED_V6"
MAX_AI_INPUT_CHARS = 500_000
MAX_AI_OUTPUT_BYTES = 5 * 1024 * 1024
MAX_AI_CHUNK_CHARS = 450_000
MAX_AI_LINE_CHARS = 50_000
MAX_AI_CHUNKS = 64
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
ALLOWED_CATEGORIES = {
    "work", "internship", "education", "project", "skill", "certification", "language", "summary",
}
ENTITY_CATEGORIES = {"work", "internship", "education", "project"}
ENTITY_CATEGORY_ALIASES = {
    "employment": "work", "professional": "work", "professional experience": "work",
    "work experience": "work", "experience": "work",
    "intern": "internship", "intern experience": "internship", "internship experience": "internship",
    "academic": "education", "academics": "education", "school": "education",
    "academic experience": "education", "educational experience": "education",
    "case": "project", "engagement": "project", "case study": "project",
    "personal project": "project", "consulting project": "project", "project experience": "project",
}
GENERIC_ENTITY_CANDIDATE_CATEGORIES = {
    "achievement", "accomplishment", "responsibility", "role", "entity", "entity summary",
    "entity_summary",
}
CLAIM_KIND_ALIASES = {
    "role summary": "entity_summary", "role_summary": "entity_summary", "experience": "entity_summary",
    "role": "entity_summary", "entity": "entity_summary",
    "responsibilities": "responsibility", "achievement summary": "achievement",
    "achievements": "achievement", "qualification summary": "qualification",
    "skills": "skill", "professional summary": "summary",
}
ALLOWED_CLAIM_KINDS = {
    "entity_summary", "responsibility", "achievement", "qualification", "skill", "summary",
}
STOP_WORDS = {
    "and", "the", "for", "from", "with", "into", "that", "this", "was", "were", "are", "as", "at", "to", "of",
    "applicant", "candidate", "provided", "statement", "source-grounded",
}
GROUNDING_RATIO = 0.50
NEAR_DUPLICATE_RATIO = 0.85
MAX_GROUNDING_ADJACENT_LINES = 2
MONTH_ALIASES = {
    "january": "jan", "february": "feb", "march": "mar", "april": "apr", "june": "jun",
    "july": "jul", "august": "aug", "september": "sep", "sept": "sep", "october": "oct",
    "november": "nov", "december": "dec",
}
ENTITY_VERB_RE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being|worked|served|led|built|ran|drove|grew|founded|"
    r"managed|created|developed|delivered|conducted|analyzed|analysed|launched|completed|earned|"
    r"studied|graduated|supported|designed|implemented|improved|increased|reduced|produced|owned|"
    r"coordinated|advised|consulted|researched|mapped|converted|translated|oversaw|performed|"
    r"[a-z]{4,}(?:ed|ing|ized|ised|ated|ened))\b",
    re.IGNORECASE,
)
CHINESE_ENTITY_VERBS = ("担任", "负责", "完成", "建立", "领导", "参与", "开发", "管理", "分析", "提升", "创建", "就读", "获得", "交付", "推动", "设计", "实施")
BULLET_PREFIX_RE = re.compile(
    r"^\s*(?:[\u2022\u2023\u25e6\u2043\u2219\uf0de\uf0b7▪▫●○◦‣·*]|[-–—]\s+|\d{1,3}[.)]\s+)"
)
SENTENCE_END_RE = re.compile(r"[.?!。！？][\"'’)）\]]*\s*$")
NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[$£€¥]\s*)?"
    r"(?:\d{1,3}(?:(?:,[ \t]*|[ '\u00a0\u2009\u202f])\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)


def _run_bounded_ai_command(
    command: list[str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
) -> tuple[int, str]:
    """Run a local AI adapter without ever buffering unbounded process output."""
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    encoded_input = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creation_flags,
            cwd=cwd,
        )
    except OSError as exc:
        raise JobOpsError("AI_ENGINE_UNAVAILABLE", "The configured local AI engine could not start.") from exc

    output = bytearray()
    output_exceeded = threading.Event()
    reader_failed = threading.Event()

    def write_input() -> None:
        try:
            if process.stdin is not None:
                process.stdin.write(encoded_input)
        except (BrokenPipeError, OSError):
            # A non-zero exit is handled after process.wait().
            pass
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

    def read_output() -> None:
        try:
            if process.stdout is None:
                reader_failed.set()
                return
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    return
                remaining = (MAX_AI_OUTPUT_BYTES + 1) - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > MAX_AI_OUTPUT_BYTES:
                    output_exceeded.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except OSError:
            reader_failed.set()
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass

    writer = threading.Thread(target=write_input, name="jobflow-ai-input", daemon=True)
    reader = threading.Thread(target=read_output, name="jobflow-ai-output", daemon=True)
    writer.start()
    reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        returncode = process.wait()
    writer.join(timeout=2)
    reader.join(timeout=2)
    if timed_out or writer.is_alive() or reader.is_alive() or reader_failed.is_set():
        raise JobOpsError("AI_ENGINE_UNAVAILABLE", "The configured local AI engine could not complete the analysis.")
    if output_exceeded.is_set():
        raise JobOpsError("AI_ENGINE_FAILED", "The configured local AI engine exceeded the bounded output limit.")
    try:
        return returncode, bytes(output).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise JobOpsError("AI_RESPONSE_INVALID", "The configured local AI engine did not return UTF-8 JSON.") from exc


def _compact(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _tokens(value: str) -> set[str]:
    output = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", value)
        if token.casefold() not in STOP_WORDS
    }
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,12}", value):
        output.add(phrase)
        output.update(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
    return output


def _looks_like_wrapped_pair(left: str, right: str) -> bool:
    """Return true only for a bounded, same-sentence-looking physical line wrap."""

    left_value = unicodedata.normalize("NFKC", left).strip()
    right_value = unicodedata.normalize("NFKC", right).strip()
    if not left_value or not right_value or BULLET_PREFIX_RE.match(right_value):
        return False
    if SENTENCE_END_RE.search(left_value):
        return False
    if (
        re.search(r"(?<!\d)\d{1,3},?\s*$", left_value)
        and re.match(r"^\d{3}(?:\D|$)", right_value)
    ):
        return True
    if re.search(r"[,;:/\-–—]\s*$", left_value):
        return True
    if re.search(r"\b(?:and|or|to|for|with|into|of|in|at|by|from)\s*$", left_value, re.IGNORECASE):
        return True
    if left_value.endswith(("并", "及", "与", "和", "为", "在", "将")):
        return True
    return right_value[:1].islower()


def _numeric_text(value: str) -> str:
    """Normalize Unicode and rejoin only obvious physical line wraps for numeric parsing."""

    normalized = unicodedata.normalize("NFKC", value)
    lines = normalized.splitlines()
    if len(lines) <= 1:
        return normalized
    output = [lines[0]]
    previous = lines[0]
    for line in lines[1:]:
        if _looks_like_wrapped_pair(previous, line):
            output[-1] = output[-1].rstrip() + " " + line.lstrip()
        else:
            output.append(line)
        previous = line
    return "\n".join(output)


def _canonical_number(value: str) -> str:
    cleaned = re.sub(r"[$£€¥, '\t\u00a0\u2009\u202f]", "", value)
    integer, separator, fraction = cleaned.partition(".")
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return integer + ("." + fraction if separator and fraction else "")


def _number_tokens(value: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for match in NUMBER_TOKEN_RE.finditer(_numeric_text(value)):
        raw = re.sub(r"\s+", " ", match.group(0)).strip().casefold()
        canonical = _canonical_number(raw)
        if canonical:
            tokens.append((canonical, raw))
    return tokens


def _numbers(value: str) -> set[str]:
    return {canonical for canonical, _ in _number_tokens(value)}


def _numeric_format_normalization_count(statement: str, source_excerpt: str) -> int:
    source_forms: dict[str, set[str]] = {}
    for canonical, raw in _number_tokens(source_excerpt):
        source_forms.setdefault(canonical, set()).add(raw)
    normalized: set[str] = set()
    for canonical, raw in _number_tokens(statement):
        if canonical in source_forms and raw not in source_forms[canonical]:
            normalized.add(canonical)
    if unicodedata.normalize("NFKC", source_excerpt) != source_excerpt:
        normalized.update(_numbers(statement) & _numbers(source_excerpt))
    return len(normalized)


def _identity_part(value: Any) -> str:
    normalized = _compact(value, limit=200).casefold()
    for source, target in MONTH_ALIASES.items():
        normalized = re.sub(rf"\b{source}\b", target, normalized)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", normalized)


def _entity_signature(entity: dict[str, Any]) -> str:
    fields = (
        entity.get("entity_type"), entity.get("organization"), entity.get("role"),
        entity.get("start_date"), entity.get("end_date"),
    )
    return "|".join(_identity_part(item) for item in fields)


def _explicit_entity_markers(source_excerpt: str) -> tuple[bool, bool]:
    internship = bool(re.search(r"\b(?:intern|internship|trainee)\b|实习", source_excerpt, re.IGNORECASE))
    education = bool(re.search(
        r"\b(?:bachelor|master|ph\.?d|degree|university|college|student|graduat(?:e|ed|ion)|"
        r"mba|bba|bsc|b\.sc|msc|m\.sc|b\.a|m\.a)\b|学士|硕士|博士|大学|学院|学历|学位|毕业",
        source_excerpt,
        re.IGNORECASE,
    ))
    return internship, education


def _normalized_entity_category(value: Any, source_excerpt: str) -> tuple[str, list[str], bool]:
    """Normalize only structural labels; never synthesize an experience fact."""

    raw = _compact(value, limit=80).casefold().replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    category = raw if raw in ENTITY_CATEGORIES else ENTITY_CATEGORY_ALIASES.get(raw, "")
    internship_explicit, education_explicit = _explicit_entity_markers(source_excerpt)
    codes: list[str] = []
    review_required = False
    if internship_explicit and category != "internship":
        category = "internship"
        codes.append("EXPLICIT_INTERNSHIP_TYPE_NORMALIZED")
        review_required = True
    elif category and category != raw:
        codes.append("ENTITY_TYPE_ALIAS_NORMALIZED")
        review_required = True
    if category == "internship" and not internship_explicit:
        codes.append("AI_INTERNSHIP_TYPE_REQUIRES_CONFIRMATION")
        review_required = True
    if category == "education" and not education_explicit:
        codes.append("AI_EDUCATION_TYPE_REQUIRES_CONFIRMATION")
        review_required = True
    return category, codes, review_required


def _normalized_candidate_category(value: Any) -> str:
    raw = _compact(value, limit=81).casefold().replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw in ALLOWED_CATEGORIES:
        return raw
    return ENTITY_CATEGORY_ALIASES.get(raw, "")


def _normalized_claim_kind(value: Any) -> str:
    raw = _compact(value, limit=80).casefold().replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw in ALLOWED_CLAIM_KINDS:
        return raw
    return CLAIM_KIND_ALIASES.get(raw, "")


def _grounded_entity_range(
    entity: dict[str, Any],
    source_lines: list[str],
    *,
    line_number_start: int,
) -> tuple[int, int, str | None]:
    """Expand by at most two total adjacent lines for a split DOCX/PDF entity header."""

    line_number_end = line_number_start + max(1, len(source_lines)) - 1
    original_start, original_end = int(entity["line_start"]), int(entity["line_end"])
    attempted: set[tuple[int, int]] = set()
    ranges: list[tuple[int, int]] = []
    for added_total in range(MAX_GROUNDING_ADJACENT_LINES + 1):
        before_values = sorted(
            range(added_total + 1),
            key=lambda before: (abs(before - (added_total - before)), -before),
        )
        for before in before_values:
            after = added_total - before
            start = max(line_number_start, original_start - before)
            end = min(line_number_end, original_end + after)
            if (start, end) not in attempted:
                attempted.add((start, end))
                ranges.append((start, end))
    for start, end in ranges:
        segment = source_lines[start - line_number_start:end - line_number_start + 1]
        expanding = (start, end) != (original_start, original_end)
        if expanding and (
            any(not line.strip() or BULLET_PREFIX_RE.match(line.strip()) for line in segment)
            or any(SENTENCE_END_RE.search(line.strip()) for line in segment[:-1])
        ):
            continue
        excerpt = "\n".join(source_lines[start - line_number_start:end - line_number_start + 1])
        identity_grounded = (
            _field_is_grounded(str(entity.get("organization", "")), excerpt)
            and _field_is_grounded(str(entity.get("role", "")), excerpt)
        )
        dates_grounded = not (
            _numbers(f"{entity.get('start_date', '')} {entity.get('end_date', '')}") - _numbers(excerpt)
        )
        if identity_grounded and dates_grounded:
            adjustment = "ADJACENT_ENTITY_HEADER_LINES" if (start, end) != (original_start, original_end) else None
            return start, end, adjustment
    raise _invalid_ai_response(
        "ENTITY_IDENTITY",
        "An AI entity identity or date is not grounded within its cited or adjacent physical lines.",
    )


def _clean_candidate_statement(value: Any) -> str:
    statement = _compact(value, limit=2_001)
    return re.sub(r"^[\s\u2022\u2023\u25e6\u2043\u2219\uf0de\uf0b7▪▫●○◦‣·*—–-]+", "", statement).strip()


def _field_is_grounded(value: str, source_excerpt: str) -> bool:
    if not value:
        return True
    normalized_value = _identity_part(value)
    normalized_source = _identity_part(source_excerpt)
    if normalized_value and normalized_value in normalized_source:
        return True
    tokens = _tokens(value)
    if not tokens:
        return False
    shared = len(tokens & _tokens(source_excerpt))
    return shared >= max(1, math.ceil(len(tokens) * 0.75))


def _has_entity_predicate(statement: str) -> bool:
    return bool(ENTITY_VERB_RE.search(statement) or any(item in statement for item in CHINESE_ENTITY_VERBS))


def _near_duplicate(left: str, right: str) -> bool:
    if _numbers(left) != _numbers(right):
        return False
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= NEAR_DUPLICATE_RATIO


def _structural_quality_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    codes = {
        str(code)
        for candidate in candidates
        for code in (candidate.get("provenance", {}).get("structural_normalizations", []) or [])
        if isinstance(code, str)
    }
    review_count = sum(
        1 for candidate in candidates
        if candidate.get("provenance", {}).get("classification_review_required") is True
    )
    return {
        "structural_normalization_codes": sorted(codes),
        "structural_normalization_count": len(codes),
        "classification_review_candidate_count": review_count,
    }


def _record_candidate_filter(diagnostics: dict[str, Any] | None, reason: str) -> None:
    """Count a rejected Claim candidate without retaining any private candidate text."""
    if diagnostics is None:
        return
    reasons = diagnostics.setdefault("filtered_candidate_reasons", {})
    reasons[reason] = int(reasons.get(reason, 0)) + 1


def _candidate_filter_summary(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    raw_reasons = (diagnostics or {}).get("filtered_candidate_reasons", {})
    reasons = {
        str(reason): int(count)
        for reason, count in raw_reasons.items()
        if isinstance(reason, str) and isinstance(count, int) and count > 0
    }
    return {
        "filtered_candidate_count": sum(reasons.values()),
        "filtered_candidate_reasons": dict(sorted(reasons.items())),
        "candidate_filter_applied": bool(reasons),
    }


def _merge_candidate_filter_diagnostics(
    target: dict[str, Any] | None,
    source: dict[str, Any] | None,
) -> None:
    if target is None:
        return
    for reason, count in (source or {}).get("filtered_candidate_reasons", {}).items():
        for _ in range(int(count)):
            _record_candidate_filter(target, str(reason))


def _statement_grounding_report(statement: str, source_excerpt: str) -> dict[str, Any]:
    format_complete = bool(20 <= len(statement) <= 2_000 and statement[-1:] in ".?!。！？")
    if statement.endswith((",", ";", ":", "-", "–", "—")) or "|" in statement or "http://" in statement.casefold() or "https://" in statement.casefold():
        format_complete = False
    letters = "".join(character for character in statement if character.isalpha())
    if len(letters) >= 8 and letters == letters.upper():
        format_complete = False
    statement_numbers = _numbers(statement)
    unsupported_numbers = statement_numbers - _numbers(source_excerpt)
    claim_tokens = _tokens(statement)
    source_tokens = _tokens(source_excerpt)
    required = max(1 if len(claim_tokens) == 1 else 2, math.ceil(len(claim_tokens) * GROUNDING_RATIO))
    shared_tokens = len(claim_tokens & source_tokens)
    return {
        "valid": bool(format_complete and not unsupported_numbers and (not claim_tokens or shared_tokens >= required)),
        "statement_format_complete": format_complete,
        "unsupported_number_count": len(unsupported_numbers),
        "shared_grounding_token_count": shared_tokens,
        "required_grounding_token_count": required,
        "numeric_format_normalization_count": _numeric_format_normalization_count(statement, source_excerpt),
    }


def _statement_is_complete(statement: str, source_excerpt: str) -> bool:
    return bool(_statement_grounding_report(statement, source_excerpt)["valid"])


def _bounded_statement_grounding(
    statement: str,
    source_lines: list[str],
    *,
    line_start: int,
    line_end: int,
    line_number_start: int,
) -> dict[str, Any]:
    """Validate one statement and, only for obvious wraps, widen its cited lines.

    The statement is never rewritten.  Expansion stays inside the current AI
    chunk, never crosses a blank line or bullet boundary, and is limited to two
    adjacent physical lines on each side.  Any accepted adjustment is persisted
    for the user's Claim review.
    """

    start_index = line_start - line_number_start
    end_index = line_end - line_number_start
    original_excerpt = "\n".join(source_lines[start_index:end_index + 1])
    original = _statement_grounding_report(statement, original_excerpt)
    original.update({
        "line_start": line_start,
        "line_end": line_end,
        "citation_adjustment": None,
        "adjacent_line_expansion_attempted": False,
    })
    if original["valid"] or not original["statement_format_complete"]:
        return original

    expanded_start, expanded_end = start_index, end_index
    before = after = 0
    while before < MAX_GROUNDING_ADJACENT_LINES and expanded_start > 0:
        if not _looks_like_wrapped_pair(source_lines[expanded_start - 1], source_lines[expanded_start]):
            break
        expanded_start -= 1
        before += 1
    while after < MAX_GROUNDING_ADJACENT_LINES and expanded_end + 1 < len(source_lines):
        if not _looks_like_wrapped_pair(source_lines[expanded_end], source_lines[expanded_end + 1]):
            break
        expanded_end += 1
        after += 1
    if expanded_start == start_index and expanded_end == end_index:
        return original

    expanded_excerpt = "\n".join(source_lines[expanded_start:expanded_end + 1])
    expanded = _statement_grounding_report(statement, expanded_excerpt)
    expanded.update({
        "line_start": line_number_start + expanded_start,
        "line_end": line_number_start + expanded_end,
        "citation_adjustment": "ADJACENT_WRAPPED_LINES" if expanded["valid"] else None,
        "adjacent_line_expansion_attempted": True,
        "original_line_start": line_start,
        "original_line_end": line_end,
    })
    return expanded if expanded["valid"] else {**original, **{
        "adjacent_line_expansion_attempted": True,
        "expanded_line_start": line_number_start + expanded_start,
        "expanded_line_end": line_number_start + expanded_end,
        "unsupported_number_count": expanded["unsupported_number_count"],
        "shared_grounding_token_count": expanded["shared_grounding_token_count"],
    }}


def _invalid_ai_response(category: str, message: str, **details: object) -> JobOpsError:
    return JobOpsError("AI_RESPONSE_INVALID", message, failure_category=category, **details)


class AIAnalysisEngine:
    """Private-document AI boundary used by onboarding.

    Implementations receive document content only through memory/stdin. The public
    status never includes a command, model path, credential, or document value.
    """

    ready = False

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "NOT_CONFIGURED",
            "mode": "AI_REQUIRED_NO_CLAIM_OUTPUT",
            "provider": None,
            "private_transport": "NONE",
            "automatic_claim_selection": False,
            "claim_output_allowed": False,
        }

    def analyze_document(self, text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raise JobOpsError("AI_ENGINE_NOT_CONFIGURED", "A private-document AI engine has not been configured.")


class LocalSubprocessAIEngine(AIAnalysisEngine):
    ready = True

    def __init__(self, command: list[str], *, timeout_seconds: int = 180) -> None:
        if not command or not all(isinstance(item, str) and item.strip() for item in command):
            raise JobOpsError("AI_COMMAND_INVALID", "The configured AI command is invalid.")
        executable = Path(command[0]).expanduser()
        if executable.is_absolute() and not executable.is_file():
            raise JobOpsError("AI_COMMAND_MISSING", "The configured local AI executable does not exist.")
        self.command = command
        self.timeout_seconds = max(15, min(int(timeout_seconds), 600))

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "READY",
            "mode": "AI_CORE_STRUCTURED_ANALYSIS",
            "provider": "LOCAL_SUBPROCESS",
            "private_transport": "STDIN_STDOUT_MEMORY_ONLY",
            "automatic_claim_selection": False,
            "claim_output_allowed": True,
            "quality_contract": AI_QUALITY_CONTRACT,
        }

    @staticmethod
    def _request(
        text: str,
        *,
        source_id: str,
        source_type: str,
        line_number_start: int = 1,
    ) -> tuple[dict[str, Any], bool]:
        truncated = len(text) > MAX_AI_INPUT_CHARS
        bounded = text[:MAX_AI_INPUT_CHARS]
        numbered = [
            f"{index}\t{line}"
            for index, line in enumerate(bounded.splitlines() or [bounded], start=line_number_start)
        ]
        rules = [
            "Reconstruct wrapped lines and page breaks before analysis. Never return a line fragment, heading, navigation, table row, URL, or contact value.",
            "Identify each real-world entity once. Merge repeated mentions of the same organization, role, and date range into one entity_key.",
            "Classify paid or professional work as work, roles explicitly described as intern/internship as internship, degree study as education, and bounded case/engagement/build work as project.",
            "Use exactly one of work, internship, education, or project for every entity_type. Every experience candidate must reuse its parent entity_key and exactly inherit that parent entity_type as category.",
            "Every candidate must be a complete standalone sentence ending in punctuation. Achievements and responsibilities inherit the category and entity_key of their parent entity.",
            "Preserve company, role, date, number, and responsibility boundaries exactly; never infer missing facts.",
            "Numeric formatting may differ only by commas, digit-grouping spaces, full-width digits, or physical PDF line wraps. Never calculate, round, scale, convert, or infer a value.",
            "Treat team and AI work as separate from sole applicant ownership.",
            "Every candidate must cite inclusive source line_start and line_end.",
            "Before returning, verify that every candidate ends in punctuation and that its cited line range contains every stated identity, date, number, responsibility, and outcome. Expand across wrapped source lines when needed; omit anything that cannot be grounded exactly.",
            "Return JSON only. Do not approve any Claim for external use.",
        ]
        if source_type == "chatgpt_export":
            rules[0] = "Each numbered line is one complete user-authored message selected from an official ChatGPT export. Never combine unrelated messages into one Claim."
            rules.insert(
                1,
                "A user request, hypothetical, pasted job description, instruction to an AI, or third-party text is not evidence about the applicant. Return a Claim only when the user explicitly states a personal fact, experience, preference, skill, education item, project, or outcome.",
            )
            rules.insert(
                2,
                "Use conservative extraction. If personal ownership or provenance is ambiguous, omit the candidate instead of summarizing the conversation topic.",
            )
        return {
            "schema_version": AI_PROTOCOL_VERSION,
            "task": "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2",
            "source": {"source_id": source_id, "source_type": source_type},
            "rules": rules,
            "output_contract": {
                "schema_version": AI_PROTOCOL_VERSION,
                "entities": [{
                    "entity_key": "document-local stable key",
                    "entity_type": "work|internship|education|project",
                    "organization": "source-grounded string or empty",
                    "role": "source-grounded string or empty",
                    "start_date": "source-grounded string or empty",
                    "end_date": "source-grounded string or empty",
                    "line_start": "positive integer",
                    "line_end": "integer >= line_start",
                }],
                "candidates": [{
                    "statement": "complete sentence, 20-2000 chars, ending in punctuation",
                    "category": "work|internship|education|project|skill|certification|language|summary",
                    "claim_kind": "entity_summary|responsibility|achievement|qualification|skill|summary",
                    "entity_key": "matching entity key, or empty for non-entity categories",
                    "confidence": "HIGH|MEDIUM|LOW",
                    "line_start": "positive integer",
                    "line_end": "integer >= line_start",
                    "reason": "short explanation",
                }],
            },
            "line_numbered_document": numbered,
        }, truncated

    @classmethod
    def _chunk_requests(
        cls,
        text: str,
        *,
        source_id: str,
        source_type: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw_lines = text.splitlines() or [text]
        source_lines: list[str] = []
        segmented_lines = 0
        for raw_line in raw_lines:
            if len(raw_line) <= MAX_AI_LINE_CHARS:
                source_lines.append(raw_line)
                continue
            segmented_lines += 1
            source_lines.extend(
                raw_line[index:index + MAX_AI_LINE_CHARS]
                for index in range(0, len(raw_line), MAX_AI_LINE_CHARS)
            )

        chunks: list[tuple[int, list[str]]] = []
        current: list[str] = []
        current_chars = 0
        current_start = 1
        for line_number, line in enumerate(source_lines, start=1):
            added = len(line) + (1 if current else 0)
            if current and current_chars + added > MAX_AI_CHUNK_CHARS:
                chunks.append((current_start, current))
                current = []
                current_chars = 0
                current_start = line_number
                added = len(line)
            current.append(line)
            current_chars += added
        if current:
            chunks.append((current_start, current))
        if len(chunks) > MAX_AI_CHUNKS:
            raise JobOpsError(
                "AI_INPUT_TOO_LARGE_FOR_COMPLETE_ANALYSIS",
                "The source exceeds the bounded complete-analysis chunk limit; no partial analysis was accepted.",
                chunk_count=len(chunks), maximum_chunks=MAX_AI_CHUNKS,
            )

        requests: list[dict[str, Any]] = []
        for index, (line_start, lines) in enumerate(chunks, start=1):
            request, truncated = cls._request(
                "\n".join(lines), source_id=source_id, source_type=source_type,
                line_number_start=line_start,
            )
            if truncated:
                raise JobOpsError("AI_CHUNK_BOUNDARY_INVALID", "An internal AI chunk exceeded its complete-analysis boundary.")
            request["chunk"] = {
                "index": index, "total": len(chunks), "line_start": line_start,
                "line_end": line_start + len(lines) - 1,
            }
            requests.append(request)
        return requests, {
            "ai_chunks": len(requests), "ai_chunking_applied": len(requests) > 1,
            "ai_input_characters": len(text), "ai_covered_characters": len(text),
            "ai_input_truncated": False, "segmented_oversize_lines": segmented_lines,
        }

    @staticmethod
    def _merge_candidate_batches(batches: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        exact: set[tuple[str, str, str]] = set()
        for batch in batches:
            for candidate in batch:
                statement = str(candidate.get("statement", ""))
                category = str(candidate.get("category", ""))
                entity_fingerprint = str((candidate.get("entity") or {}).get("entity_fingerprint") or "")
                signature = (
                    category, entity_fingerprint,
                    re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", statement.casefold()),
                )
                if signature in exact:
                    continue
                if any(
                    str(existing.get("category", "")) == category
                    and str((existing.get("entity") or {}).get("entity_fingerprint") or "") == entity_fingerprint
                    and _near_duplicate(statement, str(existing.get("statement", "")))
                    for existing in merged
                ):
                    continue
                exact.add(signature)
                merged.append(candidate)
        return merged

    @staticmethod
    def _repair_request(
        original_request: dict[str, Any],
        rejected_output: Any,
        validation_error: JobOpsError,
    ) -> dict[str, Any]:
        """Ask the same private AI to replace one invalid response; all values remain on stdin/in memory."""
        return {
            "schema_version": AI_PROTOCOL_VERSION,
            "task": "JOBOPS_REPAIR_PRIVATE_DOCUMENT_UNDERSTANDING_V2",
            "source": original_request.get("source", {}),
            "validation_failure": {
                "code": validation_error.code,
                "message": validation_error.message,
                "repair_attempt": 1,
                "non_private_diagnostics": validation_error.details,
            },
            "rules": [
                "Replace the rejected output with a complete new result; return JSON only and do not explain the repair.",
                "Re-check every entity and candidate against the numbered source. A cited range must include every stated company, role, date, number, responsibility, and outcome.",
                "Every candidate must be a complete standalone sentence of 20-2000 characters ending in . ? ! 。 ？ or ！. Never return headings, labels, table rows, URLs, contact values, or sentence fragments.",
                "When a source sentence wraps across lines, cite the full inclusive range. Do not cite nearby unrelated lines merely to gain token overlap.",
                "Preserve responsibility boundaries and exact numbers. Do not add a subject, result, date, role, or relationship that the cited lines do not support.",
                "A comma, thin space, full-width digit, or physical PDF line wrap may change numeric formatting, but never calculate, round, scale, convert, or infer a numeric value.",
                "Omit an unsupported candidate instead of guessing. Do not preserve the rejected candidate count.",
                "Identify each real-world work, internship, education, or project entity once. Do not repeat an entity under a second key.",
                "Use exactly one of work, internship, education, or project for every entity_type. An explicit Intern or Internship title is internship, not work.",
                "Attach every experience Claim to its one matching entity_key and make the Claim category exactly equal to that parent entity_type.",
                "Do not approve any Claim for external use.",
            ],
            "output_contract": original_request.get("output_contract", {}),
            "chunk": original_request.get("chunk", {}),
            "line_numbered_document": original_request.get("line_numbered_document", []),
            "rejected_output": rejected_output,
        }

    @staticmethod
    def _repair_failed(error: JobOpsError) -> JobOpsError:
        safe_detail_keys = {
            "failure_category", "candidate_index", "cited_line_start", "cited_line_end",
            "unsupported_number_count", "shared_grounding_token_count", "statement_format_complete",
            "required_grounding_token_count", "adjacent_line_expansion_attempted",
            "expanded_line_start", "expanded_line_end", "numeric_matching_policy",
        }
        diagnostics = {key: value for key, value in error.details.items() if key in safe_detail_keys}
        return JobOpsError(
            "AI_RESPONSE_REPAIR_FAILED",
            "The replacement AI response still lacked a valid top-level protocol or entity registry, so it could not be attached to a complete analysis preview.",
            validation_code=error.code,
            automatic_repair_attempts=1,
            **diagnostics,
        )

    @staticmethod
    def _validated_candidates(
        value: Any,
        *,
        source_id: str,
        source_lines: list[str],
        line_number_start: int = 1,
        quality_diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            raise _invalid_ai_response("RESPONSE_FORMAT", "The local AI response did not match the JobOps protocol.")
        try:
            protocol_version = int(value.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise _invalid_ai_response("RESPONSE_FORMAT", "The local AI response did not match the JobOps protocol.") from exc
        if protocol_version != AI_PROTOCOL_VERSION:
            raise _invalid_ai_response("RESPONSE_FORMAT", "The local AI response did not match the JobOps protocol.")
        raw_entities = value.get("entities")
        if not isinstance(raw_entities, list) or len(raw_entities) > 100:
            raise _invalid_ai_response("RESPONSE_FORMAT", "The local AI response contains an invalid entity list.")
        entities: dict[str, dict[str, Any]] = {}
        entity_aliases: dict[str, str] = {}
        entity_keys_casefold: dict[str, str] = {}
        identity_to_key: dict[str, str] = {}
        line_count = max(1, len(source_lines))
        line_number_end = line_number_start + line_count - 1
        for raw in raw_entities:
            if not isinstance(raw, dict):
                raise _invalid_ai_response("RESPONSE_FORMAT", "A local AI entity is not an object.")
            entity_key = _compact(raw.get("entity_key"), limit=120)
            try:
                line_start, line_end = int(raw.get("line_start")), int(raw.get("line_end"))
            except (TypeError, ValueError) as exc:
                raise _invalid_ai_response("PROVENANCE_LINES", "A local AI entity has invalid provenance lines.") from exc
            if (
                not entity_key
                or not line_number_start <= line_start <= line_end <= line_number_end
            ):
                raise _invalid_ai_response("ENTITY_IDENTITY", "A local AI entity has an invalid identity or provenance.")
            entity = {
                "entity_key": entity_key,
                "entity_type": "",
                "organization": _compact(raw.get("organization"), limit=300),
                "role": _compact(raw.get("role"), limit=300),
                "start_date": _compact(raw.get("start_date"), limit=120),
                "end_date": _compact(raw.get("end_date"), limit=120),
                "line_start": line_start,
                "line_end": line_end,
            }
            if not (entity["organization"] or entity["role"]):
                raise _invalid_ai_response("ENTITY_IDENTITY", "An AI entity has no grounded organization or role identity.")
            grounded_start, grounded_end, citation_adjustment = _grounded_entity_range(
                entity, source_lines, line_number_start=line_number_start,
            )
            entity["line_start"], entity["line_end"] = grounded_start, grounded_end
            context_excerpt = "\n".join(
                source_lines[grounded_start - line_number_start:grounded_end - line_number_start + 1]
            )
            entity_type, normalization_codes, classification_review = _normalized_entity_category(
                raw.get("entity_type"), context_excerpt,
            )
            if entity_type not in ENTITY_CATEGORIES:
                raise _invalid_ai_response(
                    "EXPERIENCE_CLASSIFICATION",
                    "An AI entity did not use a recognizable work, internship, education, or project type.",
                )
            entity["entity_type"] = entity_type
            if citation_adjustment:
                normalization_codes.append(citation_adjustment)
                classification_review = True
            entity["normalization_codes"] = sorted(set(normalization_codes))
            entity["classification_review_required"] = classification_review
            identity_signature = "|".join(_identity_part(entity.get(key)) for key in (
                "organization", "role", "start_date", "end_date",
            ))
            if not identity_signature.replace("|", ""):
                raise _invalid_ai_response("ENTITY_IDENTITY", "The AI returned an unidentified real-world entity.")
            canonical_key = identity_to_key.get(identity_signature)
            existing_key_target = (
                entity_aliases.get(entity_key) or entity_keys_casefold.get(entity_key.casefold())
            )
            if existing_key_target and canonical_key != existing_key_target:
                raise _invalid_ai_response(
                    "ENTITY_IDENTITY",
                    "The AI reused one entity key for different grounded identities.",
                )
            if canonical_key:
                canonical = entities[canonical_key]
                canonical["line_start"] = min(int(canonical["line_start"]), grounded_start)
                canonical["line_end"] = max(int(canonical["line_end"]), grounded_end)
                canonical["normalization_codes"] = sorted(set(
                    list(canonical.get("normalization_codes", []))
                    + list(entity.get("normalization_codes", []))
                    + ["DUPLICATE_ENTITY_CONSOLIDATED"]
                ))
                canonical["classification_review_required"] = True
                explicit_internship, explicit_education = _explicit_entity_markers(context_excerpt)
                if entity_type == "internship" and explicit_internship:
                    canonical["entity_type"] = "internship"
                    canonical["normalization_codes"] = sorted(set(
                        list(canonical["normalization_codes"]) + ["EXPLICIT_INTERNSHIP_TYPE_NORMALIZED"]
                    ))
                elif entity_type == "education" and explicit_education:
                    canonical["entity_type"] = "education"
                    canonical["normalization_codes"] = sorted(set(
                        list(canonical["normalization_codes"]) + ["EXPLICIT_EDUCATION_TYPE_NORMALIZED"]
                    ))
                elif canonical["entity_type"] != entity_type:
                    canonical["normalization_codes"] = sorted(set(
                        list(canonical["normalization_codes"]) + ["CONFLICTING_ENTITY_TYPE_CONSOLIDATED"]
                    ))
                entity_aliases[entity_key] = canonical_key
                entity_keys_casefold[entity_key.casefold()] = canonical_key
                continue
            identity_to_key[identity_signature] = entity_key
            entity_aliases[entity_key] = entity_key
            entity_keys_casefold[entity_key.casefold()] = entity_key
            entities[entity_key] = entity

        for entity in entities.values():
            signature = _entity_signature(entity)
            entity["entity_fingerprint"] = stable_id("ENTKEY", signature)
            entity["entity_id"] = stable_id("ENT", source_id, signature)
        raw_candidates = value.get("candidates")
        if not isinstance(raw_candidates, list) or len(raw_candidates) > 300:
            raise _invalid_ai_response("RESPONSE_FORMAT", "The local AI response contains an invalid candidate list.")
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_candidates: list[tuple[str, str, str]] = []
        for candidate_index, raw in enumerate(raw_candidates, start=1):
            if not isinstance(raw, dict):
                _record_candidate_filter(quality_diagnostics, "RESPONSE_FORMAT")
                continue
            statement = _clean_candidate_statement(raw.get("statement"))
            raw_category = _compact(raw.get("category"), limit=81).casefold()
            category = _normalized_candidate_category(raw_category)
            raw_claim_kind = _compact(raw.get("claim_kind"), limit=80).casefold()
            claim_kind = _normalized_claim_kind(raw_claim_kind)
            entity_key = _compact(raw.get("entity_key"), limit=120)
            confidence = str(raw.get("confidence", "LOW")).upper()
            reason = _compact(raw.get("reason"), limit=500)
            normalization_codes: list[str] = []
            try:
                line_start = int(raw.get("line_start"))
                line_end = int(raw.get("line_end"))
            except (TypeError, ValueError):
                _record_candidate_filter(quality_diagnostics, "PROVENANCE_LINES")
                continue
            canonical_key = (
                entity_aliases.get(entity_key) or entity_keys_casefold.get(entity_key.casefold())
                if entity_key else None
            )
            entity = entities.get(canonical_key) if canonical_key else None
            if category not in ALLOWED_CATEGORIES and entity is not None and raw_category in GENERIC_ENTITY_CANDIDATE_CATEGORIES:
                category = str(entity["entity_type"])
                normalization_codes.append("GENERIC_CATEGORY_REPLACED_BY_PARENT")
            if category not in ALLOWED_CATEGORIES or claim_kind not in ALLOWED_CLAIM_KINDS:
                _record_candidate_filter(quality_diagnostics, "CATEGORY_CONTRACT")
                continue
            if category != raw_category:
                normalization_codes.append("CANDIDATE_CATEGORY_ALIAS_NORMALIZED")
            if claim_kind != raw_claim_kind:
                normalization_codes.append("CLAIM_KIND_ALIAS_NORMALIZED")
            if confidence not in ALLOWED_CONFIDENCE or not line_number_start <= line_start <= line_end <= line_number_end:
                _record_candidate_filter(quality_diagnostics, "PROVENANCE_LINES")
                continue
            if category in ENTITY_CATEGORIES and entity is None:
                same_type = [item for item in entities.values() if item["entity_type"] == category]
                nearby = [
                    item for item in same_type
                    if int(item["line_start"]) <= line_end + MAX_GROUNDING_ADJACENT_LINES
                    and int(item["line_end"]) >= line_start - MAX_GROUNDING_ADJACENT_LINES
                ]
                recoverable = nearby if len(nearby) == 1 else same_type if len(same_type) == 1 else []
                if len(recoverable) == 1:
                    entity = recoverable[0]
                    canonical_key = str(entity["entity_key"])
                    normalization_codes.append("MISSING_ENTITY_KEY_RECOVERED")
                else:
                    _record_candidate_filter(quality_diagnostics, "ENTITY_RELATION")
                    continue
            if category in ENTITY_CATEGORIES and entity is not None and entity["entity_type"] != category:
                category = str(entity["entity_type"])
                normalization_codes.append("PARENT_ENTITY_TYPE_INHERITED")
            if category not in ENTITY_CATEGORIES and entity_key:
                entity = None
                canonical_key = None
                normalization_codes.append("NON_ENTITY_CLAIM_DETACHED")
            if category in ENTITY_CATEGORIES and not _has_entity_predicate(statement):
                _record_candidate_filter(quality_diagnostics, "STATEMENT_FRAGMENT")
                continue
            grounding = _bounded_statement_grounding(
                statement,
                source_lines,
                line_start=line_start,
                line_end=line_end,
                line_number_start=line_number_start,
            )
            if not grounding["valid"]:
                failure_category = (
                    "UNSUPPORTED_NUMBER" if grounding["unsupported_number_count"]
                    else "STATEMENT_FRAGMENT" if not grounding["statement_format_complete"]
                    else "CITED_LINE_GROUNDING"
                )
                _record_candidate_filter(quality_diagnostics, failure_category)
                continue
            accepted_line_start = int(grounding["line_start"])
            accepted_line_end = int(grounding["line_end"])
            signature = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", statement.casefold())
            if signature in seen:
                continue
            entity_identity = str(entity.get("entity_fingerprint")) if entity else ""
            if any(
                prior_category == category and prior_entity == entity_identity and _near_duplicate(statement, prior_statement)
                for prior_category, prior_entity, prior_statement in seen_candidates
            ):
                continue
            seen.add(signature)
            seen_candidates.append((category, entity_identity, statement))
            classification_review_required = bool(
                normalization_codes or (entity or {}).get("classification_review_required")
            )
            output.append({
                "candidate_id": stable_id("EXT", source_id, str(accepted_line_start), statement),
                "statement": statement,
                "category": category,
                "claim_kind": claim_kind,
                "confidence": confidence,
                "selected": False,
                "selection_reason": (
                    "AI_DERIVED_CLASSIFICATION_NORMALIZED_REQUIRES_CONFIRMATION"
                    if classification_review_required else "AI_DERIVED_REQUIRES_CONFIRMATION"
                ),
                "ai_reason": reason,
                "provenance": {
                    "line_start": accepted_line_start,
                    "line_end": accepted_line_end,
                    "ai_line_start": line_start,
                    "ai_line_end": line_end,
                    "citation_adjustment": grounding.get("citation_adjustment"),
                    "numeric_format_normalizations": grounding["numeric_format_normalization_count"],
                    "structural_normalizations": sorted(set(
                        normalization_codes + list((entity or {}).get("normalization_codes", []))
                    )),
                    "classification_review_required": classification_review_required,
                    "human_confirmation_required": True,
                },
                "entity_id": entity.get("entity_id") if entity else None,
                "entity": entity,
            })
        return output

    def analyze_document(self, text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        def invoke(payload: dict[str, Any]) -> Any:
            try:
                returncode, stdout = _run_bounded_ai_command(
                    self.command,
                    payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except JobOpsError:
                raise
            except (OSError, subprocess.SubprocessError) as exc:
                raise JobOpsError("AI_ENGINE_UNAVAILABLE", "The configured local AI engine could not complete the analysis.") from exc
            encoded = stdout.encode("utf-8")
            if returncode != 0 or not encoded:
                raise JobOpsError("AI_ENGINE_FAILED", "The configured local AI engine returned no valid bounded result.")
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise JobOpsError("AI_RESPONSE_INVALID", "The configured local AI engine did not return JSON.") from exc

        requests, coverage = self._chunk_requests(text, source_id=source_id, source_type=source_type)
        batches: list[list[dict[str, Any]]] = []
        values: list[dict[str, Any]] = []
        repairs = 0
        quality_diagnostics: dict[str, Any] = {}
        for request in requests:
            numbered = request["line_numbered_document"]
            source_lines = [item.split("\t", 1)[1] if "\t" in item else "" for item in numbered]
            line_number_start = int(str(numbered[0]).split("\t", 1)[0]) if numbered else 1
            value: Any = {"status": "REJECTED_BEFORE_STRUCTURED_PROTOCOL"}
            attempt_diagnostics: dict[str, Any] = {}
            try:
                value = invoke(request)
                candidates = self._validated_candidates(
                    value, source_id=source_id, source_lines=source_lines,
                    line_number_start=line_number_start,
                    quality_diagnostics=attempt_diagnostics,
                )
                if not candidates and _candidate_filter_summary(attempt_diagnostics)["filtered_candidate_count"]:
                    raise _invalid_ai_response(
                        "FILTERED_CANDIDATE_SET",
                        "The first AI response contained only candidates that require filtering.",
                    )
            except JobOpsError as first_error:
                if first_error.code != "AI_RESPONSE_INVALID":
                    raise
                repairs += 1
                repaired_diagnostics: dict[str, Any] = {}
                try:
                    repaired = invoke(self._repair_request(request, value, first_error))
                    candidates = self._validated_candidates(
                        repaired, source_id=source_id, source_lines=source_lines,
                        line_number_start=line_number_start,
                        quality_diagnostics=repaired_diagnostics,
                    )
                except JobOpsError as repaired_error:
                    if repaired_error.code == "AI_RESPONSE_INVALID":
                        raise self._repair_failed(repaired_error) from repaired_error
                    raise
                _merge_candidate_filter_diagnostics(quality_diagnostics, repaired_diagnostics)
                value = repaired
            else:
                _merge_candidate_filter_diagnostics(quality_diagnostics, attempt_diagnostics)
            batches.append(candidates)
            values.append(value)
        candidates = self._merge_candidate_batches(batches)
        entity_signatures = {
            _entity_signature(entity)
            for value in values for entity in value.get("entities", [])
            if isinstance(entity, dict)
        }
        return candidates, {
            "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
            "ai_candidates": len(candidates),
            "ai_entities": len(entity_signatures),
            **coverage,
            "ai_repair_attempted": repairs > 0,
            "ai_repair_succeeded": repairs > 0,
            "ai_repair_count": repairs,
            "automatic_claim_selection": False,
            "quality_contract": AI_QUALITY_CONTRACT,
            "quality_gate_version": 5,
            **_candidate_filter_summary(quality_diagnostics),
            **_structural_quality_summary(candidates),
            "grounding_ratio_minimum": GROUNDING_RATIO,
            "near_duplicate_ratio": NEAR_DUPLICATE_RATIO,
        }


def configured_ai_engine() -> AIAnalysisEngine:
    raw = os.environ.get("JOBOPS_AI_COMMAND_JSON", "").strip()
    if not raw:
        return AIAnalysisEngine()
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JobOpsError("AI_COMMAND_INVALID", "JOBOPS_AI_COMMAND_JSON must be a JSON array.") from exc
    if not isinstance(command, list):
        raise JobOpsError("AI_COMMAND_INVALID", "JOBOPS_AI_COMMAND_JSON must be a JSON array.")
    timeout = os.environ.get("JOBOPS_AI_TIMEOUT_SECONDS", "180")
    try:
        timeout_seconds = int(timeout)
    except ValueError as exc:
        raise JobOpsError("AI_COMMAND_INVALID", "JOBOPS_AI_TIMEOUT_SECONDS must be an integer.") from exc
    return LocalSubprocessAIEngine([str(item) for item in command], timeout_seconds=timeout_seconds)
