from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .db import JobOpsDB
from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id, utc_now


LIVE_ACCEPTANCE_FRESHNESS_DAYS = 30

PROVIDERS: tuple[str, ...] = (
    "company",
    "greenhouse",
    "lever",
    "workday",
    "ashby",
    "smartrecruiters",
)

STAGES: tuple[str, ...] = (
    "OFFICIAL_DISCOVERY",
    "ROUTE_BINDING",
    "FORM_ANALYSIS",
    "PRIVATE_VALUE_FREE_PLAN",
    "REVIEW_PACKET",
    "APPROVED_DOM_PREFILL",
    "APPROVED_FILE_ATTACHMENT",
    "EXPLICIT_NONFINAL_NAVIGATION",
    "MULTI_PAGE_RESUME",
    "RESULT_OBSERVATION",
    "MODERN_COMPONENT_REBINDING",
)

_RESULTS = {"PASS", "FAIL", "BLOCKED"}
_BROWSER_ATTESTED_STAGES = frozenset(STAGES) - {"ROUTE_BINDING", "REVIEW_PACKET"}
# Only stages whose evidence is itself an observed browser page may advance the
# report's observation timestamp. Plans, writes, attachments, and navigation
# actions can carry a page binding without being a fresh page observation.
_PAGE_OBSERVATION_STAGES = frozenset({
    "FORM_ANALYSIS",
    "RESULT_OBSERVATION",
    "MODERN_COMPONENT_REBINDING",
})
_FINISH_STATUSES = {
    "PRE_SUBMIT_VERIFIED",
    "RESULT_OBSERVED",
    "FAILED",
    "BLOCKED",
    "EXPIRED",
    "REVOKED",
}
_TERMINAL_STATUSES = {"RESULT_OBSERVED", "FAILED", "BLOCKED", "EXPIRED", "REVOKED"}
_HASH_RE = re.compile(r"sha256:[a-f0-9]{64}")
_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NONPUBLIC_SUFFIXES = (
    ".example",
    ".invalid",
    ".localhost",
    ".local",
    ".internal",
    ".home",
    ".lan",
    ".test",
    ".onion",
)
_RESERVED_EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")


def _passed_page_sequence(events: list[dict[str, Any]], page_fingerprint: str) -> bool:
    """Return whether one page has an ordered FORM -> PLAN PASS sequence."""

    form_ids = [
        int(event["event_id"])
        for event in events
        if event["stage"] == "FORM_ANALYSIS"
        and event["result"] == "PASS"
        and event["page_fingerprint"] == page_fingerprint
    ]
    plan_ids = [
        int(event["event_id"])
        for event in events
        if event["stage"] == "PRIVATE_VALUE_FREE_PLAN"
        and event["result"] == "PASS"
        and event["page_fingerprint"] == page_fingerprint
    ]
    return any(form_id < plan_id for form_id in form_ids for plan_id in plan_ids)


