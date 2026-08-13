from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .eligibility import EligibilityResult
from .jd_analyzer import NormalizedJD, UNKNOWN


DIMENSIONS = ("function", "capability", "evidence", "industry", "level", "location", "preference")
DEFAULT_WEIGHTS = {"function": 0.18, "capability": 0.20, "evidence": 0.22, "industry": 0.10, "level": 0.10, "location": 0.10, "preference": 0.10}


@dataclass(frozen=True)
class DimensionAssessment:
    score: float
    evidence: tuple[str, ...]
    calculation: str
    gaps: tuple[str, ...]
    confidence: str
    decision_impact: str

    def as_dict(self) -> dict[str, object]:
        return {**self.__dict__, "evidence": list(self.evidence), "gaps": list(self.gaps)}


@dataclass(frozen=True)
class FitResult:
    eligibility_status: str
    hard_gaps: tuple[str, ...]
    unknowns: tuple[str, ...]
    dimensions: Mapping[str, DimensionAssessment]
    overall_score: float
    recommendation: str
    explanation: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "eligibility_status": self.eligibility_status, "hard_gaps": list(self.hard_gaps),
            "unknowns": list(self.unknowns), "dimensions": {key: value.as_dict() for key, value in self.dimensions.items()},
            "overall_score": self.overall_score, "recommendation": self.recommendation, "explanation": list(self.explanation),
        }


def _assessment(score: float, evidence: Sequence[str], calculation: str, gaps: Sequence[str], confidence: str, impact: str) -> DimensionAssessment:
    return DimensionAssessment(round(max(0.0, min(100.0, score)), 1), tuple(evidence), calculation, tuple(gaps), confidence, impact)


