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


# POSIX starts in an owned process group before this handshake. On Windows the
# native launcher must also be suspended until assignment: a venv redirector
# can create the real interpreter before Python gets to read stdin.
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

        self.ctypes, self.accounting, self.limit_type = ctypes, Accounting, ExtendedLimit
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
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

    def assign(self, process: _WindowsProcess) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE; no access to other processes.
        handle = self.api.OpenProcess(0x0101, False, process.pid)
        if not handle:
            self._fail()
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                self._fail()
        finally:
            self.api.CloseHandle(handle)

    def _process_handles(self) -> list[int]:
        from ctypes import wintypes

        # Called only after disabling new process association. These handles
        # are used only to wait, never to terminate an individual PID. A process
        # can leave the job's accounting before its handle becomes signaled.
        for capacity in (64, 256, 1024, 4096):
            class ProcessIds(self.ctypes.Structure):
                _fields_ = [
                    ("assigned", wintypes.DWORD), ("returned", wintypes.DWORD),
                    ("ids", self.ctypes.c_size_t * capacity),
                ]

            processes = ProcessIds()
            ok = self.api.QueryInformationJobObject(
                self.handle, 3, self.ctypes.byref(processes), self.ctypes.sizeof(processes), None,
            )
            if not ok and self.ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                self._fail()
            if not ok or processes.returned < processes.assigned:
                continue
            handles = []
            try:
                for pid in processes.ids[:processes.returned]:
                    handle = self.api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
                    if not handle:
                        if self.ctypes.get_last_error() == 87:  # Process already exited.
                            continue
                        self._fail()
                    handles.append(handle)
                return handles
            except BaseException:
                for handle in handles:
                    self.api.CloseHandle(handle)
                raise
        self._fail()

    def terminate(self) -> None:
        deadline = time.monotonic() + 10
        handles = []
        try:
            # Stop new descendants before enumerating existing ones. Retain
            # kill-on-close and disallow breakaway throughout the entire job.
            limit = self.limit_type()
            limit.BasicLimitInformation.LimitFlags = 0x2008  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS
            limit.BasicLimitInformation.ActiveProcessLimit = 0
            if not self.api.SetInformationJobObject(
                self.handle, 9, self.ctypes.byref(limit), self.ctypes.sizeof(limit),
            ):
                self._fail()
            handles = self._process_handles()
        finally:
            # A query failure still requests termination; callers must not
            # mistake failure to inspect the job for permission to leave it live.
            if not self.api.TerminateJobObject(self.handle, 125):
                for handle in handles:
                    self.api.CloseHandle(handle)
                self._fail()
        try:
            for handle in handles:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                result = self.api.WaitForSingleObject(handle, remaining)
                if result == 258:
                    self._cleanup_timeout()
                if result != 0:
                    self._fail()
            while True:
                accounting = self.accounting()
                if not self.api.QueryInformationJobObject(
                    self.handle, 1, self.ctypes.byref(accounting), self.ctypes.sizeof(accounting), None,
                ):
                    self._fail()
                if accounting.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    self._cleanup_timeout()
                time.sleep(0.02)
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)

    @staticmethod
    def _cleanup_timeout() -> None:
        raise JobOpsError("RELEASE_SMOKE_CLEANUP_TIMEOUT", "The synthetic release process tree did not stop in time.")

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class _WindowsProcess:
    """Small owned handle wrapper; CPython 3.11–3.13's Windows binding preserves
    the primary thread handle that Popen closes before returning.
    """

    stdin = None

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str], stdout, stderr) -> None:
        import _winapi
        import msvcrt

        self.api = _winapi
        self.command = command
        self.handle = self.thread = None
        self.returncode = None
        duplicates = []
        current = _winapi.GetCurrentProcess()
        try:
            with tempfile.TemporaryFile() as ready:
                ready.write(b"1")
                ready.seek(0)
                for stream in (ready, stdout, stderr):
                    duplicates.append(_winapi.DuplicateHandle(
                        current, msvcrt.get_osfhandle(stream.fileno()), current,
                        0, True, _winapi.DUPLICATE_SAME_ACCESS,
                    ))
                startup = subprocess.STARTUPINFO()
                startup.dwFlags = subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = duplicates
                startup.lpAttributeList = {"handle_list": duplicates}
                # Explicit executable, Unicode environment, no shell/window.
                # The only inherited handles are the three synthetic streams.
                self.handle, self.thread, self.pid, _ = _winapi.CreateProcess(
                    command[0], subprocess.list2cmdline(command), None, None, True,
                    0x00000004 | subprocess.CREATE_NO_WINDOW | 0x00000400 | 0x00080000,
                    env, str(cwd), startup,
                )
        except OSError:
            raise JobOpsError("RELEASE_SMOKE_PROCESS_START_FAILED", "The synthetic release process could not be started.") from None
        finally:
            for handle in duplicates:
                _winapi.CloseHandle(handle)

    def resume(self) -> None:
        import ctypes
        from ctypes import wintypes

        resume = ctypes.WinDLL("kernel32", use_last_error=True).ResumeThread
        resume.argtypes, resume.restype = [wintypes.HANDLE], wintypes.DWORD
        if resume(self.thread) != 1:
            _WindowsJob._fail()
        self.api.CloseHandle(self.thread)
        self.thread = None

    def wait(self, timeout: float) -> int:
        result = self.api.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if result == self.api.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if result != self.api.WAIT_OBJECT_0:
            _WindowsJob._fail()
        self.returncode = self.api.GetExitCodeProcess(self.handle)
        return self.returncode

    def poll(self) -> int | None:
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        try:
            self.api.TerminateProcess(self.handle, 125)
        except OSError:
            if self.poll() is None:
                _WindowsJob._fail()

    def close(self) -> None:
        for name in ("thread", "handle"):
            handle = getattr(self, name)
            if handle is not None:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


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
                if job:
                    process = _WindowsProcess(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
                    job.assign(process)
                    process.resume()
                else:
                    process = subprocess.Popen(
                        command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                        stdout=stdout, stderr=stderr, start_new_session=True,
                    )
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
        try:
            if isinstance(process, _WindowsProcess):
                process.close()
        finally:
            if job:
                job.close()
