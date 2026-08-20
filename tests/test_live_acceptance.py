from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.live_acceptance import (
    LiveAcceptanceManager,
    normalized_public_https_origin,
    validate_live_acceptance_report,
)
from jobops.util import iso_utc


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def seed_assist(
    database: JobOpsDB,
    *,
    suffix: str = "1",
    origin: str = "https://jobs.acme-careers.com",
    provider: str = "company",
    route_kind: str = "OFFICIAL_DIRECT",
    mode: str = "ASSISTED_USER_PRESENT",
) -> str:
    job_id = f"JOB-LVA-{suffix}"
    application_id = f"APP-LVA-{suffix}"
    session_id = f"EAS-LVA-{suffix}"
    assist_id = f"BA-LVA-{suffix}"
    now = iso_utc(NOW)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, "manual", "redacted", None, "Redacted", "Role", None, "APPROVED", now, now),
        )
        connection.execute(
            "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
            (application_id, job_id, "redacted", "APPROVED", HASH_A, HASH_B, 1, None, "APPROVED", now),
        )
        connection.execute(
            """INSERT INTO external_action_sessions(
            session_id,application_id,application_context_hash,source_route_hash,form_snapshot_hash,
            uploads_hash,site_policy_version,allowed_actions_json,control_generation,mode,bound_hash,
            issued_at,expires_at,nonce,session_version,status,revoked_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                application_id,
                HASH_A,
                HASH_B,
                HASH_A,
                HASH_C,
                "policy-v1",
                "[]",
                1,
                mode,
                HASH_C,
                now,
                iso_utc(NOW + timedelta(hours=1)),
                "synthetic-nonce",
                1,
                "AUTHORIZED",
                None,
            ),
        )
        connection.execute(
            """INSERT INTO browser_assist_runs(
            assist_id,application_id,session_id,allowed_origin,provider,route_kind,current_step,max_steps,
            handoff_kind,last_page_hash,status,prepared_hash,created_at,expires_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                assist_id,
                application_id,
                session_id,
                origin,
                provider,
                route_kind,
                1,
                20,
                None,
                None,
                "READY",
                None,
                now,
                iso_utc(NOW + timedelta(minutes=30)),
                now,
            ),
        )
    return assist_id