def compute_fit(jd: NormalizedJD, profile: dict[str, Any], eligibility: EligibilityResult, *, evidence_mappings: Sequence[Any]) -> FitResult:
    target_functions = {str(item).casefold() for item in profile.get("target_functions", [])}
    role_text = " ".join([jd.title, *jd.responsibilities, *jd.keywords]).casefold()
    function_hits = sorted(term for term in target_functions if term and term in role_text)
    function_score = 50 + min(50, 25 * len(function_hits)) if target_functions else 40

    requirement_checks = [item for item in eligibility.checks if str(item.get("gate", "")).endswith("_requirement") or item.get("gate") in {"years", "education"}]
    passed = sum(item.get("result") == "PASS" for item in requirement_checks)
    capability_score = 50.0 if not requirement_checks else 100.0 * passed / len(requirement_checks)
    capability_gaps = [str(item.get("requirement_id")) for item in requirement_checks if item.get("result") != "PASS"]

    mapped = [item for item in evidence_mappings if getattr(item, "gap", None) is None]
    evidence_score = min(100.0, 25.0 * len(mapped)) if evidence_mappings else 0.0
    evidence_gaps = [getattr(item, "requirement", "unmapped") for item in evidence_mappings if getattr(item, "gap", None)] or (["no revalidated claim mappings"] if not evidence_mappings else [])

    industry_terms = {str(item).casefold() for item in profile.get("target_industries", [])}
    industry_hits = sorted(term for term in industry_terms if term and term in role_text)
    industry_score = 70.0 if industry_hits else 50.0
    level_score = 50.0 if jd.level == UNKNOWN else (90.0 if jd.level.casefold() in {str(item).casefold() for item in profile.get("target_levels", [])} else 20.0)
    location_checks = [item for item in eligibility.checks if item.get("gate") == "location"]
    location_result = location_checks[0]["result"] if location_checks else "UNKNOWN"
    location_score = {"PASS": 90.0, "UNKNOWN": 50.0, "FAIL": 0.0}[location_result]
    preference_score = 80.0 if str(profile.get("remote_preference", UNKNOWN)).casefold() in jd.location.casefold() else 50.0

    dimensions = {
        "function": _assessment(function_score, function_hits or [jd.title], f"target-function matches={len(function_hits)}", [] if function_hits else ["no confirmed target-function term match"], "MEDIUM", "Shapes role alignment but cannot override hard gates."),
        "capability": _assessment(capability_score, [str(item.get("requirement_id")) for item in requirement_checks if item.get("result") == "PASS"], f"passed {passed}/{len(requirement_checks)} parsed hard requirements", capability_gaps, "HIGH" if requirement_checks else "LOW", "Unknown or failed hard requirements keep the recommendation conditional or blocked."),
        "evidence": _assessment(evidence_score, [getattr(item, "claim_id", "") for item in mapped], f"{len(mapped)} revalidated claim mappings", evidence_gaps, "HIGH" if mapped else "LOW", "External materials may use only revalidated approved evidence."),
        "industry": _assessment(industry_score, industry_hits, f"industry matches={len(industry_hits)}", [] if industry_hits else ["industry evidence unavailable"], "LOW" if not industry_hits else "MEDIUM", "Contextual signal only."),
        "level": _assessment(level_score, [jd.level], "parsed JD level compared with confirmed target levels", [] if level_score >= 70 else ["level unknown or outside target"], "MEDIUM" if jd.level != UNKNOWN else "LOW", "Verified mismatch is a hard gate."),
        "location": _assessment(location_score, [location_result], "eligibility location gate mapped to score", [] if location_result == "PASS" else ["location unresolved or failed"], "HIGH" if location_result != "UNKNOWN" else "LOW", "Verified mismatch is a hard gate."),
        "preference": _assessment(preference_score, [str(profile.get("remote_preference", UNKNOWN))], "confirmed work-mode preference compared with JD location", [] if preference_score >= 70 else ["work-mode preference not confirmed in JD"], "MEDIUM", "Preference affects prioritization after eligibility."),
    }
    overall = round(sum(dimensions[key].score * DEFAULT_WEIGHTS[key] for key in DIMENSIONS) / sum(DEFAULT_WEIGHTS.values()), 1)
    if eligibility.status == "INELIGIBLE":
        recommendation = "DO_NOT_APPLY"
    elif eligibility.status == "NEEDS_USER_INPUT":
        recommendation = "CONDITIONAL"
    else:
        recommendation = "RECOMMEND" if overall >= 70 else ("CONDITIONAL" if overall >= 50 else "DO_NOT_APPLY")
    explanation = tuple(["Hard eligibility failures override aggregate fit." if eligibility.hard_gaps else "Unknown hard conditions require confirmation before applying." if eligibility.unknowns else "All modeled hard conditions passed."] + [f"{key}: {dimensions[key].score:.1f} - {dimensions[key].calculation}" for key in DIMENSIONS])
    return FitResult(eligibility.status, eligibility.hard_gaps, eligibility.unknowns, dimensions, overall, recommendation, explanation)


def score_fit(eligibility: EligibilityResult, dimensions: Mapping[str, float], weights: Mapping[str, float] | None = None) -> FitResult:
    """Legacy internal test helper; public orchestration never accepts caller-provided dimension scores."""
    if set(dimensions) != set(DIMENSIONS):
        raise ValueError(f"Fit dimensions must be exactly {DIMENSIONS}")
    active = dict(weights or DEFAULT_WEIGHTS)
    assessments = {key: _assessment(float(dimensions[key]), ["legacy synthetic fixture"], "caller fixture score", [], "LOW", "Test helper only; not used by public CLI.") for key in DIMENSIONS}
    overall = round(sum(assessments[key].score * active[key] for key in DIMENSIONS) / sum(active.values()), 1)
    recommendation = "DO_NOT_APPLY" if eligibility.status == "INELIGIBLE" else "CONDITIONAL" if eligibility.status == "NEEDS_USER_INPUT" else "RECOMMEND" if overall >= 70 else "CONDITIONAL"
    prefix = "Hard eligibility failures override the aggregate fit score." if eligibility.hard_gaps else "Unknown hard conditions require confirmation before applying." if eligibility.unknowns else "All modeled hard conditions passed."
    return FitResult(eligibility.status, eligibility.hard_gaps, eligibility.unknowns, assessments, overall, recommendation, tuple([prefix] + [f"{key}: {assessments[key].score:.0f}/100" for key in DIMENSIONS]))
