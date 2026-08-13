from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .claims import external_use_decision
from .errors import JobOpsError
from .util import sha256_file


@dataclass(frozen=True)
class MaterialBundle:
    resume_docx: Path
    resume_pdf: Path
    cover_letter_docx: Path
    cover_letter_pdf: Path
    resume_hash: str
    cover_letter_hash: str
    claim_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "resume_docx": str(self.resume_docx),
            "resume_pdf": str(self.resume_pdf),
            "cover_letter_docx": str(self.cover_letter_docx),
            "cover_letter_pdf": str(self.cover_letter_pdf),
            "resume_hash": self.resume_hash,
            "cover_letter_hash": self.cover_letter_hash,
            "claim_ids": list(self.claim_ids),
        }


def approved_wordings(claims: Iterable[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    result = []
    for claim in claims:
        wordings = claim.get("allowed_wording", [])
        if not wordings:
            continue
        wording = str(wordings[0])
        decision = external_use_decision(claim, wording=wording)
        if decision.allowed:
            result.append((str(claim["claim_id"]), wording, list(claim["source_refs"])))
    return result


def assert_material_text_is_claim_gated(text: str, claims: Iterable[dict[str, Any]]) -> None:
    allowed = {wording for _, wording, _ in approved_wordings(claims)}
    bullet_lines = [re.sub(r"^[\s•*-]+", "", line).strip() for line in text.splitlines() if re.match(r"^[\s•*-]+\S", line)]
    unsupported = [line for line in bullet_lines if line and line not in allowed]
    if unsupported:
        raise JobOpsError("MATERIAL_CONTAINS_UNAPPROVED_CLAIM", "Externally facing bullet text lacks an approved exact claim.", unsupported=unsupported)


def master_diff(master_text: str, tailored_text: str) -> list[str]:
    return list(difflib.unified_diff(master_text.splitlines(), tailored_text.splitlines(), fromfile="master", tofile="tailored", lineterm=""))


def material_hashes(paths: Iterable[Path]) -> dict[str, str]:
    return {path.name: sha256_file(path) for path in paths}

