from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from _support import PROJECT, project_temp
from jobops.authorized_discovery import AuthorizedDiscoveryControl
from jobops.authorized_discovery_tasks import (
    AuthorizedDiscoveryScheduler,
    TASK_LOGICAL_NAME,
    WindowsAuthorizedDiscoveryTask,
)
from jobops.cli import _reconcile_discovery_auto_pause
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.private_onboarding import PrivateOnboarding

from test_authorized_discovery import MemoryStore, sample_config


START = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def task_payload(status: str) -> str:
    return json.dumps({
        "schema_version": 1,
        "status": status,
        "task_name": TASK_LOGICAL_NAME,
        "interactive_user_only": True,
        "stores_password": False,
        "wake_interval_minutes": 15,
        "application_actions": 0,
        "browser_actions": 0,
        "material_uploads": 0,
        "final_submits": 0,
    })


class FakeTask:
    def __init__(self) -> None:
        self.registered = False
        self.fail_register_after_create = False
        self.fail_remove = False
        self.register_calls = 0
        self.remove_calls = 0

    def register(self):
        self.register_calls += 1
        self.registered = True
        if self.fail_register_after_create:
            raise JobOpsError("DISCOVERY_TASK_REGISTRATION_FAILED", "Synthetic registration validation failure.")
        return json.loads(task_payload("REGISTERED"))

    def remove(self):
        self.remove_calls += 1
        if self.fail_remove:
            raise JobOpsError("DISCOVERY_TASK_OPERATION_FAILED", "Synthetic removal failure.")
        self.registered = False
        return json.loads(task_payload("NOT_REGISTERED"))


class GuardedFakeTask(FakeTask):
    def __init__(self) -> None:
        super().__init__()
        self.guard_depth = 0
        self.guard_entries = 0

    @contextmanager
    def lifecycle_lock(self):
        self.guard_depth += 1
        self.guard_entries += 1
        try:
            yield
        finally:
            self.guard_depth -= 1

    def register(self):
        if self.guard_depth != 1:
            raise AssertionError("register was not lifecycle-guarded")
        return super().register()

    def remove(self):
        if self.guard_depth != 1:
            raise AssertionError("remove was not lifecycle-guarded")
        return super().remove()


