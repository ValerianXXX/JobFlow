from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .jd_analyzer import NormalizedJD, Requirement, UNKNOWN, _boolean_expression


@dataclass(frozen=True)
class EligibilityResult:
    status: str
    hard_gaps: tuple[str, ...]
    unknowns: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "hard_gaps": list(self.hard_gaps), "unknowns": list(self.unknowns), "checks": list(self.checks)}


def _salary_floor(value: str) -> float | None:
    if value == UNKNOWN:
        return None
    value = value.replace(",", "")
    numbers = []
    for amount, suffix in re.findall(r"\$?([0-9]+(?:\.[0-9]+)?)\s*([kK]?)", value):
        parsed = float(amount) * (1000 if suffix else 1)
        if parsed >= 1000:
            numbers.append(parsed)
    return min(numbers) if numbers else None


def _skill_name(item: str) -> str:
    value = item.casefold().strip().strip("()（）.;。")
    value = re.sub(r"(?i)^(?:knowledge of|proficiency in|experience with|熟悉|掌握|具备)\s*", "", value)
    return value


def _evaluate_requirement(requirement: Requirement, profile: dict[str, Any]) -> dict[str, Any]:
    skills = {str(value).casefold() for value in profile.get("skills", [])}
    languages = {str(value).casefold() for value in profile.get("languages", [])}
    certifications = {str(value).casefold() for value in profile.get("certifications", [])}
    components: dict[str, str] = {}
    if requirement.category == "years":
        match = re.search(r"(\d+)\s*\+?\s*(?:years?|年)", requirement.text, re.IGNORECASE)
        required = int(match.group(1)) if match else None
        actual = profile.get("years_experience", UNKNOWN)
        if required is None or actual in (UNKNOWN, None, ""):
            result = "UNKNOWN"
        else:
            result = "PASS" if float(actual) >= required else "FAIL"
        return {"gate": "years", "requirement_id": requirement.requirement_id, "logic": requirement.logic, "result": result, "components": {"required": required, "actual": actual}, "reason": requirement.text}
    if requirement.category == "education":
        actual = str(profile.get("education", UNKNOWN)).casefold()
        result = "UNKNOWN" if actual == "unknown" else ("PASS" if any(term in actual for term in re.findall(r"bachelor|master|phd|本科|硕士|博士", requirement.text.casefold())) else "UNKNOWN")
        return {"gate": "education", "requirement_id": requirement.requirement_id, "logic": requirement.logic, "result": result, "components": {"education": result}, "reason": requirement.text}
    if requirement.category in {"work_authorization", "visa"}:
        actual = str(profile.get("work_authorization", UNKNOWN))
        result = "UNKNOWN" if actual == UNKNOWN else ("FAIL" if actual == "NOT_AUTHORIZED" else "PASS")
        return {"gate": requirement.category, "requirement_id": requirement.requirement_id, "logic": requirement.logic, "result": result, "components": {"authorization": result}, "reason": requirement.text}
    source = languages if requirement.category == "language" else certifications if requirement.category == "certification" else skills
    expression = _boolean_expression(requirement.text) if requirement.category in {"skill", "language", "certification"} else None
    for raw in requirement.items:
        item = _skill_name(raw)
        components[item] = "PASS" if any(item == value or item in value or value in item for value in source) else "UNKNOWN"
    passed = sum(value == "PASS" for value in components.values())

    def evaluate(node) -> bool:
        if node[0] == "LEAF":
            return components.get(_skill_name(node[1])) == "PASS"
        values = [evaluate(child) for child in node[1]]
        return any(values) if node[0] == "ANY" else all(values)

    if expression is not None and expression[0] != "LEAF" and len(components) > 1:
        result = "PASS" if evaluate(expression) else "UNKNOWN"
    elif requirement.logic == "ANY":
        result = "PASS" if passed >= 1 else "UNKNOWN"
    elif requirement.logic == "AT_LEAST":
        result = "PASS" if passed >= requirement.threshold else "UNKNOWN"
    elif requirement.logic in {"ALL", "SINGLE"}:
        result = "PASS" if passed == len(components) else "UNKNOWN"
    else:
        result = "UNKNOWN"
    return {"gate": f"{requirement.category}_requirement", "requirement_id": requirement.requirement_id, "logic": requirement.logic, "threshold": requirement.threshold, "result": result, "components": components, "reason": requirement.text}


