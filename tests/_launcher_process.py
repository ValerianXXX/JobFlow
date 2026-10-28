"""Isolated Windows launcher capture with bounded, owned-process cleanup."""

from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path
from subprocess import CREATE_NO_WINDOW, DEVNULL, Popen as RealPopen, TimeoutExpired


def terminate_process_tree(process: RealPopen) -> None:
    """Stop descendants before their parent, so inherited capture handles close."""
    if process.poll() is not None:
        return
    # Resolve the native utility through Windows, not a test-modified PATH or
    # SystemRoot. The PID belongs to a process created by this test helper.
    system_directory = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(
        system_directory, len(system_directory)
    )
    if not 0 < length < len(system_directory):
        raise OSError("Could not resolve the Windows system directory.")
    taskkill = Path(system_directory.value) / "taskkill.exe"
    killer = RealPopen(
        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
        stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        killer.wait(timeout=30)
    except TimeoutExpired:
        killer.kill()
        killer.wait(timeout=5)
        raise
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def main() -> None:
    result_path = Path(sys.argv[1])
    timeout_seconds = float(sys.argv[2])
    stdout_path = result_path.with_suffix(".stdout")
    stderr_path = result_path.with_suffix(".stderr")
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = RealPopen(
            sys.argv[3:], stdin=DEVNULL, stdout=stdout_file, stderr=stderr_file,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
            timed_out = False
        except TimeoutExpired:
            # subprocess.run(timeout=...) kills only the immediate parent.
            # PowerShell's Python child would retain these files on Windows.
            terminate_process_tree(process)
            returncode = 124
            timed_out = True
    result_path.write_text(json.dumps({
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": stdout_path.read_bytes().decode("utf-8-sig"),
        "stderr": stderr_path.read_bytes().decode("utf-8-sig"),
    }), encoding="utf-8")


if __name__ == "__main__":
    main()
