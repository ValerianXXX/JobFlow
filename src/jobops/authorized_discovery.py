from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .errors import JobOpsError
from .runtime_schema import validate_named
from .sourcing import (
    _canonical_url,
    _provider_and_tenant,
    host_matches_registered,
    is_proven_careers_entry_url,
    registrable_domain,
    url_has_sensitive_query,
)
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id


CONTROL_METADATA_KEY = "authorized_discovery_control_v1"
CONFIG_KIND = "authorized_discovery_config"
MODE = "AUTHORIZED_READ_ONLY_DISCOVERY"
MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 24 * 60
MIN_AUTHORIZATION_HOURS = 1
MAX_AUTHORIZATION_HOURS = 7 * 24
MIN_MAX_NEW_PER_RUN = 1
MAX_MAX_NEW_PER_RUN = 100
MIN_INBOX_LIMIT = 10
MAX_INBOX_LIMIT = 1000
DEFAULT_INBOX_LIMIT = 250
MAX_SOURCES = 50
MAX_TERMS = 24
RUN_LEASE_MINUTES = 15
PAUSE_REASONS = {"USER_PAUSED", "USER_KILL_SWITCH", "REPEATED_FAILURES"}
CANDIDATE_PROVIDERS = {"company", "greenhouse", "lever", "workday", "ashby", "smartrecruiters"}
MAX_CANDIDATES_PER_RUN = 5_000

