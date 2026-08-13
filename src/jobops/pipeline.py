from __future__ import annotations

from typing import Any, Sequence

from .eligibility import check_eligibility
from .fit import compute_fit
from .jd_analyzer import analyze_jd


def analyze_offline_job(text: str, profile: dict[str, Any], evidence_mappings: Sequence[Any] = ()) -> dict[str, object]:
    jd = analyze_jd(text)
    eligibility = check_eligibility(jd, profile)
    fit = compute_fit(jd, profile, eligibility, evidence_mappings=evidence_mappings)
    return {
        "jd": jd.as_dict(),
        "eligibility": eligibility.as_dict(),
        "fit": fit.as_dict(),
        "external_actions": 0,
        "instruction_signals_ignored": list(jd.untrusted_instruction_signals),
    }
