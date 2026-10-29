from __future__ import annotations

import ctypes
import io
import json
import os
import runpy
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.release_candidate import _python_smoke_environment, _smoke_failure_details
from jobops.release_smoke_process import _WindowsJob, run_release_python


def _is_running(pid: int) -> bool:
    if os.name == "nt":
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))


class ReleaseSmokeProcessTests(unittest.TestCase):
    def _script(self, root: Path, *, exit_code: int | None) -> Path:
        child = root / "child.py"
        child.write_text(
            "import os, time\nfrom pathlib import Path\n"
            "held = open('held.txt', 'w')\n"
            "Path('child-pid.txt').write_text(str(os.getpid()))\n"
            "print('synthetic child inherited output', flush=True)\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        script = root / "parent.py"
        ending = f"raise SystemExit({exit_code})" if exit_code is not None else "time.sleep(120)"
        script.write_text(
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            "subprocess.Popen([sys.executable, '-I', '-B', 'child.py'])\n"
            "deadline = time.monotonic() + 10\n"
            "while not Path('child-pid.txt').exists():\n"
            "    if time.monotonic() >= deadline: raise RuntimeError('CHILD_NOT_READY')\n"
            "    time.sleep(0.01)\n"
            "print('synthetic parent result', flush=True)\n" + ending + "\n",
            encoding="utf-8",
        )
        return script

    def test_reclaims_descendants_even_after_parent_exits(self) -> None:
        for exit_code in (0, 17):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory(prefix="jobflow-owned-smoke-") as raw:
                root = Path(raw)
                script = self._script(root, exit_code=exit_code)
                result = run_release_python(script, cwd=root, env=_python_smoke_environment(root), timeout=15)
                self.assertEqual(result.returncode, exit_code)
                self.assertIn("synthetic parent result", result.stdout)
                self.assertFalse(_is_running(int((root / "child-pid.txt").read_text())))
                # A surviving child holds both this file and the working directory
                # on Windows; successful removal proves more than parent.poll().
                (root / "held.txt").unlink()
            self.assertFalse(root.exists())

    def test_timeout_reclaims_descendants_and_reports_only_a_safe_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-owned-timeout-") as raw:
            root = Path(raw)
            script = self._script(root, exit_code=None)
            with self.assertRaises(JobOpsError) as blocked:
                run_release_python(script, cwd=root, env=_python_smoke_environment(root), timeout=3)
            self.assertEqual(blocked.exception.code, "RELEASE_SMOKE_PROCESS_TIMEOUT")
            self.assertNotIn(raw, json.dumps(blocked.exception.as_dict()))
            self.assertFalse(_is_running(int((root / "child-pid.txt").read_text())))
        self.assertFalse(root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows job assignment")
    def test_target_never_runs_if_job_assignment_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-owned-refusal-") as raw:
            root = Path(raw)
            script = root / "target.py"
            script.write_text("from pathlib import Path\nPath('ran.txt').touch()\n", encoding="utf-8")
            failure = JobOpsError("RELEASE_SMOKE_CONTAINMENT_FAILED", "synthetic refusal")
            with patch.object(_WindowsJob, "assign", side_effect=failure), self.assertRaises(JobOpsError) as blocked:
                run_release_python(script, cwd=root, env=_python_smoke_environment(root), timeout=3)
            self.assertIs(blocked.exception, failure)
            self.assertFalse((root / "ran.txt").exists())

    def test_diagnostics_retain_only_bounded_public_fields(self) -> None:
        result = _smoke_failure_details(json.dumps({
            "status": "FAIL", "stage": "bootstrap", "failure_kind": "timeout",
            "elapsed_ms": 5010, "message": "synthetic private value", "path": str(PROJECT),
        }))
        self.assertEqual(result, {"stage": "bootstrap", "failure_kind": "timeout", "elapsed_ms": 5010})
        for value in ("not JSON", "[]", "null", '{"status":"PASS"}'):
            self.assertEqual(_smoke_failure_details(value), {})
        self.assertEqual(_smoke_failure_details(json.dumps({
            "status": "FAIL", "stage": str(PROJECT), "failure_kind": "synthetic private value",
            "elapsed_ms": True,
        })), {})
        self.assertEqual(_smoke_failure_details(json.dumps({
            "status": "FAIL", "stage": [], "failure_kind": {}, "elapsed_ms": -1,
        })), {})

    def test_source_smoke_reports_failed_stage_without_exception_content(self) -> None:
        script = PROJECT / ".agents/skills/job-application-operator/scripts/smoke-source-candidate.py"
        main = runpy.run_path(str(script))["main"]

        def timeout_at_bootstrap(progress: dict[str, object]) -> None:
            progress["stage"] = "bootstrap"
            raise TimeoutError("synthetic private exception content")

        output = io.StringIO()
        with patch.dict(main.__globals__, {"_run_smoke": timeout_at_bootstrap}), redirect_stdout(output):
            code = main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["stage"], "bootstrap")
        self.assertEqual(result["failure_kind"], "timeout")
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
