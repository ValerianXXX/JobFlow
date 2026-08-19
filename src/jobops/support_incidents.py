from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import iso_utc, load_json, write_json


MAX_SUPPORT_INCIDENTS = 32
SUPPORT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
SUPPORT_VERSION_RE = re.compile(r"^\d{1,4}(?:\.\d{1,4}){1,3}$")
SUPPORT_INCIDENT_SOURCES = frozenset({
    "UI_API_ERROR",
    "WINDOW_ERROR",
    "UNHANDLED_REJECTION",
    "COMPANION_EVENT",
})


class SupportIncidentStore:
    """Keep a bounded, value-free local incident history after explicit opt-in.

    The store accepts fixed codes and protocol metadata only.  It deliberately
    has no fields for messages, stack traces, URLs, paths, applicant values,
    browser content, or credentials, and it never performs network I/O.
    """

    def __init__(self, path: Path, schemas: Path, *, ui_protocol: int) -> None:
        self.path = path
        self.schemas = schemas
        self.ui_protocol = int(ui_protocol)

    @staticmethod
    def _empty(*, enabled: bool = False) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "SUPPORT_INCIDENT_CAPTURE_ENABLED" if enabled else "SUPPORT_INCIDENT_CAPTURE_DISABLED",
            "enabled": enabled,
            "updated_at": iso_utc(),
            "records": [],
            "automatic_transmission": False,
            "private_values_read": 0,
            "private_values_emitted": 0,
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            value = load_json(self.path)
            validate_named("support-incident-state", value, self.schemas)
        except Exception as exc:
            raise JobOpsError(
                "SUPPORT_INCIDENT_STATE_INVALID",
                "The local support incident state is invalid and recording is disabled until the user resets it.",
            ) from exc
        if len(value["records"]) > MAX_SUPPORT_INCIDENTS:
            raise JobOpsError(
                "SUPPORT_INCIDENT_STATE_INVALID",
                "The local support incident state exceeds its fixed retention limit.",
            )
        expected_status = "SUPPORT_INCIDENT_CAPTURE_ENABLED" if value["enabled"] else "SUPPORT_INCIDENT_CAPTURE_DISABLED"
        sequences = [int(item["sequence"]) for item in value["records"]]
        if value["status"] != expected_status or sequences != sorted(set(sequences)):
            raise JobOpsError(
                "SUPPORT_INCIDENT_STATE_INVALID",
                "The local support incident state is internally inconsistent.",
            )
        return value

    def _write(self, value: dict[str, Any]) -> None:
        validate_named("support-incident-state", value, self.schemas)
        if len(value["records"]) > MAX_SUPPORT_INCIDENTS:
            raise JobOpsError(
                "SUPPORT_INCIDENT_STATE_INVALID",
                "The local support incident state exceeds its fixed retention limit.",
            )
        write_json(self.path, value)

    def public_state(self) -> dict[str, Any]:
        try:
            value = self._read()
        except JobOpsError:
            return {
                "status": "SUPPORT_INCIDENT_STATE_REPAIR_REQUIRED",
                "enabled": False,
                "record_count": 0,
                "last_error_code": None,
                "automatic_transmission": False,
                "private_values_read": 0,
                "private_values_emitted": 0,
            }
        records = value["records"]
        return {
            "status": value["status"],
            "enabled": bool(value["enabled"]),
            "record_count": len(records),
            "last_error_code": records[-1]["code"] if records else None,
            "automatic_transmission": False,
            "private_values_read": 0,
            "private_values_emitted": 0,
        }

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"enabled", "user_confirmed"}:
            raise JobOpsError(
                "SUPPORT_INCIDENT_SETTINGS_INVALID",
                "Support incident settings require only enabled and user_confirmed fields.",
            )
        if type(payload["enabled"]) is not bool or payload["user_confirmed"] is not True:
            raise JobOpsError(
                "EXPLICIT_CONFIRMATION_REQUIRED",
                "Changing local incident capture requires explicit user confirmation.",
            )
        try:
            value = self._read()
        except JobOpsError:
            value = self._empty()
        enabled = bool(payload["enabled"])
        value["enabled"] = enabled
        value["status"] = "SUPPORT_INCIDENT_CAPTURE_ENABLED" if enabled else "SUPPORT_INCIDENT_CAPTURE_DISABLED"
        value["updated_at"] = iso_utc()
        self._write(value)
        return self.public_state()

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"code", "source", "ui_protocol", "observed_companion_version"}
        if not isinstance(payload, dict) or set(payload) != allowed:
            raise JobOpsError(
                "SUPPORT_INCIDENT_INPUT_INVALID",
                "A support incident must contain only the fixed diagnostic fields.",
            )
        code = payload.get("code")
        source = payload.get("source")
        ui_protocol = payload.get("ui_protocol")
        version = payload.get("observed_companion_version")
        if (
            not isinstance(code, str)
            or SUPPORT_CODE_RE.fullmatch(code) is None
            or source not in SUPPORT_INCIDENT_SOURCES
            or type(ui_protocol) is not int
            or ui_protocol != self.ui_protocol
            or (version is not None and (not isinstance(version, str) or SUPPORT_VERSION_RE.fullmatch(version) is None))
        ):
            raise JobOpsError(
                "SUPPORT_INCIDENT_INPUT_INVALID",
                "A support incident contains an invalid fixed diagnostic value.",
            )
        try:
            value = self._read()
        except JobOpsError:
            return {
                "status": "SUPPORT_INCIDENT_IGNORED_STATE_INVALID",
                "recorded": False,
                "automatic_transmission": False,
                "private_values_read": 0,
                "private_values_emitted": 0,
            }
        if value["enabled"] is not True:
            return {
                "status": "SUPPORT_INCIDENT_IGNORED_DISABLED",
                "recorded": False,
                "automatic_transmission": False,
                "private_values_read": 0,
                "private_values_emitted": 0,
            }
        records = value["records"]
        occurred_at = iso_utc()
        identity = (code, source, ui_protocol, version)
        if records and (
            records[-1]["code"],
            records[-1]["source"],
            records[-1]["ui_protocol"],
            records[-1]["observed_companion_version"],
        ) == identity:
            records[-1]["occurred_at"] = occurred_at
            records[-1]["occurrences"] = min(9999, int(records[-1]["occurrences"]) + 1)
        else:
            next_sequence = int(records[-1]["sequence"]) + 1 if records else 1
            records.append({
                "sequence": next_sequence,
                "code": code,
                "source": source,
                "occurred_at": occurred_at,
                "ui_protocol": ui_protocol,
                "observed_companion_version": version,
                "occurrences": 1,
            })
            value["records"] = records[-MAX_SUPPORT_INCIDENTS:]
        value["updated_at"] = occurred_at
        self._write(value)
        return {
            "status": "SUPPORT_INCIDENT_RECORDED",
            "recorded": True,
            "record_count": len(value["records"]),
            "automatic_transmission": False,
            "private_values_read": 0,
            "private_values_emitted": 0,
        }

    def clear(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"user_confirmed"} or payload.get("user_confirmed") is not True:
            raise JobOpsError(
                "EXPLICIT_CONFIRMATION_REQUIRED",
                "Clearing local incident history requires explicit user confirmation.",
            )
        try:
            enabled = bool(self._read()["enabled"])
        except JobOpsError:
            enabled = False
        self._write(self._empty(enabled=enabled))
        return self.public_state()

    def diagnostic_summary(self) -> dict[str, Any]:
        try:
            value = self._read()
        except JobOpsError:
            return {
                "status": "SUPPORT_INCIDENT_STATE_REPAIR_REQUIRED",
                "enabled": False,
                "record_count": 0,
                "recent": [],
                "automatic_transmission": False,
                "private_values_read": 0,
                "private_values_emitted": 0,
            }
        return {
            "status": value["status"],
            "enabled": bool(value["enabled"]),
            "record_count": len(value["records"]),
            "recent": deepcopy(value["records"][-16:]),
            "automatic_transmission": False,
            "private_values_read": 0,
            "private_values_emitted": 0,
        }
