from __future__ import annotations

import re
from typing import Any

from .util import stable_id


_SPACE = re.compile(r"\s+")
_PAGE_NOISE = re.compile(
    r"(?ix)^(?:page\s*\d+|第\s*\d+\s*页|public[- ]safe\s+work\s+sample.*page\s*\d+|"
    r"confidential.*(?:omitted|details)|source[_ ]material[_ ]requires[_ ]confirmation)$"
)
_SECTION_WORDS = {
    "summary", "professional summary", "experience", "work experience", "education", "skills",
    "key skills", "technical proficiencies", "projects", "project experience", "achievements",
    "leadership", "certifications", "languages", "overview", "method", "methodology", "appendix",
    "个人简介", "工作经历", "教育经历", "技能", "项目", "项目经历", "主要成果", "证书", "语言",
}
_TABLE_HEADER_WORDS = {
    "signal", "workstream", "decision", "deliverable", "value created", "layer", "public inputs",
    "decision use", "eligibility", "control", "quality control", "privacy", "truthfulness",
}
_ACTION_WORDS = re.compile(
    r"(?ix)\b(?:led|built|created|designed|developed|conducted|managed|mapped|analy[sz]ed|implemented|"
    r"delivered|produced|launched|improved|increased|reduced|generated|founded|operated|evaluated|"
    r"identified|translated|synthesi[sz]ed|modeled|automated|owned|drove|supported|advised|executed|"
    r"established|coordinated|researched|presented|converted|audited)\b|"
    r"(?:主导|负责|搭建|建立|创建|设计|开发|分析|实施|交付|完成|推动|提升|降低|管理|研究|评估|创办|运营|支持)"
)
_FIRST_PERSON = re.compile(r"(?i)(?:^|[.!?]\s+)(?:i|我)\s+")
_CONTINUATION = re.compile(
    r"(?ix)^(?:and|or|but|to|into|through|with|while|using|across|for|from|of|in|on|that|which|"
    r"so\s+that|as\s+well\s+as|并且|以及|并|从而|以便|其中|通过)\b"
)
_TERMINAL = re.compile(r"[.!?。！？；;:]$|[\]\)]$")
_BULLET = re.compile(r"^\s*[•▪◦●○\-*–—]+\s*")
_DATE_OR_ROLE = re.compile(
    r"(?ix)(?:\b(?:19|20)\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|"
    r"(?:present|current|至今|现在))"
)
_MARKUP_NOISE = re.compile(r"(?i)(?:https?://|www\.|<[^>]{1,120}>|\{\s*[\"']?[A-Za-z0-9_ -]+[\"']?\s*:)")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|){2,}")


def _clean(value: str) -> str:
    return _SPACE.sub(" ", value.replace("\x00", " ")).strip()


def _is_heading(value: str) -> bool:
    cleaned = _clean(value).strip(":：")
    lowered = cleaned.casefold()
    if lowered in _SECTION_WORDS or _PAGE_NOISE.match(cleaned):
        return True
    if len(cleaned) <= 90 and cleaned:
        letters = [char for char in cleaned if char.isalpha()]
        if letters and sum(char.isupper() for char in letters) / len(letters) >= 0.9:
            return True
    words = {part.casefold() for part in re.findall(r"[A-Za-z]+", cleaned)}
    return bool(words) and words <= _TABLE_HEADER_WORDS


def _is_noise(value: str) -> bool:
    cleaned = _clean(value)
    if not cleaned or _PAGE_NOISE.match(cleaned):
        return True
    if re.fullmatch(r"[\W_\d]+", cleaned):
        return True
    if _TABLE_SEPARATOR.match(cleaned) or _MARKUP_NOISE.search(cleaned):
        return True
    if cleaned.count("|") >= 3 or cleaned.count("`") >= 4:
        return True
    return False


def _starts_new_claim(value: str) -> bool:
    return bool(_BULLET.match(value) or _FIRST_PERSON.search(value) or _ACTION_WORDS.match(_BULLET.sub("", value)))


