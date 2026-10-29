"""Own every process started by a local, synthetic release smoke script."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .errors import JobOpsError


# The target script cannot start until the parent has assigned its process to
# the Windows job. In particular, there is no spawn/AssignProcess race in the
# target's code. stdin is otherwise unused by these synthetic smoke entries.
_BOOTSTRAP = (
    "import sys; "
    "ready = sys.stdin.buffer.read(1); "
    "assert ready == b'1', 'RELEASE_SMOKE_NOT_CONTAINED'; "
    "import runpy; sys.argv = sys.argv[1:]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)


class _WindowsJob:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self.ctypes, self.accounting = ctypes, Accounting
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            self._fail()
        limit = ExtendedLimit()
        limit.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
            self.close()
            self._fail()

    @staticmethod
    def _fail() -> None:
        raise JobOpsError("RELEASE_SMOKE_CONTAINMENT_FAILED", "The synthetic release process could not be contained.")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE; no access to other processes.
        handle = self.api.OpenProcess(0x0101, False, process.pid)
        if not handle:
            self._fail()
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                self._fail()
        finally:
            self.api.CloseHandle(handle)

    def terminate(self) -> None:
        if not self.api.TerminateJobObject(self.handle, 125):
            self._fail()
        deadline = time.monotonic() + 10
        while True:
            accounting = self.accounting()
            if not self.api.QueryInformationJobObject(
                self.handle, 1, self.ctypes.byref(accounting), self.ctypes.sizeof(accounting), None,
            ):
                self._fail()
            if accounting.ActiveProcesses == 0:
                return
            if time.monotonic() >= deadline:
                raise JobOpsError("RELEASE_SMOKE_CLEANUP_TIMEOUT", "The synthetic release process tree did not stop in time.")
            time.sleep(0.02)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_release_python(
    script: Path, *arguments: str, cwd: Path, env: dict[str, str], timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run an isolated Python script, reclaiming descendants even after exit.

    File capture avoids waiting forever for a grandchild holding a pipe open.
    The job handle is parent-only, so kill-on-close also covers parent failure.
    No captured output is included in errors; callers allowlist diagnostics.
    """
    command = [str(Path(sys.executable).resolve(strict=True)), "-I", "-P", "-B", "-X", "utf8", "-c", _BOOTSTRAP, str(script), *arguments]
    job = _WindowsJob() if os.name == "nt" else None
    process = None
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                    stdout=stdout, stderr=stderr,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=os.name != "nt",
                )
                if job:
                    job.assign(process)
                assert process.stdin is not None
                process.stdin.write(b"1")
                process.stdin.close()
                process.wait(timeout=timeout)
            finally:
                # This must run even when the immediate child has already died.
                # taskkill /T on that child's PID cannot reclaim its orphans.
                try:
                    if job:
                        job.terminate()
                    elif process:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                finally:
                    if process:
                        if process.stdin and not process.stdin.closed:
                            process.stdin.close()
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=10)
            stdout.seek(0)
            stderr.seek(0)
            return subprocess.CompletedProcess(
                command, process.returncode,
                stdout.read(262144).decode("utf-8", errors="replace"),
                stderr.read(262144).decode("utf-8", errors="replace"),
            )
    except subprocess.TimeoutExpired:
        raise JobOpsError("RELEASE_SMOKE_PROCESS_TIMEOUT", "The synthetic release process exceeded its time budget.") from None
    finally:
        if job:
            job.close()
