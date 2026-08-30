from __future__ import annotations

import ctypes
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
RUNNER = (
    PROJECT
    / ".agents"
    / "skills"
    / "job-application-operator"
    / "scripts"
    / "run-release-verification.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location("jobflow_skill_release_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("RELEASE_RUNNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def process_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(
            handle, ctypes.byref(exit_code)
        ):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class SkillReleaseVerificationTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_command_runner_captures_success(self) -> None:
        result = self.runner._run_command(
            [sys.executable, "-c", "print('release-runner-ok')"],
            cwd=PROJECT,
            timeout_seconds=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertIn("release-runner-ok", result.stdout)
        self.assertIsNone(result.termination_error)

    def test_timeout_report_is_structured_and_fail_closed(self) -> None:
        result = self.runner.CommandResult(
            returncode=1,
            stdout="partial output",
            stderr="",
            timed_out=True,
        )
        report = self.runner._build_test_report(
            result,
            source_commit="a" * 40,
            timeout_seconds=3600,
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["failure_kind"], "TEST_TIMEOUT")
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["failed"], 1)
        self.assertTrue(report["timed_out"])
        self.assertEqual(report["timeout_seconds"], 3600)

    def test_timeout_terminates_spawned_process_tree(self) -> None:
        child_pid = None
        with tempfile.TemporaryDirectory(prefix="jobflow-release-runner-") as temp:
            pid_path = Path(temp) / "child.pid"
            parent_script = (
                "import pathlib,subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8');"
                "print('ready',flush=True);"
                "time.sleep(120)"
            )
            try:
                result = self.runner._run_command(
                    [sys.executable, "-c", parent_script, str(pid_path)],
                    cwd=PROJECT,
                    timeout_seconds=1,
                )
                self.assertTrue(result.timed_out)
                self.assertTrue(pid_path.is_file())
                child_pid = int(pid_path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 5
                while process_is_running(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(process_is_running(child_pid))
            finally:
                if child_pid and process_is_running(child_pid):
                    if os.name == "nt":
                        taskkill = (
                            Path(os.environ.get("SystemRoot", r"C:\Windows"))
                            / "System32"
                            / "taskkill.exe"
                        )
                        subprocess.run(
                            [str(taskkill), "/PID", str(child_pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        os.kill(child_pid, 9)


if __name__ == "__main__":
    unittest.main()