class LiveAcceptanceTests(unittest.TestCase):
    def test_public_cli_reports_empty_redacted_evidence_without_overclaim(self) -> None:
        with project_temp() as temp:
            database_path = temp / "live-acceptance.db"
            command = [
                sys.executable,
                str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
                "live-acceptance",
                "--path",
                str(database_path),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "LIVE_ACCEPTANCE_EVIDENCE")
            self.assertEqual(report["current_page_route_evidence_count"], 0)
            self.assertFalse(report["universal_live_compatibility"])
            self.assertEqual(report["final_submit"], "USER_ONLY")
            self.assertEqual(report["next_safe_action"], "run-separately-authorized-user-present-page-acceptance")

    def test_public_origin_normalization_is_lexical_and_fail_closed(self) -> None:
        self.assertEqual(
            normalized_public_https_origin("https://Jobs.Acme-Careers.com:443/"),
            "https://jobs.acme-careers.com",
        )
        for value in (
            "http://jobs.acme-careers.com",
            "https://jobs.acme-careers.com/path",
            "https://jobs.acme-careers.com/?token=value",
            "https://localhost",
            "https://careers.example",
            "https://careers.example.test",
            "https://example.com",
            "https://careers.example.org",
            "https://127.0.0.1",
            "https://10.0.0.1",
            "https://singlelabel",
        ):
            self.assertIsNone(normalized_public_https_origin(value), value)

    def test_public_user_present_run_stores_only_redacted_hash_evidence(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            assist_id = seed_assist(database)
            manager = LiveAcceptanceManager(database)
            started = manager.start_for_assist(assist_id, now=NOW)
            self.assertIsNotNone(started)
            acceptance_id = str(started["acceptance_id"])
            manager.record_stage(
                acceptance_id,
                stage="FORM_ANALYSIS",
                result="PASS",
                evidence_hash=HASH_A,
                page_fingerprint=HASH_C,
                now=NOW + timedelta(seconds=1),
            )
            manager.record_stage(
                acceptance_id,
                stage="PRIVATE_VALUE_FREE_PLAN",
                result="PASS",
                evidence_hash=HASH_B,
                page_fingerprint=HASH_C,
                now=NOW + timedelta(seconds=2),
            )
            completed = manager.finish(
                acceptance_id,
                status="PRE_SUBMIT_VERIFIED",
                now=NOW + timedelta(seconds=3),
            )
            self.assertEqual(completed["status"], "PRE_SUBMIT_VERIFIED")
            with database.connect() as connection:
                run = dict(connection.execute(
                    "SELECT * FROM live_acceptance_runs WHERE acceptance_id=?",
                    (acceptance_id,),
                ).fetchone())
                events = [dict(row) for row in connection.execute(
                    "SELECT * FROM live_acceptance_events WHERE acceptance_id=? ORDER BY event_id",
                    (acceptance_id,),
                ).fetchall()]
            serialized = json.dumps({"run": run, "events": events}, sort_keys=True)
            self.assertNotIn("acme-careers", serialized)
            self.assertNotIn("https://", serialized)
            self.assertEqual(run["final_submit_actions"], 0)
            self.assertEqual(run["automatic_retries"], 0)
            self.assertEqual(run["private_values_persisted"], 0)
            self.assertEqual(run["page_text_persisted"], 0)
            self.assertEqual([item["stage"] for item in events], [
                "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN",
            ])

            report = manager.report(now=NOW + timedelta(minutes=1))
            company = report["providers"][0]
            self.assertEqual(company["current_page_route_runs"], 1)
            self.assertEqual(company["pre_submit_verified_runs"], 1)
            self.assertEqual(company["passed_stages"], [
                "ROUTE_BINDING", "FORM_ANALYSIS", "PRIVATE_VALUE_FREE_PLAN",
            ])
            self.assertFalse(report["universal_live_compatibility"])
            self.assertEqual(report["final_submit"], "USER_ONLY")
            validate_live_acceptance_report(report)
            report["current_page_route_evidence_count"] = 99
            with self.assertRaises(JobOpsError):
                validate_live_acceptance_report(report)

    def test_nonpublic_or_nonproduction_assist_never_creates_live_evidence(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = LiveAcceptanceManager(database)
            cases = (
                ("localhost", "https://localhost", "ASSISTED_USER_PRESENT"),
                ("reserved", "https://careers.example.test", "ASSISTED_USER_PRESENT"),
                ("reserved-example", "https://jobs.example.com", "ASSISTED_USER_PRESENT"),
                ("path", "https://jobs.acme-careers.com/private/path", "ASSISTED_USER_PRESENT"),
                ("fake", "https://jobs.acme-careers.com", "ISOLATED_FAKE"),
            )
            for suffix, origin, mode in cases:
                assist_id = seed_assist(database, suffix=suffix, origin=origin, mode=mode)
                self.assertIsNone(manager.start_for_assist(assist_id, now=NOW))
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM live_acceptance_runs").fetchone()[0], 0)

    def test_events_are_idempotent_append_only_and_safety_counters_are_database_locked(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            acceptance = LiveAcceptanceManager(database).start_for_assist(seed_assist(database), now=NOW)
            acceptance_id = str(acceptance["acceptance_id"])
            manager = LiveAcceptanceManager(database)
            for _ in range(2):
                manager.record_stage(
                    acceptance_id,
                    stage="APPROVED_DOM_PREFILL",
                    result="PASS",
                    evidence_hash=HASH_A,
                    page_fingerprint=HASH_B,
                    now=NOW + timedelta(seconds=1),
                )
            with database.connect() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM live_acceptance_events WHERE stage='APPROVED_DOM_PREFILL'"
                ).fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE live_acceptance_runs SET route_identity_hash=? WHERE acceptance_id=?",
                        (HASH_C, acceptance_id),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE live_acceptance_events SET result='FAIL' WHERE acceptance_id=?",
                        (acceptance_id,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE live_acceptance_runs SET final_submit_actions=1 WHERE acceptance_id=?",
                        (acceptance_id,),
                    )

    def test_expiry_removes_evidence_from_current_provider_acceptance(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = LiveAcceptanceManager(database)
            acceptance = manager.start_for_assist(seed_assist(database), now=NOW)
            acceptance_id = str(acceptance["acceptance_id"])
            report = manager.report(now=NOW + timedelta(days=31))
            company = report["providers"][0]
            self.assertEqual(company["current_page_route_runs"], 0)
            self.assertEqual(company["expired_page_route_runs"], 1)
            with self.assertRaises(JobOpsError) as caught:
                manager.record_stage(
                    acceptance_id,
                    stage="FORM_ANALYSIS",
                    result="PASS",
                    evidence_hash=HASH_A,
                    now=NOW + timedelta(days=31),
                )
            self.assertEqual(caught.exception.code, "LIVE_ACCEPTANCE_EXPIRED")

    def test_result_observation_requires_pre_submit_checkpoint_and_no_retry(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = LiveAcceptanceManager(database)
            acceptance = manager.start_for_assist(seed_assist(database), now=NOW)
            acceptance_id = str(acceptance["acceptance_id"])
            with self.assertRaises(JobOpsError):
                manager.finish(acceptance_id, status="RESULT_OBSERVED", now=NOW)
            manager.finish(acceptance_id, status="PRE_SUBMIT_VERIFIED", now=NOW)
            manager.record_stage(
                acceptance_id,
                stage="RESULT_OBSERVATION",
                result="PASS",
                evidence_hash=HASH_C,
                now=NOW + timedelta(seconds=1),
            )
            completed = manager.finish(
                acceptance_id,
                status="RESULT_OBSERVED",
                now=NOW + timedelta(seconds=2),
            )
            self.assertEqual(completed["status"], "RESULT_OBSERVED")
            with self.assertRaises(JobOpsError):
                manager.record_stage(
                    acceptance_id,
                    stage="RESULT_OBSERVATION",
                    result="PASS",
                    evidence_hash=HASH_A,
                    now=NOW + timedelta(seconds=3),
                )


if __name__ == "__main__":
    unittest.main()