_SAFE_TERM = re.compile(r"^[^\x00-\x1f\x7f]{2,80}$")
_SOURCE_FORMATS = {
    "company": "html",
    "greenhouse": "greenhouse_json",
    "lever": "lever_json",
    "ashby": "ashby_json",
    "smartrecruiters": "smartrecruiters_json",
}
_FEED_HOSTS = {
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "ashby": "api.ashbyhq.com",
    "smartrecruiters": "api.smartrecruiters.com",
}
_FEED_PATHS = {
    "greenhouse": re.compile(r"^/v1/boards/[A-Za-z0-9_-]{1,120}/jobs/?$"),
    "lever": re.compile(r"^/v0/postings/[A-Za-z0-9_-]{1,120}/?$"),
    "ashby": re.compile(r"^/posting-api/job-board/[A-Za-z0-9_-]{1,120}/?$"),
    "smartrecruiters": re.compile(r"^/v1/companies/[A-Za-z0-9_-]{1,120}/postings/?$"),
}
_ALLOWED_QUERY_KEYS = {
    "company": frozenset(),
    "greenhouse": frozenset({"content"}),
    "lever": frozenset({"mode"}),
    "ashby": frozenset(),
    "smartrecruiters": frozenset({"limit", "offset"}),
}
_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_PUBLIC_BOARD_HOSTS = {
    "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
    "lever": {"jobs.lever.co"},
    "ashby": {"jobs.ashbyhq.com"},
    "smartrecruiters": {"jobs.smartrecruiters.com"},
}
_PUBLIC_BOARD_ROOTS = {
    "greenhouse": "https://job-boards.greenhouse.io/{tenant}",
    "lever": "https://jobs.lever.co/{tenant}",
    "ashby": "https://jobs.ashbyhq.com/{tenant}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{tenant}",
}
_PUBLIC_FEEDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings",
}


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _safe_terms(value: Any, *, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TERMS:
        raise JobOpsError("DISCOVERY_FILTER_INVALID", f"{name} must be a bounded list of search terms.")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        term = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not _SAFE_TERM.fullmatch(term):
            raise JobOpsError("DISCOVERY_FILTER_INVALID", f"{name} contains an invalid search term.")
        folded = term.casefold()
        if folded not in seen:
            seen.add(folded)
            normalized.append(term)
    return normalized


def _provider_tenant_from_feed(parsed: Any) -> tuple[str, str] | None:
    host = str(parsed.hostname or "").casefold()
    provider = next((name for name, expected in _FEED_HOSTS.items() if host == expected), None)
    if provider is None or _FEED_PATHS[provider].fullmatch(parsed.path or "/") is None:
        return None
    parts = [part for part in (parsed.path or "").split("/") if part]
    indexes = {"greenhouse": 2, "lever": 2, "ashby": 2, "smartrecruiters": 2}
    index = indexes[provider]
    if len(parts) <= index or _TENANT.fullmatch(parts[index]) is None:
        return None
    return provider, parts[index]


def authorized_discovery_source_from_url(raw: Any) -> dict[str, str]:
    """Convert one explicit public careers URL into an exact read-only source.

    Recognized ATS board roots and their public list feeds map to one fixed GET
    endpoint.  Individual job URLs are never widened to the whole board.
    """
    url = _canonical_url(str(raw or "").strip())
    if url_has_sensitive_query(url):
        raise JobOpsError(
            "DISCOVERY_SOURCE_SENSITIVE_URL",
            "Company careers URLs cannot contain login, identity, session, or signature parameters.",
        )
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise JobOpsError(
            "DISCOVERY_SOURCE_URL_INVALID",
            "Authorized discovery URLs cannot contain credentials or fragments.",
        )
    host = str(parsed.hostname or "").casefold()
    parts = [part for part in (parsed.path or "").split("/") if part]
    provider: str | None = None
    tenant: str | None = None
    direct_feed = False
    if not parsed.query and len(parts) == 1 and _TENANT.fullmatch(parts[0]):
        provider = next((name for name, hosts in _PUBLIC_BOARD_HOSTS.items() if host in hosts), None)
        tenant = parts[0] if provider else None
    if provider is None:
        feed_binding = _provider_tenant_from_feed(parsed)
        if feed_binding is not None:
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if any(key.casefold() not in _ALLOWED_QUERY_KEYS[feed_binding[0]] for key, _ in query):
                raise JobOpsError(
                    "DISCOVERY_SOURCE_QUERY_INVALID",
                    "The authorized feed URL contains unsupported query parameters.",
                )
            query_map = {key.casefold(): value for key, value in query}
            if feed_binding[0] == "greenhouse" and any(
                key != "content" or value.casefold() not in {"true", "false"}
                for key, value in query_map.items()
            ):
                raise JobOpsError("DISCOVERY_SOURCE_QUERY_INVALID", "The Greenhouse feed query is unsupported.")
            if feed_binding[0] == "lever" and any(
                key != "mode" or value.casefold() != "json" for key, value in query_map.items()
            ):
                raise JobOpsError("DISCOVERY_SOURCE_QUERY_INVALID", "The Lever feed must use JSON mode.")
            if feed_binding[0] == "smartrecruiters" and any(
                key not in {"limit", "offset"} or not value.isdigit() or int(value) > 5_000
                for key, value in query_map.items()
            ):
                raise JobOpsError("DISCOVERY_SOURCE_QUERY_INVALID", "The SmartRecruiters feed query is unsupported.")
            provider, tenant = feed_binding
            direct_feed = True
    if provider is not None and tenant is not None:
        board_url = _canonical_url(_PUBLIC_BOARD_ROOTS[provider].format(tenant=tenant))
        feed_url = url if direct_feed else _canonical_url(_PUBLIC_FEEDS[provider].format(tenant=tenant))
        checked = _safe_source({
            "provider": provider,
            "company_domain": registrable_domain(urlparse(board_url).hostname or ""),
            "official_entry_url": board_url,
            "feed_url": feed_url,
        })
        return {key: checked[key] for key in (
            "provider", "company_domain", "official_entry_url", "feed_url",
        )}
    if not is_proven_careers_entry_url(url):
        raise JobOpsError(
            "OFFICIAL_CAREERS_PATH_NOT_PROVEN",
            "The URL is not identifiable as a public company careers page or supported ATS board root.",
        )
    checked = _safe_source({
        "provider": "company",
        "company_domain": registrable_domain(host),
        "official_entry_url": url,
        "feed_url": url,
    })
    return {key: checked[key] for key in (
        "provider", "company_domain", "official_entry_url", "feed_url",
    )}


def _safe_source(raw: Any) -> dict[str, str]:
    required = {"provider", "company_domain", "official_entry_url", "feed_url"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise JobOpsError("DISCOVERY_SOURCE_INVALID", "Each authorized source must use the exact source contract.")
    provider = str(raw.get("provider") or "").strip().casefold()
    if provider not in _SOURCE_FORMATS:
        raise JobOpsError("DISCOVERY_SOURCE_PROVIDER_INVALID", "The authorized source provider is unsupported.")
    company_domain = registrable_domain(str(raw.get("company_domain") or "").strip())
    official_entry_url = _canonical_url(str(raw.get("official_entry_url") or "").strip())
    feed_url = _canonical_url(str(raw.get("feed_url") or "").strip())
    if url_has_sensitive_query(official_entry_url) or url_has_sensitive_query(feed_url):
        raise JobOpsError("DISCOVERY_SOURCE_SENSITIVE_URL", "Authorized discovery URLs cannot contain credential-like query fields.")
    official = urlparse(official_entry_url)
    feed = urlparse(feed_url)
    if official.scheme != "https" or feed.scheme != "https":
        raise JobOpsError("DISCOVERY_SOURCE_HTTPS_REQUIRED", "Authorized discovery accepts HTTPS sources only.")
    if not host_matches_registered(str(official.hostname or ""), company_domain):
        raise JobOpsError("DISCOVERY_SOURCE_COMPANY_MISMATCH", "The official entry URL must belong to the declared company domain.")
    if official.username or official.password or feed.username or feed.password or official.fragment or feed.fragment:
        raise JobOpsError("DISCOVERY_SOURCE_URL_INVALID", "Authorized discovery URLs cannot contain credentials or fragments.")
    query_keys = {key.casefold() for key, _ in parse_qsl(feed.query, keep_blank_values=True)}
    if not query_keys.issubset(_ALLOWED_QUERY_KEYS[provider]):
        raise JobOpsError("DISCOVERY_SOURCE_QUERY_INVALID", "The authorized feed URL contains unsupported query parameters.")
    if provider == "company":
        if feed_url != official_entry_url:
            raise JobOpsError("DISCOVERY_SOURCE_COMPANY_FEED_MISMATCH", "A direct company source must read only its exact official entry URL.")
    else:
        if str(feed.hostname or "").casefold() != _FEED_HOSTS[provider]:
            raise JobOpsError("DISCOVERY_SOURCE_FEED_HOST_INVALID", "The authorized provider feed host is not exact.")
        if _FEED_PATHS[provider].fullmatch(feed.path or "/") is None:
            raise JobOpsError("DISCOVERY_SOURCE_FEED_PATH_INVALID", "The authorized provider feed path is invalid.")
    binding = f"{provider}|{company_domain}|{official_entry_url}|{feed_url}"
    return {
        "source_id": stable_id("ADS", binding),
        "provider": provider,
        "source_format": _SOURCE_FORMATS[provider],
        "company_domain": company_domain,
        "official_entry_url": official_entry_url,
        "feed_url": feed_url,
    }


def normalize_authorized_discovery_config(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "sources", "include_terms", "exclude_terms", "location_terms",
    }:
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The authorized discovery configuration has an invalid structure.")
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= MAX_SOURCES:
        raise JobOpsError("DISCOVERY_SOURCE_COUNT_INVALID", "Choose between 1 and 50 exact official sources.")
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        source = _safe_source(raw)
        if source["source_id"] in seen:
            continue
        seen.add(source["source_id"])
        sources.append(source)
    if not sources:
        raise JobOpsError("DISCOVERY_SOURCE_COUNT_INVALID", "At least one distinct official source is required.")
    include_terms = _safe_terms(value.get("include_terms"), name="include_terms")
    if not include_terms:
        raise JobOpsError("DISCOVERY_FILTER_INVALID", "At least one included role term is required.")
    current = _now(now)
    config = {
        "schema_version": 1,
        "authorization_id": "ADC-" + secrets.token_hex(8).upper(),
        "sources": sorted(sources, key=lambda item: item["source_id"]),
        "include_terms": include_terms,
        "exclude_terms": _safe_terms(value.get("exclude_terms"), name="exclude_terms"),
        "location_terms": _safe_terms(value.get("location_terms"), name="location_terms"),
        "created_at": iso_utc(current),
        "safety": {
            "read_only_http_get": True,
            "credentials_available": False,
            "browser_actions": False,
            "application_creation": False,
            "application_form_access": False,
            "material_upload": False,
            "final_submit": "USER_ONLY",
        },
    }
    canonical_json(config)
    return config


def validate_authorized_discovery_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "authorization_id", "sources", "include_terms", "exclude_terms",
        "location_terms", "created_at", "safety",
    }:
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The encrypted discovery configuration has an invalid structure.")
    if value.get("schema_version") != 1 or re.fullmatch(r"ADC-[A-F0-9]{16}", str(value.get("authorization_id") or "")) is None:
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The encrypted discovery configuration identity is invalid.")
    try:
        parse_iso(str(value.get("created_at") or ""))
    except (TypeError, ValueError) as exc:
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The encrypted discovery configuration time is invalid.") from exc
    expected_safety = {
        "read_only_http_get": True,
        "credentials_available": False,
        "browser_actions": False,
        "application_creation": False,
        "application_form_access": False,
        "material_upload": False,
        "final_submit": "USER_ONLY",
    }
    if value.get("safety") != expected_safety:
        raise JobOpsError("DISCOVERY_CONFIG_BOUNDARY_CHANGED", "The encrypted discovery safety boundary changed unexpectedly.")
    sources = value.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The encrypted discovery source list is invalid.")
    checked: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != {
            "source_id", "provider", "source_format", "company_domain", "official_entry_url", "feed_url",
        }:
            raise JobOpsError("DISCOVERY_CONFIG_INVALID", "An encrypted discovery source is invalid.")
        normalized = _safe_source({key: source[key] for key in (
            "provider", "company_domain", "official_entry_url", "feed_url",
        )})
        if source != normalized:
            raise JobOpsError("DISCOVERY_CONFIG_SOURCE_CHANGED", "An encrypted discovery source binding changed unexpectedly.")
        checked.append(normalized)
    for key in ("include_terms", "exclude_terms", "location_terms"):
        if value.get(key) != _safe_terms(value.get(key), name=key):
            raise JobOpsError("DISCOVERY_CONFIG_INVALID", "An encrypted discovery filter is not canonical.")
    if not value["include_terms"] or len({item["source_id"] for item in checked}) != len(checked):
        raise JobOpsError("DISCOVERY_CONFIG_INVALID", "The encrypted discovery configuration is incomplete.")
    canonical_json(value)
    return dict(value)


def _candidate_record(value: Any) -> dict[str, str]:
    required = {"source_id", "provider", "company_domain", "official_url", "title", "location"}
    if not isinstance(value, dict) or set(value) != required:
        raise JobOpsError("DISCOVERY_CANDIDATE_INVALID", "A discovered candidate has an invalid structure.")
    source_id = str(value.get("source_id") or "").strip()
    provider = str(value.get("provider") or "").strip().casefold()
    company_domain = registrable_domain(str(value.get("company_domain") or "").strip())
    official_url = _canonical_url(str(value.get("official_url") or "").strip())
    title = re.sub(r"\s+", " ", str(value.get("title") or "")).strip()
    location = re.sub(r"\s+", " ", str(value.get("location") or "")).strip()
    if re.fullmatch(r"ADS-[A-F0-9]{12}", source_id) is None or provider not in CANDIDATE_PROVIDERS:
        raise JobOpsError("DISCOVERY_CANDIDATE_INVALID", "A discovered candidate identity is invalid.")
    if url_has_sensitive_query(official_url) or urlparse(official_url).scheme != "https":
        raise JobOpsError("DISCOVERY_CANDIDATE_URL_INVALID", "A discovered candidate URL is not safe to retain.")
    if not title or not location or len(title) > 500 or len(location) > 500:
        raise JobOpsError("DISCOVERY_CANDIDATE_INVALID", "A discovered candidate summary is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in title + location):
        raise JobOpsError("DISCOVERY_CANDIDATE_INVALID", "A discovered candidate summary contains control characters.")
    public = {
        "source_id": source_id,
        "provider": provider,
        "company_domain": company_domain,
        "official_url": official_url,
        "title": title,
        "location": location,
    }
    public["candidate_id"] = stable_id("JDC", source_id, official_url)
    public["content_hash"] = sha256_bytes(canonical_json(public))
    return public


class AuthorizedDiscoveryControl:
    """Own an expiring, kill-switchable read-only discovery authorization.

    This state machine does not perform network access or register an OS task.
    Those adapters must claim a generation-bound lease, revalidate it before
    every database write, and report only aggregate results.
    """

    def __init__(self, database: Any, onboarding: Any, schema_root: Path | None = None) -> None:
        self.database = database
        self.onboarding = onboarding
        self.schemas = schema_root or project_root() / "schemas"

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        return sha256_bytes(canonical_json({key: item for key, item in value.items() if key != "control_hash"}))

    @staticmethod
    def _default(now: datetime) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "configured": False,
            "enabled": False,
            "paused": False,
            "pause_reason": None,
            "generation": 0,
            "interval_minutes": None,
            "authorized_until": None,
            "next_run_at": None,
            "last_run_at": None,
            "last_run_status": None,
            "consecutive_failures": 0,
            "max_new_per_run": None,
            "inbox_limit": None,
            "source_count": 0,
            "config_ref": None,
            "config_hash": None,
            "task_registration_state": "NOT_REGISTERED",
            "active_run_id": None,
            "active_run_generation": None,
            "run_lease_expires_at": None,
            "updated_at": iso_utc(now),
        }
        value["control_hash"] = AuthorizedDiscoveryControl._hash(value)
        return value

    @staticmethod
    def _validate_raw(value: Any) -> dict[str, Any]:
        required = {
            "schema_version", "configured", "enabled", "paused", "pause_reason", "generation",
            "interval_minutes", "authorized_until", "next_run_at", "last_run_at", "last_run_status",
            "consecutive_failures", "max_new_per_run", "source_count", "config_ref", "config_hash",
            "inbox_limit",
            "task_registration_state", "active_run_id", "active_run_generation", "run_lease_expires_at",
            "updated_at", "control_hash",
        }
        if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery control has an invalid structure.")
        if any(type(value.get(key)) is not bool for key in ("configured", "enabled", "paused")):
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery flags are invalid.")
        if type(value.get("generation")) is not int or int(value["generation"]) < 0:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery generation is invalid.")
        if value.get("pause_reason") not in {None, *PAUSE_REASONS}:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery pause reason is invalid.")
        if value.get("task_registration_state") not in {
            "NOT_REGISTERED", "REGISTRATION_REQUIRED", "REGISTERED", "REMOVAL_REQUIRED", "UNKNOWN",
        }:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved task registration state is invalid.")
        configured = bool(value["configured"])
        if configured:
            if (
                type(value.get("interval_minutes")) is not int
                or not MIN_INTERVAL_MINUTES <= int(value["interval_minutes"]) <= MAX_INTERVAL_MINUTES
                or type(value.get("max_new_per_run")) is not int
                or not MIN_MAX_NEW_PER_RUN <= int(value["max_new_per_run"]) <= MAX_MAX_NEW_PER_RUN
                or type(value.get("inbox_limit")) is not int
                or not MIN_INBOX_LIMIT <= int(value["inbox_limit"]) <= MAX_INBOX_LIMIT
                or type(value.get("source_count")) is not int
                or not 1 <= int(value["source_count"]) <= MAX_SOURCES
                or not isinstance(value.get("config_ref"), str)
                or not str(value["config_ref"]).startswith("secure-ref:")
                or re.fullmatch(r"sha256:[a-f0-9]{64}", str(value.get("config_hash") or "")) is None
            ):
                raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery configuration reference is invalid.")
        elif any(value.get(key) is not None for key in (
            "interval_minutes", "authorized_until", "next_run_at", "max_new_per_run", "inbox_limit",
            "config_ref", "config_hash",
        )) or value.get("source_count") != 0 or value.get("enabled") or value.get("paused"):
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "An unconfigured discovery control contains active values.")
        if type(value.get("consecutive_failures")) is not int or int(value["consecutive_failures"]) < 0:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery failure count is invalid.")
        timestamp_keys = ("authorized_until", "next_run_at", "last_run_at", "run_lease_expires_at", "updated_at")
        for key in timestamp_keys:
            raw = value.get(key)
            if raw is None:
                if key == "updated_at" or (configured and key in {"authorized_until", "next_run_at"}):
                    raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "A required discovery timestamp is missing.")
                continue
            if not isinstance(raw, str):
                raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "A discovery timestamp is invalid.")
            try:
                parse_iso(raw)
            except (TypeError, ValueError) as exc:
                raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "A discovery timestamp is invalid.") from exc
        active = value.get("active_run_id")
        if active is None:
            if value.get("active_run_generation") is not None or value.get("run_lease_expires_at") is not None:
                raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The discovery run lease is incomplete.")
        elif (
            re.fullmatch(r"ADR-[A-F0-9]{24}", str(active)) is None
            or type(value.get("active_run_generation")) is not int
            or value.get("run_lease_expires_at") is None
        ):
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The discovery run lease is invalid.")
        if value.get("last_run_status") not in {None, "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED_STALE"}:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery run status is invalid.")
        if value.get("control_hash") != AuthorizedDiscoveryControl._hash(value):
            raise JobOpsError("DISCOVERY_CONTROL_STATE_CHANGED", "The saved discovery control changed unexpectedly.")
        return dict(value)

    def _load(self, connection: Any, now: datetime) -> dict[str, Any]:
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,)).fetchone()
        if row is None:
            return self._default(now)
        try:
            return self._validate_raw(json.loads(str(row["value"])))
        except (TypeError, json.JSONDecodeError) as exc:
            raise JobOpsError("DISCOVERY_CONTROL_STATE_INVALID", "The saved discovery control is unreadable.") from exc

    def _save(self, connection: Any, raw: dict[str, Any]) -> None:
        raw["control_hash"] = self._hash(raw)
        self._validate_raw(raw)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CONTROL_METADATA_KEY, canonical_json(raw).decode("utf-8")),
        )

    def _private_config_from_raw(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Read and verify the encrypted configuration bound to one control snapshot."""

        value = self.onboarding.read_bytes(str(raw["config_ref"]))
        if sha256_bytes(value) != raw["config_hash"]:
            raise JobOpsError(
                "DISCOVERY_CONFIG_HASH_MISMATCH",
                "The encrypted discovery configuration no longer matches its authorization.",
            )
        try:
            decoded = json.loads(value.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise JobOpsError(
                "DISCOVERY_CONFIG_INVALID",
                "The encrypted discovery configuration is unreadable.",
            ) from exc
        return validate_authorized_discovery_config(decoded)

    @staticmethod
    def _status(raw: dict[str, Any], now: datetime) -> str:
        if not raw["configured"]:
            return "NOT_CONFIGURED"
        if raw["paused"] or not raw["enabled"]:
            return "PAUSED"
        if parse_iso(str(raw["authorized_until"])) <= now:
            return "AUTHORIZATION_EXPIRED"
        if raw["active_run_id"] is not None and parse_iso(str(raw["run_lease_expires_at"])) > now:
            return "RUNNING"
        if parse_iso(str(raw["next_run_at"])) <= now:
            return "DUE"
        return "READY"

    def _public(self, raw: dict[str, Any], now: datetime) -> dict[str, Any]:
        status = self._status(raw, now)
        value = {
            "schema_version": 1,
            "status": status,
            "mode": MODE,
            "configured": bool(raw["configured"]),
            "enabled": bool(raw["enabled"]),
            "paused": bool(raw["paused"]),
            "pause_reason": raw["pause_reason"],
            "generation": int(raw["generation"]),
            "interval_minutes": raw["interval_minutes"],
            "authorized_until": raw["authorized_until"],
            "next_run_at": raw["next_run_at"],
            "last_run_at": raw["last_run_at"],
            "last_run_status": raw["last_run_status"],
            "consecutive_failures": int(raw["consecutive_failures"]),
            "max_new_per_run": raw["max_new_per_run"],
            "inbox_limit": raw["inbox_limit"],
            "source_count": int(raw["source_count"]),
            "task_registration_state": raw["task_registration_state"],
            "run_active": status == "RUNNING",
            "read_only_network_authorized": (
                status in {"READY", "DUE", "RUNNING"}
                and raw["task_registration_state"] == "REGISTERED"
            ),
            "application_actions_authorized": False,
            "browser_actions_authorized": False,
            "material_upload_authorized": False,
            "final_submit": "USER_ONLY",
            "automatic_retry": False,
            "updated_at": raw["updated_at"],
            "control_hash": raw["control_hash"],
        }
        validate_named("authorized-discovery-control", value, self.schemas)
        return value

    @staticmethod
    def _event(connection: Any, event_type: str, from_state: str, to_state: str, payload: dict[str, Any], now: datetime) -> None:
        connection.execute(
            "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(NULL,?,?,?,?,?)",
            (event_type, from_state, to_state, canonical_json(payload).decode("utf-8"), iso_utc(now)),
        )

    def state(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.database.connect() as connection:
            raw = self._load(connection, current)
        return self._public(raw, current)

    def configure(
        self,
        config: Any,
        *,
        interval_minutes: int,
        authorization_hours: int,
        max_new_per_run: int,
        user_confirmed: bool,
        inbox_limit: int = DEFAULT_INBOX_LIMIT,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Background read-only discovery requires explicit user confirmation.")
        if type(interval_minutes) is not int or not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
            raise JobOpsError("DISCOVERY_INTERVAL_INVALID", "Choose a discovery interval from 60 to 1440 minutes.")
        if type(authorization_hours) is not int or not MIN_AUTHORIZATION_HOURS <= authorization_hours <= MAX_AUTHORIZATION_HOURS:
            raise JobOpsError("DISCOVERY_AUTHORIZATION_WINDOW_INVALID", "Choose an authorization window from 1 to 168 hours.")
        if type(max_new_per_run) is not int or not MIN_MAX_NEW_PER_RUN <= max_new_per_run <= MAX_MAX_NEW_PER_RUN:
            raise JobOpsError("DISCOVERY_RUN_LIMIT_INVALID", "Choose a per-run candidate limit from 1 to 100.")
        if type(inbox_limit) is not int or not MIN_INBOX_LIMIT <= inbox_limit <= MAX_INBOX_LIMIT:
            raise JobOpsError("DISCOVERY_INBOX_LIMIT_INVALID", "Choose a discovery inbox limit from 10 to 1000 candidates.")
        current = _now(now)
        private_config = normalize_authorized_discovery_config(config, now=current)
        config_bytes = canonical_json(private_config)
        with self.database.connect() as connection:
            snapshot = self._load(connection, current)
        snapshot_generation = int(snapshot["generation"])
        snapshot_control_hash = str(snapshot["control_hash"])
        previous_reference = str(snapshot["config_ref"]) if snapshot["configured"] else None
        # Configuration ciphertexts are immutable.  Importing a new reference
        # before the metadata CAS prevents two concurrent reconfigurations from
        # rotating (and then rolling back) the same secure reference over each
        # other.  The random authorization_id makes each confirmed renewal a
        # distinct encrypted value.
        stored = self.onboarding.import_bytes(CONFIG_KIND, config_bytes, synthetic=False)
        reference = str(stored["secure_ref"])
        created_reference = stored.get("deduplicated") is not True
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = self._load(connection, current)
                if (
                    int(previous["generation"]) != snapshot_generation
                    or str(previous["control_hash"]) != snapshot_control_hash
                ):
                    raise JobOpsError(
                        "DISCOVERY_CONTROL_CONCURRENT_CHANGE",
                        "The discovery control changed while its encrypted configuration was being replaced.",
                    )
                from_state = self._status(previous, current)
                raw = {
                    "schema_version": 1,
                    "configured": True,
                    "enabled": True,
                    "paused": False,
                    "pause_reason": None,
                    "generation": int(previous["generation"]) + 1,
                    "interval_minutes": interval_minutes,
                    "authorized_until": iso_utc(current + timedelta(hours=authorization_hours)),
                    "next_run_at": iso_utc(current + timedelta(minutes=interval_minutes)),
                    "last_run_at": previous.get("last_run_at"),
                    "last_run_status": previous.get("last_run_status"),
                    "consecutive_failures": 0,
                    "max_new_per_run": max_new_per_run,
                    "inbox_limit": inbox_limit,
                    "source_count": len(private_config["sources"]),
                    "config_ref": reference,
                    "config_hash": sha256_bytes(config_bytes),
                    "task_registration_state": "REGISTRATION_REQUIRED",
                    "active_run_id": None,
                    "active_run_generation": None,
                    "run_lease_expires_at": None,
                    "updated_at": iso_utc(current),
                }
                self._save(connection, raw)
                if previous_reference is not None and previous_reference != reference:
                    connection.execute(
                        "UPDATE private_refs SET status='REVOKED',updated_at=? "
                        "WHERE secure_ref=? AND status='ACTIVE'",
                        (iso_utc(current), previous_reference),
                    )
                self._event(connection, "AUTHORIZED_DISCOVERY_CONFIGURED", from_state, "READY", {
                    "generation": raw["generation"],
                    "source_count": raw["source_count"],
                    "interval_minutes": interval_minutes,
                    "authorization_hours": authorization_hours,
                    "max_new_per_run": max_new_per_run,
                    "inbox_limit": inbox_limit,
                    "network_scope": "EXACT_AUTHORIZED_HTTPS_SOURCES_ONLY",
                    "application_actions_authorized": False,
                    "final_submit": "USER_ONLY",
                }, current)
        except Exception:
            try:
                if created_reference:
                    self.onboarding.delete(reference, user_confirmed=True)
            except Exception as cleanup_error:
                raise JobOpsError(
                    "DISCOVERY_CONFIG_ROLLBACK_FAILED",
                    "Discovery configuration failed and its encrypted rollback could not be verified.",
                ) from cleanup_error
            raise
        if previous_reference is not None and previous_reference != reference:
            try:
                self.onboarding.delete(previous_reference, user_confirmed=True)
            except JobOpsError:
                # The prior reference was atomically revoked with the control
                # switch, so a failed ciphertext cleanup cannot reactivate or
                # alter the new authorization.  Retained revoked ciphertext is
                # still covered by the private-integrity audit.
                with self.database.connect() as connection:
                    self._event(
                        connection,
                        "AUTHORIZED_DISCOVERY_CONFIG_RETIREMENT_DEFERRED",
                        "READY",
                        "READY",
                        {"retained_revoked_references": 1, "application_actions_authorized": False},
                        current,
                    )
        return self._public(raw, current)

    def read_private_config(self, *, run_id: str, generation: int, now: datetime | None = None) -> dict[str, Any]:
        raw = self.assert_run_current(run_id=run_id, generation=generation, now=now)
        return self._private_config_from_raw(raw)

    def list_candidates(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        safe_status = None if status is None else str(status).strip().upper()
        if safe_status not in {None, "NEW", "QUEUED", "IGNORED"}:
            raise JobOpsError("DISCOVERY_CANDIDATE_STATUS_INVALID", "The discovery candidate status is invalid.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise JobOpsError("DISCOVERY_CANDIDATE_LIMIT_INVALID", "Choose a candidate list limit from 1 to 500.")
        where = "" if safe_status is None else "WHERE status=?"
        parameters: tuple[Any, ...] = (limit,) if safe_status is None else (safe_status, limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT candidate_id,provider,company_domain,official_url,title,location,status,
                first_seen_at,last_seen_at,content_hash FROM discovery_candidates {where}
                ORDER BY last_seen_at DESC,candidate_id LIMIT ?""",
                parameters,
            ).fetchall()
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM discovery_candidates GROUP BY status"
                ).fetchall()
            }
        return {
            "schema_version": 1,
            "status": "DISCOVERY_CANDIDATE_INBOX_READY",
            "counts": {key: counts.get(key, 0) for key in ("NEW", "QUEUED", "IGNORED")},
            "candidates": [dict(row) for row in rows],
            "application_actions": 0,
            "browser_actions": 0,
            "material_uploads": 0,
            "final_submit": "USER_ONLY",
        }

    def set_candidate_status(
        self,
        *,
        candidate_id: str,
        status: str,
        user_confirmed: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Changing a discovered candidate requires explicit user confirmation.")
        safe_id = str(candidate_id or "").strip()
        safe_status = str(status or "").strip().upper()
        if re.fullmatch(r"JDC-[A-F0-9]{12}", safe_id) is None or safe_status not in {"NEW", "QUEUED", "IGNORED"}:
            raise JobOpsError("DISCOVERY_CANDIDATE_STATUS_INVALID", "The discovery candidate status change is invalid.")
        current = _now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,content_hash FROM discovery_candidates WHERE candidate_id=?", (safe_id,),
            ).fetchone()
            if row is None:
                raise JobOpsError("DISCOVERY_CANDIDATE_NOT_FOUND", "The discovery candidate was not found.")
            previous = str(row["status"])
            allowed_transition = (previous, safe_status) in {
                ("NEW", "QUEUED"),
                ("NEW", "IGNORED"),
                ("IGNORED", "NEW"),
            }
            if previous != safe_status and not allowed_transition:
                raise JobOpsError(
                    "DISCOVERY_CANDIDATE_TRANSITION_INVALID",
                    "The discovery candidate cannot move between those states.",
                )
            if previous != safe_status:
                connection.execute(
                    "UPDATE discovery_candidates SET status=?,last_seen_at=? WHERE candidate_id=?",
                    (safe_status, iso_utc(current), safe_id),
                )
                connection.execute(
                    "INSERT INTO discovery_candidate_events(candidate_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?)",
                    (safe_id, "RESTORED" if safe_status == "NEW" else safe_status, str(row["content_hash"]), iso_utc(current)),
                )
        return {"candidate_id": safe_id, "from_status": previous, "status": safe_status, "application_created": False}

    def mark_task_registration(self, *, registered: bool, generation: int, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            if int(raw["generation"]) != generation:
                raise JobOpsError("DISCOVERY_CONTROL_STALE_GENERATION", "The discovery authorization changed before task registration completed.")
            if registered and (
                not raw["configured"]
                or not raw["enabled"]
                or raw["paused"]
                or parse_iso(str(raw.get("authorized_until") or "1970-01-01T00:00:00Z")) <= current
            ):
                raise JobOpsError(
                    "DISCOVERY_TASK_REGISTRATION_NOT_ALLOWED",
                    "Only a current, enabled discovery authorization can register a background task.",
                )
            raw["task_registration_state"] = "REGISTERED" if registered else "NOT_REGISTERED"
            raw["updated_at"] = iso_utc(current)
            self._save(connection, raw)
        return self._public(raw, current)

    def pause(self, *, user_confirmed: bool, reason: str = "USER_PAUSED", now: datetime | None = None) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Pausing discovery requires explicit user confirmation.")
        safe_reason = str(reason or "").strip().upper()
        if safe_reason not in PAUSE_REASONS:
            raise JobOpsError("DISCOVERY_PAUSE_REASON_INVALID", "The discovery pause reason is invalid.")
        current = _now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            if not raw["configured"]:
                raise JobOpsError("DISCOVERY_NOT_CONFIGURED", "Read-only discovery has not been configured.")
            from_state = self._status(raw, current)
            raw.update({
                "enabled": False,
                "paused": True,
                "pause_reason": safe_reason,
                "generation": int(raw["generation"]) + 1,
                "task_registration_state": "REMOVAL_REQUIRED" if raw["task_registration_state"] == "REGISTERED" else "NOT_REGISTERED",
                "active_run_id": None,
                "active_run_generation": None,
                "run_lease_expires_at": None,
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            self._event(connection, "AUTHORIZED_DISCOVERY_PAUSED", from_state, "PAUSED", {
                "generation": raw["generation"], "reason": safe_reason,
                "stale_runs_can_commit": False, "application_actions_authorized": False,
            }, current)
        return self._public(raw, current)

    def resume(
        self,
        *,
        authorization_hours: int,
        user_confirmed: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Renew an existing encrypted configuration without exposing it again."""

        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Resuming discovery requires explicit user confirmation.")
        if type(authorization_hours) is not int or not MIN_AUTHORIZATION_HOURS <= authorization_hours <= MAX_AUTHORIZATION_HOURS:
            raise JobOpsError("DISCOVERY_AUTHORIZATION_WINDOW_INVALID", "Choose an authorization window from 1 to 168 hours.")
        current = _now(now)
        with self.database.connect() as connection:
            snapshot = self._load(connection, current)
        if not snapshot["configured"]:
            raise JobOpsError("DISCOVERY_NOT_CONFIGURED", "Read-only discovery has not been configured.")
        # A renewal must not reactivate a missing, corrupted, or replaced
        # encrypted configuration.  Verify it before changing capability.
        self._private_config_from_raw(snapshot)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            if (
                int(raw["generation"]) != int(snapshot["generation"])
                or str(raw["control_hash"]) != str(snapshot["control_hash"])
            ):
                raise JobOpsError(
                    "DISCOVERY_CONTROL_CONCURRENT_CHANGE",
                    "The discovery control changed while its encrypted configuration was being verified.",
                )
            from_state = self._status(raw, current)
            raw.update({
                "enabled": True,
                "paused": False,
                "pause_reason": None,
                "generation": int(raw["generation"]) + 1,
                "authorized_until": iso_utc(current + timedelta(hours=authorization_hours)),
                "next_run_at": iso_utc(current),
                "consecutive_failures": 0,
                "task_registration_state": "REGISTRATION_REQUIRED",
                "active_run_id": None,
                "active_run_generation": None,
                "run_lease_expires_at": None,
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            self._event(connection, "AUTHORIZED_DISCOVERY_RESUMED", from_state, "DUE", {
                "generation": raw["generation"],
                "authorization_hours": authorization_hours,
                "network_scope": "EXACT_AUTHORIZED_HTTPS_SOURCES_ONLY",
                "application_actions_authorized": False,
                "final_submit": "USER_ONLY",
            }, current)
        return self._public(raw, current)

    def kill_switch(self, *, user_confirmed: bool, now: datetime | None = None) -> dict[str, Any]:
        return self.pause(user_confirmed=user_confirmed, reason="USER_KILL_SWITCH", now=now)

    def claim_due_run(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            if raw["active_run_id"] is not None and parse_iso(str(raw["run_lease_expires_at"])) <= current:
                raw.update({
                    "active_run_id": None,
                    "active_run_generation": None,
                    "run_lease_expires_at": None,
                    "last_run_status": "STOPPED_STALE",
                })
            if raw["task_registration_state"] != "REGISTERED":
                raise JobOpsError("DISCOVERY_TASK_NOT_REGISTERED", "The authorized discovery task is not registered.")
            status = self._status(raw, current)
            code = {
                "NOT_CONFIGURED": "DISCOVERY_NOT_CONFIGURED",
                "PAUSED": "DISCOVERY_PAUSED",
                "AUTHORIZATION_EXPIRED": "DISCOVERY_AUTHORIZATION_EXPIRED",
                "READY": "DISCOVERY_NOT_DUE",
                "RUNNING": "DISCOVERY_RUN_ALREADY_ACTIVE",
            }.get(status)
            if code:
                raise JobOpsError(code, "The authorized read-only discovery run cannot start in its current state.")
            run_id = "ADR-" + secrets.token_hex(12).upper()
            raw.update({
                "active_run_id": run_id,
                "active_run_generation": int(raw["generation"]),
                "run_lease_expires_at": iso_utc(current + timedelta(minutes=RUN_LEASE_MINUTES)),
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            self._event(connection, "AUTHORIZED_DISCOVERY_RUN_CLAIMED", "DUE", "RUNNING", {
                "generation": raw["generation"], "run_id_hash": sha256_bytes(run_id.encode("ascii")),
                "network_scope": "EXACT_AUTHORIZED_HTTPS_SOURCES_ONLY",
            }, current)
        return {
            "run_id": run_id,
            "generation": int(raw["generation"]),
            "source_count": int(raw["source_count"]),
            "lease_expires_at": raw["run_lease_expires_at"],
        }

    def assert_run_current(self, *, run_id: str, generation: int, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.database.connect() as connection:
            raw = self._load(connection, current)
        if (
            raw.get("active_run_id") != run_id
            or raw.get("active_run_generation") != generation
            or raw.get("generation") != generation
            or parse_iso(str(raw.get("run_lease_expires_at") or "1970-01-01T00:00:00Z")) <= current
            or self._status(raw, current) != "RUNNING"
        ):
            raise JobOpsError("DISCOVERY_RUN_STALE", "This discovery run no longer holds the current authorization lease.")
        return raw

    @staticmethod
    def _validate_result(result: Any) -> dict[str, int]:
        required = {"source_count", "network_requests", "candidate_count", "new_candidate_count", "error_count"}
        if not isinstance(result, dict) or set(result) != required or any(
            type(result[key]) is not int or result[key] < 0 for key in required
        ):
            raise JobOpsError("DISCOVERY_RUN_RESULT_INVALID", "The read-only discovery result is invalid.")
        if (
            not 1 <= result["source_count"] <= MAX_SOURCES
            or result["network_requests"] > result["source_count"]
            or result["error_count"] > result["source_count"]
            or result["candidate_count"] > MAX_CANDIDATES_PER_RUN
            or result["new_candidate_count"] > result["candidate_count"]
        ):
            raise JobOpsError("DISCOVERY_RUN_RESULT_INVALID", "The read-only discovery aggregate counts are invalid.")
        return {key: int(result[key]) for key in required}

    @staticmethod
    def _assert_lease(raw: dict[str, Any], *, run_id: str, generation: int, current: datetime) -> None:
        if (
            raw.get("active_run_id") != run_id
            or raw.get("active_run_generation") != generation
            or raw.get("generation") != generation
            or raw.get("configured") is not True
            or raw.get("enabled") is not True
            or raw.get("paused") is True
            or raw.get("task_registration_state") != "REGISTERED"
            or parse_iso(str(raw.get("authorized_until") or "1970-01-01T00:00:00Z")) <= current
            or parse_iso(str(raw.get("run_lease_expires_at") or "1970-01-01T00:00:00Z")) <= current
        ):
            raise JobOpsError("DISCOVERY_RUN_STALE", "This discovery run cannot commit after its authorization changed.")

    def _finish_run(
        self,
        connection: Any,
        raw: dict[str, Any],
        *,
        generation: int,
        result: dict[str, int],
        current: datetime,
    ) -> None:
        if int(result["source_count"]) != int(raw["source_count"]):
            raise JobOpsError(
                "DISCOVERY_RUN_RESULT_INVALID",
                "The read-only discovery result does not match the encrypted source authorization.",
            )
        errors = int(result["error_count"])
        failures = int(raw["consecutive_failures"]) + 1 if errors else 0
        auto_paused = failures >= 3
        raw.update({
            "enabled": not auto_paused,
            "paused": auto_paused,
            "pause_reason": "REPEATED_FAILURES" if auto_paused else None,
            "last_run_at": iso_utc(current),
            "last_run_status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
            "consecutive_failures": failures,
            "next_run_at": iso_utc(current + timedelta(minutes=int(raw["interval_minutes"]))),
            "active_run_id": None,
            "active_run_generation": None,
            "run_lease_expires_at": None,
            "task_registration_state": "REMOVAL_REQUIRED" if auto_paused else raw["task_registration_state"],
            "updated_at": iso_utc(current),
        })
        self._save(connection, raw)
        self._event(connection, "AUTHORIZED_DISCOVERY_RUN_COMPLETED", "RUNNING", "PAUSED" if auto_paused else "READY", {
            "generation": generation,
            "source_count": result["source_count"],
            "network_requests": result["network_requests"],
            "candidate_count": result["candidate_count"],
            "new_candidate_count": result["new_candidate_count"],
            "error_count": errors,
            "application_actions": 0,
            "browser_actions": 0,
            "material_uploads": 0,
            "final_submits": 0,
        }, current)

    def commit_candidates(
        self,
        *,
        run_id: str,
        generation: int,
        candidates: list[dict[str, Any]],
        result: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        aggregate = self._validate_result(result)
        if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES_PER_RUN:
            raise JobOpsError("DISCOVERY_CANDIDATE_LIMIT_INVALID", "The discovery run returned too many candidates.")
        config = self.read_private_config(run_id=run_id, generation=generation, now=now)
        source_bindings = {str(item["source_id"]): item for item in config["sources"]}
        normalized_by_id: dict[str, dict[str, str]] = {}
        for candidate in candidates:
            normalized = _candidate_record(candidate)
            source = source_bindings.get(normalized["source_id"])
            if source is None or normalized["company_domain"] != source["company_domain"]:
                raise JobOpsError(
                    "DISCOVERY_CANDIDATE_SOURCE_MISMATCH",
                    "A discovered candidate no longer matches its encrypted source binding.",
                )
            candidate_host = str(urlparse(normalized["official_url"]).hostname or "")
            source_host = str(urlparse(source["official_entry_url"]).hostname or "")
            try:
                source_ats = _provider_and_tenant(source_host, source["official_entry_url"])
            except JobOpsError:
                source_ats = None
            try:
                candidate_ats = _provider_and_tenant(candidate_host, normalized["official_url"])
            except JobOpsError:
                candidate_ats = None
            if source_ats is not None:
                if candidate_ats is None or candidate_ats[:3] != source_ats[:3]:
                    raise JobOpsError(
                        "DISCOVERY_CANDIDATE_SOURCE_MISMATCH",
                        "A discovered ATS candidate no longer matches its encrypted provider tenant binding.",
                    )
                expected_provider = candidate_ats[0]
            elif host_matches_registered(candidate_host, normalized["company_domain"]):
                expected_provider = "company"
            elif candidate_ats is not None:
                expected_provider = candidate_ats[0]
            else:
                raise JobOpsError(
                    "DISCOVERY_CANDIDATE_SOURCE_MISMATCH",
                    "A discovered candidate URL is outside the authorized company or ATS boundary.",
                )
            if normalized["provider"] != expected_provider:
                raise JobOpsError(
                    "DISCOVERY_CANDIDATE_PROVIDER_MISMATCH",
                    "A discovered candidate provider does not match its verified URL.",
                )
            normalized_by_id[normalized["candidate_id"]] = normalized
        current = _now(now)
        inserted = 0
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            self._assert_lease(raw, run_id=run_id, generation=generation, current=current)
            active_count = int(connection.execute(
                "SELECT COUNT(*) FROM discovery_candidates WHERE status IN ('NEW','QUEUED')"
            ).fetchone()[0])
            capacity = max(0, int(raw["inbox_limit"]) - active_count)
            insertion_limit = min(capacity, int(raw["max_new_per_run"]))
            for candidate_id in sorted(normalized_by_id):
                candidate = normalized_by_id[candidate_id]
                existing = connection.execute(
                    "SELECT content_hash FROM discovery_candidates WHERE candidate_id=?", (candidate_id,),
                ).fetchone()
                if existing is None:
                    if inserted >= insertion_limit:
                        continue
                    connection.execute(
                        """INSERT INTO discovery_candidates(
                        candidate_id,source_id,provider,company_domain,official_url,title,location,
                        content_hash,status,first_seen_at,last_seen_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            candidate_id, candidate["source_id"], candidate["provider"],
                            candidate["company_domain"], candidate["official_url"], candidate["title"],
                            candidate["location"], candidate["content_hash"], "NEW", iso_utc(current), iso_utc(current),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO discovery_candidate_events(candidate_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?)",
                        (candidate_id, "DISCOVERED", candidate["content_hash"], iso_utc(current)),
                    )
                    inserted += 1
                else:
                    changed = str(existing["content_hash"]) != candidate["content_hash"]
                    connection.execute(
                        """UPDATE discovery_candidates SET provider=?,company_domain=?,official_url=?,title=?,
                        location=?,content_hash=?,last_seen_at=? WHERE candidate_id=?""",
                        (
                            candidate["provider"], candidate["company_domain"], candidate["official_url"],
                            candidate["title"], candidate["location"], candidate["content_hash"],
                            iso_utc(current), candidate_id,
                        ),
                    )
                    if changed:
                        connection.execute(
                            "INSERT INTO discovery_candidate_events(candidate_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?)",
                            (candidate_id, "REFRESHED", candidate["content_hash"], iso_utc(current)),
                        )
            aggregate["new_candidate_count"] = inserted
            aggregate["candidate_count"] = len(normalized_by_id)
            self._finish_run(connection, raw, generation=generation, result=aggregate, current=current)
        public = self._public(raw, current)
        public["run_result"] = dict(aggregate)
        return public

    def record_run(
        self,
        *,
        run_id: str,
        generation: int,
        result: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        aggregate = self._validate_result(result)
        current = _now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            self._assert_lease(raw, run_id=run_id, generation=generation, current=current)
            self._finish_run(connection, raw, generation=generation, result=aggregate, current=current)
        return self._public(raw, current)
