from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .claims import external_use_decision


@dataclass(frozen=True)
class EvidenceMapping:
    requirement: str
    claim_id: str | None
    wording: str | None
    source_refs: tuple[dict[str, Any], ...]
    gap: str | None
    why_used: str

    def as_dict(self) -> dict[str, object]:
        return {**self.__dict__, "source_refs": list(self.source_refs)}


def map_evidence(requirements: Iterable[str], claims: Iterable[dict[str, Any]]) -> list[EvidenceMapping]:
    available = list(claims)
    mappings: list[EvidenceMapping] = []
    for requirement in requirements:
        requirement_terms = {term.casefold() for term in requirement.replace("/", " ").replace("-", " ").split() if len(term) >= 3}
        candidates = []
        for claim in available:
            wording_values = [str(value) for value in claim.get("allowed_wording", [])]
            material = " ".join([str(claim.get("raw_fact", "")), *wording_values]).casefold()
            score = sum(1 for term in requirement_terms if term in material)
            if score:
                candidates.append((score, claim, wording_values[0] if wording_values else ""))
        candidates.sort(key=lambda item: (-item[0], str(item[1].get("claim_id", ""))))
        chosen = None
        for score, claim, wording in candidates:
            decision = external_use_decision(claim, wording=wording)
            if decision.allowed:
                chosen = (score, claim, wording)
                break
        if chosen is None:
            mappings.append(EvidenceMapping(requirement, None, None, (), "NO_APPROVED_EVIDENCE", "No current approved claim directly supports this requirement."))
        else:
            score, claim, wording = chosen
            mappings.append(EvidenceMapping(
                requirement=requirement,
                claim_id=str(claim["claim_id"]),
                wording=wording,
                source_refs=tuple(claim["source_refs"]),
                gap=None,
                why_used=f"Selected because {score} requirement term(s) matched an approved, unexpired personal claim.",
            ))
    return mappings

