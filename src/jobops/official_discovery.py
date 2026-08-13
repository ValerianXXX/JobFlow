from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .errors import JobOpsError
from .sourcing import (
    CAREER_HINTS,
    _canonical_url,
    _host,
    _provider_and_tenant,
    host_matches_registered,
    registrable_domain,
)
from .util import canonical_json, sha256_bytes, stable_id


MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
PLAIN_HTTPS_URL = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
GENERIC_LINK_TEXT = {
    "", "apply", "apply now", "career", "careers", "career opportunities", "details", "job", "jobs",
    "join us", "learn more", "open", "view", "view job", "view role", "申请", "招聘", "查看", "查看职位",
    "职位", "职位详情", "立即申请",
}
JOB_PATH_HINTS = CAREER_HINTS + ("opening", "openings", "position", "positions", "vacancy", "vacancies", "requisition")


def _compact(value: object, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


@dataclass(frozen=True)
class _LinkEvidence:
    href: str
    text: str
    title: str
    location: str
    evidence_kind: str


class _SnapshotHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_LinkEvidence] = []
        self.headings: list[str] = []
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._suppressed_depth = 0
        self.visible_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        values = {key.casefold(): (value or "") for key, value in attrs}
        if lowered in {"script", "style", "noscript", "template"}:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if lowered == "a":
            self._anchor = values
            self._anchor_text = []
        if lowered in {"h1", "h2"}:
            self._heading_tag = lowered
            self._heading_text = []
        data_href = values.get("data-job-url") or values.get("data-apply-url")
        if data_href and lowered != "a":
            self.links.append(
                _LinkEvidence(
                    href=data_href,
                    text="",
                    title=_compact(values.get("data-title") or values.get("aria-label")),
                    location=_compact(values.get("data-location")),
                    evidence_kind="data_attribute",
                )
            )

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "template"}:
            if self._suppressed_depth:
                self._suppressed_depth -= 1
            return
        if self._suppressed_depth:
            return
        if lowered == "a" and self._anchor is not None:
            values = self._anchor
            self.links.append(
                _LinkEvidence(
                    href=values.get("data-job-url") or values.get("data-apply-url") or values.get("href", ""),
                    text=_compact(" ".join(self._anchor_text)),
                    title=_compact(values.get("data-title") or values.get("aria-label") or values.get("title")),
                    location=_compact(values.get("data-location")),
                    evidence_kind="anchor",
                )
            )
            self._anchor = None
            self._anchor_text = []
        if lowered == self._heading_tag:
            heading = _compact(" ".join(self._heading_text))
            if heading:
                self.headings.append(heading)
            self._heading_tag = None
            self._heading_text = []

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        if self._anchor is not None:
            self._anchor_text.append(data)
        if self._heading_tag is not None:
            self._heading_text.append(data)
        if data.strip():
            self.visible_text.append(data)


def _safe_title(evidence: _LinkEvidence) -> tuple[str, str]:
    for value in (evidence.title, evidence.text):
        compact = _compact(value)
        if compact and compact.casefold() not in GENERIC_LINK_TEXT and not compact.casefold().startswith("apply "):
            return compact, "EXTRACTED"
    return "UNKNOWN", "UNKNOWN"


def _is_allowed_ats(host: str, approved_ats_hosts: list[str]) -> bool:
    candidate = _host(host)
    return any(candidate == _host(value) or candidate.endswith("." + _host(value)) for value in approved_ats_hosts)


def _job_link(
    evidence: _LinkEvidence,
    *,
    official_entry_url: str,
    company_registered: str,
    approved_ats_hosts: list[str],
    snapshot_hash: str,
) -> dict[str, object] | None:
    href = _compact(evidence.href, limit=4096)
    if not href or href.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
        return None
    try:
        canonical = _canonical_url(urljoin(official_entry_url, href))
    except (JobOpsError, ValueError):
        return None
    parsed = urlparse(canonical)
    host = _host(parsed.hostname or "")
    is_company = host_matches_registered(host, company_registered)
    is_ats = _is_allowed_ats(host, approved_ats_hosts)
    if not (is_company or is_ats):
        return None

    title, title_status = _safe_title(evidence)
    path_signal = (parsed.path + " " + parsed.query).casefold()
    if is_company and not any(hint in path_signal for hint in JOB_PATH_HINTS) and title_status == "UNKNOWN":
        return None

    if is_company:
        provider, tenant, board = "company", company_registered, "official"
        identity = parsed.path.rstrip("/").split("/")[-1] or "UNKNOWN"
        route_kind = "OFFICIAL_DIRECT_DISCOVERED"
    else:
        try:
            provider, tenant, board, identity = _provider_and_tenant(host, canonical)
        except JobOpsError:
            return None
        route_kind = "OFFICIAL_TO_APPROVED_ATS_DISCOVERED"

    location = _compact(evidence.location)
    location_status = "EXTRACTED" if location else "UNKNOWN"
    if not location:
        location = "UNKNOWN"
    dedupe_material = canonical if identity == "UNKNOWN" else f"{provider}|{tenant}|{board}|{identity}"
    return {
        "candidate_id": stable_id("JDC", dedupe_material),
        "status": "NEEDS_LIVE_FRESHNESS_CHECK",
        "discovered_url": canonical,
        "route_kind": route_kind,
        "provider": provider,
        "ats_tenant": tenant,
        "ats_board": board,
        "ats_job_identity": identity,
        "title": title,
        "title_status": title_status,
        "location": location,
        "location_status": location_status,
        "evidence_kind": evidence.evidence_kind,
        "snapshot_hash": snapshot_hash,
        "requires_live_freshness_check": True,
        "requires_route_verification": True,
        "network_actions": 0,
        "real_external_actions": 0,
    }


