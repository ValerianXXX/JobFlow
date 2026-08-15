from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from .approvals import ApprovalContext
from .ats_browser import analyze_local_ats_form
from .db import JobOpsDB
from .ephemeral_payload import EphemeralATSPayloadBroker
from .errors import JobOpsError
from .execution_bundle import ApplicationExecutionBundleManager
from .external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from .forms import MANUAL_NAVIGATION_MODE, PROGRAMMATIC_NAVIGATION_MODE, navigation_control_mode
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .sourcing import _canonical_url, _host, host_matches_registered, source_route_hash, url_has_sensitive_query
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id


COMPANION_PROTOCOL_VERSION = 2
COMPANION_EXTENSION_VERSION = "0.6.0"
COMPANION_EXTENSION_ID = "hhlliaaafegldkmcgmaoaelabipcaooj"
COMPANION_EXTENSION_ORIGIN = f"chrome-extension://{COMPANION_EXTENSION_ID}"
ASSIST_TTL_MINUTES = 30
MAX_LIVE_FORM_HTML_BYTES = 2 * 1024 * 1024
MAX_ACTIVE_ASSISTS = 1
MAX_ASSIST_STEPS = 20
# Give a person enough time to review and complete page-specific fields before
# clicking Next.  The challenge remains one-use, page/tab/document bound, and
# cannot outlive the enclosing 30-minute assist lease.
MANUAL_NAVIGATION_CHALLENGE_TTL_SECONDS = 15 * 60
ALLOWED_LIVE_BLOCKERS = frozenset({
    "FILE_UPLOAD_STOP", "NAVIGATION_ACTION_STOP", "FINAL_SUBMIT_STOP",
})
CLIENT_REF_PATTERN = re.compile(r"^DOM-[A-F0-9]{12}$")
DOCUMENT_INSTANCE_PATTERN = re.compile(r"^DOC-[A-F0-9]{32}$")
MANUAL_CHALLENGE_PATTERN = re.compile(r"^MNC-[A-F0-9]{32}$")
SAFE_SIGNAL_CODES = frozenset({
    "CAPTCHA", "MFA", "LOGIN", "ACCOUNT_CREATION", "CROSS_ORIGIN_IFRAME", "CROSS_ORIGIN_FORM",
})
SUCCESS_MARKERS = frozenset({
    "APPLICATION_SUBMITTED", "APPLICATION_RECEIVED", "THANK_YOU_FOR_APPLYING", "SUBMISSION_COMPLETE",
})
FAILURE_MARKERS = frozenset({
    "SUBMISSION_ERROR", "VALIDATION_ERROR", "UNABLE_TO_SUBMIT", "APPLICATION_NOT_SENT",
})
TERMINAL_RUN_STATES = frozenset({"SUBMISSION_UNKNOWN", "CONFIRMED", "FAILED", "EXPIRED", "REVOKED"})
HANDOFF_SIGNAL_CODES = frozenset({"CAPTCHA", "MFA", "LOGIN", "ACCOUNT_CREATION"})
HARD_SIGNAL_CODES = frozenset({"CROSS_ORIGIN_IFRAME", "CROSS_ORIGIN_FORM"})
FORWARD_NAVIGATION = re.compile(
    r"(?:^|\b)(?:next|continue|save\s*(?:and|&)\s*continue|review(?:\s+application)?)(?:\b|$)|下一步|继续|保存并继续",
    re.IGNORECASE,
)
BACKWARD_NAVIGATION = re.compile(r"(?:^|\b)(?:back|previous|cancel)(?:\b|$)|返回|上一步|取消", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_origin(url: str) -> str:
    parsed = urlparse(_canonical_url(url))
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{host.casefold()}{port}"


def _semantic_field(value: dict[str, Any]) -> tuple[Any, ...]:
    # The companion deliberately replaces raw DOM identifiers with ephemeral
    # references.  Approval matching therefore relies on visible semantics and
    # control order, never on a site-generated ID that may contain user data.
    return (
        str(value.get("control_type", "")),
        bool(value.get("required", False)),
        str(value.get("classification", "")),
        str(value.get("prompt_hash", "")),
        int(value.get("option_count", 0)),
        tuple(str(item) for item in value.get("display_options", [])),
    )


def _safe_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _source_identity_material(application_id: str, route: dict[str, Any]) -> list[str]:
    """Immutable application identity, deliberately separate from transient page routing."""

    return [
        application_id,
        str(route.get("company_domain", "")),
        str(route.get("official_entry_url", "")),
        str(route.get("current_url", "")),
        str(route.get("route_kind", "")),
        str(route.get("provider", "")),
        str(route.get("ats_tenant", "")),
        str(route.get("ats_board", "")),
        str(route.get("ats_job_identity", "")),
        str(route.get("official_page_hash", "")),
        str(route.get("jd_snapshot_hash", "")),
    ]


@dataclass
class _AssistLease:
    token: str
    assist_id: str
    application_id: str
    session_id: str
    source_route: dict[str, Any]
    source_identity_hash: str
    allowed_page_origin: str
    provider: str
    route_kind: str
    created_at: str
    expires_at: str
    status: str = "PAIRING"
    paired: bool = False
    expected_fields: list[dict[str, str]] = field(default_factory=list)
    expected_files: list[dict[str, str]] = field(default_factory=list)
    file_tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    submit_observed: bool = False
    prepared_hash: str | None = None
    current_step: int = 1
    max_steps: int = MAX_ASSIST_STEPS
    current_page_hash: str | None = None
    current_page_kind: str | None = None
    navigation_ref: str | None = None
    navigation_token: str | None = None
    navigation_mode: str | None = None
    navigation_control_type: str | None = None
    navigation_control_semantics_hash: str | None = None
    navigation_snapshot_hash: str | None = None
    navigation_tab_id: int | None = None
    navigation_document_id: str | None = None
    manual_challenge_id: str | None = None
    manual_challenge_nonce: str | None = None
    manual_challenge_issued_at: str | None = None
    manual_challenge_expires_at: str | None = None
    manual_challenge_hash: str | None = None
    manual_challenge_consumed: bool = False
    manual_field_count: int = 0
    handoff_kind: str | None = None
    visited_page_hashes: set[str] = field(default_factory=set)
    uploaded_purposes: set[str] = field(default_factory=set)
    profile_ref: str | None = None
    answer_bank_ref: str | None = None
    private_source_hashes: dict[str, str] = field(default_factory=dict)


class BrowserAssistManager:
    """User-present company/ATS assistance with scoped multi-page navigation and no submit capability."""

    def __init__(self, project, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.project = project
        self.database = database
        self.onboarding = onboarding
        self.schemas = project_root() / "schemas"
        self._lock = threading.RLock()
        self._leases: dict[str, _AssistLease] = {}
        self._session_manager = ExternalActionSessionManager(
            database, ExternalActionSessionPolicy.assisted_user_present_mode(),
        )
        self._bundle_manager = ApplicationExecutionBundleManager(database, onboarding)
        self._payload_broker = EphemeralATSPayloadBroker(onboarding)
        self._reconcile_startup()

    @staticmethod
    def extension_origin_allowed(origin: str | None) -> bool:
        return bool(origin and secrets.compare_digest(origin, COMPANION_EXTENSION_ORIGIN))

    def _reconcile_startup(self) -> None:
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_runs = connection.execute(
                """SELECT r.assist_id,r.application_id,r.status AS run_status,a.status AS application_status
                   FROM browser_assist_runs r
                   JOIN applications a ON a.application_id=r.application_id
                   WHERE r.status NOT IN ('SUBMISSION_UNKNOWN','CONFIRMED','FAILED','EXPIRED','REVOKED')"""
            ).fetchall()
            for row in active_runs:
                uncertain = (
                    str(row["run_status"]) == "AWAITING_USER_SUBMIT"
                    or str(row["application_status"]) == "SUBMITTED"
                )
                if uncertain:
                    self._mark_unknown_in_connection(
                        connection,
                        assist_id=str(row["assist_id"]),
                        application_id=str(row["application_id"]),
                        reason="SERVICE_RESTART_DURING_USER_SUBMIT_WINDOW",
                        now=now,
                    )
                else:
                    connection.execute(
                        "UPDATE browser_assist_runs SET status='REVOKED',updated_at=? WHERE assist_id=?",
                        (now, str(row["assist_id"])),
                    )
            control = connection.execute(
                "SELECT enabled,mode,generation FROM external_action_control WHERE singleton_id=1"
            ).fetchone()
            if control and bool(control["enabled"]) and str(control["mode"]) == "ASSISTED_USER_PRESENT":
                generation = int(control["generation"]) + 1
                connection.execute(
                    """UPDATE external_action_control
                       SET enabled=0,generation=?,mode='PRODUCTION_DISABLED',updated_at=?
                       WHERE singleton_id=1""",
                    (generation, now),
                )
                connection.execute(
                    "UPDATE external_action_sessions SET status='INVALIDATED' WHERE status='AUTHORIZED'",
                )
                connection.execute(
                    """INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at)
                       VALUES(NULL,'BROWSER_ASSIST_STARTUP_RECONCILED','ENABLED','DISABLED',?,?)""",
                    (json.dumps({"generation": generation}), now),
                )

    def _prune(self) -> None:
        current = _now()
        expired: list[tuple[_AssistLease, str]] = []
        for token, lease in list(self._leases.items()):
            if parse_iso(lease.expires_at) <= current:
                self._leases.pop(token, None)
                if lease.status not in TERMINAL_RUN_STATES:
                    previous_status = lease.status
                    lease.status = "EXPIRED"
                    expired.append((lease, previous_status))
        if expired:
            now = iso_utc(current)
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for lease, previous_status in expired:
                    app = connection.execute(
                        "SELECT status FROM applications WHERE application_id=?", (lease.application_id,),
                    ).fetchone()
                    uncertain = previous_status == "AWAITING_USER_SUBMIT" or (
                        app is not None and str(app["status"]) == "SUBMITTED"
                    )
                    if uncertain:
                        self._mark_unknown_in_connection(
                            connection,
                            assist_id=lease.assist_id,
                            application_id=lease.application_id,
                            reason="ASSIST_LEASE_EXPIRED_DURING_USER_SUBMIT_WINDOW",
                            now=now,
                        )
                        lease.status = "SUBMISSION_UNKNOWN"
                    else:
                        connection.execute(
                            "UPDATE browser_assist_runs SET status='EXPIRED',updated_at=? WHERE assist_id=?",
                            (now, lease.assist_id),
                        )
                    connection.execute(
                        "UPDATE external_action_sessions SET status='EXPIRED',revoked_at=? WHERE session_id=? AND status='AUTHORIZED'",
                        (now, lease.session_id),
                    )

    @staticmethod
    def _mark_unknown_in_connection(
        connection,
        *,
        assist_id: str,
        application_id: str,
        reason: str,
        now: str,
    ) -> str:
        """Fail closed after the user could have submitted, without retrying."""

        evidence_hash = _safe_hash({
            "assist_id": assist_id,
            "application_id": application_id,
            "reason": reason,
            "automatic_retry": False,
        })
        app = connection.execute(
            "SELECT status FROM applications WHERE application_id=?", (application_id,),
        ).fetchone()
        if app is None:
            raise JobOpsError("APPLICATION_NOT_FOUND", "The assisted application no longer exists.")
        previous = str(app["status"])
        if previous not in {"APPROVED", "SUBMITTED", "SUBMISSION_UNKNOWN"}:
            raise JobOpsError(
                "BROWSER_ASSIST_STATE_INVALID",
                "An interrupted user-submit window no longer matches the application state.",
                status=previous,
            )
        connection.execute(
            "UPDATE applications SET status='SUBMISSION_UNKNOWN',last_safe_state='APPROVED',updated_at=? WHERE application_id=?",
            (now, application_id),
        )
        connection.execute(
            "UPDATE approvals SET status='CONSUMED',consumed_at=COALESCE(consumed_at,?) WHERE application_id=? AND status='APPROVED'",
            (now, application_id),
        )
        connection.execute(
            "UPDATE browser_assist_runs SET status='SUBMISSION_UNKNOWN',updated_at=? WHERE assist_id=?",
            (now, assist_id),
        )
        connection.execute(
            "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (
                application_id, "SUBMISSION_EVIDENCE_UNKNOWN", previous, "SUBMISSION_UNKNOWN",
                json.dumps({"reason": reason, "evidence_hash": evidence_hash, "automatic_retry": False}), now,
            ),
        )
        connection.execute(
            "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
            (assist_id, application_id, "RESULT_UNKNOWN", evidence_hash, now),
        )
        return evidence_hash

    def _mark_unknown(self, lease: _AssistLease, *, reason: str) -> dict[str, Any]:
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._mark_unknown_in_connection(
                connection,
                assist_id=lease.assist_id,
                application_id=lease.application_id,
                reason=reason,
                now=now,
            )
        lease.status = "SUBMISSION_UNKNOWN"
        self._disable_after_observation()
        return {
            "status": "SUBMISSION_UNKNOWN",
            "application_id": lease.application_id,
            "automatic_retry": False,
            "user_confirmation_required": True,
            "question": "是否提交成功？",
            "submit_performed_by": "USER_OR_UNKNOWN_DURING_TRUSTED_SUBMIT_WINDOW",
        }

    def _revoke_for_companion_reload(self, token: str, lease: _AssistLease) -> str:
        """End a pre-submit lease after extension state loss, without repeating any page action."""

        previous_status = lease.status
        now = iso_utc()
        evidence_hash = _safe_hash({
            "assist_id": lease.assist_id,
            "prior_status": previous_status,
            "reason": "COMPANION_STATE_LOST",
            "automatic_retry": False,
        })
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE browser_assist_runs SET status='REVOKED',updated_at=? WHERE assist_id=?",
                (now, lease.assist_id),
            )
            connection.execute(
                "UPDATE external_action_sessions SET status='REVOKED',revoked_at=? WHERE session_id=? AND status='AUTHORIZED'",
                (now, lease.session_id),
            )
            connection.execute(
                "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                (lease.assist_id, lease.application_id, "COMPANION_RECOVERY_REVOKED", evidence_hash, now),
            )
        lease.status = "REVOKED"
        lease.navigation_token = None
        lease.navigation_ref = None
        self._leases.pop(token, None)
        return previous_status

    def _lease(self, token: str, *, statuses: set[str] | None = None) -> _AssistLease:
        if not isinstance(token, str) or len(token) < 40:
            raise JobOpsError("BROWSER_ASSIST_TOKEN_INVALID", "The browser-assist lease is invalid or expired.")
        self._prune()
        lease = self._leases.get(token)
        if lease is None:
            raise JobOpsError("BROWSER_ASSIST_TOKEN_INVALID", "The browser-assist lease is invalid or expired.")
        if statuses is not None and lease.status not in statuses:
            raise JobOpsError(
                "BROWSER_ASSIST_STATE_INVALID",
                "The browser-assist operation is not valid at the current safety checkpoint.",
                status=lease.status,
            )
        return lease

    @staticmethod
    def _provider_host_allowed(provider: str, host: str, company_domain: str) -> bool:
        value = _host(host)
        return {
            "company": host_matches_registered(value, company_domain),
            "workday": value in {"myworkdayjobs.com", "myworkday.com", "workday.com"} or value.endswith((".myworkdayjobs.com", ".myworkday.com", ".workday.com")),
            "greenhouse": value.endswith(".greenhouse.io"),
            "lever": value == "jobs.lever.co" or value.endswith(".lever.co"),
        }.get(provider, False)

    @staticmethod
    def _assert_source_identity(lease: _AssistLease, canonical: str) -> None:
        """Keep the approved job identity fixed while allowing bounded child pages."""

        if not secrets.compare_digest(
            lease.source_identity_hash,
            _safe_hash(_source_identity_material(lease.application_id, lease.source_route)),
        ):
            raise JobOpsError(
                "FORM_ROUTE_IDENTITY_CHANGED",
                "The approved company, job, or application identity changed during browser assistance.",
            )
        current_path = [unquote(part).casefold() for part in urlparse(canonical).path.split("/") if part]
        original = _canonical_url(str(lease.source_route["current_url"]))
        original_path = urlparse(original).path.rstrip("/") or "/"
        current_value = urlparse(canonical).path.rstrip("/") or "/"
        if current_value != original_path and not current_value.startswith(original_path + "/"):
            raise JobOpsError(
                "FORM_ROUTE_IDENTITY_CHANGED",
                "The same-origin page is outside the approved application path.",
            )
        identity = unquote(str(lease.source_route.get("ats_job_identity", ""))).casefold()
        if lease.provider != "company" and identity and identity != "unknown":
            if identity not in current_path:
                raise JobOpsError(
                    "FORM_ROUTE_IDENTITY_CHANGED",
                    "The same-origin page no longer belongs to the approved ATS job identity.",
                )
            return

    @staticmethod
    def _browser_context(payload: dict[str, Any]) -> tuple[int, str]:
        tab_id = payload.get("companion_tab_id")
        document_id = str(payload.get("document_instance_id", ""))
        if (
            not isinstance(tab_id, int)
            or isinstance(tab_id, bool)
            or tab_id < 0
            or not DOCUMENT_INSTANCE_PATTERN.fullmatch(document_id)
        ):
            raise JobOpsError(
                "BROWSER_DOCUMENT_CONTEXT_INVALID",
                "Manual navigation requires the bound companion tab and document instance.",
            )
        return tab_id, document_id

    @classmethod
    def _validate_assisted_route(cls, route: dict[str, Any], bundle: dict[str, Any]) -> None:
        validate_named("source-route", route, project_root() / "schemas")
        provider = str(route.get("provider", ""))
        route_kind = str(route.get("route_kind", ""))
        if (
            route.get("status") != "ROUTE_APPROVED"
            or route.get("account_action") != "NONE"
            or route.get("guest_mode") != "GUEST_SELECTED"
            or provider not in {"company", "greenhouse", "lever", "workday"}
            or bundle.get("provider") != provider
            or (
                (provider == "company" and route_kind != "OFFICIAL_DIRECT")
                or (provider != "company" and route_kind != "OFFICIAL_TO_APPROVED_ATS")
            )
        ):
            raise JobOpsError(
                "BROWSER_ASSIST_ROUTE_UNSUPPORTED",
                "Browser assistance requires an approved guest route from the company site to the bound company form or supported ATS.",
            )
        canonical = _canonical_url(str(route["current_url"]))
        parsed = urlparse(canonical)
        if url_has_sensitive_query(canonical) or not cls._provider_host_allowed(
            provider, parsed.hostname or "", str(route["company_domain"]),
        ):
            raise JobOpsError("BROWSER_ASSIST_ROUTE_UNSAFE", "The approved form route is unsafe or outside the bound company/ATS host.")
        if canonical != str(bundle.get("form_snapshot", {}).get("canonical_url", "")):
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The approved execution bundle and initial application form URL differ.")

    def _latest_active_ref(self, kind: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT secure_ref FROM private_refs WHERE kind=? AND status='ACTIVE' ORDER BY created_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return str(row["secure_ref"]) if row else None

    def _private_sources(self, bundle: dict[str, Any], context: ApprovalContext) -> tuple[str | None, str | None, dict[str, str]]:
        candidates = {
            str(item.get("binding_ref")) for item in bundle.get("browser_plan", {}).get("actions", [])
            if item.get("binding_kind") == "SECURE_REF"
        }
        profile_ref = next((
            reference for reference in sorted(candidates)
            if self.onboarding.reference_metadata(reference).get("kind") == "candidate_profile"
        ), None) or self._latest_active_ref("candidate_profile")
        answer_ref = self._latest_active_ref("answer_bank")
        hashes: dict[str, str] = {}
        if profile_ref:
            metadata = self.onboarding.reference_metadata(profile_ref)
            if metadata.get("status") != "ACTIVE" or metadata.get("kind") != "candidate_profile":
                raise JobOpsError("BROWSER_PRIVATE_SOURCE_INVALID", "The approved Candidate Profile is unavailable.")
            raw = bytearray(self.onboarding.read_bytes(profile_ref))
            try:
                profile = json.loads(bytes(raw).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise JobOpsError("BROWSER_PRIVATE_SOURCE_INVALID", "The encrypted Candidate Profile is invalid.") from exc
            finally:
                raw[:] = b"\0" * len(raw)
            if not isinstance(profile, dict) or str(profile.get("profile_version")) != context.profile_version:
                raise JobOpsError("BROWSER_PROFILE_VERSION_CHANGED", "The Candidate Profile changed after application approval.")
            hashes[profile_ref] = str(metadata["content_sha256"])
        if answer_ref:
            metadata = self.onboarding.reference_metadata(answer_ref)
            if metadata.get("status") != "ACTIVE" or metadata.get("kind") != "answer_bank":
                raise JobOpsError("BROWSER_PRIVATE_SOURCE_INVALID", "The approved Answer Bank is unavailable.")
            hashes[answer_ref] = str(metadata["content_sha256"])
        return profile_ref, answer_ref, hashes

    def _validate_private_sources(self, lease: _AssistLease) -> None:
        for reference, expected_hash in lease.private_source_hashes.items():
            metadata = self.onboarding.reference_metadata(reference)
            if metadata.get("status") != "ACTIVE" or metadata.get("content_sha256") != expected_hash:
                raise JobOpsError("BROWSER_PRIVATE_SOURCE_CHANGED", "Candidate Profile or Answer Bank changed during this application session.")

    def start(
        self,
        *,
        application_id: str,
        source_route: dict[str, Any],
        user_confirmed: bool,
    ) -> dict[str, Any]:
        with self._lock:
            if not user_confirmed:
                raise JobOpsError(
                    "EXPLICIT_CONFIRMATION_REQUIRED",
                    "Starting live prefill and upload requires an explicit per-application confirmation.",
                )
            self._prune()
            active = [
                (token, item) for token, item in self._leases.items()
                if item.status not in TERMINAL_RUN_STATES
            ]
            resumable = next((
                (token, item) for token, item in active
                if item.application_id == application_id and item.status in {"PAIRING", "READY"}
            ), None)
            if resumable is not None:
                token, lease = resumable
                return {
                    "status": "BROWSER_COMPANION_PAIRING",
                    "protocol_version": COMPANION_PROTOCOL_VERSION,
                    "assist_id": lease.assist_id,
                    "assist_token": token,
                    "assist_path": f"/assist/{token}",
                    "application_id": lease.application_id,
                    "allowed_page_origin": lease.allowed_page_origin,
                    "provider": lease.provider,
                    "route_kind": lease.route_kind,
                    "multi_page": True,
                    "max_steps": lease.max_steps,
                    "approved_url": str(lease.source_route["current_url"]),
                    "expires_at": lease.expires_at,
                    "extension_id": COMPANION_EXTENSION_ID,
                    "submit_capability": False,
                    "automatic_retry": False,
                    "resumed": True,
                    "real_external_actions": 0,
                }
            if len(active) >= MAX_ACTIVE_ASSISTS:
                raise JobOpsError(
                    "BROWSER_ASSIST_ALREADY_ACTIVE",
                    "Finish or stop the current browser-assist session before starting another application.",
                )
            bundle, context, _ = self._bundle_manager.load_current(application_id)
            self._validate_assisted_route(source_route, bundle)
            if context.application_id != application_id:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "The approved application context is inconsistent.")
            self._session_manager.enable(user_confirmed=True)
            session = self._session_manager.issue(
                context=context,
                allowed_actions={
                    "inspect_application_form", "prefill_application_form", "upload_materials",
                    "navigate_application_step",
                },
                user_confirmed=True,
                ttl_minutes=ASSIST_TTL_MINUTES,
            )
            self._session_manager.persist(session, context=context)
            current = _now()
            token = secrets.token_urlsafe(48)
            assist_id = stable_id("BAS", session.session_id, token, iso_utc(current))
            profile_ref, answer_bank_ref, private_hashes = self._private_sources(bundle, context)
            lease = _AssistLease(
                token=token,
                assist_id=assist_id,
                application_id=application_id,
                session_id=session.session_id,
                source_route=dict(source_route),
                source_identity_hash=_safe_hash(_source_identity_material(application_id, source_route)),
                allowed_page_origin=_safe_origin(str(source_route["current_url"])),
                provider=str(source_route["provider"]),
                route_kind=str(source_route["route_kind"]),
                created_at=iso_utc(current),
                expires_at=iso_utc(current + timedelta(minutes=ASSIST_TTL_MINUTES)),
                profile_ref=profile_ref,
                answer_bank_ref=answer_bank_ref,
                private_source_hashes=private_hashes,
            )
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO browser_assist_runs(
                    assist_id,application_id,session_id,allowed_origin,provider,route_kind,current_step,max_steps,
                    handoff_kind,last_page_hash,status,prepared_hash,created_at,expires_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?,NULL,?,?,?)""",
                    (
                        assist_id, application_id, session.session_id, lease.allowed_page_origin,
                        lease.provider, lease.route_kind, 1, lease.max_steps,
                        "PAIRING", lease.created_at, lease.expires_at, lease.created_at,
                    ),
                )
                connection.execute(
                    """INSERT INTO browser_assist_events(
                    assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)""",
                    (assist_id, application_id, "ASSIST_STARTED", _safe_hash({
                        "session_id": session.session_id,
                        "context_hash": context.context_hash,
                        "origin": lease.allowed_page_origin,
                    }), lease.created_at),
                )
            self._leases[token] = lease
            return {
                "status": "BROWSER_COMPANION_PAIRING",
                "protocol_version": COMPANION_PROTOCOL_VERSION,
                "assist_id": assist_id,
                "assist_token": token,
                "assist_path": f"/assist/{token}",
                "application_id": application_id,
                "allowed_page_origin": lease.allowed_page_origin,
                "provider": lease.provider,
                "route_kind": lease.route_kind,
                "multi_page": True,
                "max_steps": lease.max_steps,
                "approved_url": str(source_route["current_url"]),
                "expires_at": lease.expires_at,
                "extension_id": COMPANION_EXTENSION_ID,
                "submit_capability": False,
                "automatic_retry": False,
                "real_external_actions": 0,
            }

    def pair(self, token: str, *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token)
            if lease.status == "AWAITING_USER_SUBMIT":
                self._mark_unknown(lease, reason="COMPANION_RELOADED_DURING_USER_SUBMIT_WINDOW")
                self._leases.pop(token, None)
                raise JobOpsError(
                    "BROWSER_ASSIST_SUBMISSION_UNKNOWN",
                    "The companion reloaded during the user-submit window; confirm whether submission succeeded.",
                    application_id=lease.application_id,
                    automatic_retry=False,
                )
            if lease.status not in {"PAIRING", "READY"}:
                previous_status = self._revoke_for_companion_reload(token, lease)
                raise JobOpsError(
                    "BROWSER_ASSIST_RESTART_REQUIRED",
                    "The companion lost its page checkpoint. Reopen the approved start page and begin a new assist.",
                    application_id=lease.application_id,
                    prior_status=previous_status,
                    automatic_retry=False,
                )
            if not lease.paired:
                lease.paired = True
                lease.status = "READY"
                now = iso_utc()
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE browser_assist_runs SET status='READY',updated_at=? WHERE assist_id=?",
                        (now, lease.assist_id),
                    )
                    connection.execute(
                        "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                        (lease.assist_id, lease.application_id, "COMPANION_PAIRED", _safe_hash({
                            "protocol_version": COMPANION_PROTOCOL_VERSION,
                            "extension_id": COMPANION_EXTENSION_ID,
                        }), now),
                    )
            return {
                "status": "BROWSER_COMPANION_PAIRED",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "allowed_page_origin": lease.allowed_page_origin,
                "provider": lease.provider,
                "route_kind": lease.route_kind,
                "current_step": lease.current_step,
                "max_steps": lease.max_steps,
                "expires_at": lease.expires_at,
                "submit_capability": False,
                "automatic_retry": False,
            }

    def _rotate_step_session(self, lease: _AssistLease, context: ApprovalContext) -> None:
        previous_session_id = lease.session_id
        session = self._session_manager.issue(
            context=context,
            allowed_actions={
                "inspect_application_form", "prefill_application_form", "upload_materials",
                "navigate_application_step",
            },
            user_confirmed=True,
            ttl_minutes=ASSIST_TTL_MINUTES,
        )
        self._session_manager.persist(session, context=context)
        self._session_manager.revoke(previous_session_id)
        lease.session_id = session.session_id
        lease.status = "READY"
        lease.handoff_kind = None
        lease.prepared_hash = None
        lease.expected_fields.clear()
        lease.expected_files.clear()
        lease.file_tokens.clear()
        lease.navigation_ref = None
        lease.navigation_token = None
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE browser_assist_runs
                   SET session_id=?,status='READY',handoff_kind=NULL,prepared_hash=NULL,updated_at=?
                   WHERE assist_id=?""",
                (session.session_id, iso_utc(), lease.assist_id),
            )

    @staticmethod
    def _security_signals(payload: dict[str, Any]) -> set[str]:
        signals = payload.get("blocker_signals", [])
        if (
            not isinstance(signals, list)
            or any(not isinstance(item, str) or item not in SAFE_SIGNAL_CODES for item in signals)
        ):
            raise JobOpsError("BROWSER_SECURITY_SIGNAL_INVALID", "The browser companion returned an invalid safety signal.")
        return set(signals)

    def _enter_handoff(
        self,
        lease: _AssistLease,
        context: ApprovalContext,
        *,
        signals: set[str],
        page_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        handoff = "ACCOUNT_OR_LOGIN" if "ACCOUNT_CREATION" in signals else sorted(signals)[0]
        request_hash = _safe_hash({
            "step": lease.current_step,
            "signals": sorted(signals),
            "origin": lease.allowed_page_origin,
            "page_evidence": page_evidence,
        })
        self._session_manager.record_assisted_use(
            session_id=lease.session_id,
            context=context,
            action="inspect_application_form",
            request_hash=request_hash,
            result_code="USER_HANDOFF_REQUIRED",
            real_side_effect=False,
        )
        lease.status = "HANDOFF_REQUIRED"
        lease.handoff_kind = handoff
        now = iso_utc()
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE browser_assist_runs
                   SET status='HANDOFF_REQUIRED',handoff_kind=?,updated_at=? WHERE assist_id=?""",
                (handoff, now, lease.assist_id),
            )
            connection.execute(
                "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                (lease.assist_id, lease.application_id, "USER_HANDOFF_REQUIRED", request_hash, now),
            )
        return {
            "status": "HANDOFF_REQUIRED",
            "assist_id": lease.assist_id,
            "application_id": lease.application_id,
            "handoff_kind": handoff,
            "current_step": lease.current_step,
            "user_must_complete_manually": True,
            "existing_account_only": True,
            "account_creation_capability": False,
            "captcha_bypass_capability": False,
            "resume_after_return": True,
            "submit_capability": False,
            "automatic_retry": False,
        }

    def _live_snapshot(
        self,
        *,
        lease: _AssistLease,
        payload: dict[str, Any],
        expected_snapshot: dict[str, Any],
        enforce_initial_approval: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        live_url = str(payload.get("url", ""))
        canonical = _canonical_url(live_url)
        if _safe_origin(canonical) != lease.allowed_page_origin or not self._provider_host_allowed(
            lease.provider, urlparse(canonical).hostname or "", str(lease.source_route["company_domain"]),
        ):
            raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The current tab is outside the approved company/ATS origin.")
        if (
            enforce_initial_approval
            and lease.current_step == 1
            and not lease.visited_page_hashes
            and canonical != str(lease.source_route["current_url"])
        ):
            raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The first live page is not the exact approved application URL.")
        self._assert_source_identity(lease, canonical)
        if url_has_sensitive_query(canonical):
            raise JobOpsError("ATS_ROUTE_SENSITIVE_QUERY", "The live form URL contains a sensitive query field.")
        html = payload.get("sanitized_html")
        if not isinstance(html, str):
            raise JobOpsError("ATS_FORM_SNAPSHOT_INVALID", "The browser companion did not provide a sanitized form snapshot.")
        encoded = html.encode("utf-8")
        if not encoded or len(encoded) > MAX_LIVE_FORM_HTML_BYTES:
            raise JobOpsError("ATS_FORM_SNAPSHOT_SIZE_INVALID", "The sanitized live form is empty or too large.")
        client_refs = payload.get("client_refs")
        if (
            not isinstance(client_refs, list)
            or len(client_refs) > 500
            or any(not isinstance(item, str) or not CLIENT_REF_PATTERN.fullmatch(item) for item in client_refs)
            or len(set(client_refs)) != len(client_refs)
        ):
            raise JobOpsError("BROWSER_CONTROL_REFERENCE_INVALID", "The live form control references are invalid.")
        policy = json.loads((self.project / "config" / "policy.json").read_text(encoding="utf-8"))
        history = list(lease.source_route.get("navigation_history", []))
        if not history or history[-1] != canonical:
            history.append(canonical)
        runtime_route = {**lease.source_route, "current_url": canonical, "navigation_history": history}
        # The route hash is transient page evidence.  The immutable company/job
        # identity remains bound separately by source_identity_hash above.
        runtime_route["route_hash"] = source_route_hash(runtime_route)
        validate_named("source-route", runtime_route, self.schemas)
        live = analyze_local_ats_form(
            encoded,
            route=runtime_route,
            blocked_categories=list(policy["blocked_form_categories"]),
        )
        if str(live["page_content_hash"]) in lease.visited_page_hashes:
            raise JobOpsError(
                "NAVIGATION_DID_NOT_ADVANCE",
                "Next/Continue returned to a page that this application session already completed.",
            )
        if len(client_refs) != len(live["fields"]):
            raise JobOpsError("SITE_CHANGED", "The live form control count changed after approval.")
        if set(live["blockers"]) - ALLOWED_LIVE_BLOCKERS - {
            "CAPTCHA_STOP", "MFA_STOP", "LOGIN_STOP", "ACCOUNT_CREATION_STOP",
        }:
            raise JobOpsError(
                "BROWSER_SECURITY_STOP",
                "The live form contains a step JobFlow is not permitted to automate.",
                blockers=sorted(set(live["blockers"]) - ALLOWED_LIVE_BLOCKERS),
            )
        if enforce_initial_approval and lease.current_step == 1 and not lease.visited_page_hashes:
            expected_fields = list(expected_snapshot["fields"])
            if len(expected_fields) != len(live["fields"]):
                raise JobOpsError("SITE_CHANGED", "The initial live form field count differs from the approved review packet.")
            for expected, current in zip(expected_fields, live["fields"], strict=True):
                if _semantic_field(expected) != _semantic_field(current):
                    raise JobOpsError("SITE_CHANGED", "An initial live form field differs from the approved review packet.")
        return live, list(client_refs)

    def prepare(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"READY", "HANDOFF_REQUIRED"})
            if not lease.paired:
                raise JobOpsError("BROWSER_COMPANION_NOT_PAIRED", "Pair the bundled browser companion before opening the application form.")
            bundle, context, answer_refs = self._bundle_manager.load_current(lease.application_id)
            if lease.status == "HANDOFF_REQUIRED":
                self._rotate_step_session(lease, context)
            self._validate_private_sources(lease)
            self._session_manager.validate_scope(
                session_id=lease.session_id,
                context=context,
                required_actions={"inspect_application_form"},
            )
            canonical = _canonical_url(str(payload.get("url", "")))
            if (
                _safe_origin(canonical) != lease.allowed_page_origin
                or not self._provider_host_allowed(
                    lease.provider, urlparse(canonical).hostname or "", str(lease.source_route["company_domain"]),
                )
            ):
                raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The current tab is outside the approved company/ATS origin.")
            if url_has_sensitive_query(canonical):
                raise JobOpsError("ATS_ROUTE_SENSITIVE_QUERY", "The live form URL contains a sensitive query field.")
            signals = self._security_signals(payload)
            if signals & HARD_SIGNAL_CODES:
                raise JobOpsError(
                    "BROWSER_SECURITY_STOP",
                    "Cross-origin form or iframe content is outside the approved application boundary.",
                    blockers=sorted(signals & HARD_SIGNAL_CODES),
                )
            if signals & HANDOFF_SIGNAL_CODES:
                return self._enter_handoff(
                    lease, context, signals=signals & HANDOFF_SIGNAL_CODES,
                    page_evidence={"sanitized_html_present": isinstance(payload.get("sanitized_html"), str)},
                )
            live, client_refs = self._live_snapshot(
                lease=lease, payload=payload, expected_snapshot=bundle["form_snapshot"],
            )
            parser_handoffs = {
                code.removesuffix("_STOP") for code in live["blockers"]
                if code in {"CAPTCHA_STOP", "MFA_STOP", "LOGIN_STOP", "ACCOUNT_CREATION_STOP"}
            }
            if parser_handoffs:
                return self._enter_handoff(
                    lease, context, signals=parser_handoffs,
                    page_evidence={"page_content_hash": live["page_content_hash"]},
                )

            initial_page = lease.current_step == 1 and not lease.visited_page_hashes
            live_fields = list(live["fields"])
            client_by_control = {
                str(field["control_ref"]): client_ref
                for field, client_ref in zip(live_fields, client_refs, strict=True)
            }
            fields_by_ref = {str(item["control_ref"]): item for item in live_fields}
            if initial_page:
                value_payload = self._payload_broker.materialize_assisted_payload(
                    context=context,
                    form_snapshot=bundle["form_snapshot"],
                    browser_plan=bundle["browser_plan"],
                    public_values={str(item["control_ref"]): str(item["value"]) for item in bundle["public_values"]},
                    material_references={
                        str(item["sha256"]): str(item["secure_ref"])
                        for item in bundle["material_references"]
                    },
                    application_answer_bundle_references=answer_refs,
                )
                resolved_values = [
                    {
                        **item,
                        "control_type": str(fields_by_ref[str(item["control_ref"])]["control_type"]),
                        "value_sha256": sha256_bytes(str(item["value"]).encode("utf-8")),
                    }
                    for item in value_payload["fields"]
                ]
            else:
                resolved_values = self._payload_broker.materialize_reusable_page_fields(
                    fields=live_fields,
                    profile_reference=lease.profile_ref,
                    answer_bank_reference=lease.answer_bank_ref,
                )
            outward_fields: list[dict[str, str]] = []
            expected_field_results: list[dict[str, str]] = []
            resolved_control_refs: set[str] = set()
            for item in resolved_values:
                control_ref = str(item["control_ref"])
                field = fields_by_ref.get(control_ref)
                if field is None:
                    raise JobOpsError("EPHEMERAL_FORM_BINDING_CHANGED", "A resolved value no longer matches the approved form.")
                client_ref = client_by_control[control_ref]
                value = str(item["value"])
                value_hash = str(item.get("value_sha256") or sha256_bytes(value.encode("utf-8")))
                outward_fields.append({
                    "client_ref": client_ref,
                    "control_type": str(field["control_type"]),
                    "value": value,
                    "value_sha256": value_hash,
                })
                expected_field_results.append({"client_ref": client_ref, "value_sha256": value_hash})
                resolved_control_refs.add(control_ref)

            file_controls: dict[str, list[str]] = {}
            purpose_keys = {"resume": "resume", "cover_letter": "cover_letter", "portfolio": "portfolio_file"}
            for expected, client_ref in zip(live_fields, client_refs, strict=True):
                if expected.get("classification") == "file_upload_stop":
                    file_controls.setdefault(str(expected.get("answer_key")), []).append(client_ref)
            outward_files: list[dict[str, str]] = []
            expected_file_results: list[dict[str, str]] = []
            file_tokens: dict[str, dict[str, Any]] = {}
            available_files = [
                {
                    "purpose": str(item["purpose"]), "filename": str(item["filename"]),
                    "sha256": str(item["sha256"]), "secure_ref": str(item["secure_ref"]),
                }
                for item in bundle["material_references"]
                if str(item["purpose"]) not in lease.uploaded_purposes
            ]
            for item in available_files:
                purpose = str(item["purpose"])
                candidates = file_controls.get(purpose_keys[purpose], [])
                if len(candidates) > 1:
                    raise JobOpsError(
                        "APPROVED_UPLOAD_CONTROL_MISSING",
                        "The live form does not contain one unambiguous upload control for every approved material.",
                        purpose=purpose,
                    )
                if not candidates:
                    continue
                client_ref = candidates[0]
                file_token = secrets.token_urlsafe(36)
                file_tokens[file_token] = {**item, "client_ref": client_ref, "used": False}
                outward_files.append({
                    "client_ref": client_ref,
                    "purpose": purpose,
                    "filename": str(item["filename"]),
                    "sha256": str(item["sha256"]),
                    "download_path": f"/assist/{token}/file/{file_token}",
                })
                expected_file_results.append({
                    "client_ref": client_ref, "purpose": purpose, "sha256": str(item["sha256"]),
                })

            navigation_fields = [item for item in live_fields if item["classification"] == "navigation_control_stop"]
            forward_fields = [
                item for item in navigation_fields
                if FORWARD_NAVIGATION.search(str(item["display_label"]))
                and not BACKWARD_NAVIGATION.search(str(item["display_label"]))
            ]
            final_fields = [item for item in live_fields if item["classification"] == "final_submit_stop"]
            if final_fields:
                page_kind = "FINAL_REVIEW"
                navigation_field = None
            elif len(forward_fields) == 1:
                page_kind = "INTERMEDIATE"
                navigation_field = forward_fields[0]
            elif navigation_fields:
                raise JobOpsError(
                    "NAVIGATION_CONTROL_AMBIGUOUS",
                    "The current page has no single unambiguous forward Next/Continue control.",
                )
            else:
                raise JobOpsError(
                    "APPLICATION_PAGE_ACTION_MISSING",
                    "The current page has neither an approved forward control nor a final Submit control.",
                )
            manual_fields = [
                {
                    "client_ref": client_by_control[str(field["control_ref"])],
                    "display_label": str(field["display_label"]),
                    "classification": str(field["classification"]),
                    "required": bool(field["required"]),
                }
                for field in live_fields
                if field["classification"] not in {
                    "file_upload_stop", "navigation_control_stop", "final_submit_stop",
                }
                and str(field["control_ref"]) not in resolved_control_refs
            ]

            lease.expected_fields = sorted(expected_field_results, key=lambda item: item["client_ref"])
            lease.expected_files = sorted(expected_file_results, key=lambda item: (item["purpose"], item["client_ref"]))
            lease.file_tokens = file_tokens
            lease.current_page_hash = str(live["page_content_hash"])
            lease.current_page_kind = page_kind
            lease.navigation_ref = (
                client_by_control[str(navigation_field["control_ref"])] if navigation_field else None
            )
            lease.navigation_mode = navigation_control_mode(navigation_field) if navigation_field else None
            lease.navigation_control_type = str(navigation_field["control_type"]) if navigation_field else None
            lease.navigation_snapshot_hash = str(live["page_content_hash"]) if navigation_field else None
            lease.navigation_control_semantics_hash = (
                sha256_bytes(canonical_json([
                    lease.navigation_snapshot_hash,
                    lease.navigation_ref,
                    lease.navigation_control_type,
                    str(navigation_field["display_label"]),
                ]))
                if navigation_field else None
            )
            if navigation_field and lease.navigation_mode == MANUAL_NAVIGATION_MODE:
                lease.navigation_tab_id, lease.navigation_document_id = self._browser_context(payload)
            else:
                lease.navigation_tab_id = None
                lease.navigation_document_id = None
            lease.manual_challenge_id = None
            lease.manual_challenge_nonce = None
            lease.manual_challenge_issued_at = None
            lease.manual_challenge_expires_at = None
            lease.manual_challenge_hash = None
            lease.manual_challenge_consumed = False
            lease.navigation_token = (
                secrets.token_urlsafe(36)
                if navigation_field and lease.navigation_mode == PROGRAMMATIC_NAVIGATION_MODE
                else None
            )
            lease.manual_field_count = len(manual_fields)
            lease.prepared_hash = _safe_hash({
                "live_semantics": [_semantic_field(item) for item in live["fields"]],
                "field_bindings": lease.expected_fields,
                "material_bindings": lease.expected_files,
                "page_kind": page_kind,
                "step": lease.current_step,
                "navigation_ref": lease.navigation_ref,
                "navigation_mode": lease.navigation_mode,
                "navigation_control_type": lease.navigation_control_type,
                "navigation_control_semantics_hash": lease.navigation_control_semantics_hash,
                "navigation_snapshot_hash": lease.navigation_snapshot_hash,
                "manual_fields": [
                    {key: item[key] for key in ("client_ref", "classification", "required")}
                    for item in manual_fields
                ],
            })
            self._session_manager.record_assisted_use(
                session_id=lease.session_id,
                context=context,
                action="inspect_application_form",
                request_hash=_safe_hash({
                    "sanitized_html_sha256": sha256_bytes(str(payload["sanitized_html"]).encode("utf-8")),
                    "client_refs": client_refs,
                    "url_origin": lease.allowed_page_origin,
                    "step": lease.current_step,
                }),
                result_code="LIVE_PAGE_SAFELY_ANALYZED",
                real_side_effect=False,
            )
            lease.status = "PAGE_PREPARED"
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE browser_assist_runs
                       SET status='PAGE_PREPARED',prepared_hash=?,current_step=?,last_page_hash=?,handoff_kind=NULL,updated_at=?
                       WHERE assist_id=?""",
                    (lease.prepared_hash, lease.current_step, lease.current_page_hash, now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "LIVE_PAGE_PREPARED", lease.prepared_hash, now),
                )
            return {
                "status": "LIVE_PAGE_APPROVED_FOR_ASSIST",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "fields": outward_fields,
                "files": outward_files,
                "field_count": len(outward_fields),
                "file_count": len(outward_files),
                "manual_fields": manual_fields,
                "manual_field_count": len(manual_fields),
                "page_kind": page_kind,
                "current_step": lease.current_step,
                "max_steps": lease.max_steps,
                "navigation": (
                    {
                        "client_ref": lease.navigation_ref,
                        "authorization_token": lease.navigation_token,
                        "display_label": str(navigation_field["display_label"]),
                        "mode": lease.navigation_mode,
                        "control_type": lease.navigation_control_type,
                        "page_content_hash": lease.navigation_snapshot_hash,
                        "control_semantics_hash": lease.navigation_control_semantics_hash,
                        "programmatic_allowed": lease.navigation_mode == PROGRAMMATIC_NAVIGATION_MODE,
                        "user_must_click": lease.navigation_mode == MANUAL_NAVIGATION_MODE,
                    }
                    if navigation_field else None
                ),
                "final_submit_client_refs": [
                    client_by_control[str(item["control_ref"])] for item in final_fields
                ],
                "stop_before_submit": True,
                "submit_capability": False,
                "automatic_retry": False,
            }

    def take_file(
        self,
        token: str,
        file_token: str,
        *,
        extension_origin: str | None,
    ) -> tuple[bytearray, dict[str, str]]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"PAGE_PREPARED"})
            item = lease.file_tokens.get(file_token)
            if item is None or item.get("used") is True:
                raise JobOpsError("BROWSER_FILE_TOKEN_INVALID", "The one-use material token is invalid or already consumed.")
            item["used"] = True
            raw = self._payload_broker.read_assisted_material(
                reference=str(item["secure_ref"]),
                expected_sha256=str(item["sha256"]),
                filename=str(item["filename"]),
            )
            return raw, {
                "filename": str(item["filename"]),
                "sha256": str(item["sha256"]),
                "purpose": str(item["purpose"]),
            }

    @staticmethod
    def _binding_list(payload: dict[str, Any], key: str, *, material: bool) -> list[dict[str, str]]:
        values = payload.get(key)
        if not isinstance(values, list) or len(values) > 500:
            raise JobOpsError("BROWSER_ASSIST_EVIDENCE_INVALID", "The browser companion completion evidence is invalid.")
        result: list[dict[str, str]] = []
        required = {"client_ref", "purpose", "sha256"} if material else {"client_ref", "value_sha256"}
        for item in values:
            if not isinstance(item, dict) or set(item) != required:
                raise JobOpsError("BROWSER_ASSIST_EVIDENCE_INVALID", "A browser companion binding result is invalid.")
            client_ref = str(item["client_ref"])
            digest = str(item["sha256"] if material else item["value_sha256"])
            if not CLIENT_REF_PATTERN.fullmatch(client_ref) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise JobOpsError("BROWSER_ASSIST_EVIDENCE_INVALID", "A browser companion binding hash is invalid.")
            value = {"client_ref": client_ref, "sha256": digest, "purpose": str(item["purpose"])} if material else {
                "client_ref": client_ref, "value_sha256": digest,
            }
            if material and value["purpose"] not in {"resume", "cover_letter", "portfolio"}:
                raise JobOpsError("BROWSER_ASSIST_EVIDENCE_INVALID", "A browser companion material purpose is invalid.")
            result.append(value)
        return result

    @staticmethod
    def _manual_event_hash(lease: _AssistLease) -> str:
        return _safe_hash([
            "MANUAL_FORWARD_CONTROL_CLICK",
            lease.manual_challenge_id,
            lease.manual_challenge_nonce,
            lease.assist_id,
            lease.application_id,
            lease.navigation_tab_id,
            lease.navigation_document_id,
            "MANUAL_NAVIGATION_REQUIRED",
            lease.navigation_snapshot_hash,
            lease.navigation_control_semantics_hash,
            lease.navigation_ref,
            False,
        ])

    @staticmethod
    def _issue_manual_challenge(lease: _AssistLease, *, now: datetime) -> dict[str, Any]:
        if (
            lease.navigation_tab_id is None
            or lease.navigation_document_id is None
            or lease.navigation_snapshot_hash is None
            or lease.navigation_control_semantics_hash is None
            or lease.navigation_ref is None
        ):
            raise JobOpsError(
                "MANUAL_NAVIGATION_CONTEXT_INVALID",
                "The stopped page is missing its bound tab, document, or control semantics.",
            )
        issued_at = iso_utc(now)
        expires_at = iso_utc(now + timedelta(seconds=MANUAL_NAVIGATION_CHALLENGE_TTL_SECONDS))
        lease.manual_challenge_id = "MNC-" + secrets.token_hex(16).upper()
        lease.manual_challenge_nonce = secrets.token_urlsafe(32)
        lease.manual_challenge_issued_at = issued_at
        lease.manual_challenge_expires_at = expires_at
        lease.manual_challenge_consumed = False
        public = {
            "challenge_id": lease.manual_challenge_id,
            "nonce": lease.manual_challenge_nonce,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "assist_id": lease.assist_id,
            "application_id": lease.application_id,
            "tab_id": lease.navigation_tab_id,
            "document_instance_id": lease.navigation_document_id,
            "stage": "MANUAL_NAVIGATION_REQUIRED",
            "client_ref": lease.navigation_ref,
            "prior_page_content_hash": lease.navigation_snapshot_hash,
            "control_semantics_hash": lease.navigation_control_semantics_hash,
        }
        lease.manual_challenge_hash = _safe_hash(public)
        return {**public, "challenge_hash": lease.manual_challenge_hash}

    def complete(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"PAGE_PREPARED"})
            if lease.prepared_hash is None:
                raise JobOpsError("BROWSER_ASSIST_NOT_PREPARED", "The live form must pass approval matching before fields are changed.")
            if payload.get("submit_events") != 0 or payload.get("navigation_actions") != 0:
                raise JobOpsError("FINAL_SUBMIT_FORBIDDEN", "No submit or page navigation may occur before page evidence is accepted.")
            fields = sorted(self._binding_list(payload, "field_bindings", material=False), key=lambda item: item["client_ref"])
            files = sorted(self._binding_list(payload, "material_bindings", material=True), key=lambda item: (item["purpose"], item["client_ref"]))
            if fields != lease.expected_fields or files != lease.expected_files:
                raise JobOpsError("BROWSER_ASSIST_EVIDENCE_MISMATCH", "The browser changed fewer or different approved fields than expected.")
            if any(not item.get("used") for item in lease.file_tokens.values()):
                raise JobOpsError("BROWSER_ASSIST_FILE_INCOMPLETE", "At least one approved material was not fetched for the selected upload control.")
            bundle, context, _ = self._bundle_manager.load_current(lease.application_id)
            del bundle
            if fields:
                self._session_manager.record_assisted_use(
                    session_id=lease.session_id,
                    context=context,
                    action="prefill_application_form",
                    request_hash=_safe_hash(fields),
                    result_code="APPROVED_FIELDS_PREFILLED",
                    real_side_effect=True,
                )
            if files:
                self._session_manager.record_assisted_use(
                    session_id=lease.session_id,
                    context=context,
                    action="upload_materials",
                    request_hash=_safe_hash(files),
                    result_code="APPROVED_MATERIALS_ATTACHED",
                    real_side_effect=True,
                )
                lease.uploaded_purposes.update(item["purpose"] for item in files)
            if lease.current_page_kind == "FINAL_REVIEW":
                lease.status = "AWAITING_USER_SUBMIT"
                target_status = "AWAITING_USER_SUBMIT"
                event_type = "STOPPED_BEFORE_SUBMIT"
            elif lease.current_page_kind == "INTERMEDIATE" and lease.navigation_ref:
                if lease.navigation_mode == PROGRAMMATIC_NAVIGATION_MODE and lease.navigation_token:
                    lease.status = "PAGE_REVIEW_REQUIRED"
                    target_status = "PAGE_REVIEW_REQUIRED"
                    event_type = "PAGE_READY_FOR_NAVIGATION"
                elif lease.navigation_mode == MANUAL_NAVIGATION_MODE and not lease.navigation_token:
                    lease.status = "MANUAL_NAVIGATION_REQUIRED"
                    target_status = "MANUAL_NAVIGATION_REQUIRED"
                    event_type = "STOPPED_FOR_USER_NAVIGATION"
                else:
                    raise JobOpsError("BROWSER_PAGE_KIND_INVALID", "The forward-control safety mode is inconsistent.")
            else:
                raise JobOpsError("BROWSER_PAGE_KIND_INVALID", "The prepared page no longer has a safe next action.")
            challenge: dict[str, Any] | None = None
            current = _now()
            if target_status == "MANUAL_NAVIGATION_REQUIRED":
                challenge = self._issue_manual_challenge(lease, now=current)
            now = iso_utc(current)
            evidence_hash = _safe_hash({
                "prepared_hash": lease.prepared_hash,
                "field_bindings": fields,
                "material_bindings": files,
                "submit_events": 0,
                "navigation_actions": 0,
                "page_kind": lease.current_page_kind,
                "step": lease.current_step,
                "manual_challenge_hash": lease.manual_challenge_hash,
            })
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE browser_assist_runs SET status=?,updated_at=? WHERE assist_id=?",
                    (target_status, now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, event_type, evidence_hash, now),
                )
            result = {
                "status": target_status,
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "current_step": lease.current_step,
                "page_kind": lease.current_page_kind,
                "field_count": len(fields),
                "file_count": len(files),
                "manual_field_count": lease.manual_field_count,
                "user_must_click_submit": target_status == "AWAITING_USER_SUBMIT",
                "submit_capability": False,
                "automatic_retry": False,
                "real_external_actions": int(bool(fields)) + int(bool(files)),
            }
            if target_status == "PAGE_REVIEW_REQUIRED":
                result["navigation"] = {
                    "client_ref": lease.navigation_ref,
                    "authorization_token": lease.navigation_token,
                    "requires_manual_review": lease.manual_field_count > 0,
                    "mode": lease.navigation_mode,
                    "control_type": lease.navigation_control_type,
                    "page_content_hash": lease.navigation_snapshot_hash,
                    "control_semantics_hash": lease.navigation_control_semantics_hash,
                    "programmatic_allowed": True,
                }
            elif target_status == "MANUAL_NAVIGATION_REQUIRED":
                result["manual_navigation"] = {
                    "client_ref": lease.navigation_ref,
                    "mode": MANUAL_NAVIGATION_MODE,
                    "control_type": lease.navigation_control_type,
                    "prior_page_content_hash": lease.navigation_snapshot_hash,
                    "control_semantics_hash": lease.navigation_control_semantics_hash,
                    "programmatic_allowed": False,
                    "user_must_click": True,
                    "resume_after_changed_page": True,
                    "challenge": challenge,
                }
            return result

    def authorize_navigation(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        extension_origin: str | None,
    ) -> dict[str, Any]:
        """Consume one page-scoped Next/Continue authorization; never a final Submit."""

        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"PAGE_REVIEW_REQUIRED", "MANUAL_NAVIGATION_REQUIRED"})
            if (
                lease.navigation_mode != PROGRAMMATIC_NAVIGATION_MODE
                or lease.navigation_control_type != "button"
            ):
                raise JobOpsError(
                    "NAVIGATION_REQUIRES_USER_CLICK",
                    "Submit-like Next/Continue controls must be clicked manually by the user.",
                )
            if payload.get("form_valid") is not True:
                raise JobOpsError("APPLICATION_PAGE_INCOMPLETE", "Complete the required fields on this page before continuing.")
            if payload.get("submit_events") != 0:
                raise JobOpsError("FINAL_SUBMIT_FORBIDDEN", "A final submit event cannot authorize a page transition.")
            client_ref = str(payload.get("client_ref", ""))
            authorization_token = str(payload.get("authorization_token", ""))
            page_content_hash = str(payload.get("page_content_hash", ""))
            control_semantics_hash = str(payload.get("control_semantics_hash", ""))
            if (
                client_ref != lease.navigation_ref
                or not authorization_token
                or not lease.navigation_token
                or not secrets.compare_digest(authorization_token, lease.navigation_token)
                or page_content_hash != lease.navigation_snapshot_hash
                or control_semantics_hash != lease.navigation_control_semantics_hash
            ):
                raise JobOpsError("NAVIGATION_AUTHORIZATION_INVALID", "The one-use Next/Continue authorization is invalid.")
            bundle, context, _ = self._bundle_manager.load_current(lease.application_id)
            del bundle
            navigation_hash = _safe_hash({
                "step": lease.current_step,
                "page_hash": lease.current_page_hash,
                "client_ref": client_ref,
                "form_valid": True,
                "final_submit": False,
                "page_content_hash": page_content_hash,
                "control_semantics_hash": control_semantics_hash,
            })
            self._session_manager.record_assisted_use(
                session_id=lease.session_id,
                context=context,
                action="navigate_application_step",
                request_hash=navigation_hash,
                result_code="FORWARD_NAVIGATION_AUTHORIZED",
                real_side_effect=True,
            )
            lease.navigation_token = None
            lease.status = "AWAITING_NAVIGATION"
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE browser_assist_runs SET status='AWAITING_NAVIGATION',updated_at=? WHERE assist_id=?",
                    (now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "FORWARD_NAVIGATION_AUTHORIZED", navigation_hash, now),
                )
            return {
                "status": "NAVIGATION_AUTHORIZED",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "client_ref": client_ref,
                "current_step": lease.current_step,
                "final_submit": False,
                "page_content_hash": page_content_hash,
                "control_semantics_hash": control_semantics_hash,
                "automatic_retry": False,
            }

    def resume_manual_navigation(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        extension_origin: str | None,
    ) -> dict[str, Any]:
        """Accept a freshly captured page only after a trusted manual forward click.

        This endpoint never activates a page control.  It merely proves that the
        user clicked the prior submit-like Next/Continue and that the same-origin,
        sanitized page structure actually changed before a new step is prepared.
        """

        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"MANUAL_NAVIGATION_REQUIRED"})
            if lease.navigation_mode != MANUAL_NAVIGATION_MODE or lease.navigation_token is not None:
                raise JobOpsError("MANUAL_NAVIGATION_BINDING_INVALID", "The prior control was not bound for manual navigation.")
            if payload.get("trusted_user_event") is not True:
                raise JobOpsError("MANUAL_NAVIGATION_NOT_PROVEN", "A trusted user click is required before the next page can be resumed.")
            if payload.get("manual_navigation_default_prevented") is not False:
                raise JobOpsError(
                    "MANUAL_NAVIGATION_EVENT_CANCELLED",
                    "A cancelled click or form submission cannot prove manual navigation.",
                )
            if lease.manual_challenge_consumed:
                raise JobOpsError(
                    "MANUAL_NAVIGATION_CHALLENGE_REPLAYED",
                    "The one-use manual navigation challenge was already consumed.",
                )
            challenge_id = str(payload.get("manual_navigation_challenge_id", ""))
            challenge_nonce = str(payload.get("manual_navigation_nonce", ""))
            challenge_hash = str(payload.get("manual_navigation_challenge_hash", ""))
            prior_document_id = str(payload.get("manual_navigation_document_id", ""))
            tab_id, _current_document_id = self._browser_context(payload)
            if (
                not MANUAL_CHALLENGE_PATTERN.fullmatch(challenge_id)
                or challenge_id != lease.manual_challenge_id
                or not challenge_nonce
                or lease.manual_challenge_nonce is None
                or not secrets.compare_digest(challenge_nonce, lease.manual_challenge_nonce)
                or challenge_hash != lease.manual_challenge_hash
                or tab_id != lease.navigation_tab_id
                or prior_document_id != lease.navigation_document_id
                or str(payload.get("manual_navigation_assist_id", "")) != lease.assist_id
                or str(payload.get("manual_navigation_application_id", "")) != lease.application_id
                or payload.get("manual_navigation_tab_id") != lease.navigation_tab_id
                or str(payload.get("manual_navigation_stage", "")) != "MANUAL_NAVIGATION_REQUIRED"
                or str(payload.get("manual_navigation_client_ref", "")) != lease.navigation_ref
            ):
                raise JobOpsError(
                    "MANUAL_NAVIGATION_CHALLENGE_INVALID",
                    "The manual navigation challenge does not match this application, tab, document, stage, or control.",
                )
            if (
                lease.manual_challenge_expires_at is None
                or parse_iso(lease.manual_challenge_expires_at) <= _now()
            ):
                lease.manual_challenge_consumed = True
                raise JobOpsError(
                    "MANUAL_NAVIGATION_CHALLENGE_EXPIRED",
                    "The manual navigation challenge expired before the changed page was collected.",
                )
            event_hash = str(payload.get("event_hash", ""))
            expected_event_hash = self._manual_event_hash(lease)
            if (
                not re.fullmatch(r"sha256:[a-f0-9]{64}", event_hash)
                or not secrets.compare_digest(event_hash, expected_event_hash)
            ):
                raise JobOpsError(
                    "MANUAL_NAVIGATION_EVIDENCE_INVALID",
                    "The trusted click proof was not derived from the active one-use challenge.",
                )
            if (
                str(payload.get("prior_page_content_hash", "")) != lease.navigation_snapshot_hash
                or str(payload.get("control_semantics_hash", "")) != lease.navigation_control_semantics_hash
            ):
                raise JobOpsError("MANUAL_NAVIGATION_BINDING_INVALID", "The manual navigation evidence does not match the stopped page.")
            # Consume before interpreting the destination.  Same-page,
            # preventDefault, unrelated navigation and malformed-page attempts
            # cannot replay this click proof against a later page.
            lease.manual_challenge_consumed = True
            signals = self._security_signals(payload)
            if signals & HARD_SIGNAL_CODES:
                raise JobOpsError(
                    "BROWSER_SECURITY_STOP",
                    "The changed page crossed the approved application boundary.",
                    blockers=sorted(signals & HARD_SIGNAL_CODES),
                )
            bundle, context, _ = self._bundle_manager.load_current(lease.application_id)
            live, _ = self._live_snapshot(
                lease=lease,
                payload=payload,
                expected_snapshot=bundle["form_snapshot"],
                enforce_initial_approval=False,
            )
            new_page_hash = str(live["page_content_hash"])
            if not lease.current_page_hash or new_page_hash == lease.current_page_hash:
                raise JobOpsError(
                    "NAVIGATION_DID_NOT_ADVANCE",
                    "The sanitized application page did not change after the manual click.",
                )
            if lease.current_step >= lease.max_steps:
                raise JobOpsError("APPLICATION_STEP_LIMIT", "The application exceeded the maximum safe page count.")
            lease.visited_page_hashes.add(lease.current_page_hash)
            lease.current_step += 1
            self._rotate_step_session(lease, context)
            prior_page_hash = lease.current_page_hash
            consumed_challenge_hash = lease.manual_challenge_hash
            lease.status = "READY"
            lease.prepared_hash = None
            lease.current_page_hash = None
            lease.current_page_kind = None
            lease.navigation_ref = None
            lease.navigation_token = None
            lease.navigation_mode = None
            lease.navigation_control_type = None
            lease.navigation_control_semantics_hash = None
            lease.navigation_snapshot_hash = None
            lease.navigation_tab_id = None
            lease.navigation_document_id = None
            lease.manual_challenge_id = None
            lease.manual_challenge_nonce = None
            lease.manual_challenge_issued_at = None
            lease.manual_challenge_expires_at = None
            lease.manual_challenge_hash = None
            lease.manual_challenge_consumed = False
            now = iso_utc()
            evidence_hash = _safe_hash({
                "step": lease.current_step,
                "origin": lease.allowed_page_origin,
                "prior_page_hash": prior_page_hash,
                "next_page_hash": new_page_hash,
                "event_hash": event_hash,
                "manual_challenge_hash": consumed_challenge_hash,
                "performed_by": "USER",
            })
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """UPDATE browser_assist_runs
                       SET status='READY',current_step=?,last_page_hash=NULL,updated_at=? WHERE assist_id=?""",
                    (lease.current_step, now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "MANUAL_NEXT_PAGE_OBSERVED", evidence_hash, now),
                )
            return {
                "status": "NEXT_PAGE_READY",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "current_step": lease.current_step,
                "max_steps": lease.max_steps,
                "navigation_performed_by": "USER",
                "next_page_content_hash": new_page_hash,
                "submit_capability": False,
                "automatic_retry": False,
            }

    def navigation_observed(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        extension_origin: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"AWAITING_NAVIGATION"})
            canonical = _canonical_url(str(payload.get("url", "")))
            event_hash = str(payload.get("event_hash", ""))
            if (
                _safe_origin(canonical) != lease.allowed_page_origin
                or url_has_sensitive_query(canonical)
                or not self._provider_host_allowed(
                    lease.provider, urlparse(canonical).hostname or "", str(lease.source_route["company_domain"]),
                )
            ):
                raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "Next/Continue left the approved company/ATS origin.")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", event_hash):
                raise JobOpsError("NAVIGATION_EVIDENCE_INVALID", "The page transition evidence is invalid.")
            if lease.current_step >= lease.max_steps:
                raise JobOpsError("APPLICATION_STEP_LIMIT", "The application exceeded the maximum safe page count.")
            if lease.current_page_hash:
                lease.visited_page_hashes.add(lease.current_page_hash)
            lease.current_step += 1
            bundle, context, _ = self._bundle_manager.load_current(lease.application_id)
            del bundle
            self._rotate_step_session(lease, context)
            now = iso_utc()
            evidence_hash = _safe_hash({
                "step": lease.current_step,
                "origin": lease.allowed_page_origin,
                "event_hash": event_hash,
            })
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE browser_assist_runs
                       SET status='READY',current_step=?,last_page_hash=NULL,updated_at=? WHERE assist_id=?""",
                    (lease.current_step, now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "NEXT_PAGE_OBSERVED", evidence_hash, now),
                )
            return {
                "status": "NEXT_PAGE_READY",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "current_step": lease.current_step,
                "max_steps": lease.max_steps,
                "submit_capability": False,
                "automatic_retry": False,
            }

    def submit_observed(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"AWAITING_USER_SUBMIT"})
            if payload.get("trusted_user_event") is not True:
                raise JobOpsError("USER_SUBMIT_NOT_PROVEN", "Only a trusted user submit event may start result observation.")
            if _safe_origin(str(payload.get("url", ""))) != lease.allowed_page_origin:
                raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The observed submit event came from another site.")
            event_hash = str(payload.get("event_hash", ""))
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", event_hash):
                raise JobOpsError("USER_SUBMIT_EVIDENCE_INVALID", "The user submit evidence hash is invalid.")
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                app = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (lease.application_id,),
                ).fetchone()
                approval = connection.execute(
                    "SELECT approval_id,status FROM approvals WHERE application_id=? ORDER BY issued_at DESC LIMIT 1",
                    (lease.application_id,),
                ).fetchone()
                if app is None or str(app["status"]) != "APPROVED" or approval is None or str(approval["status"]) != "APPROVED":
                    raise JobOpsError("APPROVAL_INVALIDATED", "The application approval is no longer current.")
                connection.execute(
                    "UPDATE applications SET status='SUBMITTED',last_safe_state='APPROVED',updated_at=? WHERE application_id=?",
                    (now, lease.application_id),
                )
                connection.execute(
                    "UPDATE approvals SET status='CONSUMED',consumed_at=? WHERE approval_id=?",
                    (now, str(approval["approval_id"])),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        lease.application_id, "USER_SUBMIT_OBSERVED", "APPROVED", "SUBMITTED",
                        json.dumps({"assist_id": lease.assist_id, "event_hash": event_hash, "automatic_retry": False}), now,
                    ),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "USER_SUBMIT_OBSERVED", event_hash, now),
                )
            lease.submit_observed = True
            return {
                "status": "OBSERVING_RESULT_PAGE",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "automatic_retry": False,
                "submit_performed_by": "USER",
            }

    @staticmethod
    def _result_signals(payload: dict[str, Any]) -> dict[str, Any]:
        success = payload.get("success_markers", [])
        failure = payload.get("failure_markers", [])
        if (
            not isinstance(success, list) or not isinstance(failure, list)
            or any(item not in SUCCESS_MARKERS for item in success)
            or any(item not in FAILURE_MARKERS for item in failure)
        ):
            raise JobOpsError("RESULT_SIGNAL_INVALID", "The result-page marker set is invalid.")
        invalid_count = payload.get("invalid_control_count", 0)
        if isinstance(invalid_count, bool) or not isinstance(invalid_count, int) or not 0 <= invalid_count <= 500:
            raise JobOpsError("RESULT_SIGNAL_INVALID", "The result-page invalid-control count is invalid.")
        booleans = {}
        for key in ("form_present", "submit_control_present", "success_route"):
            if not isinstance(payload.get(key), bool):
                raise JobOpsError("RESULT_SIGNAL_INVALID", "A result-page state flag is invalid.")
            booleans[key] = bool(payload[key])
        page_hash = str(payload.get("page_fingerprint", ""))
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", page_hash):
            raise JobOpsError("RESULT_SIGNAL_INVALID", "The result-page fingerprint is invalid.")
        return {
            "success_markers": sorted(set(str(item) for item in success)),
            "failure_markers": sorted(set(str(item) for item in failure)),
            "invalid_control_count": invalid_count,
            **booleans,
            "page_fingerprint": page_hash,
        }

    def _disable_after_observation(self) -> None:
        control = self._session_manager.control_state()
        if control["enabled"] and control["mode"] == "ASSISTED_USER_PRESENT":
            self._session_manager.disable(reason="ASSISTED_SESSION_FINISHED")

    def _record_receipt(self, lease: _AssistLease, *, source: str, confirmation_type: str, evidence_hash: str) -> str:
        now = iso_utc()
        receipt = {
            "receipt_id": stable_id("RCP", lease.application_id, source, evidence_hash, now),
            "application_id": lease.application_id,
            "source": source,
            "confirmation_type": confirmation_type,
            "confirmation_hash": evidence_hash,
            "verified": True,
            "verified_at": now,
        }
        validate_named("receipt", receipt, self.schemas)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            app = connection.execute(
                "SELECT status FROM applications WHERE application_id=?", (lease.application_id,),
            ).fetchone()
            if app is None or str(app["status"]) not in {"SUBMITTED", "SUBMISSION_UNKNOWN"}:
                raise JobOpsError("APPLICATION_NOT_SUBMITTED", "Confirmation requires a user-submitted application.")
            previous = str(app["status"])
            connection.execute(
                """INSERT INTO receipts(
                receipt_id,application_id,confirmation_type,confirmation_hash,verified_at,source,verified
                ) VALUES(?,?,?,?,?,?,1)""",
                (
                    receipt["receipt_id"], lease.application_id, confirmation_type,
                    evidence_hash, now, source,
                ),
            )
            connection.execute(
                "UPDATE applications SET status='CONFIRMED',last_safe_state='CONFIRMED',updated_at=? WHERE application_id=?",
                (now, lease.application_id),
            )
            connection.execute(
                "UPDATE browser_assist_runs SET status='CONFIRMED',updated_at=? WHERE assist_id=?",
                (now, lease.assist_id),
            )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    lease.application_id, "RECEIPT_VERIFIED", previous, "CONFIRMED",
                    json.dumps({"receipt_id": receipt["receipt_id"], "source": source}), now,
                ),
            )
            connection.execute(
                "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                (lease.assist_id, lease.application_id, "RESULT_CONFIRMED", evidence_hash, now),
            )
        lease.status = "CONFIRMED"
        return str(receipt["receipt_id"])

    def observe_result(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"AWAITING_USER_SUBMIT"})
            if not lease.submit_observed:
                raise JobOpsError("USER_SUBMIT_NOT_PROVEN", "Result observation cannot start before a trusted user submit event.")
            if _safe_origin(str(payload.get("url", ""))) != lease.allowed_page_origin:
                return self._mark_unknown(lease, reason="RESULT_PAGE_ORIGIN_CHANGED")
            signals = self._result_signals(payload)
            evidence_hash = _safe_hash(signals)
            explicit_failure = bool(signals["failure_markers"]) or (
                signals["invalid_control_count"] > 0 and signals["form_present"]
            )
            strong_success = not signals["failure_markers"] and (
                (
                    bool(signals["success_markers"])
                    and (signals["success_route"] or not signals["form_present"] or not signals["submit_control_present"])
                )
                or (signals["success_route"] and not signals["form_present"] and not signals["submit_control_present"])
            )
            if strong_success:
                receipt_id = self._record_receipt(
                    lease,
                    source="browser-companion",
                    confirmation_type="confirmation_page",
                    evidence_hash=evidence_hash,
                )
                self._disable_after_observation()
                return {
                    "status": "CONFIRMED",
                    "application_id": lease.application_id,
                    "receipt_id": receipt_id,
                    "automatic_retry": False,
                    "submit_performed_by": "USER",
                }

            now = iso_utc()
            target = "FAILED" if explicit_failure else "SUBMISSION_UNKNOWN"
            application_target = "AWAITING_APPROVAL" if explicit_failure else "SUBMISSION_UNKNOWN"
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                app = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (lease.application_id,),
                ).fetchone()
                if app is None or str(app["status"]) != "SUBMITTED":
                    raise JobOpsError("APPLICATION_NOT_SUBMITTED", "The result observer no longer matches a user-submitted application.")
                connection.execute(
                    "UPDATE applications SET status=?,last_safe_state='APPROVED',updated_at=? WHERE application_id=?",
                    (application_target, now, lease.application_id),
                )
                connection.execute(
                    "UPDATE browser_assist_runs SET status=?,updated_at=? WHERE assist_id=?",
                    (target, now, lease.assist_id),
                )
                if explicit_failure:
                    connection.execute(
                        "UPDATE review_packets SET status='AWAITING_APPROVAL' WHERE application_id=? AND status='APPROVED'",
                        (lease.application_id,),
                    )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        lease.application_id,
                        "USER_SUBMISSION_FAILED" if explicit_failure else "SUBMISSION_EVIDENCE_UNKNOWN",
                        "SUBMITTED", application_target,
                        json.dumps({"evidence_hash": evidence_hash, "automatic_retry": False}), now,
                    ),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (
                        lease.assist_id, lease.application_id,
                        "RESULT_FAILED" if explicit_failure else "RESULT_UNKNOWN", evidence_hash, now,
                    ),
                )
            lease.status = target
            self._disable_after_observation()
            return {
                "status": application_target,
                "application_id": lease.application_id,
                "automatic_retry": False,
                "user_confirmation_required": not explicit_failure,
                "question": "是否提交成功？" if not explicit_failure else None,
                "submit_performed_by": "USER",
            }

    def result_unavailable(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        """Convert an unreadable post-submit page into an explicit manual question."""

        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"AWAITING_USER_SUBMIT"})
            if not lease.submit_observed:
                raise JobOpsError("USER_SUBMIT_NOT_PROVEN", "An unreadable result page is not actionable before a trusted user submit event.")
            reason = str(payload.get("reason", ""))
            if reason not in {"PAGE_UNAVAILABLE", "RESULT_READ_FAILED", "RESULT_PERMISSION_CHANGED"}:
                raise JobOpsError("RESULT_SIGNAL_INVALID", "The result-page failure reason is invalid.")
            return self._mark_unknown(lease, reason=reason)

    def resolve_unknown(self, *, application_id: str, submitted: bool, user_confirmed: bool) -> dict[str, Any]:
        with self._lock:
            if not user_confirmed:
                raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Resolving an unknown submission requires explicit confirmation.")
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT assist_id,session_id,allowed_origin,created_at,expires_at,status
                       FROM browser_assist_runs WHERE application_id=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (application_id,),
                ).fetchone()
                app = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (application_id,),
                ).fetchone()
            if row is None or app is None or str(row["status"]) != "SUBMISSION_UNKNOWN" or str(app["status"]) != "SUBMISSION_UNKNOWN":
                raise JobOpsError("SUBMISSION_UNKNOWN_NOT_FOUND", "The selected application is not awaiting manual submission verification.")
            lease = next((item for item in self._leases.values() if item.assist_id == str(row["assist_id"])), None)
            if lease is None:
                lease = _AssistLease(
                    token="expired-manual-resolution-token-placeholder",
                    assist_id=str(row["assist_id"]),
                    application_id=application_id,
                    session_id=str(row["session_id"]),
                    source_route={},
                    source_identity_hash=_safe_hash(_source_identity_material(application_id, {})),
                    allowed_page_origin=str(row["allowed_origin"]),
                    provider="company",
                    route_kind="OFFICIAL_DIRECT",
                    created_at=str(row["created_at"]),
                    expires_at=str(row["expires_at"]),
                    status="SUBMISSION_UNKNOWN",
                    submit_observed=True,
                )
            evidence_hash = _safe_hash({
                "application_id": application_id,
                "decision": "SUBMITTED" if submitted else "NOT_SUBMITTED",
                "confirmed_at": iso_utc(),
            })
            if submitted:
                receipt_id = self._record_receipt(
                    lease,
                    source="manual-evidence",
                    confirmation_type="manual",
                    evidence_hash=evidence_hash,
                )
                return {
                    "status": "CONFIRMED",
                    "application_id": application_id,
                    "receipt_id": receipt_id,
                    "automatic_retry": False,
                }
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE applications SET status='AWAITING_APPROVAL',last_safe_state='AWAITING_APPROVAL',updated_at=? WHERE application_id=?",
                    (now, application_id),
                )
                connection.execute(
                    "UPDATE review_packets SET status='AWAITING_APPROVAL' WHERE application_id=? AND status='APPROVED'",
                    (application_id,),
                )
                connection.execute(
                    "UPDATE browser_assist_runs SET status='FAILED',updated_at=? WHERE assist_id=?",
                    (now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        application_id, "USER_CONFIRMED_NOT_SUBMITTED", "SUBMISSION_UNKNOWN", "AWAITING_APPROVAL",
                        json.dumps({"evidence_hash": evidence_hash, "automatic_retry": False}), now,
                    ),
                )
            lease.status = "FAILED"
            return {
                "status": "AWAITING_APPROVAL",
                "application_id": application_id,
                "automatic_retry": False,
                "next_safe_action": "REVIEW_AND_APPROVE_AGAIN",
            }

    def stop(self, *, user_confirmed: bool) -> dict[str, Any]:
        with self._lock:
            if not user_confirmed:
                raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Stopping browser assistance requires explicit confirmation.")
            now = iso_utc()
            active = [item for item in self._leases.values() if item.status not in TERMINAL_RUN_STATES]
            active_ids = [item.assist_id for item in active]
            unknown_ids: set[str] = set()
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for lease in active:
                    app = connection.execute(
                        "SELECT status FROM applications WHERE application_id=?", (lease.application_id,),
                    ).fetchone()
                    uncertain = lease.status == "AWAITING_USER_SUBMIT" or (
                        app is not None and str(app["status"]) == "SUBMITTED"
                    )
                    if uncertain:
                        self._mark_unknown_in_connection(
                            connection,
                            assist_id=lease.assist_id,
                            application_id=lease.application_id,
                            reason="USER_STOPPED_DURING_SUBMIT_WINDOW",
                            now=now,
                        )
                        lease.status = "SUBMISSION_UNKNOWN"
                        unknown_ids.add(lease.assist_id)
                    else:
                        connection.execute(
                            "UPDATE browser_assist_runs SET status='REVOKED',updated_at=? WHERE assist_id=?",
                            (now, lease.assist_id),
                        )
                        lease.status = "REVOKED"
            self._leases.clear()
            control = self._session_manager.control_state()
            if control["enabled"] and control["mode"] == "ASSISTED_USER_PRESENT":
                self._session_manager.disable(reason="USER_EMERGENCY_STOP")
            return {
                "status": "BROWSER_ASSIST_STOPPED",
                "revoked_assists": len(active_ids),
                "submission_unknown_assists": len(unknown_ids),
                "automatic_retry": False,
            }

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            self._prune()
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT assist_id,application_id,provider,route_kind,current_step,max_steps,
                              handoff_kind,status,created_at,expires_at,updated_at
                       FROM browser_assist_runs ORDER BY created_at DESC LIMIT 10"""
                ).fetchall()
                inspection_count = int(connection.execute(
                    "SELECT COUNT(*) FROM external_action_session_uses WHERE action='inspect_application_form' AND adapter_kind='browser_companion'"
                ).fetchone()[0])
                navigation_count = int(connection.execute(
                    "SELECT COUNT(*) FROM external_action_session_uses WHERE action='navigate_application_step' AND adapter_kind='browser_companion'"
                ).fetchone()[0])
            active = next((item for item in self._leases.values() if item.status not in TERMINAL_RUN_STATES), None)
            return {
                "status": "AVAILABLE",
                "protocol_version": COMPANION_PROTOCOL_VERSION,
                "extension_id": COMPANION_EXTENSION_ID,
                "paired": bool(active and active.paired),
                "active_assist_id": active.assist_id if active else None,
                "active_application_id": active.application_id if active else None,
                "active_status": active.status if active else None,
                "active_provider": active.provider if active else None,
                "active_step": active.current_step if active else None,
                "active_handoff_kind": active.handoff_kind if active else None,
                "real_website_inspections": inspection_count,
                "assisted_page_navigations": navigation_count,
                "supported_providers": ["company", "greenhouse", "lever", "workday"],
                "multi_page": True,
                "login_handoff": True,
                "captcha_mfa_handoff": True,
                "submit_capability": False,
                "automatic_retry": False,
                "recent_runs": [dict(row) for row in rows],
            }

    def close(self) -> None:
        with self._lock:
            if any(item.status not in TERMINAL_RUN_STATES for item in self._leases.values()):
                self.stop(user_confirmed=True)
