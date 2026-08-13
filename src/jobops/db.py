from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .claims import validate_claim_shape
from .errors import JobOpsError
from .state_machine import BLOCKING_STATES, assert_transition
from .util import iso_utc


LATEST_SCHEMA_VERSION = 3


MIGRATION_001_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id TEXT PRIMARY KEY, classification TEXT NOT NULL, resolved_path TEXT NOT NULL,
    content_fingerprint TEXT, verified_at TEXT NOT NULL, read_only INTEGER NOT NULL CHECK(read_only = 1)
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY, raw_fact TEXT NOT NULL, allowed_wording_json TEXT NOT NULL,
    forbidden_wording_json TEXT NOT NULL, responsibility_boundary_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL, source_refs_json TEXT NOT NULL,
    approved_for_external INTEGER NOT NULL CHECK(approved_for_external IN (0,1)),
    sensitivity TEXT NOT NULL, last_verified_at TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_locator TEXT NOT NULL, official_url TEXT,
    company TEXT NOT NULL, title TEXT NOT NULL, location TEXT, status TEXT NOT NULL,
    discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jd_snapshots (
    snapshot_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
    content_hash TEXT NOT NULL UNIQUE, snapshot_path TEXT NOT NULL, captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id), site TEXT NOT NULL,
    status TEXT NOT NULL, resume_hash TEXT, answers_hash TEXT,
    dry_run INTEGER NOT NULL CHECK(dry_run = 1), secure_profile_ref TEXT,
    last_safe_state TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    job_id TEXT NOT NULL, site TEXT NOT NULL, resume_hash TEXT NOT NULL, answers_hash TEXT NOT NULL,
    bound_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL, external_actions_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
    material_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    kind TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT NOT NULL, claim_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(application_id, kind, content_hash)
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, event_type TEXT NOT NULL,
    from_state TEXT, to_state TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    kind TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, UNIQUE(application_id, kind, due_at)
);
CREATE TABLE IF NOT EXISTS source_routes (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), company_domain TEXT NOT NULL,
    official_entry_url TEXT NOT NULL, current_url TEXT NOT NULL, route_kind TEXT NOT NULL,
    guest_mode TEXT NOT NULL, account_action TEXT NOT NULL, route_json TEXT NOT NULL, verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_settings (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    pending_approval_limit INTEGER NOT NULL CHECK(pending_approval_limit >= 1),
    continue_after_awaiting_approval INTEGER NOT NULL CHECK(continue_after_awaiting_approval = 1), updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_events_application ON events(application_id, event_id);
INSERT OR IGNORE INTO queue_settings(singleton_id,pending_approval_limit,continue_after_awaiting_approval,updated_at)
VALUES(1,10,1,datetime('now'));
"""


MIGRATION_002_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS application_bindings (
    application_id TEXT PRIMARY KEY REFERENCES applications(application_id) ON DELETE CASCADE,
    context_hash TEXT NOT NULL, context_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_packets (
    packet_id TEXT PRIMARY KEY, application_id TEXT NOT NULL UNIQUE REFERENCES applications(application_id),
    content_hash TEXT NOT NULL, relative_path TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_reservations (
    reservation_id TEXT PRIMARY KEY, intake_key TEXT NOT NULL UNIQUE,
    application_id TEXT UNIQUE REFERENCES applications(application_id),
    status TEXT NOT NULL CHECK(status IN ('RESERVED','CONSUMED','RELEASED')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
    event_type TEXT NOT NULL, claim_content_hash TEXT NOT NULL, source_hashes_json TEXT NOT NULL,
    responsibility_boundary_json TEXT NOT NULL, allowed_uses_json TEXT NOT NULL,
    sensitivity TEXT NOT NULL, occurred_at TEXT NOT NULL, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS external_action_attempts (
    attempt_id TEXT PRIMARY KEY, application_id TEXT, action TEXT NOT NULL,
    adapter_kind TEXT NOT NULL, result_code TEXT NOT NULL, context_hash TEXT,
    real_side_effect INTEGER NOT NULL CHECK(real_side_effect = 0), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS private_refs (
    secure_ref TEXT PRIMARY KEY, kind TEXT NOT NULL, display_name TEXT NOT NULL,
    ciphertext_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL, version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED','DELETED','CORRUPT')),
    synthetic INTEGER NOT NULL CHECK(synthetic IN (0,1)),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_events (
    recovery_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    blocked_state TEXT NOT NULL, last_safe_state TEXT NOT NULL, validation_hash TEXT NOT NULL,
    decision TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    confirmation_type TEXT NOT NULL, confirmation_hash TEXT NOT NULL, verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_analyses (
    analysis_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    snapshot_hash TEXT NOT NULL, analysis_json TEXT NOT NULL, analysis_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_findings (
    finding_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
    snapshot_hash TEXT NOT NULL, finding_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_fields (
    field_id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(application_id),
    classification TEXT NOT NULL, status TEXT NOT NULL, secure_ref TEXT, redacted_summary TEXT,
    field_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_queue_reservations_status ON queue_reservations(status);
CREATE INDEX IF NOT EXISTS idx_approvals_application_status ON approvals(application_id,status);
CREATE INDEX IF NOT EXISTS idx_action_attempts_application ON external_action_attempts(application_id,created_at);
CREATE TRIGGER IF NOT EXISTS events_append_only_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS claim_events_append_only_update BEFORE UPDATE ON claim_events
BEGIN SELECT RAISE(ABORT, 'claim_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS claim_events_append_only_delete BEFORE DELETE ON claim_events
BEGIN SELECT RAISE(ABORT, 'claim_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_attempts_append_only_update BEFORE UPDATE ON external_action_attempts
BEGIN SELECT RAISE(ABORT, 'external_action_attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_attempts_append_only_delete BEFORE DELETE ON external_action_attempts
BEGIN SELECT RAISE(ABORT, 'external_action_attempts are append-only'); END;
"""


MIGRATION_003_SQL = """
CREATE TABLE IF NOT EXISTS intake_queue (
    intake_key TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_locator TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DEFERRED','RESERVED','ACCEPTED','CLOSED')),
    reservation_id TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_queue_status_created ON intake_queue(status,created_at);
"""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _column_names(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


class JobOpsDB:
    LATEST_SCHEMA_VERSION = LATEST_SCHEMA_VERSION

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate_1_to_2(self, connection: sqlite3.Connection) -> None:
        for definition in (
            "context_hash TEXT", "context_json TEXT", "jd_snapshot_hash TEXT", "jd_freshness_hash TEXT",
            "source_route_hash TEXT", "canonical_url TEXT", "ats_tenant TEXT", "ats_board TEXT",
            "ats_job_identity TEXT", "profile_version TEXT", "claim_set_hash TEXT", "form_snapshot_hash TEXT",
            "review_packet_hash TEXT", "uploads_json TEXT", "site_policy_version TEXT", "nonce TEXT",
            "approval_version INTEGER", "issued_at TEXT", "consumed_at TEXT",
        ):
            _add_column(connection, "approvals", definition)
        for definition in ("lifecycle_status TEXT DEFAULT 'proposed'", "content_hash TEXT", "version INTEGER DEFAULT 1"):
            _add_column(connection, "claims", definition)
        for definition in ("route_hash TEXT", "ats_tenant TEXT", "ats_board TEXT", "ats_job_identity TEXT"):
            _add_column(connection, "source_routes", definition)
        connection.executescript(MIGRATION_002_TABLES_SQL)
        connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','2')")

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.connect() as connection:
            has_metadata = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone()
            if not has_metadata:
                connection.executescript(MIGRATION_001_SQL)
                connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','1')")
                applied.append(1)
            row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if row is None:
                connection.execute("INSERT INTO metadata(key,value) VALUES('schema_version','1')")
                version = 1
            else:
                version = int(row[0])
            if version > self.LATEST_SCHEMA_VERSION:
                raise JobOpsError("DATABASE_VERSION_UNSUPPORTED", "Database schema is newer than this JobOps build.", version=version)
            if version == 1:
                self._migrate_1_to_2(connection)
                applied.append(2)
                version = 2
            if version == 2:
                connection.executescript(MIGRATION_003_SQL)
                connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','3')")
                applied.append(3)
        return applied

    def initialize(self) -> None:
        self.migrate()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO queue_settings(singleton_id,pending_approval_limit,continue_after_awaiting_approval,updated_at) VALUES(1,10,1,?)",
                (iso_utc(),),
            )

    def schema_version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0])

    def sync_knowledge_sources(self, sources: tuple[dict[str, object], ...], fingerprints: dict[str, str]) -> None:
        with self.connect() as connection:
            for source in sources:
                source_id = str(source["source_id"])
                connection.execute(
                    """INSERT INTO knowledge_sources(source_id,classification,resolved_path,content_fingerprint,verified_at,read_only)
                    VALUES(?,?,?,?,?,1) ON CONFLICT(source_id) DO UPDATE SET
                    classification=excluded.classification,resolved_path=excluded.resolved_path,
                    content_fingerprint=excluded.content_fingerprint,verified_at=excluded.verified_at,read_only=1""",
                    (source_id, source["classification"], f"$KNOWLEDGE_ROOT/{source_id}", fingerprints.get(source_id), iso_utc()),
                )

    def upsert_claim(self, claim: dict[str, Any]) -> None:
        validate_claim_shape(claim)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO claims(
                claim_id,raw_fact,allowed_wording_json,forbidden_wording_json,responsibility_boundary_json,
                evidence_json,source_refs_json,approved_for_external,sensitivity,last_verified_at,expires_at,updated_at,
                lifecycle_status,content_hash,version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(claim_id) DO UPDATE SET
                raw_fact=excluded.raw_fact,allowed_wording_json=excluded.allowed_wording_json,
                forbidden_wording_json=excluded.forbidden_wording_json,
                responsibility_boundary_json=excluded.responsibility_boundary_json,
                evidence_json=excluded.evidence_json,source_refs_json=excluded.source_refs_json,
                approved_for_external=excluded.approved_for_external,sensitivity=excluded.sensitivity,
                last_verified_at=excluded.last_verified_at,expires_at=excluded.expires_at,
                updated_at=excluded.updated_at,lifecycle_status=excluded.lifecycle_status,
                content_hash=excluded.content_hash,version=claims.version+1""",
                (
                    claim["claim_id"], claim["raw_fact"], json.dumps(claim["allowed_wording"], ensure_ascii=False),
                    json.dumps(claim["forbidden_wording"], ensure_ascii=False),
                    json.dumps(claim["responsibility_boundary"], ensure_ascii=False),
                    json.dumps(claim["evidence"], ensure_ascii=False), json.dumps(claim["source_refs"], ensure_ascii=False),
                    int(claim["approved_for_external"]), claim["sensitivity"], claim["last_verified_at"],
                    claim["expires_at"], iso_utc(), claim.get("lifecycle_status", "approved" if claim["approved_for_external"] else "proposed"),
                    claim.get("content_hash"), int(claim.get("version", 1)),
                ),
            )

    def transition_application(self, application_id: str, target: str, payload: dict[str, Any] | None = None) -> None:
        if target in {"APPROVED", "SUBMITTING", "SUBMITTED", "CONFIRMED"}:
            raise JobOpsError("EXTERNAL_GATEWAY_REQUIRED", "Protected external-action states may only be entered through ExternalActionGateway.", target=target)
        with self.connect() as connection:
            row = connection.execute("SELECT status,last_safe_state FROM applications WHERE application_id=?", (application_id,)).fetchone()
            if row is None:
                raise KeyError(application_id)
            current = str(row["status"])
            assert_transition(current, target)
            last_safe = str(row["last_safe_state"]) if target in BLOCKING_STATES else target
            now = iso_utc()
            connection.execute("UPDATE applications SET status=?,last_safe_state=?,updated_at=? WHERE application_id=?", (target, last_safe, now, application_id))
            if target in {"SITE_CHANGED", "APPROVAL_EXPIRED", "MATERIALS_NEEDS_CORRECTION"}:
                connection.execute(
                    "UPDATE approvals SET status='INVALIDATED' WHERE application_id=? AND status='APPROVED'",
                    (application_id,),
                )
                connection.execute(
                    "UPDATE review_packets SET status='NEEDS_REVISION' WHERE application_id=? AND status IN ('AWAITING_APPROVAL','APPROVED')",
                    (application_id,),
                )
            connection.execute(
                "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (application_id, "STATE_TRANSITION", current, target, json.dumps(payload or {}, ensure_ascii=False), now),
            )

    def table_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            tables = [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}

    def set_pending_limit(self, limit: int) -> None:
        from .queueing import validate_pending_limit
        validate_pending_limit(limit)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            awaiting = int(connection.execute("SELECT COUNT(*) FROM applications WHERE status='AWAITING_APPROVAL'").fetchone()[0])
            reserved = int(connection.execute("SELECT COUNT(*) FROM queue_reservations WHERE status='RESERVED'").fetchone()[0])
            active = awaiting + reserved
            if limit < active:
                raise JobOpsError(
                    "PENDING_LIMIT_BELOW_ACTIVE",
                    "The pending limit cannot be lower than the number of occupied approval slots.",
                    occupied_slots=active,
                )
            connection.execute("UPDATE queue_settings SET pending_approval_limit=?,updated_at=? WHERE singleton_id=1", (limit, iso_utc()))

    def pending_queue_decision(self):
        from .queueing import queue_decision
        with self.connect() as connection:
            setting = connection.execute("SELECT pending_approval_limit FROM queue_settings WHERE singleton_id=1").fetchone()
            pending = connection.execute("SELECT COUNT(*) FROM applications WHERE status='AWAITING_APPROVAL'").fetchone()[0]
        return queue_decision(int(pending), int(setting[0]))

    def record_source_route(self, job_id: str, route: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO source_routes(job_id,company_domain,official_entry_url,current_url,route_kind,guest_mode,account_action,route_json,verified_at,route_hash,ats_tenant,ats_board,ats_job_identity)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                company_domain=excluded.company_domain,official_entry_url=excluded.official_entry_url,
                current_url=excluded.current_url,route_kind=excluded.route_kind,guest_mode=excluded.guest_mode,
                account_action=excluded.account_action,route_json=excluded.route_json,verified_at=excluded.verified_at,
                route_hash=excluded.route_hash,ats_tenant=excluded.ats_tenant,ats_board=excluded.ats_board,ats_job_identity=excluded.ats_job_identity""",
                (
                    job_id, route["company_domain"], route["official_entry_url"], route["current_url"], route["route_kind"],
                    route["guest_mode"], route["account_action"], json.dumps(route, ensure_ascii=False), iso_utc(),
                    route.get("route_hash"), route.get("ats_tenant"), route.get("ats_board"), route.get("ats_job_identity"),
                ),
            )
