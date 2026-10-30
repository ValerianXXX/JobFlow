"""Conservative calendar-duration evidence from reviewed work entities.

Calendar coverage is neither full-time equivalence nor years using a skill.
Incomplete source history cannot establish that an applicant lacks experience.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any


METHOD = "CONFIRMED_WORK_CALENDAR_BOUNDS_V1"
UNKNOWN = "UNKNOWN"
_ONGOING = {"present", "current", "ongoing", "至今", "现在", "目前"}


def _date_range(value: object, *, as_of: date, end: bool = False) -> tuple[date, date]:
    if not isinstance(value, str):
        raise ValueError("INVALID_DATE")
    value = value.strip()
    if end and value.casefold() in _ONGOING:
        return as_of, as_of
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("INVALID_DATE")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    if len(parts) == 1:
        low, high = date(year, 1, 1), date(year, 12, 31)
    elif len(parts) == 2:
        month = parts[1]
        low = date(year, month, 1)
        high = date(year, month, calendar.monthrange(year, month)[1])
    else:
        low = high = date(*parts)
    if high > as_of:
        raise ValueError("FUTURE_OR_UNRESOLVED_CURRENT_DATE")
    return low, high


def _month(value: date) -> int:
    return value.year * 12 + value.month - 1


def _union_months(intervals: list[tuple[int, int]]) -> int:
    total, last_end = 0, -1
    for start, end in sorted(intervals):
        if end > max(start, last_end):
            total += end - max(start, last_end)
        last_end = max(last_end, end)
    return total


def documented_experience(claims: list[dict[str, Any]], *, as_of: date) -> tuple[int | float | str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    internships: set[str] = set()
    claim_ids: set[str] = set()
    for index, item in enumerate(claims):
        if item.get("decision") != "CONFIRMED" or item.get("deleted"):
            continue
        if item.get("category") not in {"work", "internship"}:
            continue
        entity = item.get("entity") if isinstance(item.get("entity"), dict) else {}
        key = str(entity.get("entity_id") or item.get("entity_id") or f"unresolved-{index}")
        if item["category"] == "internship":
            internships.add(key)
            continue
        group = groups.setdefault(key, {"dates": set(), "invalid": False})
        source_id = item.get("claim_id")
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", source_id):
            group["invalid"] = True
        else:
            claim_ids.add(source_id)
        if entity.get("entity_type") not in (None, "work"):
            group["invalid"] = True
        start, end = entity.get("start_date"), entity.get("end_date")
        group["dates"].add((start if isinstance(start, str) else None, end if isinstance(end, str) else None))
    lower_intervals, upper_intervals = [], []
    unresolved = 0
    uncertainties: set[str] = set()
    for key, group in groups.items():
        if key in internships or group["invalid"] or len(group["dates"]) != 1:
            unresolved += 1
            uncertainties.add("ENTITY_OR_SOURCE_CONFLICT")
            continue
        start, end = next(iter(group["dates"]))
        try:
            start_low, start_high = _date_range(start, as_of=as_of)
            end_low, end_high = _date_range(end, as_of=as_of, end=True)
            if start_low > end_high:
                raise ValueError("REVERSED_DATES")
        except (TypeError, ValueError, OverflowError):
            unresolved += 1
            uncertainties.add("MISSING_INVALID_OR_FUTURE_DATES")
            continue
        # Only whole calendar months guaranteed by every possible endpoint are
        # included below. Upper bounds include all potentially touched months.
        lower_start = _month(start_high) + (start_high.day != 1)
        lower_end = _month(end_low) + (end_low.day == calendar.monthrange(end_low.year, end_low.month)[1])
        if lower_end > lower_start:
            lower_intervals.append((lower_start, lower_end))
        upper_intervals.append((_month(start_low), _month(end_high) + 1))
        if (lower_start, lower_end) != upper_intervals[-1]:
            uncertainties.add("DATE_PRECISION_OR_PARTIAL_MONTH")
    lower = _union_months(lower_intervals)
    upper = None if unresolved or not groups else _union_months(upper_intervals)
    scalar: int | float | str = UNKNOWN
    if groups and not unresolved and lower == upper:
        scalar = lower // 12 if lower % 12 == 0 else lower / 12
    return scalar, {
        "method": METHOD, "coverage": "DOCUMENTED_CLAIMS_ONLY", "as_of_date": as_of.isoformat(),
        "calendar_months_lower_bound": lower, "calendar_months_upper_bound": upper,
        "work_entities": len(groups), "internship_entities_excluded": len(internships),
        "unresolved_entities": unresolved, "source_claim_ids": sorted(claim_ids),
        "uncertainty_codes": sorted(uncertainties),
    }


def valid_experience_basis(basis: object) -> bool:
    if not isinstance(basis, dict):
        return False
    low, high = basis.get("calendar_months_lower_bound"), basis.get("calendar_months_upper_bound")
    count, unresolved = basis.get("work_entities"), basis.get("unresolved_entities")
    excluded, ids = basis.get("internship_entities_excluded"), basis.get("source_claim_ids")
    if not (basis.get("method") == METHOD and basis.get("coverage") == "DOCUMENTED_CLAIMS_ONLY"
            and type(low) is int and low >= 0 and (high is None or type(high) is int and high >= low)
            and type(count) is int and count >= 0 and type(unresolved) is int and 0 <= unresolved <= count
            and type(excluded) is int and excluded >= 0 and isinstance(ids, list)
            and all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", item) for item in ids)):
        return False
    if len(set(ids)) != len(ids) or (low > 0 and (not count or not ids)):
        return False
    if (unresolved or not count) and high is not None:
        return False
    if not unresolved and count and high is None:
        return False
    try:
        return date.fromisoformat(basis.get("as_of_date", "")) <= datetime.now(timezone.utc).date()
    except (TypeError, ValueError):
        return False


def _generic_requirement(text: str) -> Decimal | None:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".;。；")
    number = r"(?P<years>\d{1,3}(?:\.\d{1,3})?)"
    patterns = (
        rf"(?:(?:at least|minimum(?: of)?|must have(?: at least)?|requires?) )?{number}\s*\+?\s*years?(?: (?:of )?(?:(?:work|professional|employment) )?experience)?(?: required)?",
        rf"(?:至少|不少于|需|需要)?\s*{number}\s*年(?:以上)?(?:工作|职业)?经验(?:及以上)?",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return Decimal(match.group("years"))
    return None


def evaluate_years(profile: dict[str, Any], requirement: str) -> dict[str, Any]:
    required = _generic_requirement(requirement)
    raw_actual = profile.get("years_experience", UNKNOWN)
    actual: int | float | str = UNKNOWN
    if isinstance(raw_actual, (int, float, str)) and not isinstance(raw_actual, bool):
        try:
            parsed = Decimal(str(raw_actual))
            if parsed.is_finite() and parsed >= 0:
                converted = float(parsed)
                if math.isfinite(converted):
                    actual = converted
        except (InvalidOperation, ValueError, OverflowError):
            pass
    components: dict[str, Any] = {"required": float(required) if required is not None else None, "actual": actual}
    if required is None:
        return {"result": UNKNOWN, "components": components, "duration_status": "QUALIFIED_OR_UNSUPPORTED_REQUIREMENT"}
    basis = profile.get("experience_calculation")
    if isinstance(basis, dict):
        low, high = basis.get("calendar_months_lower_bound"), basis.get("calendar_months_upper_bound")
        if not valid_experience_basis(basis) or not basis["source_claim_ids"]:
            return {"result": UNKNOWN, "components": components, "duration_status": "DURATION_BASIS_INVALID"}
        components.update(documented_calendar_months_lower_bound=low, documented_calendar_months_upper_bound=high)
        return {"result": "PASS" if Decimal(low) >= required * 12 else UNKNOWN,
                "components": components, "duration_status": "DOCUMENTED_LOWER_BOUND_ONLY"}
    if "experience_calculation" in profile or profile.get("profile_schema_version") in (2, 3):
        return {"result": UNKNOWN, "components": components, "duration_status": "LEGACY_DURATION_REVIEW_REQUIRED"}
    # Preserve the explicit scalar contract for independently supplied legacy
    # profiles. Generated versioned profiles above need duration provenance.
    result = UNKNOWN if actual == UNKNOWN else "PASS" if Decimal(str(actual)) >= required else "FAIL"
    return {"result": result, "components": components, "duration_status": "LEGACY_EXPLICIT_SCALAR"}
