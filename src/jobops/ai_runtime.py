from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .util import stable_id


AI_PROTOCOL_VERSION = 2
MAX_AI_INPUT_CHARS = 500_000
MAX_AI_OUTPUT_BYTES = 5 * 1024 * 1024
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
ALLOWED_CATEGORIES = {
    "work", "internship", "education", "project", "skill", "certification", "language", "summary",
}
ENTITY_CATEGORIES = {"work", "internship", "education", "project"}
ALLOWED_CLAIM_KINDS = {
    "entity_summary", "responsibility", "achievement", "qualification", "skill", "summary",
}
STOP_WORDS = {
    "and", "the", "for", "from", "with", "into", "that", "this", "was", "were", "are", "as", "at", "to", "of",
    "applicant", "candidate", "provided", "statement", "source-grounded",
}
GROUNDING_RATIO = 0.50
NEAR_DUPLICATE_RATIO = 0.85
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


def _numbers(value: str) -> set[str]:
    return {re.sub(r"[^0-9.]", "", item) for item in re.findall(r"\$?\d[\d,.]*", value)}


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


def _statement_is_complete(statement: str, source_excerpt: str) -> bool:
    if not 20 <= len(statement) <= 2_000 or statement[-1:] not in ".?!。！？":
        return False
    if statement.endswith((",", ";", ":", "-", "–", "—")) or "|" in statement or "http://" in statement.casefold() or "https://" in statement.casefold():
        return False
    letters = "".join(character for character in statement if character.isalpha())
    if len(letters) >= 8 and letters == letters.upper():
        return False
    statement_numbers = _numbers(statement)
    if statement_numbers - _numbers(source_excerpt):
        return False
    claim_tokens = _tokens(statement)
    source_tokens = _tokens(source_excerpt)
    required = max(1 if len(claim_tokens) == 1 else 2, math.ceil(len(claim_tokens) * GROUNDING_RATIO))
    if claim_tokens and len(claim_tokens & source_tokens) < required:
        return False
    return True


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
            "quality_contract": "ENTITY_DEDUPED_LINE_ANCHORED_V3",
        }

    @staticmethod
    def _request(text: str, *, source_id: str, source_type: str) -> tuple[dict[str, Any], bool]:
        truncated = len(text) > MAX_AI_INPUT_CHARS
        bounded = text[:MAX_AI_INPUT_CHARS]
        numbered = [f"{index}\t{line}" for index, line in enumerate(bounded.splitlines(), start=1)]
        rules = [
            "Reconstruct wrapped lines and page breaks before analysis. Never return a line fragment, heading, navigation, table row, URL, or contact value.",
            "Identify each real-world entity once. Merge repeated mentions of the same organization, role, and date range into one entity_key.",
            "Classify paid or professional work as work, roles explicitly described as intern/internship as internship, degree study as education, and bounded case/engagement/build work as project.",
            "Every candidate must be a complete standalone sentence ending in punctuation. Achievements and responsibilities inherit the category and entity_key of their parent entity.",
            "Preserve company, role, date, number, and responsibility boundaries exactly; never infer missing facts.",
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
                "Omit an unsupported candidate instead of guessing. Do not preserve the rejected candidate count.",
                "Identify each real-world work, internship, education, or project entity once and attach each experience Claim to exactly one matching entity.",
                "Do not approve any Claim for external use.",
            ],
            "output_contract": original_request.get("output_contract", {}),
            "line_numbered_document": original_request.get("line_numbered_document", []),
            "rejected_output": rejected_output,
        }

    @staticmethod
    def _repair_failed(error: JobOpsError) -> JobOpsError:
        return JobOpsError(
            "AI_RESPONSE_REPAIR_FAILED",
            "The AI result still contained an incomplete or unsupported Claim after one automatic correction. Nothing from this attempt was imported.",
            validation_code=error.code,
            automatic_repair_attempts=1,
        )

    @staticmethod
    def _validated_candidates(value: Any, *, source_id: str, source_lines: list[str]) -> list[dict[str, Any]]:
        if not isinstance(value, dict) or int(value.get("schema_version", 0)) != AI_PROTOCOL_VERSION:
            raise JobOpsError("AI_RESPONSE_INVALID", "The local AI response did not match the JobOps protocol.")
        raw_entities = value.get("entities")
        if not isinstance(raw_entities, list) or len(raw_entities) > 100:
            raise JobOpsError("AI_RESPONSE_INVALID", "The local AI response contains an invalid entity list.")
        entities: dict[str, dict[str, Any]] = {}
        signatures: set[str] = set()
        line_count = max(1, len(source_lines))
        for raw in raw_entities:
            if not isinstance(raw, dict):
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI entity is not an object.")
            entity_key = _compact(raw.get("entity_key"), limit=120)
            entity_type = _compact(raw.get("entity_type"), limit=30).casefold()
            try:
                line_start, line_end = int(raw.get("line_start")), int(raw.get("line_end"))
            except (TypeError, ValueError) as exc:
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI entity has invalid provenance lines.") from exc
            if not entity_key or entity_key in entities or entity_type not in ENTITY_CATEGORIES or not 1 <= line_start <= line_end <= line_count:
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI entity has an invalid identity, type, or provenance.")
            entity = {
                "entity_key": entity_key,
                "entity_type": entity_type,
                "organization": _compact(raw.get("organization"), limit=300),
                "role": _compact(raw.get("role"), limit=300),
                "start_date": _compact(raw.get("start_date"), limit=120),
                "end_date": _compact(raw.get("end_date"), limit=120),
                "line_start": line_start,
                "line_end": line_end,
            }
            entity_excerpt = "\n".join(source_lines[line_start - 1:line_end])
            if not (entity["organization"] or entity["role"]):
                raise JobOpsError("AI_RESPONSE_INVALID", "An AI entity has no grounded organization or role identity.")
            if not _field_is_grounded(entity["organization"], entity_excerpt) or not _field_is_grounded(entity["role"], entity_excerpt):
                raise JobOpsError("AI_RESPONSE_INVALID", "An AI entity identity is not grounded in its cited lines.")
            entity_numbers = _numbers(f"{entity['start_date']} {entity['end_date']}")
            if entity_numbers - _numbers(entity_excerpt):
                raise JobOpsError("AI_RESPONSE_INVALID", "An AI entity date is not grounded in its cited lines.")
            context_excerpt = entity_excerpt
            internship_explicit = bool(re.search(r"\b(?:intern|internship|trainee)\b|实习", context_excerpt, re.IGNORECASE))
            education_explicit = bool(re.search(
                r"\b(?:bachelor|master|ph\.?d|degree|university|college|student|graduat(?:e|ed|ion))\b|学士|硕士|博士|大学|学院|学历|学位|毕业",
                context_excerpt,
                re.IGNORECASE,
            ))
            if internship_explicit and entity_type != "internship":
                raise JobOpsError("AI_RESPONSE_INVALID", "An explicitly identified internship was assigned to another experience category.")
            if entity_type == "internship" and not internship_explicit:
                raise JobOpsError("AI_RESPONSE_INVALID", "An internship entity is not explicitly supported by its cited context.")
            if entity_type == "education" and not education_explicit:
                raise JobOpsError("AI_RESPONSE_INVALID", "An education entity is not explicitly supported by its cited context.")
            signature = _entity_signature(entity)
            if not signature.replace("|", "") or signature in signatures:
                raise JobOpsError("AI_RESPONSE_INVALID", "The AI returned a duplicate or unidentified real-world entity.")
            signatures.add(signature)
            entity["entity_fingerprint"] = stable_id("ENTKEY", signature)
            entity["entity_id"] = stable_id("ENT", source_id, signature)
            entities[entity_key] = entity
        raw_candidates = value.get("candidates")
        if not isinstance(raw_candidates, list) or len(raw_candidates) > 300:
            raise JobOpsError("AI_RESPONSE_INVALID", "The local AI response contains an invalid candidate list.")
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_candidates: list[tuple[str, str, str]] = []
        for candidate_index, raw in enumerate(raw_candidates, start=1):
            if not isinstance(raw, dict):
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI candidate is not an object.")
            statement = _clean_candidate_statement(raw.get("statement"))
            category = _compact(raw.get("category"), limit=81).casefold()
            claim_kind = _compact(raw.get("claim_kind"), limit=50).casefold()
            entity_key = _compact(raw.get("entity_key"), limit=120)
            confidence = str(raw.get("confidence", "LOW")).upper()
            reason = _compact(raw.get("reason"), limit=500)
            try:
                line_start = int(raw.get("line_start"))
                line_end = int(raw.get("line_end"))
            except (TypeError, ValueError) as exc:
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI candidate has invalid provenance lines.") from exc
            if category not in ALLOWED_CATEGORIES or claim_kind not in ALLOWED_CLAIM_KINDS:
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI candidate contains an unsupported category or Claim kind.")
            if confidence not in ALLOWED_CONFIDENCE or not 1 <= line_start <= line_end <= line_count:
                raise JobOpsError("AI_RESPONSE_INVALID", "A local AI candidate contains invalid confidence or provenance.")
            entity = entities.get(entity_key) if entity_key else None
            if category in ENTITY_CATEGORIES and (entity is None or entity["entity_type"] != category):
                raise JobOpsError("AI_RESPONSE_INVALID", "An experience Claim is not attached to exactly one matching entity.")
            if category not in ENTITY_CATEGORIES and entity_key:
                raise JobOpsError("AI_RESPONSE_INVALID", "A non-entity Claim must not be attached to an experience entity.")
            if category in ENTITY_CATEGORIES and not _has_entity_predicate(statement):
                raise JobOpsError("AI_RESPONSE_INVALID", "An experience Claim is a heading or fragment without a complete action or relationship.")
            source_excerpt = "\n".join(source_lines[line_start - 1:line_end])
            if not _statement_is_complete(statement, source_excerpt):
                unsupported_numbers = len(_numbers(statement) - _numbers(source_excerpt))
                claim_tokens = _tokens(statement)
                shared_tokens = len(claim_tokens & _tokens(source_excerpt))
                raise JobOpsError(
                    "AI_RESPONSE_INVALID",
                    "The AI returned a fragment or a statement that is not grounded in its cited lines.",
                    candidate_index=candidate_index,
                    cited_line_start=line_start,
                    cited_line_end=line_end,
                    unsupported_number_count=unsupported_numbers,
                    shared_grounding_token_count=shared_tokens,
                    statement_format_complete=bool(20 <= len(statement) <= 2_000 and statement[-1:] in ".?!。！？"),
                )
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
            output.append({
                "candidate_id": stable_id("EXT", source_id, str(line_start), statement),
                "statement": statement,
                "category": category,
                "claim_kind": claim_kind,
                "confidence": confidence,
                "selected": False,
                "selection_reason": "AI_DERIVED_REQUIRES_CONFIRMATION",
                "ai_reason": reason,
                "provenance": {"line_start": line_start, "line_end": line_end},
                "entity_id": entity.get("entity_id") if entity else None,
                "entity": entity,
            })
        return output

    def analyze_document(self, text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        request, truncated = self._request(text, source_id=source_id, source_type=source_type)
        def invoke(payload: dict[str, Any]) -> Any:
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                completed = subprocess.run(
                    self.command,
                    input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                    creationflags=creation_flags,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise JobOpsError("AI_ENGINE_UNAVAILABLE", "The configured local AI engine could not complete the analysis.") from exc
            encoded = completed.stdout.encode("utf-8")
            if completed.returncode != 0 or not encoded or len(encoded) > MAX_AI_OUTPUT_BYTES:
                raise JobOpsError("AI_ENGINE_FAILED", "The configured local AI engine returned no valid bounded result.")
            try:
                return json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise JobOpsError("AI_RESPONSE_INVALID", "The configured local AI engine did not return JSON.") from exc

        source_lines = [item.split("\t", 1)[1] if "\t" in item else "" for item in request["line_numbered_document"]]
        repair_attempted = False
        value: Any = {"status": "REJECTED_BEFORE_STRUCTURED_PROTOCOL"}
        try:
            value = invoke(request)
            candidates = self._validated_candidates(value, source_id=source_id, source_lines=source_lines)
        except JobOpsError as first_error:
            if first_error.code != "AI_RESPONSE_INVALID":
                raise
            repair_attempted = True
            try:
                repaired = invoke(self._repair_request(request, value, first_error))
                candidates = self._validated_candidates(repaired, source_id=source_id, source_lines=source_lines)
            except JobOpsError as repaired_error:
                if repaired_error.code == "AI_RESPONSE_INVALID":
                    raise self._repair_failed(repaired_error) from repaired_error
                raise
            value = repaired
        return candidates, {
            "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
            "ai_candidates": len(candidates),
            "ai_entities": len(value.get("entities", [])),
            "ai_input_truncated": truncated,
            "ai_repair_attempted": repair_attempted,
            "ai_repair_succeeded": repair_attempted,
            "automatic_claim_selection": False,
            "quality_contract": "ENTITY_DEDUPED_LINE_ANCHORED_V3",
            "quality_gate_version": 3,
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