def check_eligibility(jd: NormalizedJD, profile: dict[str, Any]) -> EligibilityResult:
    gaps: list[str] = []
    unknowns: list[str] = []
    checks: list[dict[str, Any]] = []
    authorization = str(profile.get("work_authorization", UNKNOWN))
    jd_auth = jd.work_authorization.casefold()
    if authorization == UNKNOWN:
        unknowns.append("candidate_work_authorization")
        checks.append({"gate": "work_authorization", "result": "UNKNOWN", "reason": "candidate answer not confirmed"})
    elif authorization == "NOT_AUTHORIZED":
        gaps.append("work_authorization")
        checks.append({"gate": "work_authorization", "result": "FAIL", "reason": "candidate marked not authorized"})
    elif authorization == "REQUIRES_SPONSORSHIP" and any(term in jd_auth for term in ("no sponsorship", "cannot sponsor", "not sponsor")):
        gaps.append("visa_sponsorship")
        checks.append({"gate": "work_authorization", "result": "FAIL", "reason": "role states no sponsorship"})
    else:
        checks.append({"gate": "work_authorization", "result": "PASS", "reason": "no verified conflict"})

    allowed_locations = [str(value).casefold() for value in profile.get("locations", [])]
    if jd.location == UNKNOWN:
        unknowns.append("job_location")
        checks.append({"gate": "location", "result": "UNKNOWN", "reason": "JD location not stated"})
    elif not allowed_locations:
        unknowns.append("candidate_location_preferences")
        checks.append({"gate": "location", "result": "UNKNOWN", "reason": "candidate preferences not confirmed"})
    elif not any(value in jd.location.casefold() or value in ("any", "flexible") for value in allowed_locations):
        remote = str(profile.get("remote_preference", UNKNOWN)).casefold()
        if "remote" not in jd.location.casefold() or remote not in ("remote", "flexible"):
            gaps.append("location")
            checks.append({"gate": "location", "result": "FAIL", "reason": "location is outside confirmed preferences"})
    else:
        checks.append({"gate": "location", "result": "PASS", "reason": "location matches confirmed preferences"})

    target_levels = [str(value).casefold() for value in profile.get("target_levels", [])]
    if jd.level == UNKNOWN:
        unknowns.append("job_level")
        checks.append({"gate": "level", "result": "UNKNOWN", "reason": "level not reliably inferable"})
    elif target_levels and jd.level.casefold() not in target_levels:
        gaps.append("level")
        checks.append({"gate": "level", "result": "FAIL", "reason": f"{jd.level} is outside target levels"})
    else:
        checks.append({"gate": "level", "result": "PASS", "reason": "level matches or no restrictive preference"})

    minimum_salary = profile.get("minimum_salary")
    role_floor = _salary_floor(jd.salary)
    if minimum_salary in (None, UNKNOWN, ""):
        unknowns.append("candidate_minimum_salary")
        checks.append({"gate": "salary", "result": "UNKNOWN", "reason": "candidate minimum not confirmed"})
    elif role_floor is None:
        unknowns.append("job_salary")
        checks.append({"gate": "salary", "result": "UNKNOWN", "reason": "JD salary unavailable"})
    elif role_floor < float(minimum_salary):
        gaps.append("salary")
        checks.append({"gate": "salary", "result": "FAIL", "reason": "posted minimum is below confirmed minimum"})
    else:
        checks.append({"gate": "salary", "result": "PASS", "reason": "posted floor meets confirmed minimum"})

    for requirement in jd.requirements:
        result = _evaluate_requirement(requirement, profile)
        checks.append(result)
        if result["result"] == "FAIL":
            gaps.append(f"hard_requirement:{requirement.requirement_id}")
        elif result["result"] == "UNKNOWN":
            unknowns.append(f"hard_requirement:{requirement.requirement_id}")
    status = "INELIGIBLE" if gaps else ("NEEDS_USER_INPUT" if unknowns else "ELIGIBLE")
    return EligibilityResult(status, tuple(dict.fromkeys(gaps)), tuple(dict.fromkeys(unknowns)), tuple(checks))
