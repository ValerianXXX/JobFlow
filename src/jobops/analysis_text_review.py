"""Exact analyzed-input snapshots, kept only inside encrypted source state.

Reading a snapshot never extracts again, invokes a model or approves a fact.
Old sources and unstable segmented coordinates deliberately have no fallback.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import JobOpsError
from .util import sha256_bytes

MAX_TRANSCRIPT_CHARACTERS = 2_000_000
MAX_WINDOW_LINES = 100
MAX_WINDOW_CHARACTERS = 32_768
_HASH = re.compile(r"sha256:[0-9a-f]{64}")


def capture(text: str, *, source_id: str, source_hash: str, retained: bool, segmented: bool) -> tuple[dict | None, dict]:
    status = ("NOT_RETAINED" if not retained else "COORDINATES_UNAVAILABLE" if segmented
              else "LIMIT_EXCEEDED" if len(text) > MAX_TRANSCRIPT_CHARACTERS else "AVAILABLE")
    descriptor: dict[str, Any] = {"status": status, "fact_completeness_verified": False}
    if status != "AVAILABLE":
        return None, descriptor
    snapshot = {"schema_version": 1, "source_id": source_id, "source_sha256": source_hash,
                "text_sha256": sha256_bytes(text.encode("utf-8")), "text": text}
    descriptor.update(source_sha256=source_hash, text_sha256=snapshot["text_sha256"], total_lines=len(text.splitlines()))
    return snapshot, descriptor


def window(source: dict, raw: bytes, request: dict) -> dict:
    permitted = {"source_id", "expected_source_sha256", "expected_text_sha256", "line_start"}
    if (set(request) != permitted or not isinstance(request.get("source_id"), str)
            or any(not isinstance(request.get(key), str) or not _HASH.fullmatch(request[key])
                   for key in ("expected_source_sha256", "expected_text_sha256"))
            or type(request.get("line_start")) is not int or request["line_start"] < 1):
        raise JobOpsError("SOURCE_TEXT_REVIEW_REQUEST_INVALID", "Choose a valid analysis-text location from the current source review.")
    snapshot = source.get("_analysis_transcript")
    if not isinstance(snapshot, dict) or source.get("raw_retained") is not True:
        raise JobOpsError("SOURCE_TEXT_REVIEW_UNAVAILABLE", "No saved analysis text is available. Review the original; re-analysis is a separate action.")
    if request["expected_source_sha256"] != source.get("sha256") or request["expected_text_sha256"] != snapshot.get("text_sha256"):
        raise JobOpsError("SOURCE_TEXT_REVIEW_STALE", "The source analysis changed. Refresh its review before opening these lines.")
    text = snapshot.get("text")
    if (snapshot.get("schema_version") != 1 or snapshot.get("source_id") != source.get("source_id")
            or snapshot.get("source_id") != request["source_id"] or snapshot.get("source_sha256") != source.get("sha256")
            or not isinstance(text, str) or not 0 < len(text) <= MAX_TRANSCRIPT_CHARACTERS
            or sha256_bytes(text.encode("utf-8")) != snapshot.get("text_sha256") or sha256_bytes(raw) != source.get("sha256")):
        raise JobOpsError("SOURCE_TEXT_REVIEW_MISMATCH", "Saved source or analysis text failed its integrity check. No text was displayed.")
    lines = text.splitlines()
    start = request["line_start"]
    if start > len(lines):
        raise JobOpsError("SOURCE_TEXT_REVIEW_REQUEST_INVALID", "The requested line is outside this saved analysis text.")
    rows, used = [], 0
    for number in range(start, min(len(lines) + 1, start + MAX_WINDOW_LINES)):
        line = lines[number - 1]
        if used + len(line) > MAX_WINDOW_CHARACTERS:
            break
        rows.append({"number": number, "text": line})
        used += len(line)
    if not rows:
        raise JobOpsError("SOURCE_TEXT_REVIEW_LIMIT", "This single line exceeds the display limit. Check the original source.")
    last = rows[-1]["number"]
    return {"status": "SAVED_ANALYSIS_TEXT", "source_id": source["source_id"],
            "source_sha256": source["sha256"], "text_sha256": snapshot["text_sha256"],
            "lines": rows, "first_line": start, "last_line": last, "total_lines": len(lines),
            "next_line": last + 1 if last < len(lines) else None,
            "fact_completeness_verified": False, "real_external_actions": 0}
