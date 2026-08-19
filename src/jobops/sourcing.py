from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse, urlunparse

from .errors import JobOpsError
from .util import canonical_json, parse_iso, sha256_bytes


CAREER_HINTS = ("career", "careers", "jobs", "job", "join-us", "joinus", "work-with-us", "招聘", "职位")
SUPPORTED_ROUTE_PROVIDERS = frozenset({
    "company", "greenhouse", "lever", "workday", "ashby", "smartrecruiters",
})
COMMON_CC_SLD = {"ac", "co", "com", "edu", "gov", "go", "net", "ne", "org", "or", "mil", "nom"}
SENSITIVE_QUERY_KEYS = {
    "api_key", "apikey", "auth", "authorization", "code", "credential", "email", "id_token",
    "login", "oauth_token", "password", "secret", "session", "session_id", "sig", "signature",
    "token", "username",
}
SENSITIVE_QUERY_KEY_PARTS = re.compile(
    r"(?:^|[_-])(?:auth|credential|email|password|secret|session|signature|token|username)(?:$|[_-])",
    re.IGNORECASE,
)


def _host(value: str) -> str:
    return value.casefold().strip().strip(".").removeprefix("www.")


def registrable_domain(host: str) -> str:
    """Conservative PSL-equivalent extraction for ICANN-style domains and common ccTLD SLDs."""
    value = _host(host)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise JobOpsError("PUBLIC_SUFFIX_NOT_COMPANY", "An IP address cannot establish a company-domain identity.", host=value)
    labels = [label for label in value.split(".") if label]
    if len(labels) < 2 or any(not label.replace("-", "").isalnum() for label in labels):
        raise JobOpsError("PUBLIC_SUFFIX_NOT_COMPANY", "A company domain must contain a registrable label above a public suffix.", host=value)
    suffix_size = 2 if len(labels[-1]) == 2 and labels[-2] in COMMON_CC_SLD else 1
    if len(labels) <= suffix_size:
        raise JobOpsError("PUBLIC_SUFFIX_NOT_COMPANY", "A public suffix cannot be used as a company identity.", host=value)
    return ".".join(labels[-(suffix_size + 1):])


def host_matches_registered(host: str, registered: str) -> bool:
    value = _host(host)
    return value == registered or value.endswith("." + registered)


def url_has_sensitive_query(value: str) -> bool:
    try:
        keys = (key.casefold() for key, _ in parse_qsl(urlparse(value).query, keep_blank_values=True))
        return any(key in SENSITIVE_QUERY_KEYS or SENSITIVE_QUERY_KEY_PARTS.search(key) for key in keys)
    except ValueError:
        return True


def _canonical_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise JobOpsError("HTTPS_REQUIRED", "Application routes require canonical HTTPS URLs without embedded credentials.")
    host = _host(parsed.hostname)
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    path = parsed.path or "/"
    return urlunparse(("https", host + port, path, "", parsed.query, ""))


