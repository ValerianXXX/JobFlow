from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json, iso_utc, parse_iso, project_root, sha256_bytes


CONTROL_METADATA_KEY = "user_present_intake_control_v1"
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 24 * 60
MIN_AUTHORIZATION_HOURS = 1
MAX_AUTHORIZATION_HOURS = 7 * 24
PAUSE_REASONS = {"USER_PAUSED", "USER_KILL_SWITCH"}


class UserPresentIntakeControl:
    """Persist a bounded local wake plan without creating a scheduler.

    This control is deliberately not an unattended runner.  It records how
    often the user wants to be reminded, whether new intake is paused, and the
    expiry of the user's local-run authorization.  Processing happens only
    when the local UI explicitly calls the manual-run endpoint.
    """

    def __init__(self, database: Any, schema_root: Path | None = None) -> None:
        self.database = database
        self.schemas = schema_root or project_root() / "schemas"

    @staticmethod
    def _now(value: datetime | None = None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _default(now: datetime) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "configured": False,
            "paused": False,
            "pause_reason": None,
            "interval_minutes": None,
            "authorized_until": None,
            "next_user_run_at": None,
            "last_user_run_at": None,
            "generation": 0,
            "updated_at": iso_utc(now),
        }
        value["control_hash"] = UserPresentIntakeControl._hash(value)
        return value

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        return sha256_bytes(canonical_json({key: item for key, item in value.items() if key != "control_hash"}))

    @staticmethod
    def _validate_raw(value: Any) -> dict[str, Any]:
        required = {
            "schema_version", "configured", "paused", "pause_reason",
            "interval_minutes", "authorized_until", "next_user_run_at",
            "last_user_run_at", "generation", "updated_at", "control_hash",
        }
        if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
            raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "The saved local intake control has an invalid structure.")
        if (
            type(value.get("configured")) is not bool
            or type(value.get("paused")) is not bool
            or type(value.get("generation")) is not int
            or int(value["generation"]) < 0
            or value.get("pause_reason") not in {None, *PAUSE_REASONS}
        ):
            raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "The saved local intake control has invalid state values.")
        configured = bool(value["configured"])
        interval = value.get("interval_minutes")
        timestamps = ("authorized_until", "next_user_run_at", "last_user_run_at", "updated_at")
        if configured:
            if type(interval) is not int or not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
                raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "The saved local wake interval is invalid.")
            if not isinstance(value.get("authorized_until"), str) or not isinstance(value.get("next_user_run_at"), str):
                raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "The saved local wake authorization is incomplete.")
        elif interval is not None or value.get("authorized_until") is not None or value.get("next_user_run_at") is not None:
            raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "An unconfigured local wake control contains schedule values.")
        for key in timestamps:
            raw = value.get(key)
            if raw is None and key == "last_user_run_at":
                continue
            if raw is None and key in {"authorized_until", "next_user_run_at"} and not configured:
                continue
            if not isinstance(raw, str):
                raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "A local wake timestamp is invalid.")
            try:
                parse_iso(raw)
            except (TypeError, ValueError) as exc:
                raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "A local wake timestamp is invalid.") from exc
        if value.get("control_hash") != UserPresentIntakeControl._hash(value):
            raise JobOpsError("INTAKE_CONTROL_STATE_CHANGED", "The saved local intake control changed unexpectedly.")
        return dict(value)

    def _load(self, connection: Any, now: datetime) -> dict[str, Any]:
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,)).fetchone()
        if row is None:
            return self._default(now)
        try:
            decoded = json.loads(str(row["value"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise JobOpsError("INTAKE_CONTROL_STATE_INVALID", "The saved local intake control is unreadable.") from exc
        return self._validate_raw(decoded)

    @staticmethod
    def _status(raw: dict[str, Any], now: datetime) -> str:
        if raw["paused"]:
            return "PAUSED"
        if not raw["configured"]:
            return "NOT_CONFIGURED"
        if parse_iso(str(raw["authorized_until"])) <= now:
            return "AUTHORIZATION_EXPIRED"
        if parse_iso(str(raw["next_user_run_at"])) <= now:
            return "DUE"
        return "READY"

    def _public(self, raw: dict[str, Any], now: datetime) -> dict[str, Any]:
        status = self._status(raw, now)
        value = {
            "schema_version": 1,
            "status": status,
            "mode": "USER_PRESENT_MANUAL_WAKE_ONLY",
            "generation": int(raw["generation"]),
            "configured": bool(raw["configured"]),
            "new_intake_allowed": status != "PAUSED",
            "manual_run_allowed": status in {"READY", "DUE"},
            "paused": bool(raw["paused"]),
            "pause_reason": raw["pause_reason"],
            "interval_minutes": raw["interval_minutes"],
            "authorized_until": raw["authorized_until"],
            "next_user_run_at": raw["next_user_run_at"],
            "last_user_run_at": raw["last_user_run_at"],
            "updated_at": raw["updated_at"],
            "requires_explicit_invocation": True,
            "background_service_started": False,
            "system_tasks_registered": 0,
            "browser_actions": 0,
            "network_actions": 0,
            "real_external_actions": 0,
            "control_hash": raw["control_hash"],
        }
        validate_named("user-present-intake-control", value, self.schemas)
        return value

    def state(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = self._now(now)
        with self.database.connect() as connection:
            raw = self._load(connection, current)
        return self._public(raw, current)

    def _save(self, connection: Any, raw: dict[str, Any]) -> None:
        raw["control_hash"] = self._hash(raw)
        self._validate_raw(raw)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CONTROL_METADATA_KEY, canonical_json(raw).decode("utf-8")),
        )

    @staticmethod
    def _event(connection: Any, event_type: str, from_state: str, to_state: str, payload: dict[str, Any], now: datetime) -> None:
        connection.execute(
            "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(NULL,?,?,?,?,?)",
            (event_type, from_state, to_state, canonical_json(payload).decode("utf-8"), iso_utc(now)),
        )

    def configure(
        self,
        *,
        interval_minutes: int,
        authorization_hours: int,
        user_confirmed: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "A local wake plan requires explicit user confirmation.")
        if type(interval_minutes) is not int or not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
            raise JobOpsError("INTAKE_INTERVAL_INVALID", "Choose a local wake interval from 5 to 1440 minutes.")
        if type(authorization_hours) is not int or not MIN_AUTHORIZATION_HOURS <= authorization_hours <= MAX_AUTHORIZATION_HOURS:
            raise JobOpsError("INTAKE_AUTHORIZATION_WINDOW_INVALID", "Choose an authorization window from 1 to 168 hours.")
        current = self._now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._load(connection, current)
            from_state = self._status(previous, current)
            raw = {
                "schema_version": 1,
                "configured": True,
                "paused": False,
                "pause_reason": None,
                "interval_minutes": interval_minutes,
                "authorized_until": iso_utc(current + timedelta(hours=authorization_hours)),
                "next_user_run_at": iso_utc(current + timedelta(minutes=interval_minutes)),
                "last_user_run_at": previous.get("last_user_run_at"),
                "generation": int(previous["generation"]) + 1,
                "updated_at": iso_utc(current),
            }
            self._save(connection, raw)
            self._event(connection, "USER_PRESENT_WAKE_CONFIGURED", from_state, "READY", {
                "generation": raw["generation"],
                "interval_minutes": interval_minutes,
                "authorization_hours": authorization_hours,
                "requires_explicit_invocation": True,
                "system_tasks_registered": 0,
                "real_external_actions": 0,
            }, current)
        return self._public(raw, current)

    def pause(
        self,
        *,
        user_confirmed: bool,
        reason: str = "USER_PAUSED",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Pausing intake requires explicit user confirmation.")
        safe_reason = str(reason).strip().upper()
        if safe_reason not in PAUSE_REASONS:
            raise JobOpsError("INTAKE_PAUSE_REASON_INVALID", "The local intake pause reason is invalid.")
        current = self._now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            from_state = self._status(raw, current)
            raw.update({
                "paused": True,
                "pause_reason": safe_reason,
                "generation": int(raw["generation"]) + 1,
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            self._event(connection, "USER_PRESENT_INTAKE_PAUSED", from_state, "PAUSED", {
                "generation": raw["generation"], "reason": safe_reason,
                "system_tasks_registered": 0, "real_external_actions": 0,
            }, current)
        return self._public(raw, current)

    def resume(self, *, user_confirmed: bool, now: datetime | None = None) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Resuming intake requires explicit user confirmation.")
        current = self._now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            from_state = self._status(raw, current)
            raw.update({
                "paused": False,
                "pause_reason": None,
                "generation": int(raw["generation"]) + 1,
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            to_state = self._status(raw, current)
            self._event(connection, "USER_PRESENT_INTAKE_RESUMED", from_state, to_state, {
                "generation": raw["generation"],
                "requires_explicit_invocation": True,
                "system_tasks_registered": 0, "real_external_actions": 0,
            }, current)
        return self._public(raw, current)

    def assert_new_intake_allowed(self, *, now: datetime | None = None) -> dict[str, Any]:
        value = self.state(now=now)
        if value["status"] == "PAUSED":
            raise JobOpsError(
                "NEW_INTAKE_PAUSED",
                "New job intake is paused. Resume it in the local control panel before starting another job.",
                pause_reason=value["pause_reason"],
            )
        return value

    def assert_manual_run_allowed(self, *, now: datetime | None = None) -> dict[str, Any]:
        value = self.state(now=now)
        code = {
            "NOT_CONFIGURED": "INTAKE_WAKE_NOT_CONFIGURED",
            "PAUSED": "NEW_INTAKE_PAUSED",
            "AUTHORIZATION_EXPIRED": "INTAKE_WAKE_AUTHORIZATION_EXPIRED",
        }.get(str(value["status"]))
        if code:
            raise JobOpsError(code, "The user-present local wake plan is not currently authorized.")
        return value

    def record_manual_run(self, result: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        boundary_keys = (
            "background_service_started", "system_tasks_registered", "browser_actions",
            "network_actions", "real_external_actions",
        )
        try:
            crossed_boundary = not isinstance(result, dict) or any(
                int(result.get(key, 0)) != 0 for key in boundary_keys
            )
        except (TypeError, ValueError):
            crossed_boundary = True
        if crossed_boundary:
            raise JobOpsError("INTAKE_WAKE_RESULT_INVALID", "A local wake result crossed the no-background or no-external-action boundary.")
        current = self._now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = self._load(connection, current)
            from_state = self._status(raw, current)
            if from_state not in {"READY", "DUE"}:
                raise JobOpsError("INTAKE_WAKE_NOT_AUTHORIZED", "The local wake authorization is no longer active.")
            raw.update({
                "last_user_run_at": iso_utc(current),
                "next_user_run_at": iso_utc(current + timedelta(minutes=int(raw["interval_minutes"]))),
                "generation": int(raw["generation"]) + 1,
                "updated_at": iso_utc(current),
            })
            self._save(connection, raw)
            self._event(connection, "USER_PRESENT_LOCAL_WAKE_RAN", from_state, "READY", {
                "generation": raw["generation"],
                "processed_count": int(result.get("processed_count", 0)),
                "prepared_count": int(result.get("prepared_count", 0)),
                "failed_count": int(result.get("failed_count", 0)),
                "requires_explicit_invocation": True,
                "system_tasks_registered": 0,
                "real_external_actions": 0,
            }, current)
        return self._public(raw, current)
