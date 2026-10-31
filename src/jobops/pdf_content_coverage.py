"""Bounded structural warnings for text-only PDF intake, never OCR validation.

Detect pages containing only pagination and substantial raster areas with no
overlapping alphanumeric text. Small images and partially represented images
remain unverified; passing this guard does not establish factual completeness.
"""
from __future__ import annotations

import math
from typing import Any

from .document_coverage import _PAGE, _ZH_PAGE
from .errors import JobOpsError

MAX_PAGE_IMAGES = 256
MAX_PAGE_CHARACTERS = 50_000
UNREPRESENTED_IMAGE_AREA_FRACTION = 1 / 8
_REASONS = frozenset({
    'PAGE_BODY_TEXT_MISSING', 'PAGE_RASTER_TEXT_MISSING',
    'PAGE_CONTENT_INSPECTION_LIMIT', 'PAGE_CONTENT_GEOMETRY_UNAVAILABLE',
})


def local_content_failure_code(error: JobOpsError) -> str:
    """Keep resource/geometry failures distinct from an OCR requirement."""
    reasons = error.details.get('document_quality', {}).get('reason_codes', [])
    if 'PAGE_CONTENT_INSPECTION_LIMIT' in reasons:
        return 'LOCAL_PARSER_LIMIT'
    if 'PAGE_CONTENT_GEOMETRY_UNAVAILABLE' in reasons:
        return 'LOCAL_PARSER_PROVENANCE_INVALID'
    return 'LOCAL_PARSER_NEEDS_OCR'


def _union_area(boxes: list[tuple[float, float, float, float]]) -> float:
    """Area of clipped image placements, counting overlaps exactly once."""
    xs = sorted({x for box in boxes for x in (box[0], box[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((top, bottom) for x0, top, x1, bottom in boxes if x0 < right and x1 > left)
        height = 0.0
        end = -math.inf
        for top, bottom in intervals:
            height += max(0.0, bottom - max(top, end))
            end = max(end, bottom)
        area += (right - left) * height
    return area


def inspect_pdf_page_content(page: Any, text: str) -> list[str]:
    """Return content-free reason codes using the already-open local PDF page.

    A 12.5% union-area threshold is a conservative routing policy, not a learned
    quality threshold. Large decorative photos can also require explicit visual
    review. Text over an image is not proof that its pixels were transcribed.
    """
    reasons = []
    remaining = []
    for line in text.splitlines():
        match = _PAGE.search(line.strip()) or _ZH_PAGE.search(line.strip())
        remaining.append(line.strip()[:match.start()] if match else line)
    if text.strip() and not any(c.isalnum() for line in remaining for c in line):
        reasons.append('PAGE_BODY_TEXT_MISSING')
    images = page.images
    if not images:
        return reasons
    if len(images) > MAX_PAGE_IMAGES or len(page.chars) > MAX_PAGE_CHARACTERS:
        return reasons + ['PAGE_CONTENT_INSPECTION_LIMIT']
    try:
        bounds = tuple(float(v) for v in page.bbox)
        if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
            raise ValueError()
        left, top, right, bottom = bounds
        if left >= right or top >= bottom:
            raise ValueError()
        centers = []
        for char in page.chars:
            if not any(c.isalnum() for c in char['text']):
                continue
            values = [float(char[key]) for key in ('x0', 'top', 'x1', 'bottom')]
            if not all(math.isfinite(v) for v in values):
                raise ValueError()
            centers.append(((values[0] + values[2]) / 2, (values[1] + values[3]) / 2))
        unrepresented = []
        for image in images:
            values = [float(image[key]) for key in ('x0', 'top', 'x1', 'bottom')]
            if not all(math.isfinite(v) for v in values) or values[0] > values[2] or values[1] > values[3]:
                raise ValueError()
            x0, y0 = max(left, values[0]), max(top, values[1])
            x1, y1 = min(right, values[2]), min(bottom, values[3])
            if x0 >= x1 or y0 >= y1:
                continue
            if not any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in centers):
                unrepresented.append((x0, y0, x1, y1))
        if _union_area(unrepresented) >= (right - left) * (bottom - top) * UNREPRESENTED_IMAGE_AREA_FRACTION:
            reasons.append('PAGE_RASTER_TEXT_MISSING')
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append('PAGE_CONTENT_GEOMETRY_UNAVAILABLE')
    return reasons


def require_pdf_content_reports(reports: Any, page_count: int) -> None:
    """Validate helper parity and reject unsafe partial text before model input."""
    if (type(page_count) is not int or page_count < 1 or not isinstance(reports, list) or len(reports) != page_count
            or any(not isinstance(report, list) or any(not isinstance(code, str) or code not in _REASONS for code in report)
                   for report in reports)):
        raise JobOpsError('PDF_PAGE_COVERAGE_UNAVAILABLE', 'The local PDF helper did not provide valid per-page content checks; no partial source was analyzed.')
    affected = [number for number, codes in enumerate(reports, 1) if codes]
    if not affected:
        return
    reasons = sorted({code for report in reports for code in report})
    if {'PAGE_BODY_TEXT_MISSING', 'PAGE_RASTER_TEXT_MISSING'} & set(reasons):
        reasons.append('OCR_REQUIRED')
    raise JobOpsError(
        'ONBOARDING_DOCUMENT_QUALITY_FAILED',
        'Some PDF page content is not represented by the text extraction. Check the original pages and explicitly choose a visual route or provide a readable complete source; no partial text was analyzed.',
        document_quality={'status': 'FAIL', 'reason_codes': reasons, 'page_count': page_count,
                          'affected_pages': affected, 'contains_document_text': False},
    )
