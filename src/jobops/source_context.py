"""Unconfirmed document context, separate from Claims and Profile answers.

The model selects a bounded source span and an advisory destination. The host
copies the excerpt; model-written values, summaries and approval flags are absent.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .util import sha256_bytes, stable_id


MAX_CONTEXT_NOTES = 64
MAX_CONTEXT_LINES = 8
MAX_CONTEXT_CHARACTERS = 2_000
CONTEXT_FIELDS = frozenset({
    "remote_preference", "preferred_locations", "available_start_date",
    "work_authorization", "visa_sponsorship", "minimum_salary",
})


class DocumentAnalysisResult(tuple):
    """Keep the two-value analysis API while separating private context payload.

    Existing callers can still unpack/index candidates and safe summary. The
    onboarding service explicitly consumes context_notes into encrypted state;
    the notes are never placed in the diagnostic summary dictionary.
    """

    def __new__(cls, candidates, summary, context_notes=()):
        value = super().__new__(cls, (candidates, summary))
        value._context_notes = tuple(deepcopy(context_notes))
        return value

    @property
    def context_notes(self):
        return deepcopy(self._context_notes)

    def __getnewargs__(self):
        return self[0], self[1], self._context_notes


def quoted_context(selector, *, source_id: str, source_lines: list[str], line_number_start=1):
    if not isinstance(selector, dict) or set(selector) != {"kind", "field_id", "line_start", "line_end"}:
        return None
    kind, field = selector["kind"], selector["field_id"]
    if not isinstance(field, str) or not (
        kind == "profile_constraint" and field in CONTEXT_FIELDS
        or kind == "evidence_gap" and field == ""
    ):
        return None
    start, end = selector["line_start"], selector["line_end"]
    if type(start) is not int or type(end) is not int or not (
        line_number_start <= start <= end < line_number_start + len(source_lines)
        and end - start + 1 <= MAX_CONTEXT_LINES
    ):
        return None
    quote = "\n".join(source_lines[start - line_number_start:end - line_number_start + 1]).strip()
    if not quote or len(quote) > MAX_CONTEXT_CHARACTERS:
        return None
    digest = sha256_bytes(quote.encode("utf-8"))
    return {
        "note_id": stable_id("CTX", source_id, kind, field, str(start), str(end), digest),
        "source_id": source_id, "kind": kind, "field_id": field,
        "line_start": start, "line_end": end, "coordinate_space": "ANALYZED_TEXT_LINES",
        "quote": quote, "quote_sha256": digest,
        "status": "UNCONFIRMED_SOURCE_CONTEXT", "approved_for_external": False,
    }


class SourceContextAccumulator:
    def __init__(self, source_id: str, *, enabled=True):
        self.source_id, self.enabled = source_id, enabled
        self.notes: list[dict[str, Any]] = []
        self.proposed = self.filtered = self.duplicates = 0
        self.provided_chunks = self.missing_chunks = 0
        self._seen: set[str] = set()

    def add(self, value, source_lines: list[str], *, line_number_start=1):
        if not self.enabled:
            return
        if not isinstance(value, dict) or "review_notes" not in value:
            self.missing_chunks += 1
            return
        self.provided_chunks += 1
        selectors = value["review_notes"]
        if not isinstance(selectors, list):
            self.filtered += 1
            return
        self.proposed += len(selectors)
        if len(selectors) > MAX_CONTEXT_NOTES:
            self.filtered += len(selectors)
            return
        for selector in selectors:
            note = quoted_context(selector, source_id=self.source_id, source_lines=source_lines,
                                  line_number_start=line_number_start)
            if note is None:
                self.filtered += 1
            elif note["note_id"] in self._seen:
                self.duplicates += 1
            elif len(self.notes) >= MAX_CONTEXT_NOTES:
                self.filtered += 1
            else:
                self.notes.append(note)
                self._seen.add(note["note_id"])

    def summary(self):
        status = ("DISABLED_FOR_CHAT_EXPORT" if not self.enabled else
                  "NOT_PROVIDED" if not self.provided_chunks else
                  "PARTIAL" if self.missing_chunks else "PROVIDED")
        return {
            "contract_version": 1, "status": status, "proposed": self.proposed,
            "retained": len(self.notes), "filtered": self.filtered, "duplicates": self.duplicates,
            "provided_chunks": self.provided_chunks, "missing_chunks": self.missing_chunks,
            "automatic_profile_update": False, "automatic_claim_creation": False,
            "semantic_completeness_verified": False,
        }


def bind_context_notes(notes, *, text: str, source_id: str, source_sha256: str):
    """Recheck excerpts against the current text before private persistence."""
    if not isinstance(notes, (list, tuple)) or len(notes) > MAX_CONTEXT_NOTES:
        return []
    lines = text.splitlines() or [text]
    output, seen = [], set()
    for raw in notes:
        if not isinstance(raw, dict) or raw.get("source_id") != source_id:
            continue
        selector = {key: raw.get(key) for key in ("kind", "field_id", "line_start", "line_end")}
        note = quoted_context(selector, source_id=source_id, source_lines=lines)
        if note is None or raw.get("quote") != note["quote"] or raw.get("quote_sha256") != note["quote_sha256"]:
            continue
        if note["note_id"] in seen:
            continue
        seen.add(note["note_id"])
        output.append({**note, "source_sha256": source_sha256})
    return output


def safe_context_summary(value, *, retained: int, status: str | None = None):
    value = value if isinstance(value, dict) else {}
    allowed = {"PROVIDED", "PARTIAL", "NOT_PROVIDED", "DISABLED_FOR_CHAT_EXPORT", "UNAVAILABLE_SEGMENTED_COORDINATES"}
    status = status or value.get("status")
    output = {"contract_version": 1, "status": status if status in allowed else "NOT_PROVIDED"}
    for key in ("proposed", "filtered", "duplicates", "provided_chunks", "missing_chunks"):
        count = value.get(key, 0)
        output[key] = count if type(count) is int and 0 <= count <= 1_000_000 else 0
    return {**output, "retained": retained, "automatic_profile_update": False,
            "automatic_claim_creation": False, "semantic_completeness_verified": False}