def discover_official_jobs(
    snapshot: bytes,
    *,
    official_entry_url: str,
    company_domain: str,
    approved_ats_hosts: list[str],
    source_format: str = "html",
) -> dict[str, object]:
    """Parse a caller-supplied local snapshot. This function has no network capability."""
    if not snapshot or len(snapshot) > MAX_SNAPSHOT_BYTES:
        raise JobOpsError(
            "OFFICIAL_SNAPSHOT_SIZE_INVALID",
            "The local official-careers snapshot must be non-empty and no larger than the offline parser limit.",
            maximum_bytes=MAX_SNAPSHOT_BYTES,
        )
    if source_format not in {"html", "page_snapshot"}:
        raise JobOpsError("OFFICIAL_SNAPSHOT_FORMAT_UNSUPPORTED", "Only local HTML and saved-page JSON snapshots are supported.")
    try:
        decoded = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JobOpsError("OFFICIAL_SNAPSHOT_ENCODING_INVALID", "The local snapshot must be UTF-8.") from exc

    entry_url = _canonical_url(official_entry_url)
    entry_host = _host(urlparse(entry_url).hostname or "")
    registered = registrable_domain(company_domain)
    if not host_matches_registered(entry_host, registered):
        raise JobOpsError("COMPANY_DOMAIN_MISMATCH", "The official careers URL does not belong to the declared company domain.")
    if not any(hint in (urlparse(entry_url).path + " " + urlparse(entry_url).query).casefold() for hint in CAREER_HINTS):
        raise JobOpsError("OFFICIAL_CAREERS_PATH_NOT_PROVEN", "The saved page URL is not identifiable as an official careers/jobs page.")

    html = decoded
    if source_format == "page_snapshot":
        try:
            envelope = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise JobOpsError("OFFICIAL_PAGE_SNAPSHOT_INVALID", "The saved-page snapshot is not valid JSON.") from exc
        if not isinstance(envelope, dict) or set(envelope) - {"source_url", "html"}:
            raise JobOpsError("OFFICIAL_PAGE_SNAPSHOT_INVALID", "The saved-page snapshot contains missing or unrecognized fields.")
        if _canonical_url(str(envelope.get("source_url", ""))) != entry_url:
            raise JobOpsError("OFFICIAL_PAGE_SOURCE_MISMATCH", "The saved-page source URL does not match the declared official careers URL.")
        html = envelope.get("html")
        if not isinstance(html, str) or not html.strip():
            raise JobOpsError("OFFICIAL_PAGE_SNAPSHOT_INVALID", "The saved-page snapshot must contain non-empty HTML.")

    snapshot_hash = sha256_bytes(snapshot)
    parser = _SnapshotHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise JobOpsError("OFFICIAL_SNAPSHOT_HTML_INVALID", "The local careers snapshot could not be parsed safely.") from exc

    evidences = list(parser.links)
    visible = " ".join(parser.visible_text)
    existing_hrefs = {item.href for item in evidences}
    for match in PLAIN_HTTPS_URL.findall(visible):
        value = match.rstrip(".,);]}")
        if value not in existing_hrefs:
            evidences.append(_LinkEvidence(value, "", "", "", "plain_url"))

    candidates_by_key: dict[str, dict[str, object]] = {}
    ignored = 0
    duplicates = 0
    for evidence in evidences:
        candidate = _job_link(
            evidence,
            official_entry_url=entry_url,
            company_registered=registered,
            approved_ats_hosts=approved_ats_hosts,
            snapshot_hash=snapshot_hash,
        )
        if candidate is None:
            ignored += 1
            continue
        key = str(candidate["candidate_id"])
        if key in candidates_by_key:
            duplicates += 1
            current = candidates_by_key[key]
            if current["title_status"] == "UNKNOWN" and candidate["title_status"] == "EXTRACTED":
                current["title"] = candidate["title"]
                current["title_status"] = "EXTRACTED"
            if current["location_status"] == "UNKNOWN" and candidate["location_status"] == "EXTRACTED":
                current["location"] = candidate["location"]
                current["location_status"] = "EXTRACTED"
            continue
        candidates_by_key[key] = candidate

    candidates = sorted(candidates_by_key.values(), key=lambda item: (str(item["provider"]), str(item["ats_job_identity"]), str(item["discovered_url"])))
    if len(candidates) == 1 and candidates[0]["title_status"] == "UNKNOWN" and parser.headings:
        heading = _compact(parser.headings[0])
        if heading.casefold() not in GENERIC_LINK_TEXT:
            candidates[0]["title"] = heading
            candidates[0]["title_status"] = "EXTRACTED"

    report = {
        "schema_version": 1,
        "status": "LOCAL_SNAPSHOT_PARSED",
        "source_mode": "LOCAL_SNAPSHOT_ONLY",
        "source_format": source_format,
        "company_domain": registered,
        "official_entry_url": entry_url,
        "snapshot_hash": snapshot_hash,
        "candidate_count": len(candidates),
        "ignored_link_count": ignored,
        "deduplicated_link_count": duplicates,
        "candidates": candidates,
        "untrusted_page_content_executed": False,
        "network_actions": 0,
        "real_external_actions": 0,
        "knowledge_write_operations": 0,
    }
    # Force canonical serialization here so malformed/non-JSON values cannot escape this boundary.
    canonical_json(report)
    return report
