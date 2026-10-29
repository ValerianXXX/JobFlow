"""Bounded opt-in PDF geometry reader. No OCR, model, network or Claim creation.

Column ordering is a geometric proposal, not semantic interpretation. Ruled
tables retain their row order; scans, rotated text and missing pages fail closed.
Only the parent uses this process's text, through the existing review boundary.
"""
from __future__ import annotations

import base64
from importlib.metadata import version
import io
import json
import logging
import math
import statistics
import sys


def _rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not rows or abs(word["top"] - statistics.median(w["top"] for w in rows[-1])) > 3:
            rows.append([word])
        else:
            rows[-1].append(word)
    return [sorted(row, key=lambda w: w["x0"]) for row in rows]


def _split(row: list[dict], x: float) -> tuple[list[dict], list[dict], bool]:
    return ([w for w in row if w["x1"] <= x], [w for w in row if w["x0"] >= x],
            any(w["x0"] < x < w["x1"] for w in row))


def _support(parts: list[tuple[list[dict], list[dict], bool]]) -> int:
    left = [a for a, _, crossing in parts if a and not crossing]
    right = [b for _, b, crossing in parts if b and not crossing]
    if len(left) < 3 or len(right) < 3:
        return 0
    if min(max(r[0]["top"] for r in left), max(r[0]["top"] for r in right)) <= max(
        min(r[0]["top"] for r in left), min(r[0]["top"] for r in right)):
        return 0
    return min(len(left), len(right))


def _ordered_rows(rows: list[list[dict]], width: float, ruled: bool) -> tuple[list[list[dict]], bool]:
    if ruled or len(rows) < 3:
        return rows, False
    # A repeated empty gutter is required. One aligned date or wide word gap
    # cannot alone turn a page into columns. Full-width lines partition bands.
    gaps = []
    for row in rows:
        for left, right in zip(row, row[1:]):
            if right["x0"] - left["x1"] >= 24:
                x = (right["x0"] + left["x1"]) / 2
                if width * .2 < x < width * .8:
                    gaps.append(x)
    # Different column baselines need not align. Repeated starts plus an empty
    # gutter and overlapping vertical ranges also provide geometric support.
    starts: dict[int, list[list[dict]]] = {}
    for row in rows:
        starts.setdefault(round(row[0]["x0"] / 12), []).append(row)
    groups = [group for group in starts.values() if len(group) >= 3]
    for left in groups:
        for right in groups:
            right_start = min(row[0]["x0"] for row in right)
            if right_start - max(row[0]["x0"] for row in left) < width * .2:
                continue
            ends = [row[-1]["x1"] for row in left if row[-1]["x1"] <= right_start - 24]
            if len(ends) >= 3:
                gaps.append((max(ends) + right_start) / 2)
    best = None
    for x in sorted(set(round(gap, 1) for gap in gaps)):
        support = _support([_split(row, x) for row in rows])
        if support >= 3 and (best is None or support > best[0]):
            best = support, x
    if best is None:
        return rows, False
    x = best[1]
    ordered: list[list[dict]] = []
    band: list[list[dict]] = []
    changed = False

    def flush() -> None:
        nonlocal changed
        parts = [_split(row, x) for row in band]
        if _support(parts) >= 3:
            ordered.extend(a for a, _, _ in parts if a)
            ordered.extend(b for _, b, _ in parts if b)
            changed = True
        else:
            ordered.extend(band)
        band.clear()

    for row in rows:
        if _split(row, x)[2]:
            flush()
            ordered.append(row)
        else:
            band.append(row)
    flush()
    return ordered, changed


def parse_region_pdf(raw: bytes) -> dict:
    import pdfplumber

    pages = []
    total_characters = 0
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if not 1 <= len(pdf.pages) <= 6:
            raise ValueError("LOCAL_PARSER_LIMIT")
        for number, page in enumerate(pdf.pages, 1):
            if not all(math.isfinite(float(v)) and float(v) > 0 for v in (page.width, page.height)):
                raise ValueError("LOCAL_PARSER_PROVENANCE_INVALID")
            if len(page.chars) > 50000:
                raise ValueError("LOCAL_PARSER_LIMIT")
            words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
            if not words:
                raise ValueError("LOCAL_PARSER_NEEDS_OCR")
            if any(not w.get("upright", True) for w in words):
                raise ValueError("LOCAL_PARSER_PROVENANCE_INVALID")
            origin_x, origin_y = page.bbox[:2]
            for word in words:
                if not isinstance(word.get("text"), str) or not word["text"].strip():
                    raise ValueError("LOCAL_PARSER_PROVENANCE_INVALID")
                for key, origin, limit in (("x0", origin_x, page.width), ("x1", origin_x, page.width),
                                            ("top", origin_y, page.height), ("bottom", origin_y, page.height)):
                    value = float(word[key]) - origin
                    if not math.isfinite(value) or not 0 <= value <= limit:
                        raise ValueError("LOCAL_PARSER_PROVENANCE_INVALID")
                    word[key] = value
            # Conservative table policy. Even decorative rules may retain row
            # order; unruled tables remain an explicit unsupported ambiguity.
            ruled = bool(page.lines or page.rects)
            physical_rows = _rows(words)
            if len(physical_rows) > 500:
                raise ValueError("LOCAL_PARSER_LIMIT")
            rows, reordered = _ordered_rows(physical_rows, float(page.width), ruled)
            blocks = []
            for row in rows:
                text = " ".join(w["text"] for w in row)
                if not 1 <= len(text) <= 1200:
                    raise ValueError("LOCAL_PARSER_LIMIT")
                total_characters += len(text)
                blocks.append({"text": text, "bbox": [
                    min(w["x0"] for w in row) / page.width,
                    min(w["top"] for w in row) / page.height,
                    max(w["x1"] for w in row) / page.width,
                    max(w["bottom"] for w in row) / page.height]})
            if not 1 <= len(blocks) <= 500 or total_characters > 200000:
                raise ValueError("LOCAL_PARSER_LIMIT")
            pages.append({"page": number, "blank": False, "blocks": blocks,
                          "column_reordered": reordered, "table_order_retained": ruled})
    return {"pages": pages, "parser_version": "pdf-regions-1", "backend_version": version("pdfplumber")}


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        request = json.loads(sys.stdin.buffer.read(23 * 1024 * 1024 + 1))
        if not isinstance(request, dict) or set(request) != {"pdf_base64"}:
            raise ValueError("LOCAL_PARSER_REQUEST_INVALID")
        raw = base64.b64decode(request["pdf_base64"], validate=True)
        if not raw or len(raw) > 16 * 1024 * 1024:
            raise ValueError("LOCAL_PARSER_LIMIT")
        response = {"ok": True, "result": parse_region_pdf(raw)}
    except ImportError:
        response = {"ok": False, "code": "LOCAL_PARSER_NOT_INSTALLED"}
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "LOCAL_PARSER_REQUEST_INVALID", "LOCAL_PARSER_LIMIT", "LOCAL_PARSER_NEEDS_OCR",
            "LOCAL_PARSER_PROVENANCE_INVALID"} else "LOCAL_PARSER_FAILED"
        response = {"ok": False, "code": code}
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
