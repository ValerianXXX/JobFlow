from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .db import JobOpsDB
from .errors import JobOpsError
from .util import canonical_json, iso_utc, sha256_bytes


OFFLINE_KINDS = frozenset({"fake", "mock", "dry-run", "disabled"})


class OfficialSourceAdapter(Protocol):
    def discover(self, request: dict[str, Any]) -> dict[str, Any]: ...


class BrowserPrefillAdapter(Protocol):
    def prefill(self, request: dict[str, Any]) -> dict[str, Any]: ...


class SubmissionAdapter(Protocol):
    def submit(self, request: dict[str, Any]) -> dict[str, Any]: ...


class AccountCreationAdapter(Protocol):
    def create_account(self, request: dict[str, Any]) -> dict[str, Any]: ...


class EmailAdapter(Protocol):
    def send_email(self, request: dict[str, Any]) -> dict[str, Any]: ...


class RecruiterContactAdapter(Protocol):
    def contact(self, request: dict[str, Any]) -> dict[str, Any]: ...


class SchedulerAdapter(Protocol):
    def schedule(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ReceiptAdapter(Protocol):
    def verify(self, request: dict[str, Any]) -> dict[str, Any]: ...


def _safe_application_id(request: dict[str, Any]) -> str | None:
    value = request.get("application_id")
    return str(value) if value else None


@dataclass
class DisabledAdapter:
    capability: str
    database: JobOpsDB | None = None
    kind: str = "disabled"

    def _deny(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.database is not None:
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO external_action_attempts(attempt_id,application_id,action,adapter_kind,result_code,context_hash,real_side_effect,created_at) VALUES(?,?,?,?,?,?,0,?)",
                    (
                        f"ATT-{uuid.uuid4().hex}", _safe_application_id(request), action, self.kind,
                        "PHASE_NOT_AUTHORIZED", sha256_bytes(canonical_json(request)), iso_utc(),
                    ),
                )
        raise JobOpsError(
            "PHASE_NOT_AUTHORIZED",
            "Real external actions are not authorized or operational in this build.",
            capability=self.capability,
        )

    def discover(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("discover_official_jobs", request)
    def prefill(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("prefill_real_form", request)
    def submit(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("submit_application", request)
    def create_account(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("create_recruiting_account", request)
    def send_email(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("send_email", request)
    def contact(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("contact_recruiter", request)
    def schedule(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("register_system_schedule", request)
    def verify(self, request: dict[str, Any]) -> dict[str, Any]: return self._deny("verify_real_receipt", request)


@dataclass
class FakeOfficialSourceAdapter:
    fixture_root: Path
    kind: str = "fake"

    def discover(self, request: dict[str, Any]) -> dict[str, Any]:
        name = Path(str(request.get("fixture", ""))).name
        path = (self.fixture_root / name).resolve()
        if path.parent != self.fixture_root.resolve() or not path.is_file():
            raise JobOpsError("LOCAL_FIXTURE_REQUIRED", "Fake discovery only accepts an existing local fixture filename.")
        return {"status": "LOCAL_FIXTURE_LOADED", "fixture": name, "content_sha256": sha256_bytes(path.read_bytes()), "network_actions": 0}


@dataclass
class FakeBrowserPrefillAdapter:
    kind: str = "fake"

    def prefill(self, request: dict[str, Any]) -> dict[str, Any]:
        fields = list(request.get("fields", []))
        if any(item.get("action") == "SUBMIT" or item.get("classification") == "final_submit_stop" for item in fields):
            raise JobOpsError("FINAL_SUBMIT_BLOCKED", "The fake browser never activates final submit controls.")
        return {"status": "FAKE_PREFILL_COMPLETE", "field_count": len(fields), "uploaded_files": [], "network_actions": 0, "real_side_effects": 0}


@dataclass
class FakeSubmissionAdapter:
    database: JobOpsDB
    kind: str = "fake"

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("isolation_policy") != "ISOLATED_FAKE_ONLY":
            raise JobOpsError("FAKE_ISOLATION_REQUIRED", "Synthetic submission requires the explicit isolated fake policy.")
        attempt_id = f"ATT-{uuid.uuid4().hex}"
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO external_action_attempts(attempt_id,application_id,action,adapter_kind,result_code,context_hash,real_side_effect,created_at) VALUES(?,?,?,?,?,?,0,?)",
                (attempt_id, _safe_application_id(request), "submit_application", self.kind, "FAKE_SUBMISSION_RECORDED", sha256_bytes(canonical_json(request)), iso_utc()),
            )
        return {"status": "FAKE_SUBMISSION_RECORDED", "attempt_id": attempt_id, "real_side_effects": 0, "confirmation": None}


@dataclass
class FakeOutboxAdapter:
    messages: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "fake"

    def send_email(self, request: dict[str, Any]) -> dict[str, Any]:
        item = {"message_id": f"MSG-{uuid.uuid4().hex}", "payload_hash": sha256_bytes(canonical_json(request)), "created_at": iso_utc()}
        self.messages.append(item)
        return {"status": "FAKE_OUTBOX_ONLY", **item, "real_side_effects": 0}

    def contact(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.send_email({**request, "kind": "recruiter_contact"})


@dataclass
class FakeReceiptAdapter:
    kind: str = "fake"

    def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("source") != "fake-receipt" or not request.get("confirmation_number"):
            return {"status": "SUBMISSION_UNKNOWN", "verified": False, "real_side_effects": 0}
        evidence = {"source": "fake-receipt", "confirmation_number": str(request["confirmation_number"]), "received_at": request.get("received_at", iso_utc())}
        return {"status": "CONFIRMED", "verified": True, "receipt_hash": sha256_bytes(canonical_json(evidence)), "real_side_effects": 0}


@dataclass
class AdapterRegistry:
    adapters: dict[str, Any]

    @classmethod
    def offline_only(cls, *, database: JobOpsDB, fixture_root: Path) -> "AdapterRegistry":
        return cls({
            "official_source": FakeOfficialSourceAdapter(fixture_root),
            "browser_prefill": FakeBrowserPrefillAdapter(),
            "submission": DisabledAdapter("submission", database),
            "account_creation": DisabledAdapter("account_creation", database),
            "email": DisabledAdapter("email", database),
            "recruiter_contact": DisabledAdapter("recruiter_contact", database),
            "scheduler": DisabledAdapter("system_scheduler", database),
            "receipt": FakeReceiptAdapter(),
        })

    def resolve(self, capability: str) -> Any:
        if capability not in self.adapters:
            raise JobOpsError("ADAPTER_NOT_REGISTERED", "Only offline fake, mock, dry-run, and disabled adapters are registered.", capability=capability)
        adapter = self.adapters[capability]
        if getattr(adapter, "kind", None) not in OFFLINE_KINDS:
            raise JobOpsError("REAL_ADAPTER_FORBIDDEN", "A non-offline adapter cannot be registered in this build.")
        return adapter

    def manifest(self) -> dict[str, str]:
        return {name: str(getattr(adapter, "kind", "unknown")) for name, adapter in sorted(self.adapters.items())}


def audit_real_external_actions(database: JobOpsDB) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS attempts, COALESCE(SUM(real_side_effect),0) AS real FROM external_action_attempts").fetchone()
    return {
        "attempt_count": int(row["attempts"]),
        "real_external_actions": int(row["real"]),
        "status": "PASS" if int(row["real"]) == 0 else "FAIL",
        "evidence": "append-only external_action_attempts",
    }
