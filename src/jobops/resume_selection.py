"""A formatting projection of current approved facts, never a new approval."""
from __future__ import annotations

from typing import Any

from .errors import JobOpsError
from .external_claims import approved_external_claims
from .util import canonical_json, sha256_bytes


STANDARD_RESUME_LAYOUT = "STANDARD_SINGLE_COLUMN_V2"


def select_resume_claims(
    claim_set: dict[str, Any], included_claim_ids: object = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    approved = approved_external_claims(claim_set, use="resume")
    by_id = {item["claim_id"]: item for item in approved}
    requested = list(by_id) if included_claim_ids is None else included_claim_ids
    if (not isinstance(requested, list) or not requested or len(requested) > len(approved)
            or any(not isinstance(value, str) or value not in by_id for value in requested)
            or len(set(requested)) != len(requested) or len(by_id) != len(approved)):
        raise JobOpsError("STANDARD_RESUME_SELECTION_INVALID", "Choose one or more different, currently approved facts.")
    ids = sorted(requested)
    selection = {"protocol": "STANDARD_RESUME_SELECTION_V1",
                 "claim_set_hash": claim_set["content_hash"], "included_claim_ids": ids}
    return [by_id[claim_id] for claim_id in ids], {
        "included_claim_ids": ids, "eligible_claim_count": len(approved),
        "excluded_claim_count": len(approved) - len(ids),
        "selection_hash": sha256_bytes(canonical_json(selection)),
    }


def validate_resume_selection(preview: dict[str, Any], claim_set: dict[str, Any]) -> dict[str, Any]:
    """Check saved preview/selection consistency; legacy drafts represent all facts."""
    fields = ("included_claim_ids", "eligible_claim_count", "excluded_claim_count", "selection_hash")
    present = [key in preview for key in fields]
    try:
        if any(present) and (not all(present) or preview["included_claim_ids"] is None):
            raise ValueError("incomplete selection receipt")
        claims, receipt = select_resume_claims(claim_set, preview.get("included_claim_ids"))
        if any(present) and any(preview[key] != receipt[key] for key in fields):
            raise ValueError("selection receipt mismatch")
        items = [item for section in preview["sections"] for item in section["items"]]
        expected = {item["claim_id"]: item for item in claims}
        if (len(items) != len(expected) or preview["claim_count"] != len(expected)
                or len({item["claim_id"] for item in items}) != len(expected)
                or any(item["claim_id"] not in expected
                       or item["text"] != expected[item["claim_id"]]["allowed_wording"][0] for item in items)):
            raise ValueError("preview wording mismatch")
    except (JobOpsError, KeyError, TypeError, ValueError) as exc:
        raise JobOpsError("STANDARD_RESUME_DRAFT_INVALID", "The saved fact selection does not match this draft.") from exc
    return {**preview, **receipt}
