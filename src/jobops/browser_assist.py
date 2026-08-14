from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .approvals import ApprovalContext
from .ats_browser import analyze_local_ats_form
from .db import JobOpsDB
from .ephemeral_payload import EphemeralATSPayloadBroker
from .errors import JobOpsError
from .execution_bundle import ApplicationExecutionBundleManager
from .external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from .private_onboarding import PrivateOnboarding
from .runtime_schema import validate_named
from .sourcing import _canonical_url, host_matches_registered, url_has_sensitive_query
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes, stable_id


COMPANION_PROTOCOL_VERSION = 1
COMPANION_EXTENSION_ID = "hhlliaaafegldkmcgmaoaelabipcaooj"
COMPANION_EXTENSION_ORIGIN = f"chrome-extension://{COMPANION_EXTENSION_ID}"
ASSIST_TTL_MINUTES = 30
MAX_LIVE_FORM_HTML_BYTES = 2 * 1024 * 1024
MAX_ACTIVE_ASSISTS = 1
ALLOWED_LIVE_BLOCKERS = frozenset({"FILE_UPLOAD_STOP", "FINAL_SUBMIT_STOP"})
CLIENT_REF_PATTERN = re.compile(r"^DOM-[A-F0-9]{12}$")
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


@dataclass
class _AssistLease:
    token: str
    assist_id: str
    application_id: str
    session_id: str
    source_route: dict[str, Any]
    allowed_page_origin: str
    created_at: str
    expires_at: str
    status: str = "PAIRING"
    paired: bool = False
    expected_fields: list[dict[str, str]] = field(default_factory=list)
    expected_files: list[dict[str, str]] = field(default_factory=list)
    file_tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    submit_observed: bool = False
    prepared_hash: str | None = None