def _latest_form_page(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if (
            event["stage"] == "FORM_ANALYSIS"
            and event["result"] == "PASS"
            and event["page_fingerprint"] is not None
        ):
            return str(event["page_fingerprint"])
    return None


def _require_hash(value: str, *, field: str) -> str:
    if _HASH_RE.fullmatch(str(value)) is None:
        raise JobOpsError(
            "LIVE_ACCEPTANCE_HASH_INVALID",
            "Live acceptance evidence accepts only SHA-256 references.",
            field=field,
        )
    return str(value)


def normalized_public_https_origin(value: str) -> str | None:
    """Return a normalized public HTTPS origin without performing a network lookup."""

    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        return None
    try:
        port = parsed.port
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return None
    if port not in (None, 443) or "." not in hostname or len(hostname) > 253:
        return None
    if (
        hostname == "localhost"
        or hostname.endswith(_NONPUBLIC_SUFFIXES)
        or any(
            hostname == reserved or hostname.endswith(f".{reserved}")
            for reserved in _RESERVED_EXAMPLE_DOMAINS
        )
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return None
    labels = hostname.split(".")
    if any(_HOST_LABEL_RE.fullmatch(label) is None for label in labels):
        return None
    return f"https://{hostname}"


def _report_hash(value: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json({key: item for key, item in value.items() if key != "report_hash"}))


class LiveAcceptanceManager:
    """Persist redacted, expiring evidence from user-present real-page runs.

    The manager never stores an origin, URL, page text, field value, or material
    path. A single successful run remains page/route-specific evidence and is
    never promoted to universal provider compatibility.
    """

    def __init__(self, database: JobOpsDB, *, freshness_days: int = LIVE_ACCEPTANCE_FRESHNESS_DAYS) -> None:
        if freshness_days < 1 or freshness_days > 90:
            raise ValueError("freshness_days must be between 1 and 90")
        self.database = database
        self.freshness_days = freshness_days

    def expire_stale(self, *, now: datetime | None = None) -> int:
        now_value = now or utc_now()
        now_text = iso_utc(now_value)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """UPDATE live_acceptance_runs
                   SET status='EXPIRED',updated_at=?
                   WHERE expires_at<=? AND status IN ('ACTIVE','PRE_SUBMIT_VERIFIED')""",
                (now_text, now_text),
            )
            return int(cursor.rowcount)

    def start_for_assist(self, assist_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        now_value = now or utc_now()
        self.expire_stale(now=now_value)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT b.assist_id,b.application_id,b.allowed_origin,b.provider,b.route_kind,
                          s.source_route_hash,s.mode
                   FROM browser_assist_runs b
                   JOIN external_action_sessions s ON s.session_id=b.session_id
                   WHERE b.assist_id=?""",
                (assist_id,),
            ).fetchone()
            if row is None:
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_ASSIST_NOT_FOUND",
                    "The browser-assist run is not available for acceptance evidence.",
                )
            existing = connection.execute(
                "SELECT * FROM live_acceptance_runs WHERE assist_id=?",
                (assist_id,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            if str(row["mode"]) != "ASSISTED_USER_PRESENT":
                return None
            origin = normalized_public_https_origin(str(row["allowed_origin"]))
            if origin is None:
                return None
            route_hash = _require_hash(str(row["source_route_hash"]), field="route_identity_hash")
            provider = str(row["provider"])
            if provider not in PROVIDERS:
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_PROVIDER_INVALID",
                    "The browser-assist provider is outside the acceptance support matrix.",
                )
            acceptance_id = stable_id("LVA", str(row["assist_id"]), route_hash)
            created_at = iso_utc(now_value)
            expires_at = iso_utc(now_value + timedelta(days=self.freshness_days))
            site_fingerprint = sha256_bytes(canonical_json({"origin": origin}))
            connection.execute(
                """INSERT INTO live_acceptance_runs(
                       acceptance_id,assist_id,application_id,provider,route_kind,site_fingerprint,
                       route_identity_hash,status,created_at,expires_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'ACTIVE',?,?,?)""",
                (
                    acceptance_id,
                    str(row["assist_id"]),
                    str(row["application_id"]),
                    provider,
                    str(row["route_kind"]),
                    site_fingerprint,
                    route_hash,
                    created_at,
                    expires_at,
                    created_at,
                ),
            )
            route_evidence_hash = sha256_bytes(canonical_json({
                "acceptance_id": acceptance_id,
                "assist_id": str(row["assist_id"]),
                "application_id": str(row["application_id"]),
                "provider": provider,
                "route_kind": str(row["route_kind"]),
                "site_fingerprint": site_fingerprint,
                "route_identity_hash": route_hash,
            }))
            connection.execute(
                """INSERT INTO live_acceptance_events(
                       acceptance_id,stage,result,evidence_hash,created_at
                   ) VALUES(?,'ROUTE_BINDING','PASS',?,?)""",
                (acceptance_id, route_evidence_hash, created_at),
            )
            return dict(connection.execute(
                "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
                (acceptance_id,),
            ).fetchone())

    def _active_row(self, acceptance_id: str, *, now: datetime) -> Any:
        self.expire_stale(now=now)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
                (acceptance_id,),
            ).fetchone()
        if row is None:
            raise JobOpsError("LIVE_ACCEPTANCE_NOT_FOUND", "The live acceptance run was not found.")
        if str(row["status"]) == "EXPIRED" or parse_iso(str(row["expires_at"])) <= now:
            raise JobOpsError("LIVE_ACCEPTANCE_EXPIRED", "The live acceptance evidence window has expired.")
        return row

    def record_stage(
        self,
        acceptance_id: str,
        *,
        stage: str,
        result: str,
        evidence_hash: str,
        page_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now_value = now or utc_now()
        row = self._active_row(acceptance_id, now=now_value)
        if str(row["status"]) in _TERMINAL_STATUSES:
            raise JobOpsError(
                "LIVE_ACCEPTANCE_TERMINAL",
                "No further evidence may be appended to a terminal live acceptance run.",
            )
        if stage not in STAGES or result not in _RESULTS:
            raise JobOpsError(
                "LIVE_ACCEPTANCE_STAGE_INVALID",
                "The live acceptance stage or result is not recognized.",
            )
        evidence = _require_hash(evidence_hash, field="evidence_hash")
        page = None if page_fingerprint is None else _require_hash(page_fingerprint, field="page_fingerprint")
        if stage == "FORM_ANALYSIS" and page is None:
            raise JobOpsError(
                "LIVE_ACCEPTANCE_PAGE_FINGERPRINT_REQUIRED",
                "A live form observation requires a redacted page fingerprint.",
            )
        prior_events: list[dict[str, Any]] = []
        if stage in _BROWSER_ATTESTED_STAGES:
            with self.database.connect() as connection:
                attested = connection.execute(
                    """SELECT e.created_at
                       FROM live_acceptance_runs r
                       JOIN browser_assist_events e ON e.assist_id=r.assist_id
                       WHERE r.acceptance_id=? AND e.event_type='COMPANION_PAIRED'
                       ORDER BY e.event_id LIMIT 1""",
                    (acceptance_id,),
                ).fetchone()
                prior_events = [dict(item) for item in connection.execute(
                    """SELECT event_id,stage,result,page_fingerprint,created_at
                       FROM live_acceptance_events
                       WHERE acceptance_id=? ORDER BY event_id""",
                    (acceptance_id,),
                ).fetchall()]
            if attested is None or parse_iso(str(attested["created_at"])) > now_value:
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_BROWSER_ATTESTATION_REQUIRED",
                    "Only evidence observed after Browser Companion pairing may enter live acceptance.",
                )
        if stage == "PRIVATE_VALUE_FREE_PLAN":
            if page is None or not any(
                event["stage"] == "FORM_ANALYSIS"
                and event["result"] == "PASS"
                and event["page_fingerprint"] == page
                for event in prior_events
            ):
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_STAGE_ORDER_INVALID",
                    "A private-value-free plan requires a prior passed form analysis for the same page.",
                )
        elif stage in {
            "APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "MULTI_PAGE_RESUME",
        }:
            if page is None or not _passed_page_sequence(prior_events, page):
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_STAGE_ORDER_INVALID",
                    "Page writes, files, and multi-page evidence require an ordered form analysis and plan.",
                )
        elif stage == "EXPLICIT_NONFINAL_NAVIGATION":
            latest_page = _latest_form_page(prior_events)
            if latest_page is None or not _passed_page_sequence(prior_events, latest_page):
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_STAGE_ORDER_INVALID",
                    "Non-final navigation requires an ordered form analysis and plan for the current page.",
                )
        elif stage == "RESULT_OBSERVATION" and str(row["status"]) != "PRE_SUBMIT_VERIFIED":
            raise JobOpsError(
                "LIVE_ACCEPTANCE_STAGE_ORDER_INVALID",
                "Result observation requires the pre-submit checkpoint.",
            )
        now_text = iso_utc(now_value)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO live_acceptance_events(
                       acceptance_id,stage,result,evidence_hash,page_fingerprint,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (acceptance_id, stage, result, evidence, page, now_text),
            )
            if result in {"FAIL", "BLOCKED"}:
                connection.execute(
                    "UPDATE live_acceptance_runs SET status=?,updated_at=? WHERE acceptance_id=?",
                    ("FAILED" if result == "FAIL" else "BLOCKED", now_text, acceptance_id),
                )
            else:
                connection.execute(
                    "UPDATE live_acceptance_runs SET updated_at=? WHERE acceptance_id=?",
                    (now_text, acceptance_id),
                )
            return dict(connection.execute(
                "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
                (acceptance_id,),
            ).fetchone())

    def finish(
        self,
        acceptance_id: str,
        *,
        status: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if status not in _FINISH_STATUSES:
            raise JobOpsError(
                "LIVE_ACCEPTANCE_STATUS_INVALID",
                "The requested live acceptance completion status is not allowed.",
            )
        now_value = now or utc_now()
        row = self._active_row(acceptance_id, now=now_value)
        current = str(row["status"])
        allowed = {
            "ACTIVE": {"PRE_SUBMIT_VERIFIED", "FAILED", "BLOCKED", "EXPIRED", "REVOKED"},
            "PRE_SUBMIT_VERIFIED": {
                "RESULT_OBSERVED", "FAILED", "BLOCKED", "EXPIRED", "REVOKED",
            },
        }
        if status == current:
            return dict(row)
        if status not in allowed.get(current, set()):
            raise JobOpsError(
                "LIVE_ACCEPTANCE_TRANSITION_INVALID",
                "The live acceptance status transition is not allowed.",
                current_status=current,
                requested_status=status,
            )
        with self.database.connect() as connection:
            events = [dict(item) for item in connection.execute(
                """SELECT event_id,stage,result,page_fingerprint,created_at
                   FROM live_acceptance_events
                   WHERE acceptance_id=? ORDER BY event_id""",
                (acceptance_id,),
            ).fetchall()]
            if status == "PRE_SUBMIT_VERIFIED":
                latest_page = _latest_form_page(events)
                if latest_page is None or not _passed_page_sequence(events, latest_page):
                    raise JobOpsError(
                        "LIVE_ACCEPTANCE_PRE_SUBMIT_EVIDENCE_REQUIRED",
                        "Pre-submit verification requires the final observed page's form analysis and plan.",
                    )
            if status == "RESULT_OBSERVED" and not any(
                event["stage"] == "RESULT_OBSERVATION" and event["result"] == "PASS"
                for event in events
            ):
                raise JobOpsError(
                    "LIVE_ACCEPTANCE_RESULT_EVIDENCE_REQUIRED",
                    "A passed result observation is required before marking the result observed.",
                )
            connection.execute(
                "UPDATE live_acceptance_runs SET status=?,updated_at=? WHERE acceptance_id=?",
                (status, iso_utc(now_value), acceptance_id),
            )
            return dict(connection.execute(
                "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
                (acceptance_id,),
            ).fetchone())

    def finish_for_assist_in_connection(
        self,
        connection,
        *,
        assist_id: str,
        status: str,
        now: str,
    ) -> dict[str, Any] | None:
        """Finish acceptance evidence inside an existing browser-assist transaction.

        This keeps service restart, lease expiry, emergency stop, and unknown
        submission handling atomic with the authoritative browser-assist state.
        A missing row is expected for offline and reserved-domain fixtures.
        """

        if status not in _FINISH_STATUSES:
            raise JobOpsError(
                "LIVE_ACCEPTANCE_STATUS_INVALID",
                "The requested live acceptance completion status is not allowed.",
            )
        row = connection.execute(
            "SELECT * FROM live_acceptance_runs WHERE assist_id=?",
            (assist_id,),
        ).fetchone()
        if row is None:
            return None
        current = str(row["status"])
        if current == status or current in _TERMINAL_STATUSES:
            return dict(row)
        allowed = {
            "ACTIVE": {"PRE_SUBMIT_VERIFIED", "FAILED", "BLOCKED", "EXPIRED", "REVOKED"},
            "PRE_SUBMIT_VERIFIED": {
                "RESULT_OBSERVED", "FAILED", "BLOCKED", "EXPIRED", "REVOKED",
            },
        }
        if status not in allowed.get(current, set()):
            raise JobOpsError(
                "LIVE_ACCEPTANCE_TRANSITION_INVALID",
                "The live acceptance status transition is not allowed.",
                current_status=current,
                requested_status=status,
            )
        connection.execute(
            "UPDATE live_acceptance_runs SET status=?,updated_at=? WHERE acceptance_id=?",
            (status, now, str(row["acceptance_id"])),
        )
        return dict(connection.execute(
            "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
            (str(row["acceptance_id"]),),
        ).fetchone())

    def report(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_value = now or utc_now()
        self.expire_stale(now=now_value)
        with self.database.connect() as connection:
            runs = [dict(row) for row in connection.execute(
                "SELECT * FROM live_acceptance_runs ORDER BY provider,created_at"
            ).fetchall()]
            events = [dict(row) for row in connection.execute(
                "SELECT * FROM live_acceptance_events ORDER BY event_id"
            ).fetchall()]
            companion_assist_ids = {
                str(row["assist_id"])
                for row in connection.execute(
                    "SELECT DISTINCT assist_id FROM browser_assist_events WHERE event_type='COMPANION_PAIRED'"
                ).fetchall()
            }
        events_by_run: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            events_by_run.setdefault(str(event["acceptance_id"]), []).append(event)
        runs_by_id = {str(run["acceptance_id"]): run for run in runs}
        observed_ids = {
            acceptance_id
            for acceptance_id, run_events in events_by_run.items()
            if str(runs_by_id[acceptance_id]["assist_id"]) in companion_assist_ids
            and any(
                event["stage"] == "FORM_ANALYSIS"
                and event["page_fingerprint"] is not None
                for event in run_events
            )
        }
        providers: list[dict[str, Any]] = []
        for provider in PROVIDERS:
            provider_runs = [run for run in runs if run["provider"] == provider]
            observed_runs = [
                run for run in provider_runs if str(run["acceptance_id"]) in observed_ids
            ]
            current_runs = [
                run for run in observed_runs
                if run["status"] != "EXPIRED" and parse_iso(str(run["expires_at"])) > now_value
            ]
            expired_runs = [
                run for run in observed_runs
                if run["status"] == "EXPIRED" or parse_iso(str(run["expires_at"])) <= now_value
            ]
            current_ids = {str(run["acceptance_id"]) for run in current_runs}
            passed_stages = sorted({
                str(event["stage"])
                for acceptance_id in current_ids
                for event in events_by_run.get(acceptance_id, [])
                if event["result"] == "PASS"
            }, key=STAGES.index)
            observed_at = [
                str(event["created_at"])
                for acceptance_id in current_ids
                for event in events_by_run.get(acceptance_id, [])
                if event["stage"] in _PAGE_OBSERVATION_STAGES
                and event["page_fingerprint"] is not None
            ]
            providers.append({
                "provider": provider,
                "evidence_scope": "PAGE_ROUTE_SPECIFIC_NOT_UNIVERSAL",
                "current_page_route_runs": len(current_runs),
                "expired_page_route_runs": len(expired_runs),
                "distinct_site_fingerprints": len({run["site_fingerprint"] for run in current_runs}),
                "pre_submit_verified_runs": sum(
                    run["status"] in {"PRE_SUBMIT_VERIFIED", "RESULT_OBSERVED"} for run in current_runs
                ),
                "result_observed_runs": sum(run["status"] == "RESULT_OBSERVED" for run in current_runs),
                "blocked_or_failed_runs": sum(run["status"] in {"BLOCKED", "FAILED"} for run in current_runs),
                "passed_stages": passed_stages,
                "latest_observed_at": max(observed_at) if observed_at else None,
                "universal_live_compatibility": False,
            })
        report: dict[str, Any] = {
            "schema_version": 1,
            "status": "LIVE_ACCEPTANCE_EVIDENCE",
            "generated_at": iso_utc(now_value),
            "freshness_days": self.freshness_days,
            "provider_count": len(providers),
            "providers": providers,
            "current_page_route_evidence_count": sum(
                item["current_page_route_runs"] for item in providers
            ),
            "live_site_accessed": any(item["current_page_route_runs"] for item in providers),
            "universal_live_compatibility": False,
            "final_submit": "USER_ONLY",
            "final_submit_actions": 0,
            "automatic_retries": 0,
            "private_values_persisted": 0,
            "page_text_persisted": 0,
        }
        report["report_hash"] = _report_hash(report)
        validate_live_acceptance_report(report)
        return report


def validate_live_acceptance_report(value: dict[str, Any]) -> None:
    validate_named("live-acceptance-report", value, project_root() / "schemas")
    if value["provider_count"] != len(value["providers"]):
        raise JobOpsError(
            "LIVE_ACCEPTANCE_PROVIDER_COUNT_INVALID",
            "The live acceptance provider count does not match the report.",
        )
    if [item["provider"] for item in value["providers"]] != list(PROVIDERS):
        raise JobOpsError(
            "LIVE_ACCEPTANCE_PROVIDER_SET_INVALID",
            "The live acceptance report must disclose every provider in stable order.",
        )
    if value["current_page_route_evidence_count"] != sum(
        item["current_page_route_runs"] for item in value["providers"]
    ):
        raise JobOpsError(
            "LIVE_ACCEPTANCE_COUNT_INVALID",
            "The live acceptance evidence count does not match the provider totals.",
        )
    if value["live_site_accessed"] is not bool(value["current_page_route_evidence_count"]):
        raise JobOpsError(
            "LIVE_ACCEPTANCE_ACCESS_FLAG_INVALID",
            "The live-site access flag must reflect current observed page evidence.",
        )
    if value["report_hash"] != _report_hash(value):
        raise JobOpsError(
            "LIVE_ACCEPTANCE_REPORT_INTEGRITY_FAILED",
            "The live acceptance report hash no longer matches its contents.",
        )
