"""Content-free completeness checks, not a claim that every fact was understood."""
from __future__ import annotations

import re
from .errors import JobOpsError

_PAGE = re.compile(r"(?:^|\|)\s*Page\s+(\d{1,4})\s+(?:of|/)\s+(\d{1,4})\s*$", re.I)
_ZH_PAGE = re.compile(r"(?:^|\|)\s*第\s*(\d{1,4})\s*页\s*(?:[,，]\s*共?|/|共)\s*(\d{1,4})\s*页?\s*$")


def require_complete_page_text(parts: list[str]) -> None:
    """Reject empty pages or explicit inconsistent pagination before analysis.

    Numbering is checked only when at least two page-edge labels are present.
    Unnumbered omissions, image content beside a text layer and footer-only
    pages are not proved complete by this check. Preserve original review.
    """
    empty = [i for i, part in enumerate(parts, 1) if not any(char.isalnum() for char in part)]
    declared = []
    for index, part in enumerate(parts, 1):
        lines = [line.strip() for line in part.splitlines() if line.strip()]
        labels = set()
        for line in lines[:3] + lines[-3:]:
            match = _PAGE.search(line) or _ZH_PAGE.search(line)
            if match:
                labels.add(tuple(map(int, match.groups())))
        if labels:
            declared.append((index, labels))
    # Numbered sections may have an unnumbered cover or addendum. Validate
    # each declared sequence rather than comparing its total to the whole PDF.
    # Complete repeated chapter sequences are allowed; gaps remain explicit.
    sequences: dict[int, list[int]] = {}
    inconsistent = False
    if len(declared) >= 2:
        for _, labels in declared:
            if len(labels) != 1:
                inconsistent = True
            for number, total in sorted(labels):
                sequences.setdefault(total, []).append(number)
        inconsistent = inconsistent or any(
            total < 1 or len(numbers) % total != 0
            or any(number != index % total + 1 for index, number in enumerate(numbers))
            for total, numbers in sequences.items()
        )
    if empty or inconsistent:
        codes = []
        if empty:
            codes += ['PAGE_TEXT_MISSING', 'OCR_REQUIRED']
        if inconsistent:
            codes.append('PRINTED_PAGINATION_INCOMPLETE')
        raise JobOpsError(
            'ONBOARDING_DOCUMENT_QUALITY_FAILED',
            'The PDF has unreadable pages or inconsistent printed pagination. Check every original page, supply the complete source or explicitly use the visual route; no partial text was analyzed.',
            document_quality={'status': 'FAIL', 'reason_codes': codes, 'page_count': len(parts),
                              'unreadable_pages': empty, 'pagination_inconsistent': bool(inconsistent),
                              'contains_document_text': False},
        )