class BrowserAssistManager:
    """User-present company-form assistance with no submit capability."""

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
    def _validate_company_route(route: dict[str, Any], bundle: dict[str, Any]) -> None:
        validate_named("source-route", route, project_root() / "schemas")
        if (
            route.get("status") != "ROUTE_APPROVED"
            or route.get("provider") != "company"
            or route.get("route_kind") != "OFFICIAL_DIRECT"
            or route.get("account_action") != "NONE"
            or route.get("guest_mode") != "GUEST_SELECTED"
            or bundle.get("provider") != "company"
        ):
            raise JobOpsError(
                "BROWSER_ASSIST_ROUTE_UNSUPPORTED",
                "The fastest browser-assist version accepts only approved guest-mode forms on the company domain.",
            )
        canonical = _canonical_url(str(route["current_url"]))
        parsed = urlparse(canonical)
        if url_has_sensitive_query(canonical) or not host_matches_registered(
            parsed.hostname or "", str(route["company_domain"]),
        ):
            raise JobOpsError("BROWSER_ASSIST_ROUTE_UNSAFE", "The approved company form route is unsafe or outside the company domain.")
        if canonical != str(bundle.get("form_snapshot", {}).get("canonical_url", "")):
            raise JobOpsError("EXECUTION_ROUTE_CHANGED", "The approved execution bundle and company form URL differ.")

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
            active = [item for item in self._leases.values() if item.status not in TERMINAL_RUN_STATES]
            if len(active) >= MAX_ACTIVE_ASSISTS:
                raise JobOpsError(
                    "BROWSER_ASSIST_ALREADY_ACTIVE",
                    "Finish or stop the current browser-assist session before starting another application.",
                )
            bundle, context, _ = self._bundle_manager.load_current(application_id)
            self._validate_company_route(source_route, bundle)
            if context.application_id != application_id:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "The approved application context is inconsistent.")
            self._session_manager.enable(user_confirmed=True)
            session = self._session_manager.issue(
                context=context,
                allowed_actions={"inspect_application_form", "prefill_application_form", "upload_materials"},
                user_confirmed=True,
                ttl_minutes=ASSIST_TTL_MINUTES,
            )
            self._session_manager.persist(session, context=context)
            current = _now()
            token = secrets.token_urlsafe(48)
            assist_id = stable_id("BAS", session.session_id, token, iso_utc(current))
            lease = _AssistLease(
                token=token,
                assist_id=assist_id,
                application_id=application_id,
                session_id=session.session_id,
                source_route=dict(source_route),
                allowed_page_origin=_safe_origin(str(source_route["current_url"])),
                created_at=iso_utc(current),
                expires_at=iso_utc(current + timedelta(minutes=ASSIST_TTL_MINUTES)),
            )
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO browser_assist_runs(
                    assist_id,application_id,session_id,allowed_origin,status,prepared_hash,
                    created_at,expires_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?,?)""",
                    (
                        assist_id, application_id, session.session_id, lease.allowed_page_origin,
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
            lease = self._lease(token, statuses={"PAIRING", "READY"})
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
                "expires_at": lease.expires_at,
                "submit_capability": False,
                "automatic_retry": False,
            }

    def _live_snapshot(
        self,
        *,
        lease: _AssistLease,
        payload: dict[str, Any],
        expected_snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        live_url = str(payload.get("url", ""))
        canonical = _canonical_url(live_url)
        if canonical != str(lease.source_route["current_url"]) or _safe_origin(canonical) != lease.allowed_page_origin:
            raise JobOpsError("FORM_ROUTE_BINDING_CHANGED", "The current tab is not the exact approved company form.")
        if url_has_sensitive_query(canonical):
            raise JobOpsError("ATS_ROUTE_SENSITIVE_QUERY", "The live form URL contains a sensitive query field.")
        signals = payload.get("blocker_signals", [])
        if (
            not isinstance(signals, list)
            or any(not isinstance(item, str) or item not in SAFE_SIGNAL_CODES for item in signals)
        ):
            raise JobOpsError("BROWSER_SECURITY_SIGNAL_INVALID", "The browser companion returned an invalid safety signal.")
        if signals:
            raise JobOpsError(
                "BROWSER_SECURITY_STOP",
                "Login, account creation, CAPTCHA, MFA, or cross-origin content requires the user to continue manually.",
                blockers=sorted(set(signals)),
            )
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
        live = analyze_local_ats_form(
            encoded,
            route=lease.source_route,
            blocked_categories=list(policy["blocked_form_categories"]),
        )
        if len(client_refs) != len(live["fields"]):
            raise JobOpsError("SITE_CHANGED", "The live form control count changed after approval.")
        if set(live["blockers"]) - ALLOWED_LIVE_BLOCKERS:
            raise JobOpsError(
                "BROWSER_SECURITY_STOP",
                "The live form contains a step JobFlow is not permitted to automate.",
                blockers=sorted(set(live["blockers"]) - ALLOWED_LIVE_BLOCKERS),
            )
        expected_fields = list(expected_snapshot["fields"])
        if len(expected_fields) != len(live["fields"]):
            raise JobOpsError("SITE_CHANGED", "The live form field count differs from the approved review packet.")
        for expected, current in zip(expected_fields, live["fields"], strict=True):
            if _semantic_field(expected) != _semantic_field(current):
                raise JobOpsError("SITE_CHANGED", "A live form field differs from the approved review packet.")
        return live, list(client_refs)

    def prepare(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"READY"})
            if not lease.paired:
                raise JobOpsError("BROWSER_COMPANION_NOT_PAIRED", "Pair the bundled browser companion before opening the company form.")
            bundle, context, answer_refs = self._bundle_manager.load_current(lease.application_id)
            self._session_manager.validate_scope(
                session_id=lease.session_id,
                context=context,
                required_actions={"inspect_application_form", "prefill_application_form", "upload_materials"},
            )
            live, client_refs = self._live_snapshot(
                lease=lease, payload=payload, expected_snapshot=bundle["form_snapshot"],
            )
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
            expected_fields = list(bundle["form_snapshot"]["fields"])
            client_by_control = {
                str(expected["control_ref"]): client_ref
                for expected, client_ref in zip(expected_fields, client_refs, strict=True)
            }
            fields_by_ref = {str(item["control_ref"]): item for item in expected_fields}
            outward_fields: list[dict[str, str]] = []
            expected_field_results: list[dict[str, str]] = []
            for item in value_payload["fields"]:
                control_ref = str(item["control_ref"])
                field = fields_by_ref.get(control_ref)
                if field is None:
                    raise JobOpsError("EPHEMERAL_FORM_BINDING_CHANGED", "A resolved value no longer matches the approved form.")
                client_ref = client_by_control[control_ref]
                value = str(item["value"])
                value_hash = sha256_bytes(value.encode("utf-8"))
                outward_fields.append({
                    "client_ref": client_ref,
                    "control_type": str(field["control_type"]),
                    "value": value,
                    "value_sha256": value_hash,
                })
                expected_field_results.append({"client_ref": client_ref, "value_sha256": value_hash})

            file_controls: dict[str, list[str]] = {}
            purpose_keys = {"resume": "resume", "cover_letter": "cover_letter", "portfolio": "portfolio_file"}
            for expected, client_ref in zip(expected_fields, client_refs, strict=True):
                if expected.get("classification") == "file_upload_stop":
                    file_controls.setdefault(str(expected.get("answer_key")), []).append(client_ref)
            outward_files: list[dict[str, str]] = []
            expected_file_results: list[dict[str, str]] = []
            file_tokens: dict[str, dict[str, Any]] = {}
            for item in value_payload["files"]:
                purpose = str(item["purpose"])
                candidates = file_controls.get(purpose_keys[purpose], [])
                if len(candidates) != 1:
                    raise JobOpsError(
                        "APPROVED_UPLOAD_CONTROL_MISSING",
                        "The live form does not contain one unambiguous upload control for every approved material.",
                        purpose=purpose,
                    )
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

            lease.expected_fields = sorted(expected_field_results, key=lambda item: item["client_ref"])
            lease.expected_files = sorted(expected_file_results, key=lambda item: (item["purpose"], item["client_ref"]))
            lease.file_tokens = file_tokens
            lease.prepared_hash = _safe_hash({
                "live_semantics": [_semantic_field(item) for item in live["fields"]],
                "field_bindings": lease.expected_fields,
                "material_bindings": lease.expected_files,
            })
            self._session_manager.record_assisted_use(
                session_id=lease.session_id,
                context=context,
                action="inspect_application_form",
                request_hash=_safe_hash({
                    "sanitized_html_sha256": sha256_bytes(str(payload["sanitized_html"]).encode("utf-8")),
                    "client_refs": client_refs,
                    "url_origin": lease.allowed_page_origin,
                }),
                result_code="LIVE_FORM_MATCHED_APPROVAL",
                real_side_effect=False,
            )
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE browser_assist_runs SET prepared_hash=?,updated_at=? WHERE assist_id=?",
                    (lease.prepared_hash, now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "LIVE_FORM_PREPARED", lease.prepared_hash, now),
                )
            return {
                "status": "LIVE_FORM_APPROVED_FOR_ASSIST",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "fields": outward_fields,
                "files": outward_files,
                "field_count": len(outward_fields),
                "file_count": len(outward_files),
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
            lease = self._lease(token, statuses={"READY"})
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

    def complete(self, token: str, payload: dict[str, Any], *, extension_origin: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.extension_origin_allowed(extension_origin):
                raise JobOpsError("BROWSER_COMPANION_ORIGIN_FORBIDDEN", "The request did not come from the bundled JobFlow companion.")
            lease = self._lease(token, statuses={"READY"})
            if lease.prepared_hash is None:
                raise JobOpsError("BROWSER_ASSIST_NOT_PREPARED", "The live form must pass approval matching before fields are changed.")
            if payload.get("submit_events") != 0 or payload.get("navigation_actions") != 0:
                raise JobOpsError("FINAL_SUBMIT_FORBIDDEN", "The browser companion must never submit or navigate the application form.")
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
            lease.status = "AWAITING_USER_SUBMIT"
            now = iso_utc()
            evidence_hash = _safe_hash({
                "prepared_hash": lease.prepared_hash,
                "field_bindings": fields,
                "material_bindings": files,
                "submit_events": 0,
                "navigation_actions": 0,
            })
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE browser_assist_runs SET status='AWAITING_USER_SUBMIT',updated_at=? WHERE assist_id=?",
                    (now, lease.assist_id),
                )
                connection.execute(
                    "INSERT INTO browser_assist_events(assist_id,application_id,event_type,evidence_hash,created_at) VALUES(?,?,?,?,?)",
                    (lease.assist_id, lease.application_id, "STOPPED_BEFORE_SUBMIT", evidence_hash, now),
                )
            return {
                "status": "AWAITING_USER_SUBMIT",
                "assist_id": lease.assist_id,
                "application_id": lease.application_id,
                "field_count": len(fields),
                "file_count": len(files),
                "user_must_click_submit": True,
                "submit_capability": False,
                "automatic_retry": False,
                "real_external_actions": int(bool(fields)) + int(bool(files)),
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
                    allowed_page_origin=str(row["allowed_origin"]),
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
                    """SELECT assist_id,application_id,status,created_at,expires_at,updated_at
                       FROM browser_assist_runs ORDER BY created_at DESC LIMIT 10"""
                ).fetchall()
                inspection_count = int(connection.execute(
                    "SELECT COUNT(*) FROM external_action_session_uses WHERE action='inspect_application_form' AND adapter_kind='browser_companion'"
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
                "real_website_inspections": inspection_count,
                "submit_capability": False,
                "automatic_retry": False,
                "recent_runs": [dict(row) for row in rows],
            }

    def close(self) -> None:
        with self._lock:
            if any(item.status not in TERMINAL_RUN_STATES for item in self._leases.values()):
                self.stop(user_confirmed=True)
