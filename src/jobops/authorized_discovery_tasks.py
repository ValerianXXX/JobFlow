from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .util import has_reparse_component


TASK_LOGICAL_NAME = "JOBFLOW_AUTHORIZED_DISCOVERY"
TASK_WAKE_INTERVAL_MINUTES = 15
_ACTIONS = {"Register", "Remove", "Status"}
_STATUSES = {"REGISTERED", "NOT_REGISTERED"}
_TERMINAL_REASONS = {
    "DISCOVERY_NOT_CONFIGURED",
    "DISCOVERY_PAUSED",
    "DISCOVERY_AUTHORIZATION_EXPIRED",
    "DISCOVERY_REPEATED_FAILURES",
}
_TASK_LIFECYCLE_THREAD_LOCK = threading.RLock()
_TASK_LIFECYCLE_STATE = threading.local()


class WindowsAuthorizedDiscoveryTask:
    """Manage the fixed, current-user Windows wake-up task.

    The OS task contains only a stable local launcher path. Discovery URLs,
    filters, secure references, applicant data, and authorization generations
    are deliberately absent from the command line and task definition.
    """

    def __init__(
        self,
        project: Path,
        *,
        process_runner: Callable[..., Any] = subprocess.run,
        powershell_path: str | None = None,
        script_path: Path | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self.project = project.resolve(strict=True)
        self.process_runner = process_runner
        self.powershell_path = powershell_path or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
        )
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        self.lock_path = (
            Path(os.path.abspath(lock_path))
            if lock_path is not None
            else (
                Path(os.path.abspath(Path(local_app_data) / "JobOps" / "Data" / "state" / ".authorized-discovery-task.lock"))
                if local_app_data
                else None
            )
        )
        self.script = (
            Path(os.path.abspath(script_path))
            if script_path is not None
            else (
                Path(os.path.abspath(Path(local_app_data) / "JobOps" / "bin" / "manage-authorized-discovery-task.ps1"))
                if local_app_data
                else None
            )
        )

    @contextmanager
    def lifecycle_lock(self, *, timeout_seconds: float = 30.0):
        """Serialize every fixed-task lifecycle transition across processes.

        The scheduler, rollback helper, and uninstaller all operate on one
        current-user task.  Holding this lock around both the Windows task
        mutation and the generation-bound database mutation prevents an old
        cleanup from racing a newly confirmed authorization.
        """

        with _TASK_LIFECYCLE_THREAD_LOCK:
            depth = int(getattr(_TASK_LIFECYCLE_STATE, "depth", 0))
            if depth:
                _TASK_LIFECYCLE_STATE.depth = depth + 1
                try:
                    yield
                finally:
                    _TASK_LIFECYCLE_STATE.depth = depth
                return
            if os.environ.get("JOBFLOW_DISCOVERY_TASK_LOCK_HELD") == "1":
                # The stable PowerShell wake-up runner acquires this same byte
                # range before it resolves current.json and holds it until the
                # Python process exits.  Re-enter locally without attempting a
                # second cross-process lock owned by the parent process.
                _TASK_LIFECYCLE_STATE.depth = 1
                try:
                    yield
                finally:
                    _TASK_LIFECYCLE_STATE.depth = 0
                return
            _TASK_LIFECYCLE_STATE.depth = 1
            handle = None
            locked = False
            try:
                if os.name == "nt":
                    if self.lock_path is None or not self.lock_path.parent.is_dir():
                        raise JobOpsError(
                            "DISCOVERY_TASK_LOCK_UNAVAILABLE",
                            "The installed discovery task lock directory is unavailable.",
                        )
                    if has_reparse_component(self.lock_path.parent) or (
                        self.lock_path.exists() and has_reparse_component(self.lock_path)
                    ):
                        raise JobOpsError(
                            "DISCOVERY_TASK_LOCK_UNAVAILABLE",
                            "The installed discovery task lock path is not trusted.",
                        )
                    import msvcrt

                    handle = self.lock_path.open("a+b")
                    if handle.seek(0, os.SEEK_END) < 1:
                        handle.write(b"\0")
                        handle.flush()
                    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
                    while True:
                        try:
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            locked = True
                            break
                        except OSError as exc:
                            if time.monotonic() >= deadline:
                                raise JobOpsError(
                                    "DISCOVERY_TASK_LOCK_TIMEOUT",
                                    "Another discovery task lifecycle operation is still running.",
                                ) from exc
                            time.sleep(0.05)
                yield
            finally:
                if handle is not None:
                    if locked:
                        try:
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    handle.close()
                _TASK_LIFECYCLE_STATE.depth = 0

    @staticmethod
    def _validate(value: Any) -> dict[str, Any]:
        expected = {
            "schema_version", "status", "task_name", "interactive_user_only",
            "stores_password", "wake_interval_minutes", "application_actions",
            "browser_actions", "material_uploads", "final_submits",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise JobOpsError("DISCOVERY_TASK_RESPONSE_INVALID", "The Windows task manager returned an invalid response.")
        if (
            value.get("schema_version") != 1
            or value.get("status") not in _STATUSES
            or value.get("task_name") != TASK_LOGICAL_NAME
            or value.get("interactive_user_only") is not True
            or value.get("stores_password") is not False
            or value.get("wake_interval_minutes") != TASK_WAKE_INTERVAL_MINUTES
            or any(value.get(key) != 0 for key in (
                "application_actions", "browser_actions", "material_uploads", "final_submits",
            ))
        ):
            raise JobOpsError("DISCOVERY_TASK_RESPONSE_INVALID", "The Windows task manager response changed its safety contract.")
        return dict(value)

    def _invoke(self, action: str) -> dict[str, Any]:
        if action not in _ACTIONS:
            raise JobOpsError("DISCOVERY_TASK_ACTION_INVALID", "The Windows task action is invalid.")
        if (
            os.name != "nt"
            or self.script is None
            or not self.script.is_file()
            or has_reparse_component(self.script)
        ):
            raise JobOpsError("DISCOVERY_TASK_PLATFORM_UNAVAILABLE", "Authorized scheduling requires the installed Windows runtime.")
        command = [
            self.powershell_path,
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(self.script), "-Action", action,
        ]
        environment = os.environ.copy()
        if int(getattr(_TASK_LIFECYCLE_STATE, "depth", 0)) > 0:
            environment["JOBFLOW_DISCOVERY_TASK_LOCK_HELD"] = "1"
        try:
            completed = self.process_runner(
                command,
                cwd=self.project,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JobOpsError("DISCOVERY_TASK_OPERATION_FAILED", "The Windows discovery task operation did not complete.") from exc
        if int(completed.returncode) != 0:
            raise JobOpsError("DISCOVERY_TASK_OPERATION_FAILED", "The Windows discovery task operation did not complete.")
        output = str(completed.stdout or "").lstrip("\ufeff").strip()
        if not output or len(output.encode("utf-8")) > 8_192:
            raise JobOpsError("DISCOVERY_TASK_RESPONSE_INVALID", "The Windows task manager returned an invalid response.")
        try:
            return self._validate(json.loads(output))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("DISCOVERY_TASK_RESPONSE_INVALID", "The Windows task manager returned an invalid response.") from exc

    def register(self) -> dict[str, Any]:
        with self.lifecycle_lock():
            result = self._invoke("Register")
            if result["status"] != "REGISTERED":
                raise JobOpsError("DISCOVERY_TASK_REGISTRATION_FAILED", "The Windows discovery task was not registered.")
            return result

    def remove(self) -> dict[str, Any]:
        with self.lifecycle_lock():
            result = self._invoke("Remove")
            if result["status"] != "NOT_REGISTERED":
                raise JobOpsError("DISCOVERY_TASK_REMOVAL_FAILED", "The Windows discovery task was not removed.")
            return result

    def status(self) -> dict[str, Any]:
        with self.lifecycle_lock():
            return self._invoke("Status")


class AuthorizedDiscoveryScheduler:
    """Reconcile OS task lifecycle with the generation-bound control state."""

    def __init__(self, control: Any, task: WindowsAuthorizedDiscoveryTask) -> None:
        self.control = control
        self.task = task

    @contextmanager
    def _guard(self):
        lifecycle = getattr(self.task, "lifecycle_lock", None)
        with (lifecycle() if callable(lifecycle) else nullcontext()):
            yield

    def register(self, *, generation: int, user_confirmed: bool, now: Any = None) -> dict[str, Any]:
        with self._guard():
            return self._register(generation=generation, user_confirmed=user_confirmed, now=now)

    def _register(self, *, generation: int, user_confirmed: bool, now: Any = None) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Scheduling read-only discovery requires explicit confirmation.")
        state = self.control.state(now=now)
        if state.get("generation") != generation:
            raise JobOpsError("DISCOVERY_CONTROL_STALE_GENERATION", "The discovery authorization changed before scheduling.")
        try:
            task_result = self.task.register()
        except Exception:
            # Register-then-validate adapters can fail after Windows has
            # already created or replaced the fixed task.  Always reconcile
            # that possible side effect before reporting the failure.
            try:
                self.task.remove()
            except Exception as rollback_error:
                raise JobOpsError(
                    "DISCOVERY_TASK_REGISTRATION_ROLLBACK_FAILED",
                    "Scheduling failed and the Windows task rollback could not be verified.",
                ) from rollback_error
            raise
        try:
            control_state = self.control.mark_task_registration(
                registered=True, generation=generation, now=now,
            )
        except Exception:
            try:
                self.task.remove()
            except Exception as rollback_error:
                raise JobOpsError(
                    "DISCOVERY_TASK_REGISTRATION_ROLLBACK_FAILED",
                    "Scheduling failed and the Windows task rollback could not be verified.",
                ) from rollback_error
            raise
        return {"status": "AUTHORIZED_DISCOVERY_SCHEDULED", "control": control_state, "task": task_result}

    def remove(self, *, generation: int, user_confirmed: bool, now: Any = None) -> dict[str, Any]:
        with self._guard():
            return self._remove(generation=generation, user_confirmed=user_confirmed, now=now)

    def _remove(self, *, generation: int, user_confirmed: bool, now: Any = None) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Removing read-only discovery scheduling requires explicit confirmation.")
        task_result = self.task.remove()
        control_state = self.control.mark_task_registration(
            registered=False, generation=generation, now=now,
        )
        return {"status": "AUTHORIZED_DISCOVERY_UNSCHEDULED", "control": control_state, "task": task_result}

    def pause_and_remove(self, *, user_confirmed: bool, kill: bool = False, now: Any = None) -> dict[str, Any]:
        with self._guard():
            return self._pause_and_remove(user_confirmed=user_confirmed, kill=kill, now=now)

    def _pause_and_remove(self, *, user_confirmed: bool, kill: bool = False, now: Any = None) -> dict[str, Any]:
        if user_confirmed is not True:
            raise JobOpsError("EXPLICIT_CONFIRMATION_REQUIRED", "Stopping read-only discovery requires explicit confirmation.")
        control_state = (
            self.control.kill_switch(user_confirmed=True, now=now)
            if kill else self.control.pause(user_confirmed=True, now=now)
        )
        try:
            task_result = self.task.remove()
        except JobOpsError:
            # The incremented generation has already revoked every active run.
            # Preserve REMOVAL_REQUIRED so the UI can offer a safe retry.
            return {
                "status": "AUTHORIZED_DISCOVERY_PAUSED_REMOVAL_REQUIRED",
                "control": control_state,
                "task": None,
            }
        final_state = self.control.mark_task_registration(
            registered=False, generation=int(control_state["generation"]), now=now,
        )
        return {"status": "AUTHORIZED_DISCOVERY_STOPPED", "control": final_state, "task": task_result}

    def reconcile_terminal_state(self, *, reason: str, now: Any = None) -> dict[str, Any]:
        """Remove a harmless wake-up task after its authorization becomes terminal.

        This is intentionally an internal cleanup operation.  It can only
        reduce capability, never create or renew authorization.
        """

        with self._guard():
            return self._reconcile_terminal_state(reason=reason, now=now)

    def _reconcile_terminal_state(self, *, reason: str, now: Any = None) -> dict[str, Any]:
        safe_reason = str(reason or "").strip().upper()
        if safe_reason not in _TERMINAL_REASONS:
            raise JobOpsError(
                "DISCOVERY_TASK_RECONCILE_REASON_INVALID",
                "The Windows task cleanup reason is not terminal.",
            )
        before = self.control.state(now=now)
        generation = int(before["generation"])
        # Removal is idempotent.  Always ask Windows to reconcile because the
        # fixed task may outlive a replaced or freshly initialized database.
        try:
            task_result = self.task.remove()
        except JobOpsError:
            return {
                "status": "AUTHORIZED_DISCOVERY_TASK_REMOVAL_REQUIRED",
                "reason": safe_reason,
                "control": before,
                "task": None,
            }
        try:
            after = self.control.mark_task_registration(
                registered=False, generation=generation, now=now,
            )
        except JobOpsError as exc:
            if exc.code != "DISCOVERY_CONTROL_STALE_GENERATION":
                raise
            # A concurrent renewal may have installed the same fixed task.
            # Restore it only when the new generation is explicitly current
            # and expects a registered wake-up task.
            current = self.control.state(now=now)
            if (
                current.get("task_registration_state") == "REGISTERED"
                and current.get("read_only_network_authorized") is True
            ):
                try:
                    self.task.register()
                except JobOpsError:
                    return {
                        "status": "AUTHORIZED_DISCOVERY_TASK_RESTORE_REQUIRED",
                        "reason": safe_reason,
                        "control": current,
                        "task": task_result,
                    }
            return {
                "status": "AUTHORIZED_DISCOVERY_TASK_STATE_CHANGED",
                "reason": safe_reason,
                "control": current,
                "task": task_result,
            }
        return {
            "status": "AUTHORIZED_DISCOVERY_TASK_REMOVED",
            "reason": safe_reason,
            "control": after,
            "task": task_result,
        }
