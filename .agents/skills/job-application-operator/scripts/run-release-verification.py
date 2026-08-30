#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


def root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


PROJECT = root()
sys.path.insert(0, str(PROJECT / "src"))
from jobops.db import JobOpsDB  # noqa: E402
from jobops.adapters import audit_real_external_actions  # noqa: E402
from jobops.errors import JobOpsError  # noqa: E402
from jobops.release import _source_commit, verify_release, write_release_reports  # noqa: E402
from jobops.util import write_json  # noqa: E402


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    termination_error: str | None = None


def _terminate_process_tree(process: subprocess.Popen[str]) -> str | None:
    if process.poll() is not None:
        return None
    errors: list[str] = []
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            if not taskkill.is_file():
                raise FileNotFoundError(str(taskkill))
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(f"taskkill:{type(error).__name__}")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError) as error:
            errors.append(f"killpg:{type(error).__name__}")
    if process.poll() is None:
        try:
            process.kill()
        except OSError as error:
            errors.append(f"kill:{type(error).__name__}")
    return ";".join(errors) or None


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    process_options: dict[str, object] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": dict(env) if env is not None else None,
    }
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(list(command), **process_options)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return CommandResult(
            returncode=int(process.returncode or 0),
            stdout=stdout or "",
            stderr=stderr or "",
        )
    except subprocess.TimeoutExpired:
        termination_error = _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError as error:
                suffix = f"kill:{type(error).__name__}"
                termination_error = (
                    f"{termination_error};{suffix}" if termination_error else suffix
                )
            stdout, stderr = process.communicate()
        return CommandResult(
            returncode=int(process.returncode if process.returncode is not None else -9),
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=True,
            termination_error=termination_error,
        )


def _build_test_report(
    result: CommandResult,
    *,
    source_commit: str,
    timeout_seconds: int,
) -> dict[str, object]:
    output = result.stdout + "\n" + result.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    passed = int(match.group(1)) if match and result.returncode == 0 and not result.timed_out else 0
    categories: dict[str, int] = {}
    for module in re.findall(r"\((test_[A-Za-z0-9_]+)\.[^)]+\)\s+\.\.\.\s+ok", output):
        categories[module] = categories.get(module, 0) + 1
    status = "PASS" if result.returncode == 0 and match and not result.timed_out else "FAIL"
    failure_kind = None
    if result.timed_out:
        failure_kind = "TEST_TIMEOUT"
    elif status != "PASS":
        failure_kind = "TEST_FAILURE"
    return {
        "status": status,
        "passed": passed,
        "failed": 0 if status == "PASS" else 1,
        "categories": categories,
        "schema_count": len(list((PROJECT / "schemas").glob("*.schema.json"))),
        "command": "python -m unittest discover -s tests -v",
        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "source_commit": source_commit,
        "timeout_seconds": timeout_seconds,
        "timed_out": result.timed_out,
        "returncode": result.returncode,
        "failure_kind": failure_kind,
        "termination_error": result.termination_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-independent", action="store_true")
    parser.add_argument("--git-path", type=Path)
    parser.add_argument("--test-timeout-seconds", type=int, default=3600)
    parser.add_argument("--skill-timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    if not 60 <= args.test_timeout_seconds <= 7200:
        parser.error("--test-timeout-seconds must be between 60 and 7200")
    if not 10 <= args.skill_timeout_seconds <= 600:
        parser.error("--skill-timeout-seconds must be between 10 and 600")
    database = JobOpsDB(PROJECT / "state" / "jobops.db")
    database.initialize()
    external_action_baseline = audit_real_external_actions(database)
    try:
        source_commit = _source_commit(PROJECT, git_path=args.git_path)
    except JobOpsError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    test = _run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=PROJECT,
        timeout_seconds=args.test_timeout_seconds,
    )
    test_report = _build_test_report(
        test,
        source_commit=source_commit,
        timeout_seconds=args.test_timeout_seconds,
    )
    write_json(PROJECT / "reports" / "release-test-results.json", test_report)
    if test_report["status"] != "PASS":
        write_json(PROJECT / "reports" / "skill-validation.json", {
            "status": "SKIPPED",
            "reason": "RELEASE_TESTS_NOT_PASS",
            "test_failure_kind": test_report["failure_kind"],
        })
        print(json.dumps({"status": "FAIL", "tests": test_report}, ensure_ascii=False, indent=2))
        return 2
    skill_script = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT / "tests" / "skill_validation_shim")
    skill = _run_command(
        [sys.executable, str(skill_script), str(PROJECT / ".agents" / "skills" / "job-application-operator")],
        cwd=PROJECT,
        timeout_seconds=args.skill_timeout_seconds,
        env=environment,
    )
    write_json(PROJECT / "reports" / "skill-validation.json", {
        "status": "PASS" if skill.returncode == 0 and not skill.timed_out else "FAIL", "returncode": skill.returncode,
        "validator": "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py",
        "output": (skill.stdout + skill.stderr).strip()[:2000],
        "timeout_seconds": args.skill_timeout_seconds,
        "timed_out": skill.timed_out,
        "failure_kind": "SKILL_TIMEOUT" if skill.timed_out else None,
        "termination_error": skill.termination_error,
    })
    if skill.returncode != 0 or skill.timed_out:
        print(json.dumps({"status": "FAIL", "tests": test_report, "skill_returncode": skill.returncode}, ensure_ascii=False, indent=2))
        return 2
    result = verify_release(
        PROJECT,
        database,
        require_independent=args.require_independent,
        external_action_baseline=external_action_baseline,
        git_path=args.git_path,
    )
    result["source_commit"] = source_commit
    write_release_reports(PROJECT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
