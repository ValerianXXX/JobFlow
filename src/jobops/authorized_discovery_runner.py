from __future__ import annotations

import ipaddress
import http.client
import socket
import ssl
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .authorized_discovery import AuthorizedDiscoveryControl
from .errors import JobOpsError
from .official_discovery import MAX_SNAPSHOT_BYTES, discover_official_jobs
from .sourcing import _canonical_url


FETCH_TIMEOUT_SECONDS = 20
USER_AGENT = "JobFlow-Authorized-Discovery/1.0"
DISCOVERY_PUBLIC_ATS_HOSTS = {
    "myworkdayjobs.com",
    "myworkday.com",
    "workday.com",
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "smartrecruiters.com",
    "jobs.smartrecruiters.com",
}


def _resolve_public_endpoints(host: str) -> tuple[tuple[int, int, int, tuple[Any, ...]], ...]:
    lowered = host.strip().casefold().rstrip(".")
    if not lowered or lowered == "localhost" or lowered.endswith((".localhost", ".local")):
        raise JobOpsError("DISCOVERY_NETWORK_HOST_BLOCKED", "The authorized discovery host is not public.")
    try:
        literal = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        raise JobOpsError("DISCOVERY_NETWORK_HOST_BLOCKED", "Literal IP addresses are not allowed for discovery.")
    try:
        resolved = socket.getaddrinfo(lowered, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise JobOpsError("DISCOVERY_NETWORK_DNS_FAILED", "The authorized discovery host could not be resolved.") from exc
    if not resolved:
        raise JobOpsError("DISCOVERY_NETWORK_DNS_FAILED", "The authorized discovery host did not resolve.")
    endpoints: list[tuple[int, int, int, tuple[Any, ...]]] = []
    seen: set[tuple[int, str]] = set()
    for family, socktype, proto, _canonname, sockaddr in resolved:
        address = str(sockaddr[0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise JobOpsError("DISCOVERY_NETWORK_DNS_FAILED", "The authorized discovery host returned an invalid address.") from exc
        if not parsed.is_global:
            raise JobOpsError("DISCOVERY_NETWORK_HOST_BLOCKED", "The authorized discovery host resolved to a non-public address.")
        key = (int(family), address)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append((int(family), int(socktype), int(proto), tuple(sockaddr)))
    if not endpoints:
        raise JobOpsError("DISCOVERY_NETWORK_DNS_FAILED", "The authorized discovery host did not resolve safely.")
    return tuple(endpoints)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to one already validated address.

    TLS certificate and SNI checks still use the authorized hostname, while the
    socket cannot trigger a second DNS lookup after the public-address gate.
    """

    def __init__(
        self,
        host: str,
        endpoint: tuple[int, int, int, tuple[Any, ...]],
        *,
        timeout: float,
    ) -> None:
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._endpoint = endpoint

    def connect(self) -> None:
        family, socktype, proto, sockaddr = self._endpoint
        raw_socket = socket.socket(family, socktype, proto)
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(sockaddr)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def fetch_authorized_source(source: dict[str, Any]) -> bytes:
    url = _canonical_url(str(source.get("feed_url") or ""))
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise JobOpsError("DISCOVERY_NETWORK_URL_INVALID", "The authorized discovery URL is invalid.")
    host = str(parsed.hostname or "")
    endpoints = _resolve_public_endpoints(host)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    headers = {
        "Accept": "application/json,text/html,application/xhtml+xml;q=0.9",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Connection": "close",
        "Host": host,
        "User-Agent": USER_AGENT,
    }
    last_error: Exception | None = None
    for endpoint in endpoints:
        connection = _PinnedHTTPSConnection(host, endpoint, timeout=FETCH_TIMEOUT_SECONDS)
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            status = int(response.status)
            if 300 <= status < 400:
                raise JobOpsError("DISCOVERY_NETWORK_REDIRECT_BLOCKED", "Authorized discovery does not follow redirects.")
            if status != 200:
                raise JobOpsError("DISCOVERY_NETWORK_RESPONSE_INVALID", "The authorized source returned a non-success response.")
            content_encoding = str(response.headers.get("Content-Encoding") or "").strip().casefold()
            if content_encoding not in {"", "identity"}:
                raise JobOpsError(
                    "DISCOVERY_NETWORK_CONTENT_ENCODING_INVALID",
                    "Authorized discovery accepts only bounded identity-encoded responses.",
                )
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except ValueError as exc:
                    raise JobOpsError("DISCOVERY_NETWORK_RESPONSE_INVALID", "The authorized source returned an invalid length.") from exc
                if declared_length <= 0 or declared_length > MAX_SNAPSHOT_BYTES:
                    raise JobOpsError("DISCOVERY_NETWORK_SIZE_INVALID", "The authorized source response size is outside the safe limit.")
            content_type = str(response.headers.get_content_type() or "").casefold()
            source_format = str(source.get("source_format") or "")
            if source_format == "html":
                allowed_type = content_type in {"text/html", "application/xhtml+xml"}
            else:
                allowed_type = content_type in {"application/json", "text/json"} or content_type.endswith("+json")
            if not allowed_type:
                raise JobOpsError("DISCOVERY_NETWORK_CONTENT_TYPE_INVALID", "The authorized source returned an unexpected content type.")
            payload = response.read(MAX_SNAPSHOT_BYTES + 1)
            break
        except JobOpsError:
            raise
        except (http.client.HTTPException, ssl.SSLError, TimeoutError, OSError) as exc:
            last_error = exc
        finally:
            connection.close()
    else:
        raise JobOpsError(
            "DISCOVERY_NETWORK_REQUEST_FAILED",
            "The authorized source request did not complete safely.",
        ) from last_error
    if not payload or len(payload) > MAX_SNAPSHOT_BYTES:
        raise JobOpsError("DISCOVERY_NETWORK_SIZE_INVALID", "The authorized source response size is outside the safe limit.")
    return payload


def _matches(candidate: dict[str, Any], config: dict[str, Any]) -> bool:
    title = str(candidate.get("title") or "").casefold()
    location = str(candidate.get("location") or "").casefold()
    if not title or title == "unknown":
        return False
    include_terms = [str(value).casefold() for value in config["include_terms"]]
    exclude_terms = [str(value).casefold() for value in config["exclude_terms"]]
    location_terms = [str(value).casefold() for value in config["location_terms"]]
    if not any(term in title for term in include_terms):
        return False
    if any(term in f"{title} {location}" for term in exclude_terms):
        return False
    if location_terms and (not location or location == "unknown" or not any(term in location for term in location_terms)):
        return False
    return True


def run_authorized_discovery(
    control: AuthorizedDiscoveryControl,
    *,
    approved_ats_hosts: list[str],
    fetcher: Callable[[dict[str, Any]], bytes] = fetch_authorized_source,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one due, generation-bound discovery cycle.

    The function performs only exact-source HTTPS GET requests and local writes to
    the discovery candidate inbox. It cannot create applications or call browser
    automation, upload, communication, account, or submission components.
    """
    lease = control.claim_due_run(now=now)
    source_count = int(lease["source_count"])
    network_requests = 0
    error_count = 0
    matched: dict[str, dict[str, Any]] = {}
    try:
        config = control.read_private_config(
            run_id=lease["run_id"], generation=lease["generation"], now=now,
        )
        if len(config["sources"]) != source_count:
            raise JobOpsError(
                "DISCOVERY_CONFIG_SOURCE_COUNT_MISMATCH",
                "The encrypted discovery sources no longer match the authorized control.",
            )
        safe_ats_hosts = sorted({*DISCOVERY_PUBLIC_ATS_HOSTS, *(str(value) for value in approved_ats_hosts)})
        for source in config["sources"]:
            try:
                control.assert_run_current(run_id=lease["run_id"], generation=lease["generation"], now=now)
                # Count the real network attempt before invoking the adapter so
                # DNS, TLS, timeout, and response failures cannot be reported as
                # zero external requests.
                network_requests += 1
                payload = fetcher(source)
                control.assert_run_current(run_id=lease["run_id"], generation=lease["generation"], now=now)
                report = discover_official_jobs(
                    payload,
                    official_entry_url=source["official_entry_url"],
                    company_domain=source["company_domain"],
                    approved_ats_hosts=safe_ats_hosts,
                    source_format=source["source_format"],
                )
                for candidate in report["candidates"]:
                    if not _matches(candidate, config):
                        continue
                    official_url = str(candidate["discovered_url"])
                    key = f"{source['source_id']}|{official_url}"
                    matched[key] = {
                        "source_id": source["source_id"],
                        "provider": candidate["provider"],
                        "company_domain": source["company_domain"],
                        "official_url": official_url,
                        "title": candidate["title"],
                        "location": candidate["location"],
                    }
            except (JobOpsError, OSError, ValueError, TypeError):
                error_count += 1
        aggregate = {
            "source_count": source_count,
            "network_requests": network_requests,
            "candidate_count": len(matched),
            "new_candidate_count": 0,
            "error_count": error_count,
        }
        state = control.commit_candidates(
            run_id=lease["run_id"],
            generation=lease["generation"],
            candidates=list(matched.values()),
            result=aggregate,
            now=now,
        )
        return {
            "schema_version": 1,
            "status": "AUTHORIZED_DISCOVERY_RUN_COMPLETED",
            "control": state,
            "inbox": control.list_candidates(status="NEW", limit=100),
            "network_requests": network_requests,
            "application_actions": 0,
            "browser_actions": 0,
            "material_uploads": 0,
            "final_submits": 0,
            "automatic_retry": False,
        }
    except Exception:
        try:
            control.record_run(
                run_id=lease["run_id"],
                generation=lease["generation"],
                result={
                    "source_count": source_count,
                    "network_requests": network_requests,
                    "candidate_count": 0,
                    "new_candidate_count": 0,
                    "error_count": max(1, source_count),
                },
                now=now,
            )
        except JobOpsError:
            pass
        raise