def _provider_and_tenant(host: str, url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(url)
    labels = _host(host).split(".")
    path_parts = [part for part in parsed.path.split("/") if part]
    workday_host = _host(host)
    if workday_host in {"myworkdayjobs.com", "myworkday.com"} or workday_host.endswith((".myworkdayjobs.com", ".myworkday.com")):
        tenant = path_parts[0] if (workday_host == "myworkday.com" or workday_host.endswith(".myworkday.com")) and path_parts else labels[0]
        job_identity = path_parts[-1] if path_parts else "UNKNOWN"
        board = next((part for part in path_parts if part.casefold() in {"careers", "jobs"}), "careers")
        return "workday", tenant, board.casefold(), job_identity
    if _host(host) in {"boards.greenhouse.io", "job-boards.greenhouse.io"} or _host(host).endswith(".greenhouse.io"):
        tenant = path_parts[0] if path_parts else labels[0]
        job_identity = path_parts[-1] if path_parts else "UNKNOWN"
        return "greenhouse", tenant.casefold(), "default", job_identity
    if _host(host) in {"jobs.lever.co", "lever.co"} or _host(host).endswith(".lever.co"):
        tenant = path_parts[0] if path_parts else labels[0]
        job_identity = path_parts[-1] if path_parts else "UNKNOWN"
        return "lever", tenant.casefold(), "default", job_identity
    if _host(host) == "jobs.ashbyhq.com" or _host(host).endswith(".jobs.ashbyhq.com"):
        tenant = path_parts[0] if path_parts else labels[0]
        # Ashby job and application URLs retain the board name followed by the
        # stable posting identity; child application paths must not replace it.
        job_identity = path_parts[1] if len(path_parts) >= 2 else "UNKNOWN"
        return "ashby", tenant.casefold(), "default", job_identity
    if _host(host) == "smartrecruiters.com" or _host(host).endswith(".smartrecruiters.com"):
        # Public SmartRecruiters posting URLs use /{company}/{posting}; the
        # browser route may add child paths but must keep that posting segment.
        tenant = path_parts[0] if path_parts else "UNKNOWN"
        job_identity = path_parts[1] if len(path_parts) >= 2 else "UNKNOWN"
        return "smartrecruiters", tenant.casefold(), "default", job_identity
    raise JobOpsError("ATS_PROVIDER_UNKNOWN", "The ATS host is not recognized by an offline provider parser.", host=_host(host))


@dataclass(frozen=True)
class SourceRoute:
    status: str
    company_domain: str
    official_entry_url: str
    current_url: str
    route_kind: str
    provider: str
    ats_tenant: str
    ats_board: str
    ats_job_identity: str
    guest_mode: str
    account_action: str
    official_page_hash: str
    jd_snapshot_hash: str
    route_hash: str
    navigation_history: tuple[str, ...]

    @property
    def history(self) -> tuple[str, ...]:
        return self.navigation_history

    def as_dict(self) -> dict[str, object]:
        return {**self.__dict__, "navigation_history": list(self.navigation_history)}


def source_route_hash(value: dict[str, object]) -> str:
    material = {
        "company_domain": value["company_domain"], "official_entry_url": value["official_entry_url"],
        "current_url": value["current_url"], "route_kind": value["route_kind"], "provider": value["provider"],
        "tenant": value["ats_tenant"], "board": value["ats_board"], "job_identity": value["ats_job_identity"],
        "official_page_hash": value["official_page_hash"], "jd_snapshot_hash": value["jd_snapshot_hash"],
        "navigation_history": list(value["navigation_history"]),
    }
    return sha256_bytes(canonical_json(material))


def assess_job_freshness(*, official_listing_present: bool, application_form_available: bool, checked_at: str, max_age_minutes: int = 30, now=None) -> dict[str, object]:
    from datetime import datetime, timezone
    current = now or datetime.now(timezone.utc)
    age_minutes = (current.astimezone(timezone.utc) - parse_iso(checked_at)).total_seconds() / 60
    if age_minutes < 0 or age_minutes > max_age_minutes:
        return {"status": "NEEDS_REFRESH", "age_minutes": round(age_minutes, 1), "may_apply": False}
    if not official_listing_present:
        return {"status": "OFFICIAL_LISTING_REMOVED", "age_minutes": round(age_minutes, 1), "may_apply": False}
    if not application_form_available:
        return {"status": "APPLICATION_FORM_UNAVAILABLE", "age_minutes": round(age_minutes, 1), "may_apply": False}
    return {"status": "CURRENT", "age_minutes": round(age_minutes, 1), "may_apply": True}


def verify_source_route(
    *,
    official_entry_url: str,
    current_url: str,
    navigation_history: list[str],
    approved_ats_hosts: list[str],
    guest_available: bool | None,
    tenant_binding: dict[str, str] | None = None,
    official_page_hash: str | None = None,
    jd_snapshot_hash: str | None = None,
    approved_intermediary_hosts: list[str] | None = None,
    company_domain: str | None = None,
) -> SourceRoute:
    entry_url = _canonical_url(official_entry_url)
    current_canonical = _canonical_url(current_url)
    entry = urlparse(entry_url)
    current = urlparse(current_canonical)
    derived_company = registrable_domain(entry.hostname or "")
    if company_domain is not None:
        declared = registrable_domain(company_domain)
        if declared != derived_company:
            raise JobOpsError("COMPANY_DOMAIN_MISMATCH", "Caller-declared company domain does not match the official entry host.")
    if not any(hint in (entry.path + " " + entry.query).casefold() for hint in CAREER_HINTS):
        raise JobOpsError("OFFICIAL_CAREERS_PATH_NOT_PROVEN", "The official entry is not identifiable as a careers/jobs page.")
    if not navigation_history or _canonical_url(navigation_history[0]) != entry_url:
        raise JobOpsError("ROUTE_PROVENANCE_MISSING", "Navigation history must begin at the verified official careers URL.")
    canonical_history = [_canonical_url(value) for value in navigation_history]
    if any(url_has_sensitive_query(value) for value in (entry_url, current_canonical, *canonical_history)):
        raise JobOpsError(
            "ROUTE_URL_SENSITIVE_QUERY",
            "Application routes cannot retain credential-, session-, identity-, or signature-like query fields.",
        )
    if canonical_history[-1] != current_canonical:
        raise JobOpsError("ROUTE_CURRENT_URL_MISMATCH", "navigation_history[-1] must exactly equal current_url after canonicalization.")
    intermediaries = {_host(value) for value in (approved_intermediary_hosts or [])}
    approved_suffixes = {_host(value) for value in approved_ats_hosts}
    current_host = _host(current.hostname or "")
    final_is_company = host_matches_registered(current_host, derived_company)
    final_is_ats = any(current_host == suffix or current_host.endswith("." + suffix) for suffix in approved_suffixes)
    for index, url in enumerate(canonical_history):
        hop_host = _host(urlparse(url).hostname or "")
        is_company = host_matches_registered(hop_host, derived_company)
        is_intermediary = hop_host in intermediaries
        is_final_ats = index == len(canonical_history) - 1 and final_is_ats and hop_host == current_host
        if not (is_company or is_intermediary or is_final_ats):
            raise JobOpsError("UNSAFE_ROUTE_HOP", "Route contains an unknown or malicious intermediate host.", hop_index=index)
    page_hash = official_page_hash or ""
    snapshot_hash = jd_snapshot_hash or ""
    if final_is_company:
        kind, provider, tenant, board, identity = "OFFICIAL_DIRECT", "company", derived_company, "official", current.path.rstrip("/").split("/")[-1]
    elif final_is_ats:
        if not tenant_binding or not page_hash or not snapshot_hash:
            raise JobOpsError("ATS_TENANT_BINDING_REQUIRED", "An ATS route needs a local official-page tenant binding and snapshot hashes.")
        provider, tenant, board, identity = _provider_and_tenant(current_host, current_canonical)
        expected = {
            "provider": provider, "company_registrable_domain": derived_company, "ats_host": current_host,
            "tenant": tenant, "board": board, "job_identity": identity,
            "official_page_hash": page_hash, "jd_snapshot_hash": snapshot_hash,
        }
        actual = {key: str(tenant_binding.get(key, "")).casefold() if key in {"provider", "company_registrable_domain", "ats_host", "tenant", "board"} else str(tenant_binding.get(key, "")) for key in expected}
        expected_cmp = {key: value.casefold() if key in {"provider", "company_registrable_domain", "ats_host", "tenant", "board"} else value for key, value in expected.items()}
        if actual != expected_cmp:
            raise JobOpsError("ATS_TENANT_BINDING_MISMATCH", "ATS tenant, board, job or official snapshot binding does not match the route.")
        kind = "OFFICIAL_TO_APPROVED_ATS"
    else:
        raise JobOpsError("UNAPPROVED_APPLICATION_HOST", "The route ended outside the company and approved ATS domains.")
    if guest_available is True:
        guest_mode, account_action, status = "GUEST_SELECTED", "NONE", "ROUTE_APPROVED"
    elif guest_available is False:
        guest_mode, account_action, status = "GUEST_UNAVAILABLE", "NEEDS_ACCOUNT_APPROVAL", "NEEDS_ACCOUNT_APPROVAL"
    else:
        guest_mode, account_action, status = "UNKNOWN", "NEEDS_USER_INPUT", "NEEDS_USER_INPUT"
    material = {
        "company_domain": derived_company, "official_entry_url": entry_url, "current_url": current_canonical,
        "route_kind": kind, "provider": provider, "ats_tenant": tenant, "ats_board": board, "ats_job_identity": identity,
        "official_page_hash": page_hash, "jd_snapshot_hash": snapshot_hash, "navigation_history": canonical_history,
    }
    return SourceRoute(
        status, derived_company, entry_url, current_canonical, kind, provider, tenant, board, identity,
        guest_mode, account_action, page_hash, snapshot_hash, source_route_hash(material), tuple(canonical_history),
    )
