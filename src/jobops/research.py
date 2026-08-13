from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse
from pathlib import Path

from .errors import JobOpsError
from .sourcing import url_has_sensitive_query
from .util import parse_iso, sha256_bytes


MAX_RESEARCH_SNAPSHOT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ResearchSource:
    title: str
    url: str
    published_at: str
    accessed_at: str
    source_type: str
    supports: str

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class OfflineResearchSource:
    title: str
    url: str
    source_type: str
    snapshot_path: Path
    snapshot_hash: str
    published_at: str
    accessed_at: str
    evidence_excerpt: str
    evidence_fingerprint: str
    official: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title, "url": self.url, "source_type": self.source_type,
            "snapshot_name": self.snapshot_path.name, "snapshot_hash": self.snapshot_hash,
            "published_at": self.published_at, "accessed_at": self.accessed_at,
            "evidence_excerpt": self.evidence_excerpt, "evidence_fingerprint": self.evidence_fingerprint,
            "official": self.official,
        }


def validate_research_sources(sources: Iterable[ResearchSource], *, now: datetime | None = None, max_age_days: int = 730) -> list[ResearchSource]:
    current = now or datetime.now(timezone.utc)
    validated = []
    for source in sources:
        parsed = urlparse(source.url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise JobOpsError("RESEARCH_SOURCE_INVALID", "Research sources require an HTTPS URL.", url=source.url)
        if url_has_sensitive_query(source.url):
            raise JobOpsError("RESEARCH_SOURCE_SENSITIVE_QUERY", "Research source URLs cannot contain private query parameters.")
        if not source.title.strip() or not source.supports.strip():
            raise JobOpsError("RESEARCH_SOURCE_INCOMPLETE", "Research sources need a title and the exact claim they support.")
        age = (current.astimezone(timezone.utc) - parse_iso(source.accessed_at)).days
        if age < 0 or age > max_age_days:
            raise JobOpsError("RESEARCH_ACCESS_DATE_STALE", "The source access date is outside the allowed research window.", url=source.url, age_days=age)
        validated.append(source)
    if not validated:
        raise JobOpsError("RESEARCH_SOURCES_REQUIRED", "Company and environment research requires dated sources.")
    return validated


def build_research_packet(*, company: str, industry: str, findings: list[dict[str, str]], sources: list[ResearchSource]) -> dict[str, object]:
    validated = validate_research_sources(sources)
    supported = {source.supports for source in validated}
    unsupported = [finding for finding in findings if finding.get("claim") not in supported]
    if unsupported:
        raise JobOpsError("RESEARCH_FINDING_UNSUPPORTED", "Every research finding must have a dated source that supports the exact claim.", unsupported=unsupported)
    return {
        "company": company,
        "industry": industry,
        "findings": findings,
        "sources": [source.as_dict() for source in validated],
        "source_count": len(validated),
    }


def build_offline_research_packet(*, company: str, findings: list[dict[str, str]], sources: list[OfflineResearchSource], max_age_days: int = 730) -> dict[str, object]:
    current = datetime.now(timezone.utc)
    verified = []
    supported: set[str] = set()
    for source in sources:
        parsed = urlparse(source.url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise JobOpsError("RESEARCH_SOURCE_INVALID", "Offline research metadata requires an HTTPS source URL.")
        if url_has_sensitive_query(source.url):
            raise JobOpsError("RESEARCH_SOURCE_SENSITIVE_QUERY", "Research source URLs cannot contain private query parameters.")
        if not source.snapshot_path.is_file():
            raise JobOpsError("RESEARCH_SNAPSHOT_CHANGED", "Local research snapshot is missing or its SHA-256 changed.")
        with source.snapshot_path.open("rb") as handle:
            raw = handle.read(MAX_RESEARCH_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_RESEARCH_SNAPSHOT_BYTES:
            raise JobOpsError(
                "RESEARCH_SNAPSHOT_TOO_LARGE",
                "The local research snapshot exceeds the safe input limit.",
                maximum_bytes=MAX_RESEARCH_SNAPSHOT_BYTES,
            )
        if sha256_bytes(raw) != source.snapshot_hash:
            raise JobOpsError("RESEARCH_SNAPSHOT_CHANGED", "Local research snapshot is missing or its SHA-256 changed.")
        age = (current - parse_iso(source.accessed_at)).days
        if age < 0 or age > max_age_days:
            raise JobOpsError("RESEARCH_ACCESS_DATE_STALE", "Research snapshot access date is outside the allowed window.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise JobOpsError("RESEARCH_SNAPSHOT_ENCODING_INVALID", "The local research snapshot must be valid UTF-8 text.") from exc
        excerpt = source.evidence_excerpt.strip()
        if not excerpt or excerpt not in text:
            raise JobOpsError("RESEARCH_EVIDENCE_MISSING", "The claimed evidence excerpt does not exist in the local snapshot.")
        if sha256_bytes(excerpt.encode("utf-8")) != source.evidence_fingerprint:
            raise JobOpsError("RESEARCH_EVIDENCE_CHANGED", "Research evidence fingerprint is invalid.")
        supported.add(excerpt)
        verified.append(source.as_dict())
    unsupported = [finding for finding in findings if finding.get("claim") not in supported]
    if unsupported:
        raise JobOpsError("RESEARCH_FINDING_UNSUPPORTED", "Every finding must equal a verified local snapshot excerpt.", unsupported=unsupported)
    return {"company": company, "findings": findings, "sources": verified, "source_count": len(verified), "network_actions": 0}