class AuthorizedDiscoveryTaskTests(unittest.TestCase):
    def test_unexpected_runner_failure_cleanup_is_best_effort_and_capability_reducing(self) -> None:
        class PausedControl:
            @staticmethod
            def state():
                return {"status": "PAUSED", "pause_reason": "REPEATED_FAILURES"}

        class CleanupScheduler:
            def __init__(self) -> None:
                self.reasons: list[str] = []

            def reconcile_terminal_state(self, *, reason: str):
                self.reasons.append(reason)
                raise RuntimeError("synthetic cleanup failure")

        scheduler = CleanupScheduler()
        # Cleanup failure must not replace the original scheduled-run error.
        self.assertIsNone(_reconcile_discovery_auto_pause(PausedControl(), scheduler))
        self.assertEqual(scheduler.reasons, ["DISCOVERY_REPEATED_FAILURES"])

    def control(self, temp: Path) -> AuthorizedDiscoveryControl:
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        onboarding = PrivateOnboarding(database, MemoryStore(temp / "private"))
        return AuthorizedDiscoveryControl(database, onboarding, PROJECT / "schemas")

    def test_windows_adapter_uses_only_a_fixed_action_argument(self) -> None:
        with project_temp() as temp:
            lock_path = temp / "state" / ".authorized-discovery-task.lock"
            lock_path.parent.mkdir(parents=True)
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                return CompletedProcess(command, 0, task_payload("REGISTERED"), "")

            adapter = WindowsAuthorizedDiscoveryTask(
                PROJECT,
                process_runner=run,
                script_path=PROJECT / "scripts" / "windows-runtime" / "manage-authorized-discovery-task.ps1",
                lock_path=lock_path,
            )
            result = adapter.register()
            self.assertEqual(result["status"], "REGISTERED")
            self.assertEqual(len(calls), 1)
            serialized = json.dumps(calls[0][0])
            self.assertIn("manage-authorized-discovery-task.ps1", serialized)
            self.assertIn("Register", calls[0][0])
            self.assertEqual(calls[0][1]["env"]["JOBFLOW_DISCOVERY_TASK_LOCK_HELD"], "1")
            for forbidden in (
                "secure-ref:", "example.com", "credit risk", "authorization_id",
                "application", "resume", "submit",
            ):
                self.assertNotIn(forbidden, serialized.casefold())

    def test_parent_held_task_lock_reenters_without_opening_a_second_file_lock(self) -> None:
        with project_temp() as temp:
            adapter = WindowsAuthorizedDiscoveryTask(
                PROJECT,
                process_runner=lambda *_args, **_kwargs: None,
                script_path=PROJECT / "scripts" / "windows-runtime" / "manage-authorized-discovery-task.ps1",
                lock_path=temp / "missing" / ".authorized-discovery-task.lock",
            )
            with (
                mock.patch.dict(os.environ, {"JOBFLOW_DISCOVERY_TASK_LOCK_HELD": "1"}),
                mock.patch("jobops.authorized_discovery_tasks.os.name", "nt"),
                mock.patch("pathlib.Path.open", side_effect=AssertionError("nested lock file opened")),
            ):
                with adapter.lifecycle_lock():
                    with adapter.lifecycle_lock():
                        pass

            # The parent-held exception is scoped to the child process
            # environment.  A normal process must still fail closed when the
            # installed lifecycle-lock directory is unavailable.
            with (
                mock.patch.dict(os.environ, {"JOBFLOW_DISCOVERY_TASK_LOCK_HELD": ""}),
                mock.patch("jobops.authorized_discovery_tasks.os.name", "nt"),
                self.assertRaises(JobOpsError) as rejected,
            ):
                with adapter.lifecycle_lock():
                    pass
            self.assertEqual(rejected.exception.code, "DISCOVERY_TASK_LOCK_UNAVAILABLE")

    def test_windows_adapter_rejects_a_changed_safety_contract(self) -> None:
        def run(command, **_kwargs):
            value = json.loads(task_payload("REGISTERED"))
            value["final_submits"] = 1
            return CompletedProcess(command, 0, json.dumps(value), "")

        with project_temp() as temp, self.assertRaises(JobOpsError) as rejected:
            lock_path = temp / "state" / ".authorized-discovery-task.lock"
            lock_path.parent.mkdir(parents=True)
            WindowsAuthorizedDiscoveryTask(
                PROJECT,
                process_runner=run,
                script_path=PROJECT / "scripts" / "windows-runtime" / "manage-authorized-discovery-task.ps1",
                lock_path=lock_path,
            ).register()
        self.assertEqual(rejected.exception.code, "DISCOVERY_TASK_RESPONSE_INVALID")

    def test_windows_adapter_defaults_to_stable_installed_manager_and_rejects_reparse(self) -> None:
        with project_temp() as temp:
            local = temp / "LocalAppData"
            manager = local / "JobOps" / "bin" / "manage-authorized-discovery-task.ps1"
            manager.parent.mkdir(parents=True)
            manager.write_text("# synthetic\n", encoding="utf-8")
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                return CompletedProcess(command, 0, task_payload("REGISTERED"), "")

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                lock_path = local / "JobOps" / "Data" / "state" / ".authorized-discovery-task.lock"
                lock_path.parent.mkdir(parents=True)
                adapter = WindowsAuthorizedDiscoveryTask(PROJECT, process_runner=run, lock_path=lock_path)
            with mock.patch("jobops.authorized_discovery_tasks.os.name", "nt"):
                adapter.register()
            self.assertEqual(adapter.script, manager.resolve())
            self.assertIn(str(manager.resolve()), calls[0])

            with (
                mock.patch("jobops.authorized_discovery_tasks.os.name", "nt"),
                mock.patch(
                    "jobops.authorized_discovery_tasks.has_reparse_component",
                    side_effect=lambda path: Path(path) == adapter.script,
                ),
            ):
                with self.assertRaises(JobOpsError) as rejected:
                    adapter.status()
            self.assertEqual(rejected.exception.code, "DISCOVERY_TASK_PLATFORM_UNAVAILABLE")

    def test_scheduler_registers_and_pause_removes_without_application_actions(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = FakeTask()
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            scheduled = scheduler.register(
                generation=configured["generation"], user_confirmed=True, now=START,
            )
            self.assertTrue(task.registered)
            self.assertEqual(scheduled["control"]["task_registration_state"], "REGISTERED")
            stopped = scheduler.pause_and_remove(user_confirmed=True, kill=True, now=START)
            self.assertFalse(task.registered)
            self.assertEqual(stopped["control"]["status"], "PAUSED")
            self.assertEqual(stopped["control"]["task_registration_state"], "NOT_REGISTERED")

    def test_scheduler_removes_a_task_left_by_failed_registration_validation(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = FakeTask()
            task.fail_register_after_create = True
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            with self.assertRaises(JobOpsError) as rejected:
                scheduler.register(
                    generation=configured["generation"], user_confirmed=True, now=START,
                )
            self.assertEqual(rejected.exception.code, "DISCOVERY_TASK_REGISTRATION_FAILED")
            self.assertFalse(task.registered)
            self.assertEqual(task.remove_calls, 1)
            self.assertEqual(control.state(now=START)["task_registration_state"], "REGISTRATION_REQUIRED")

            task.fail_remove = True
            with self.assertRaises(JobOpsError) as rollback:
                scheduler.register(
                    generation=configured["generation"], user_confirmed=True, now=START,
                )
            self.assertEqual(rollback.exception.code, "DISCOVERY_TASK_REGISTRATION_ROLLBACK_FAILED")

    def test_scheduler_holds_one_lifecycle_guard_across_task_and_control_changes(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = GuardedFakeTask()
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            scheduler.register(generation=configured["generation"], user_confirmed=True, now=START)
            scheduler.pause_and_remove(user_confirmed=True, now=START)
            self.assertEqual(task.guard_depth, 0)
            self.assertEqual(task.guard_entries, 2)

    def test_task_removal_failure_still_revokes_every_run_generation(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = FakeTask()
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            scheduler.register(generation=configured["generation"], user_confirmed=True, now=START)
            task.fail_remove = True
            stopped = scheduler.pause_and_remove(user_confirmed=True, now=START)
            self.assertEqual(stopped["status"], "AUTHORIZED_DISCOVERY_PAUSED_REMOVAL_REQUIRED")
            self.assertEqual(stopped["control"]["task_registration_state"], "REMOVAL_REQUIRED")
            self.assertFalse(stopped["control"]["read_only_network_authorized"])

    def test_terminal_state_cleanup_removes_task_and_never_renews_authorization(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=1,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = FakeTask()
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            scheduler.register(generation=configured["generation"], user_confirmed=True, now=START)
            expired = scheduler.reconcile_terminal_state(
                reason="DISCOVERY_AUTHORIZATION_EXPIRED",
                now=datetime(2026, 8, 19, 13, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(expired["status"], "AUTHORIZED_DISCOVERY_TASK_REMOVED")
            self.assertEqual(expired["control"]["status"], "AUTHORIZATION_EXPIRED")
            self.assertEqual(expired["control"]["task_registration_state"], "NOT_REGISTERED")
            self.assertFalse(expired["control"]["read_only_network_authorized"])
            self.assertEqual(task.remove_calls, 1)

    def test_terminal_cleanup_failure_is_redacted_and_leaves_removal_required(self) -> None:
        with project_temp() as temp:
            control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            task = FakeTask()
            scheduler = AuthorizedDiscoveryScheduler(control, task)
            scheduler.register(generation=configured["generation"], user_confirmed=True, now=START)
            paused = control.pause(user_confirmed=True, now=START)
            task.fail_remove = True
            cleanup = scheduler.reconcile_terminal_state(reason="DISCOVERY_PAUSED", now=START)
            self.assertEqual(cleanup["status"], "AUTHORIZED_DISCOVERY_TASK_REMOVAL_REQUIRED")
            self.assertEqual(cleanup["control"]["generation"], paused["generation"])
            self.assertEqual(cleanup["control"]["task_registration_state"], "REMOVAL_REQUIRED")

    def test_runtime_scripts_keep_private_configuration_out_of_task_arguments(self) -> None:
        manager = (PROJECT / "scripts" / "windows-runtime" / "manage-authorized-discovery-task.ps1").read_text(
            encoding="utf-8"
        )
        runner = (PROJECT / "scripts" / "windows-runtime" / "run-authorized-discovery-task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("-LogonType Interactive -RunLevel Limited", manager)
        self.assertIn("stores_password = $false", manager)
        self.assertIn("-MultipleInstances IgnoreNew", manager)
        self.assertIn("JOBFLOW_DISCOVERY_TASK_LOCK_HELD", manager)
        self.assertIn("$lockStream.Lock(0, 1)", manager)
        self.assertIn("JOBFLOW_DISCOVERY_TASK_TRIGGER_CHANGED", manager)
        self.assertIn("JOBFLOW_DISCOVERY_TASK_SETTINGS_CHANGED", manager)
        self.assertIn('([string]$triggers[0].Repetition.Interval) -ne "PT15M"', manager)
        self.assertIn('([string]$task.Settings.ExecutionTimeLimit) -ne "PT10M"', manager)
        self.assertIn("authorized-discovery-run", runner)
        self.assertIn("Assert-LocalPath $pythonPath", runner)
        self.assertIn("Assert-LocalPath $versionMarkerPath", runner)
        self.assertIn("Assert-LocalPath $dataMarkerPath", runner)
        self.assertIn(".Equals($taskArguments, [StringComparison]::Ordinal)", manager)
        self.assertIn("JOBFLOW_DISCOVERY_TASK_PRINCIPAL_CHANGED", manager)
        combined = (manager + runner).casefold()
        for forbidden in ("secure-ref:", "include_terms", "feed_url", "application form", "final submit button"):
            self.assertNotIn(forbidden, combined)
        self.assertNotIn("-password", combined)


if __name__ == "__main__":
    unittest.main()
