from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse


PROFILE_SCHEMA_VERSION = 2

# One vocabulary is shared by onboarding, form analysis, encrypted payload
# resolution, and browser assistance. Values remain inside DPAPI-protected
# records; this module only normalizes field identity and applicant-provided
# resume hints.
PROFILE_FIELD_ALIASES = {
    "full_name": "candidate_display_name",
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "phone_type": "phone_type",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "portfolio": "portfolio_url",
    "website": "website_url",
    "address": "address",
    "city": "city",
    "state": "state",
    "postal_code": "postal_code",
    "country": "country",
}

BASIC_PROFILE_FIELDS = tuple(PROFILE_FIELD_ALIASES.values())

US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
})


def _clean_scalar(value: object, *, limit: int = 2_000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:limit]


def unique_values(values: object, *, limit: int = 10) -> list[str]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, bytearray, dict)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean_scalar(raw)
        key = value.casefold()
        if not value or key in seen:
            continue
        output.append(value)
        seen.add(key)
        if len(output) >= limit:
            break
    return output


def split_display_name(value: object) -> dict[str, str]:
    """Return conservative editable name parts from an applicant resume header.

    The result is only a resume-provided hint and is never treated as a legal-name
    assertion until the applicant completes Profile review. Single-token and
    structurally ambiguous values stay unresolved.
    """

    text = _clean_scalar(value, limit=160).strip(" ,")
    if not text or any(char.isdigit() for char in text):
        return {}
    if "," in text:
        family, given = (_clean_scalar(part, limit=80) for part in text.split(",", 1))
        given_parts = given.split()
        if family and given_parts:
            result = {"first_name": given_parts[0], "last_name": family}
            if len(given_parts) > 1:
                result["middle_name"] = " ".join(given_parts[1:])
            return result
        return {}
    parts = text.split()
    if not 2 <= len(parts) <= 6:
        return {}
    if not all(
        any(character.isalpha() for character in part)
        and all(character.isalpha() or character in "-'’" for character in part)
        for part in parts
    ):
        return {}
    result = {"first_name": parts[0], "last_name": parts[-1]}
    if len(parts) > 2:
        result["middle_name"] = " ".join(parts[1:-1])
    return result


def parse_mailing_address(value: object) -> dict[str, str]:
    """Split a common US mailing address without inventing missing components."""

    text = _clean_scalar(value, limit=500)
    if not text:
        return {}
    normalized = re.sub(r"\s*[\r\n]+\s*", ", ", text)
    normalized = re.sub(r"\s*,\s*", ", ", normalized).strip(" ,")
    match = re.fullmatch(
        r"(?P<street>.+?),\s*(?P<city>[^,]+?),\s*"
        r"(?P<state>[A-Za-z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)"
        r"(?:,\s*(?P<country>United States(?: of America)?|USA|US))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return {"address": normalized}
    state = match.group("state").upper()
    if state not in US_STATE_CODES:
        return {"address": normalized}
    country = _clean_scalar(match.group("country") or "United States", limit=80)
    if country.casefold() in {"us", "usa", "united states of america"}:
        country = "United States"
    return {
        "address": _clean_scalar(match.group("street"), limit=240),
        "city": _clean_scalar(match.group("city"), limit=120),
        "state": state,
        "postal_code": match.group("postal"),
        "country": country,
    }


def classify_public_urls(values: object) -> dict[str, str]:
    output: dict[str, str] = {}
    for value in unique_values(values):
        candidate = value if value.startswith("https://") else "https://" + value.lstrip("/")
        try:
            host = (urlparse(candidate).hostname or "").casefold()
        except ValueError:
            continue
        if not host:
            continue
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            output.setdefault("linkedin_url", candidate)
        elif host == "github.com" or host.endswith(".github.com"):
            output.setdefault("github_url", candidate)
        else:
            output.setdefault("website_url", candidate)
    return output


def resume_profile_hints(parsed_resume: dict[str, Any]) -> dict[str, str]:
    """Build unique applicant-provided hints for encrypted onboarding state."""

    hints = split_display_name(parsed_resume.get("candidate_display_name"))
    contact = parsed_resume.get("contact_values")
    if not isinstance(contact, dict):
        return hints
    for source_key, target_key in (("email", "email"), ("phone", "phone")):
        values = unique_values(contact.get(source_key))
        if len(values) == 1:
            hints[target_key] = values[0]
    addresses = unique_values(contact.get("address"))
    if len(addresses) == 1:
        hints.update(parse_mailing_address(addresses[0]))
    hints.update(classify_public_urls(contact.get("linkedin")))
    hints.update(classify_public_urls(contact.get("website")))
    return {key: value for key, value in hints.items() if value}


def profile_value(profile: dict[str, Any], answer_key: str) -> Any:
    return profile.get(PROFILE_FIELD_ALIASES.get(answer_key, answer_key))
