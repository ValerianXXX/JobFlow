"""Describe wording history without treating a hash or human edit as fact verification."""
from __future__ import annotations

from typing import Any, Iterable

from .util import canonical_json, sha256_bytes


ORIGINS = {"AI_CANDIDATE", "USER_EDITED", "USER_MERGED", "USER_SPLIT", "UNVERIFIED"}


def wording_hash(claim: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json({
        "statement": claim.get("statement"),
        "category": claim.get("category"),
        "entity_fingerprint": (claim.get("entity") or {}).get("entity_fingerprint"),
    }))


def wording_record(
    claim: dict[str, Any], origin: str, *, candidate_sha256: str | None = None,
    parents: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Called only with server-held candidates/parents, never a client provenance record."""
    return {
        "schema_version": 1, "origin": origin, "wording_sha256": wording_hash(claim),
        "candidate_sha256": candidate_sha256,
        "parents": [{"claim_id": item["claim_id"], "wording_sha256": wording_hash(item)} for item in parents],
    }


def wording_provenance(claim: dict[str, Any], record: Any = None, *, legacy_origin: str = "UNVERIFIED") -> dict[str, Any]:
    """Old records lack candidate/snapshot proof; do not reconstruct a fictional receipt."""
    current = wording_hash(claim)
    result = {"schema_version": 1, "origin": legacy_origin, "wording_sha256": current,
              "candidate_sha256": None, "parents": [], "matches_ai_candidate": False}
    if record is None:
        return result
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("origin") not in ORIGINS or record.get("wording_sha256") != current
            or not isinstance(record.get("parents"), list)
            or any(not isinstance(item, dict) or not isinstance(item.get("claim_id"), str)
                   or not _is_hash(item.get("wording_sha256")) for item in record["parents"])
            or (record.get("candidate_sha256") is not None and not _is_hash(record["candidate_sha256"]))):
        return {**result, "origin": "UNVERIFIED"}
    return {**result, "origin": record["origin"], "candidate_sha256": record.get("candidate_sha256"),
            "parents": [{"claim_id": item["claim_id"], "wording_sha256": item["wording_sha256"]} for item in record["parents"]],
            "matches_ai_candidate": record.get("candidate_sha256") == current and record["origin"] in {"AI_CANDIDATE", "USER_EDITED"}}


def _is_hash(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
            and all(character in "0123456789abcdef" for character in value[7:]))


def dependent_claim_ids(claims: Iterable[dict[str, Any]], changed_ids: set[str]) -> set[str]:
    """Walk deleted intermediate Claims too; cycles cannot cause an unbounded traversal."""
    claims = list(claims)
    affected = set(changed_ids)
    dependent: set[str] = set()
    while True:
        descendants = {item["claim_id"] for item in claims
                       if affected.intersection(item.get("provenance_claim_ids", []))}
        new = descendants - affected
        dependent.update(descendants)
        if not new:
            return dependent
        affected.update(new)
