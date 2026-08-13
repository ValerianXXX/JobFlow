from __future__ import annotations

import re
from typing import Any


PRIVATE_USE_GLYPHS = re.compile(r"[\ue000-\uf8ff]")
CONTROL_GLYPHS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WORDISH = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


def document_text_preflight(
    text: str,
    *,
    extension: str,
    page_count: int | None = None,
) -> dict[str, Any]:
    """Return content-free extraction diagnostics before private text reaches AI.

    The report deliberately contains counts and reason codes only. It is safe to
    attach to errors and redacted onboarding metadata without copying document
    text, names, contact details, paths, or model output.
    """

    normalized_extension = extension.casefold().strip()
    lines = [line for line in text.splitlines() if line.strip()]
    characters = len(text)
    replacement_characters = text.count("\ufffd")
    private_use_characters = len(PRIVATE_USE_GLYPHS.findall(text))
    control_characters = len(CONTROL_GLYPHS.findall(text))
    wordish_characters = len(WORDISH.findall(text))
    short_lines = sum(len(line.strip()) <= 3 for line in lines)
    long_lines = sum(len(line) >= 500 for line in lines)
    unique_lines = len({re.sub(r"\s+", " ", line).strip().casefold() for line in lines})
    denominator = max(1, characters)
    line_denominator = max(1, len(lines))
    reason_codes: list[str] = []

    if characters == 0 or wordish_characters == 0:
        reason_codes.append("NO_EXTRACTABLE_TEXT")
        if normalized_extension == ".pdf":
            reason_codes.append("OCR_REQUIRED")
    if replacement_characters / denominator > 0.002:
        reason_codes.append("TEXT_DECODING_CORRUPTION")
    if control_characters / denominator > 0.001:
        reason_codes.append("CONTROL_CHARACTER_CORRUPTION")
    if private_use_characters:
        reason_codes.append("CUSTOM_FONT_GLYPHS_PRESENT")
    if len(lines) >= 20 and short_lines / line_denominator > 0.60:
        reason_codes.append("READING_ORDER_FRAGMENTATION_RISK")
    if long_lines:
        reason_codes.append("TABLE_OR_LAYOUT_FLATTENING_RISK")
    if len(lines) >= 20 and unique_lines / line_denominator < 0.50:
        reason_codes.append("REPEATED_HEADER_FOOTER_RISK")
    if page_count and page_count > 0 and characters / page_count < 40:
        reason_codes.append("LOW_TEXT_DENSITY")
        if normalized_extension == ".pdf" and "OCR_REQUIRED" not in reason_codes:
            reason_codes.append("OCR_RECOMMENDED")

    fatal = any(
        code in reason_codes
        for code in ("NO_EXTRACTABLE_TEXT", "TEXT_DECODING_CORRUPTION", "CONTROL_CHARACTER_CORRUPTION")
    )
    warning = bool(reason_codes)
    status = "FAIL" if fatal else ("WARNING" if warning else "PASS")
    recommendation = (
        "USE_OCR_OR_A_TEXT_BASED_DOCX"
        if "OCR_REQUIRED" in reason_codes
        else "TRY_EDITABLE_DOCX_OR_SIMPLER_PDF"
        if status == "WARNING"
        else "READY_FOR_STRUCTURED_AI"
    )
    return {
        "schema_version": 1,
        "status": status,
        "extension": normalized_extension,
        "page_count": page_count,
        "character_count": characters,
        "nonempty_line_count": len(lines),
        "wordish_character_count": wordish_characters,
        "replacement_character_count": replacement_characters,
        "private_use_glyph_count": private_use_characters,
        "control_character_count": control_characters,
        "short_line_ratio": round(short_lines / line_denominator, 4),
        "long_line_count": long_lines,
        "unique_line_ratio": round(unique_lines / line_denominator, 4),
        "characters_per_page": round(characters / page_count, 1) if page_count else None,
        "reason_codes": reason_codes,
        "recommendation": recommendation,
        "contains_document_text": False,
    }


def document_quality_rank(report: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Rank two content-free extraction reports without exposing source text.

    A clean extraction wins before character volume.  This prevents a PDF
    layout extractor from winning merely because it emitted a large amount of
    whitespace, duplicated headers, or custom-font glyphs.
    """

    status_rank = {"FAIL": 0, "WARNING": 1, "PASS": 2}.get(str(report.get("status")), 0)
    corruption = (
        int(report.get("replacement_character_count") or 0)
        + int(report.get("private_use_glyph_count") or 0)
        + int(report.get("control_character_count") or 0)
    )
    layout_risks = sum(
        code in set(report.get("reason_codes") or [])
        for code in (
            "READING_ORDER_FRAGMENTATION_RISK",
            "TABLE_OR_LAYOUT_FLATTENING_RISK",
            "REPEATED_HEADER_FOOTER_RISK",
        )
    )
    return (
        status_rank,
        -corruption,
        -layout_risks,
        int(report.get("wordish_character_count") or 0),
        int(report.get("nonempty_line_count") or 0),
    )


def safe_ai_failure_category(code: str, details: dict[str, Any] | None = None) -> str:
    """Collapse implementation errors into stable, non-private UI categories."""

    details = details or {}
    explicit = details.get("failure_category")
    if isinstance(explicit, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", explicit):
        return explicit
    if code in {"AI_ENGINE_UNAVAILABLE", "AI_AGENT_UNAVAILABLE", "AI_ENGINE_FAILED"}:
        return "AI_TRANSPORT_OR_RUNTIME"
    if code in {"AI_RESPONSE_INVALID", "AI_RESPONSE_REPAIR_FAILED"}:
        return "STRUCTURED_OUTPUT_CONTRACT"
    return "UNKNOWN_AI_FAILURE"
