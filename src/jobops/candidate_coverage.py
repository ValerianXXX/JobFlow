"""Content-free citation coverage for source review, never semantic correctness."""
from __future__ import annotations

from typing import Any

MAX_VISIBLE_GAP_RANGES = 40


def source_reference_coverage(text: str, candidates: list[dict[str, Any]], *, segmented_lines: int = 0) -> dict[str, Any]:
    lines = text.splitlines()
    content = {index for index, line in enumerate(lines, 1) if line.strip()}
    if segmented_lines:
        return {"schema_version": 1, "status": "COORDINATES_UNAVAILABLE",
                "reason": "AI_SEGMENTED_OVERSIZE_LINES", "semantic_recall": None,
                "fact_completeness_verified": False}
    cited: set[int] = set()
    invalid = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            invalid += 1
            continue
        references = [candidate.get("provenance")]
        if candidate.get("entity") is not None:
            references.append(candidate["entity"])
        for reference in references:
            if not isinstance(reference, dict):
                invalid += 1
                continue
            start, end = reference.get("line_start"), reference.get("line_end")
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                invalid += 1
                continue
            cited.update(range(start, end + 1))
    cited &= content
    gaps = sorted(content - cited)
    ranges: list[dict[str, int]] = []
    for line in gaps:
        if ranges and line == ranges[-1]["line_end"] + 1:
            ranges[-1]["line_end"] = line
        else:
            ranges.append({"line_start": line, "line_end": line})
    return {"schema_version": 1, "status": "LINE_REFERENCES_ONLY",
            "nonempty_source_lines": len(content), "cited_source_lines": len(cited),
            "uncited_source_lines": len(gaps), "uncited_line_ranges": ranges[:MAX_VISIBLE_GAP_RANGES],
            "range_list_truncated": len(ranges) > MAX_VISIBLE_GAP_RANGES,
            "invalid_reference_count": invalid, "semantic_recall": None,
            "fact_completeness_verified": False}
