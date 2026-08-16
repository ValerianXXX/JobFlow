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


def choose_tailoring_replacements(
    *, manifest: dict[str, Any], external_claim_set: dict[str, Any], job_text: str,
    preferred_claim_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """Choose only exact approved Claim wording for approved paragraph categories.

    This ranker does not rewrite text.  It selects a one-to-one subset whose wording
    overlaps the saved JD; the selected Claim text is still shown in the review packet.
    """

    from .external_claims import approved_external_claims

    validate_resume_tailoring_manifest_integrity(manifest)
    claims = approved_external_claims(external_claim_set, use="resume")
    job_tokens = _tokens(job_text)
    if not job_tokens:
        raise JobOpsError("TAILORING_JOB_TEXT_INVALID", "The saved job description has no usable relevance terms.")
    preference = {claim_id: index for index, claim_id in enumerate(preferred_claim_ids or [])}
    by_category: dict[str, list[tuple[int, int, str, dict[str, Any]]]] = {}
    for claim in claims:
        wording = str(claim["allowed_wording"][0]).strip()
        claim_tokens = _tokens(wording)
        score = len(job_tokens & claim_tokens)
        claim_id = str(claim["claim_id"])
        if score < 1 and claim_id not in preference:
            continue
        category = str(claim.get("category", ""))
        by_category.setdefault(category, []).append((
            preference.get(claim_id, len(preference) + 1_000), -score, claim_id, claim,
        ))
    for values in by_category.values():
        values.sort(key=lambda item: (item[0], item[1], item[2]))

    used: set[str] = set()
    replacements: list[dict[str, str]] = []
    for block in sorted(manifest.get("blocks", []), key=lambda item: (str(item["part_name"]), int(item["paragraph_index"]))):
        candidates = [item for item in by_category.get(str(block["category"]), []) if item[1] not in used]
        if not candidates:
            continue
        _, _, claim_id, claim = candidates[0]
        wording = str(claim["allowed_wording"][0]).strip()
        if len(wording) > int(block["maximum_characters"]):
            continue
        used.add(claim_id)
        replacements.append({"block_ref": str(block["block_ref"]), "claim_id": claim_id})
    if not replacements:
        raise JobOpsError(
            "TAILORING_RELEVANCE_INSUFFICIENT",
            "No applicant-approved Claim both matches the saved job description and fits an approved resume position.",
        )
    return replacements


def choose_template_replacements(
    *, template_slots: list[str], external_claim_set: dict[str, Any], job_text: str,
    candidate_display_name: str, target_role: str,
    preferred_claim_ids: list[str] | None = None,
) -> dict[str, str]:
    """Fill legacy explicit slots with exact approved wording and public job context."""

    from .external_claims import approved_external_claims

    slots = set(template_slots)
    output: dict[str, str] = {}
    if "CANDIDATE_NAME" in slots:
        output["CANDIDATE_NAME"] = candidate_display_name
    if "TARGET_ROLE" in slots:
        output["TARGET_ROLE"] = target_role
    category_map = {
        "SUMMARY": ("summary",), "EXPERIENCE_BULLET": ("work", "internship"),
        "PROJECT": ("project",), "SKILLS": ("skill",), "EDUCATION": ("education",),
    }
    job_tokens = _tokens(job_text)
    claims = approved_external_claims(external_claim_set, use="resume")
    preference = {claim_id: index for index, claim_id in enumerate(preferred_claim_ids or [])}
    used: set[str] = set()
    for slot, categories in category_map.items():
        if slot not in slots:
            continue
        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        for claim in claims:
            claim_id = str(claim["claim_id"])
            if claim_id in used or str(claim.get("category")) not in categories:
                continue
            score = len(job_tokens & _tokens(str(claim["allowed_wording"][0])))
            if score or claim_id in preference:
                ranked.append((preference.get(claim_id, len(preference) + 1_000), -score, claim_id, claim))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        if not ranked:
            raise JobOpsError(
                "TAILORING_RELEVANCE_INSUFFICIENT",
                "No applicant-approved Claim matches a required template slot and the saved job description.",
                slot=slot,
            )
        _, _, claim_id, claim = ranked[0]
        used.add(claim_id)
        output[slot] = str(claim["allowed_wording"][0])
    return output
