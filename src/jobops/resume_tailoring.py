from __future__ import annotations

import re
from typing import Any

from .errors import JobOpsError
from .util import canonical_json, iso_utc, sha256_bytes, stable_id


TAILORABLE_CATEGORIES = (
    "summary", "work", "internship", "education", "project", "skill", "certification", "language",
)
MAX_TAILORING_BLOCKS = 200


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.casefold())


def _tokens(value: str) -> set[str]:
    output = {
        token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{2,}", value)
        if token.casefold() not in {"the", "and", "for", "with", "from", "present", "resume"}
    }
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,12}", value):
        output.add(phrase)
        output.update(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
    return output


def _claim_matches_block(claim: dict[str, Any], block: dict[str, Any], *, master_source_id: str | None) -> bool:
    if claim.get("decision") != "CONFIRMED" or claim.get("deleted") is True:
        return False
    category = str(claim.get("category", ""))
    if category not in TAILORABLE_CATEGORIES:
        return False
    source_id = str(claim.get("source_id") or "")
    if master_source_id and source_id != master_source_id:
        return False
    provenance = claim.get("provenance") if isinstance(claim.get("provenance"), dict) else {}
    try:
        line_start, line_end = int(provenance.get("line_start")), int(provenance.get("line_end"))
    except (TypeError, ValueError):
        line_start = line_end = 0
    if line_start and line_start <= int(block["line_number"]) <= line_end:
        return True
    statement, text = str(claim.get("statement") or ""), str(block.get("text") or "")
    left, right = _normalized(statement), _normalized(text)
    if len(right) >= 12 and (right in left or left in right):
        return True
    common = _tokens(statement) & _tokens(text)
    return len(common) >= 3 and len(common) / max(1, min(len(_tokens(statement)), len(_tokens(text)))) >= 0.45


def build_tailoring_proposal(
    *,
    onboarding_state_ref: str,
    master_resume: dict[str, Any],
    blocks: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    if not master_resume.get("editable_docx"):
        raise JobOpsError("EDITABLE_MASTER_DOCX_MISSING", "A DOCX Master Resume is required for safe tailoring.")
    master_source_id = str(master_resume.get("source_id") or "") or None
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        matched = [claim for claim in claims if _claim_matches_block(claim, block, master_source_id=master_source_id)]
        categories = sorted({str(item.get("category")) for item in matched if str(item.get("category")) in TAILORABLE_CATEGORIES})
        if not categories:
            continue
        text = str(block.get("text") or "")
        sentence_like = len(text) >= 20 and (
            bool(block.get("is_list")) or text[-1:] in ".?!。！？" or len(_tokens(text)) >= 5
        )
        candidates.append({
            "block_ref": str(block["block_ref"]), "part_name": str(block["part_name"]),
            "paragraph_index": int(block["paragraph_index"]), "line_number": int(block["line_number"]),
            "text": text, "original_text_sha256": str(block["text_sha256"]),
            "original_characters": int(block["text_length"]),
            "maximum_characters": max(160, min(2_000, int(block["text_length"] * 1.35) + 80)),
            "style_id": str(block.get("style_id") or ""), "is_list": bool(block.get("is_list")),
            "allowed_categories": categories,
            "matched_claim_ids": sorted({str(item["claim_id"]) for item in matched}),
            "recommended": bool(sentence_like),
        })
        if len(candidates) >= MAX_TAILORING_BLOCKS:
            break
    if not candidates:
        raise JobOpsError(
            "TAILORING_PROPOSAL_EMPTY",
            "No editable body block could be mapped to a confirmed AI-reviewed Claim from this Master Resume.",
        )
    hash_material = [{key: item[key] for key in (
        "block_ref", "part_name", "paragraph_index", "line_number", "original_text_sha256",
        "original_characters", "maximum_characters", "allowed_categories", "matched_claim_ids", "recommended",
    )} for item in candidates]
    proposal_hash = sha256_bytes(canonical_json({
        "onboarding_state_ref": onboarding_state_ref,
        "master_resume_sha256": master_resume.get("sha256"),
        "template_fingerprint": master_resume.get("template_fingerprint"),
        "candidates": hash_material,
    }))
    return {
        "status": "TAILORING_MANIFEST_PROPOSED", "proposal_hash": proposal_hash,
        "master_resume_sha256": master_resume.get("sha256"),
        "template_fingerprint": master_resume.get("template_fingerprint"),
        "candidate_count": len(candidates), "recommended_count": sum(item["recommended"] for item in candidates),
        "candidates": candidates, "private_values_persisted": 0, "real_external_actions": 0,
    }


def build_resume_tailoring_manifest(
    *,
    onboarding_state_ref: str,
    master_resume: dict[str, Any],
    proposal: dict[str, Any],
    selections: list[dict[str, Any]],
    expected_proposal_hash: str,
    user_confirmed: bool,
) -> dict[str, Any]:
    if user_confirmed is not True:
        raise JobOpsError("TAILORING_CONFIRMATION_REQUIRED", "Approving editable resume positions requires explicit confirmation.")
    if expected_proposal_hash != proposal.get("proposal_hash"):
        raise JobOpsError("TAILORING_PROPOSAL_STALE", "The reviewed tailoring proposal changed; open it again.")
    by_ref = {str(item["block_ref"]): item for item in proposal.get("candidates", [])}
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for selection in selections:
        block_ref, category = str(selection.get("block_ref", "")), str(selection.get("category", ""))
        candidate = by_ref.get(block_ref)
        if candidate is None or block_ref in seen or category not in candidate.get("allowed_categories", []):
            raise JobOpsError("TAILORING_SELECTION_INVALID", "A selected resume position or category is invalid.")
        seen.add(block_ref)
        blocks.append({
            "block_ref": block_ref, "part_name": str(candidate["part_name"]),
            "paragraph_index": int(candidate["paragraph_index"]),
            "original_text_sha256": str(candidate["original_text_sha256"]),
            "category": category, "maximum_characters": int(candidate["maximum_characters"]),
            "applicant_confirmed": True,
        })
    if not blocks:
        raise JobOpsError("TAILORING_SELECTION_EMPTY", "Choose at least one resume position for safe tailoring.")
    blocks.sort(key=lambda item: (item["part_name"], item["paragraph_index"]))
    approved_at = iso_utc()
    content: dict[str, Any] = {
        "schema_version": 1, "status": "TAILORING_MANIFEST_APPROVED",
        "onboarding_state_ref": onboarding_state_ref,
        "master_resume_ref": str(master_resume.get("secure_ref")),
        "master_resume_sha256": str(master_resume.get("sha256")),
        "template_fingerprint": str(master_resume.get("template_fingerprint")),
        "proposal_hash": expected_proposal_hash, "block_count": len(blocks), "blocks": blocks,
        "approved_at": approved_at, "applicant_confirmed": True, "real_external_actions": 0,
    }
    content["manifest_id"] = stable_id("RMF", onboarding_state_ref, content["master_resume_sha256"], expected_proposal_hash, approved_at)
    content["content_hash"] = sha256_bytes(canonical_json(content))
    return content


def validate_resume_tailoring_manifest_integrity(value: dict[str, Any]) -> None:
    expected = value.get("content_hash")
    material = {key: item for key, item in value.items() if key != "content_hash"}
    if expected != sha256_bytes(canonical_json(material)):
        raise JobOpsError("TAILORING_MANIFEST_HASH_INVALID", "The encrypted tailoring manifest failed its integrity check.")
    if value.get("block_count") != len(value.get("blocks", [])):
        raise JobOpsError("TAILORING_MANIFEST_COUNT_INVALID", "The tailoring block count is inconsistent.")
    if value.get("applicant_confirmed") is not True or any(item.get("applicant_confirmed") is not True for item in value.get("blocks", [])):
        raise JobOpsError("TAILORING_MANIFEST_APPROVAL_INVALID", "Every editable resume position requires applicant approval.")
