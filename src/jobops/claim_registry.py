from __future__ import annotations

import json
from typing import Any, Iterable

from .claims import validate_claim_shape, verify_claim_evidence
from .db import JobOpsDB
from .errors import JobOpsError
from .util import canonical_json, iso_utc, sha256_bytes


def _content_hash(claim: dict[str, Any]) -> str:
    material = {key: value for key, value in claim.items() if key not in {"approved_for_external", "lifecycle_status", "version"}}
    return sha256_bytes(canonical_json(material))


class ClaimRegistry:
    def __init__(self, database: JobOpsDB, gateway) -> None:
        self.database = database
        self.gateway = gateway

    @staticmethod
    def _row_to_claim(row) -> dict[str, Any]:
        return {
            "claim_id": row["claim_id"], "raw_fact": row["raw_fact"],
            "allowed_wording": json.loads(row["allowed_wording_json"]),
            "forbidden_wording": json.loads(row["forbidden_wording_json"]),
            "responsibility_boundary": json.loads(row["responsibility_boundary_json"]),
            "evidence": json.loads(row["evidence_json"]), "source_refs": json.loads(row["source_refs_json"]),
            "approved_for_external": bool(row["approved_for_external"]), "sensitivity": row["sensitivity"],
            "last_verified_at": row["last_verified_at"], "expires_at": row["expires_at"],
            "lifecycle_status": row["lifecycle_status"], "content_hash": row["content_hash"], "version": row["version"],
        }

    def get(self, claim_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise JobOpsError("CLAIM_NOT_FOUND", "Claim does not exist.", claim_id=claim_id)
        return self._row_to_claim(row)

    def propose(self, claim: dict[str, Any]) -> dict[str, Any]:
        value = dict(claim)
        value["approved_for_external"] = False
        value["lifecycle_status"] = "proposed"
        validate_claim_shape(value)
        content_hash = _content_hash(value)
        now = iso_utc()
        with self.database.connect() as connection:
            existing = connection.execute("SELECT content_hash FROM claims WHERE claim_id=?", (value["claim_id"],)).fetchone()
            if existing:
                if existing[0] == content_hash:
                    return self.get(value["claim_id"])
                raise JobOpsError("CLAIM_ID_CONFLICT", "Claim ID already exists with different content.")
            connection.execute(
                """INSERT INTO claims(
                claim_id,raw_fact,allowed_wording_json,forbidden_wording_json,responsibility_boundary_json,
                evidence_json,source_refs_json,approved_for_external,sensitivity,last_verified_at,expires_at,updated_at,
                lifecycle_status,content_hash,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    value["claim_id"], value["raw_fact"], json.dumps(value["allowed_wording"], ensure_ascii=False),
                    json.dumps(value["forbidden_wording"], ensure_ascii=False), json.dumps(value["responsibility_boundary"], ensure_ascii=False),
                    json.dumps(value["evidence"], ensure_ascii=False), json.dumps(value["source_refs"], ensure_ascii=False),
                    0, value["sensitivity"], value["last_verified_at"], value["expires_at"], now, "proposed", content_hash,
                ),
            )
            connection.execute(
                """INSERT INTO claim_events(claim_id,event_type,claim_content_hash,source_hashes_json,
                responsibility_boundary_json,allowed_uses_json,sensitivity,occurred_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    value["claim_id"], "PROPOSED", content_hash,
                    json.dumps([ref.get("fingerprint") for ref in value["source_refs"]]),
                    json.dumps(value["responsibility_boundary"], ensure_ascii=False), "[]", value["sensitivity"], now, value["expires_at"],
                ),
            )
        return self.get(value["claim_id"])

    def approve(self, claim_id: str, *, allowed_uses: Iterable[str]) -> dict[str, Any]:
        claim = self.get(claim_id)
        if claim["lifecycle_status"] not in {"proposed", "awaiting_review", "evidence_changed", "expired"}:
            raise JobOpsError("CLAIM_STATE_INVALID", "Claim cannot be approved from its current lifecycle.", status=claim["lifecycle_status"])
        uses = tuple(sorted(set(str(value) for value in allowed_uses if str(value))))
        if not uses:
            raise JobOpsError("CLAIM_ALLOWED_USES_REQUIRED", "Claim approval requires at least one allowed use.")
        verified = verify_claim_evidence(claim, self.gateway)
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE claims SET approved_for_external=1,lifecycle_status='approved',updated_at=?,version=version+1 WHERE claim_id=?",
                (now, claim_id),
            )
            connection.execute(
                """INSERT INTO claim_events(claim_id,event_type,claim_content_hash,source_hashes_json,
                responsibility_boundary_json,allowed_uses_json,sensitivity,occurred_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id, "APPROVED", claim["content_hash"], json.dumps(verified, ensure_ascii=False),
                    json.dumps(claim["responsibility_boundary"], ensure_ascii=False), json.dumps(uses),
                    claim["sensitivity"], now, claim["expires_at"],
                ),
            )
        return self.get(claim_id)

    def _disable(self, claim_id: str, status: str, event: str) -> dict[str, Any]:
        claim = self.get(claim_id)
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE claims SET approved_for_external=0,lifecycle_status=?,updated_at=?,version=version+1 WHERE claim_id=?",
                (status, now, claim_id),
            )
            connection.execute(
                """INSERT INTO claim_events(claim_id,event_type,claim_content_hash,source_hashes_json,
                responsibility_boundary_json,allowed_uses_json,sensitivity,occurred_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (claim_id, event, claim["content_hash"], "[]", json.dumps(claim["responsibility_boundary"], ensure_ascii=False), "[]", claim["sensitivity"], now, claim["expires_at"]),
            )
        return self.get(claim_id)

    def reject(self, claim_id: str) -> dict[str, Any]:
        return self._disable(claim_id, "rejected", "REJECTED")

    def revoke(self, claim_id: str) -> dict[str, Any]:
        return self._disable(claim_id, "revoked", "REVOKED")

    def revalidate(self, claim_id: str) -> dict[str, Any]:
        claim = self.get(claim_id)
        try:
            verify_claim_evidence(claim, self.gateway)
            return claim
        except JobOpsError:
            return self._disable(claim_id, "evidence_changed", "EVIDENCE_CHANGED")
