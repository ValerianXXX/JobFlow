from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.intake_control import CONTROL_METADATA_KEY, UserPresentIntakeControl


START = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class UserPresentIntakeControlTests(unittest.TestCase):
    def test_configuration_persists_and_time_only_changes_public_due_state(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            control = UserPresentIntakeControl(database, PROJECT / "schemas")

            initial = control.state(now=START)
            self.assertEqual(initial["status"], "NOT_CONFIGURED")
            self.assertTrue(initial["new_intake_allowed"])
            self.assertFalse(initial["manual_run_allowed"])
            self.assertFalse(initial["background_service_started"])
            self.assertEqual(initial["system_tasks_registered"], 0)

            configured = control.configure(
                interval_minutes=30, authorization_hours=24,
                user_confirmed=True, now=START,
            )
            self.assertEqual(configured["status"], "READY")
            self.assertEqual(configured["interval_minutes"], 30)
            self.assertTrue(configured["manual_run_allowed"])
            self.assertEqual(configured["next_user_run_at"], "2026-08-18T12:30:00Z")

            restarted = UserPresentIntakeControl(database, PROJECT / "schemas")
            self.assertEqual(restarted.state(now=START + timedelta(minutes=29))["status"], "READY")
            self.assertEqual(restarted.state(now=START + timedelta(minutes=30))["status"], "DUE")
            expired = restarted.state(now=START + timedelta(hours=24))
            self.assertEqual(expired["status"], "AUTHORIZATION_EXPIRED")
            self.assertFalse(expired["manual_run_allowed"])
            self.assertTrue(expired["new_intake_allowed"])

            with database.connect() as connection:
                events = connection.execute(
                    "SELECT event_type,payload_json FROM events WHERE event_type='USER_PRESENT_WAKE_CONFIGURED'"
                ).fetchall()
            self.assertEqual(len(events), 1)
            self.assertNotIn("@", str(events[0]["payload_json"]))
            self.assertIn('"system_tasks_registered":0', str(events[0]["payload_json"]))

    def test_pause_blocks_new_intake_and_resume_does_not_extend_expired_authorization(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            control = UserPresentIntakeControl(database, PROJECT / "schemas")
            control.configure(interval_minutes=15, authorization_hours=1, user_confirmed=True, now=START)

            paused = control.pause(user_confirmed=True, reason="USER_KILL_SWITCH", now=START)
            self.assertEqual(paused["status"], "PAUSED")
            self.assertEqual(paused["pause_reason"], "USER_KILL_SWITCH")
            self.assertFalse(paused["new_intake_allowed"])
            with self.assertRaises(JobOpsError) as blocked:
                control.assert_new_intake_allowed(now=START)
            self.assertEqual(blocked.exception.code, "NEW_INTAKE_PAUSED")

            resumed = control.resume(user_confirmed=True, now=START + timedelta(minutes=10))
            self.assertEqual(resumed["status"], "READY")
            expired = control.resume(user_confirmed=True, now=START + timedelta(hours=2))
            self.assertEqual(expired["status"], "AUTHORIZATION_EXPIRED")
            with self.assertRaises(JobOpsError) as expired_run:
                control.assert_manual_run_allowed(now=START + timedelta(hours=2))
            self.assertEqual(expired_run.exception.code, "INTAKE_WAKE_AUTHORIZATION_EXPIRED")

    def test_explicit_local_run_advances_only_local_wake_time(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            control = UserPresentIntakeControl(database, PROJECT / "schemas")
            control.configure(interval_minutes=60, authorization_hours=4, user_confirmed=True, now=START)
            control.assert_manual_run_allowed(now=START)
            result = control.record_manual_run({
                "processed_count": 2,
                "prepared_count": 1,
                "failed_count": 1,
                "background_service_started": False,
                "system_tasks_registered": 0,
                "browser_actions": 0,
                "network_actions": 0,
                "real_external_actions": 0,
            }, now=START + timedelta(minutes=5))
            self.assertEqual(result["last_user_run_at"], "2026-08-18T12:05:00Z")
            self.assertEqual(result["next_user_run_at"], "2026-08-18T13:05:00Z")
            self.assertEqual(result["status"], "READY")

            with database.connect() as connection:
                event = connection.execute(
                    "SELECT payload_json FROM events WHERE event_type='USER_PRESENT_LOCAL_WAKE_RAN'"
                ).fetchone()
            payload = json.loads(str(event["payload_json"]))
            self.assertEqual(payload["processed_count"], 2)
            self.assertEqual(payload["system_tasks_registered"], 0)
            self.assertEqual(payload["real_external_actions"], 0)

            with self.assertRaises(JobOpsError) as unsafe:
                control.record_manual_run({"real_external_actions": 1}, now=START + timedelta(minutes=6))
            self.assertEqual(unsafe.exception.code, "INTAKE_WAKE_RESULT_INVALID")

    def test_confirmation_bounds_and_hash_tampering_fail_closed(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            control = UserPresentIntakeControl(database, PROJECT / "schemas")
            cases = [
                ({"interval_minutes": 4, "authorization_hours": 1, "user_confirmed": True}, "INTAKE_INTERVAL_INVALID"),
                ({"interval_minutes": 5, "authorization_hours": 169, "user_confirmed": True}, "INTAKE_AUTHORIZATION_WINDOW_INVALID"),
                ({"interval_minutes": 5, "authorization_hours": 1, "user_confirmed": False}, "EXPLICIT_CONFIRMATION_REQUIRED"),
            ]
            for kwargs, code in cases:
                with self.subTest(code=code), self.assertRaises(JobOpsError) as failure:
                    control.configure(now=START, **kwargs)
                self.assertEqual(failure.exception.code, code)

            control.configure(interval_minutes=15, authorization_hours=2, user_confirmed=True, now=START)
            with database.connect() as connection:
                raw = json.loads(str(connection.execute(
                    "SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,),
                ).fetchone()["value"]))
                raw["interval_minutes"] = 16
                connection.execute(
                    "UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), CONTROL_METADATA_KEY),
                )
            with self.assertRaises(JobOpsError) as changed:
                control.state(now=START)
            self.assertEqual(changed.exception.code, "INTAKE_CONTROL_STATE_CHANGED")


if __name__ == "__main__":
    unittest.main()