def _should_join(previous: str, current: str) -> bool:
    if not previous:
        return False
    if _is_heading(previous) or _is_heading(current) or _starts_new_claim(current):
        return False
    cleaned = _clean(current)
    if not cleaned:
        return False
    if cleaned[0].islower() or _CONTINUATION.match(cleaned):
        return True
    if previous.rstrip().endswith((",", ";", ":", "–", "—", "/")):
        return True
    return not _TERMINAL.search(previous) and len(previous) < 220 and len(cleaned) < 180


def reconstruct_blocks(text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Reconstruct readable blocks while retaining line provenance.

    This deliberately does not decide that every block is a personal Claim.
    """

    raw_lines = text.replace("\r", "\n").splitlines()
    blocks: list[dict[str, Any]] = []
    buffer = ""
    start = 0
    filtered = 0

    def flush(end: int) -> None:
        nonlocal buffer, start
        value = _clean(_BULLET.sub("", buffer))
        if value:
            blocks.append({"text": value, "line_start": start, "line_end": end})
        buffer = ""
        start = 0

    for number, raw in enumerate(raw_lines, start=1):
        line = _clean(raw)
        if _is_noise(line):
            filtered += 1
            flush(number - 1)
            continue
        if _is_heading(line):
            flush(number - 1)
            blocks.append({"text": line.strip(":："), "line_start": number, "line_end": number, "kind": "heading"})
            continue
        if not buffer:
            buffer, start = line, number
        elif _should_join(buffer, line):
            buffer = f"{buffer} {line}"
        else:
            flush(number - 1)
            buffer, start = line, number
    flush(len(raw_lines))
    return blocks, {
        "raw_lines": len(raw_lines),
        "reconstructed_blocks": len(blocks),
        "filtered_noise_lines": filtered,
    }


def _normalized_signature(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _category_for(source_type: str, statement: str) -> str:
    if source_type == "resume":
        if _DATE_OR_ROLE.search(statement) and not _ACTION_WORDS.search(statement):
            return "experience"
        if re.search(r"(?i)\b(?:python|sql|excel|tableau|mandarin|english|strategy|analytics)\b", statement) and len(statement) < 220:
            return "skill"
        return "achievement" if re.search(r"\d", statement) else "experience"
    return "project"


def structured_claim_candidates(text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    blocks, summary = reconstruct_blocks(text)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected = 0
    for block in blocks:
        statement = str(block["text"])
        if block.get("kind") == "heading" or len(statement) < 28 or len(statement) > 900 or _is_noise(statement):
            rejected += 1
            continue
        action = bool(_ACTION_WORDS.search(statement) or _FIRST_PERSON.search(statement))
        action_at_start = bool(_ACTION_WORDS.match(_BULLET.sub("", statement)) or _FIRST_PERSON.search(statement))
        quantified_action = bool(action and re.search(r"\d", statement))
        if source_type in {"project_case", "supporting_material"} and not action_at_start:
            rejected += 1
            continue
        if source_type == "resume" and not (action or quantified_action):
            rejected += 1
            continue
        signature = _normalized_signature(statement)
        if not signature or signature in seen:
            rejected += 1
            continue
        seen.add(signature)
        confidence = "HIGH" if action_at_start and len(statement) <= 500 else "MEDIUM"
        candidate_id = stable_id("EXT", source_id, str(block["line_start"]), statement)
        candidates.append({
            "candidate_id": candidate_id,
            "statement": statement,
            "category": _category_for(source_type, statement),
            "confidence": confidence,
            "selected": confidence == "HIGH",
            "provenance": {"line_start": block["line_start"], "line_end": block["line_end"]},
        })
    summary.update({"claim_candidates": len(candidates), "non_claim_blocks": rejected})
    return candidates, summary


def merge_resume_continuations(lines: list[str]) -> list[str]:
    """Join only obvious continuation lines without collapsing resume structure."""

    output: list[str] = []
    for raw in lines:
        line = _clean(raw)
        if not line:
            continue
        if output and not _is_heading(line) and _should_join(output[-1], line):
            output[-1] = _clean(f"{output[-1]} {line}")
        else:
            output.append(line)
    return output
