from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .adapters import audit_real_external_actions
from .db import JobOpsDB
from .knowledge import KnowledgeGateway
from .locator import locate_knowledge_root
from .public_release import SAFE_PUBLIC_EMAILS
from .release import verify_release
from .release_toolchain import (
    LockedToolIdentity,
    ReleaseToolchainError,
    load_release_toolchain_policy,
    locked_authenticated_tool,
    locked_javascript_dependency_tree,
    sanitized_command_environment,
)
from .util import has_reparse_component, project_root, sha256_bytes


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_ABSOLUTE_USER_PATH = re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:password|cookie|oauth[_ -]?token|api[_ -]?key)\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"])")
_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UNITTEST_COUNT = re.compile(r"(?m)^Ran\s+([0-9]+)\s+tests?\s+in\s+")
_NODE_VERSION = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")
_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_CERTIFICATE_THUMBPRINT = re.compile(r"[0-9A-F]{40}")
_TRANSACTION_NONCE = re.compile(r"[0-9a-f]{64}")
_REPORT_KEYS = {
    "schema_version",
    "status",
    "source_commit",
    "passed",
    "failed",
    "schema_count",
    "javascript_e2e_count",
    "command_count",
    "network_actions",
    "recruiting_sites_visited",
    "knowledge_write_operations",
    "categories",
    "command",
    "command_summary",
    "tool_identities",
    "output_sha256",
    "external_actions",
    "observation_scope",
}
_LOCAL_OBSERVATION_SCOPE = {
    "counter_semantics": "JOBFLOW_COMPATIBILITY_COUNTERS_ONLY",
    "network_actions": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
    "recruiting_sites_visited": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
    "knowledge_write_operations": "JOBFLOW_KNOWLEDGE_GATEWAY_SNAPSHOTS_ONLY",
    "process_network_isolation": "UNATTESTED",
    "write_restore_detection": "UNATTESTED",
    "public_release_authority": "NONE",
}


class ReleaseVerificationError(RuntimeError):
    """A redacted, fail-closed release-verification failure."""


class _CommandRecorder:
    """Execute and attest checks without accepting caller-authored evidence."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.summary: list[dict[str, Any]] = []
        self._transcript_hash_inputs: list[str] = []
        self._seen: set[str] = set()

    def _record(self, command_id: str, exit_code: int, output: str) -> str:
        if _COMMAND_ID.fullmatch(command_id) is None or command_id in self._seen:
            raise ReleaseVerificationError("RELEASE_COMMAND_ID_INVALID")
        self._seen.add(command_id)
        sanitized = _sanitize_output(output, self.project)
        line_count = 0 if not sanitized else len(sanitized.split("\n"))
        digest = sha256_bytes(sanitized.encode("utf-8"))
        self.summary.append(
            {
                "id": command_id,
                "exit_code": exit_code,
                "line_count": line_count,
                "output_sha256": digest,
            }
        )
        self._transcript_hash_inputs.append(
            f"{command_id}\t{exit_code}\t{line_count}\t{digest}\t{sanitized}"
        )
        if exit_code != 0:
            raise ReleaseVerificationError(f"RELEASE_COMMAND_FAILED:{command_id}")
        return sanitized

    def run(
        self,
        command_id: str,
        executable: Path,
        arguments: list[str],
        *,
        tool: str,
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        print(f"[RUN] {command_id}", file=sys.stderr, flush=True)
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=self.project,
            env=sanitized_command_environment(
                tool,
                executable=executable,
                project=self.project,
                extra=extra_environment,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = self._record(command_id, completed.returncode, completed.stdout or "")
        print(f"[PASS] {command_id}", file=sys.stderr, flush=True)
        return output

    def record_operation(self, command_id: str, value: object) -> None:
        """Attest an operation executed directly in this process."""

        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._record(command_id, 0, encoded)

    def output_sha256(self) -> str:
        joined = "\n--JOBFLOW-COMMAND--\n".join(self._transcript_hash_inputs)
        return sha256_bytes(joined.encode("utf-8"))


def _native_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _sanitize_output(value: str, project: Path) -> str:
    """Redact machine-local values before hashing an in-memory transcript."""

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    replacements = [
        (str(project), "$PROJECT"),
        (os.environ.get("USERPROFILE", ""), "$USERPROFILE"),
        (os.environ.get("LOCALAPPDATA", ""), "$LOCALAPPDATA"),
        (os.environ.get("TEMP", ""), "$TEMP"),
        (os.environ.get("TMP", ""), "$TEMP"),
    ]
    for original, replacement in replacements:
        if original:
            text = text.replace(original, replacement)
    text = _EMAIL.sub("[redacted-email]", text)
    text = re.sub(
        r"(?i)\b(?:Bearer\s+[A-Za-z0-9._~+/=-]{12,}|sk-[A-Za-z0-9_-]{20,}|"
        r"gh[pousr]_[A-Za-z0-9]{20,})\b",
        "[redacted-secret]",
        text,
    )
    return text.rstrip()


def _safe_reports_root(project: Path) -> Path:
    project = project.resolve(strict=True)
    reports = project / "reports"
    if not reports.is_dir() or has_reparse_component(reports, project):
        raise ReleaseVerificationError("RELEASE_REPORTS_ROOT_UNSAFE")
    return reports


def _existing_regular_unique_file(path: Path, reports: Path) -> bytes | None:
    if not path.exists():
        return None
    if has_reparse_component(path, reports):
        raise ReleaseVerificationError("RELEASE_REPORT_TARGET_UNSAFE")
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ReleaseVerificationError("RELEASE_REPORT_TARGET_UNSAFE")
    return path.read_bytes()


def safe_atomic_write_bytes(path: Path, payload: bytes, project: Path) -> str:
    """Replace one report without following links or truncating a hardlink target."""

    reports = _safe_reports_root(project)
    path = Path(os.path.abspath(path))
    try:
        path.relative_to(reports)
    except ValueError as exc:
        raise ReleaseVerificationError("RELEASE_REPORT_PATH_OUTSIDE_ROOT") from exc
    _existing_regular_unique_file(path, reports)
    temporary = reports / f".jobflow-release-{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        binary_flag = getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | binary_flag, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ReleaseVerificationError("RELEASE_REPORT_TEMP_UNSAFE")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReleaseVerificationError("RELEASE_REPORT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        final_opened = os.fstat(descriptor)
        if not stat.S_ISREG(final_opened.st_mode) or final_opened.st_nlink != 1:
            raise ReleaseVerificationError("RELEASE_REPORT_TEMP_UNSAFE")
        os.close(descriptor)
        descriptor = -1
        if has_reparse_component(temporary, reports):
            raise ReleaseVerificationError("RELEASE_REPORT_TEMP_UNSAFE")
        os.replace(temporary, path)
        written = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(written.st_mode) or written.st_nlink != 1:
            raise ReleaseVerificationError("RELEASE_REPORT_REPLACEMENT_UNSAFE")
        return sha256_bytes(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_unlink(path: Path, project: Path) -> None:
    reports = _safe_reports_root(project)
    absolute = Path(os.path.abspath(path))
    try:
        absolute.relative_to(reports)
    except ValueError as exc:
        raise ReleaseVerificationError("RELEASE_REPORT_PATH_OUTSIDE_ROOT") from exc
    if absolute.exists() and has_reparse_component(absolute, reports):
        raise ReleaseVerificationError("RELEASE_REPORT_TARGET_UNSAFE")
    absolute.unlink(missing_ok=True)


def _restore(path: Path, previous: bytes | None, project: Path) -> None:
    if previous is None:
        _safe_unlink(path, project)
    else:
        safe_atomic_write_bytes(path, previous, project)


def _assert_redacted_report(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseVerificationError("RELEASE_REPORT_NOT_UTF8") from exc
    searchable = text.replace("\\\\", "\\")
    if _ABSOLUTE_USER_PATH.search(searchable) or _SECRET.search(searchable):
        raise ReleaseVerificationError("RELEASE_REPORT_PRIVATE_CONTENT_FORBIDDEN")
    for email in _EMAIL.findall(searchable):
        normalized = email.casefold()
        if normalized not in SAFE_PUBLIC_EMAILS and not normalized.endswith(("@users.noreply.github.com", "@jobops.local", "@example.test")):
            raise ReleaseVerificationError("RELEASE_REPORT_PRIVATE_CONTENT_FORBIDDEN")


def _strict_action_counts(value: object) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise ReleaseVerificationError("RELEASE_ACTION_AUDIT_INVALID")
    attempts = value.get("attempt_count")
    real = value.get("real_external_actions")
    if not _native_nonnegative_int(attempts) or not _native_nonnegative_int(real):
        raise ReleaseVerificationError("RELEASE_ACTION_AUDIT_INVALID")
    return attempts, real


def validate_release_test_report(
    report: object,
    *,
    project: Path,
    expected_commit: str,
    baseline_attempts: int,
    baseline_real: int,
    current_attempts: int,
    current_real: int,
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != _REPORT_KEYS:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    integer_fields = (
        "passed",
        "failed",
        "schema_count",
        "javascript_e2e_count",
        "command_count",
        "network_actions",
        "recruiting_sites_visited",
        "knowledge_write_operations",
    )
    if any(not _native_nonnegative_int(report.get(name)) for name in integer_fields):
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    observation_scope = report.get("observation_scope")
    if type(observation_scope) is not dict or observation_scope != _LOCAL_OBSERVATION_SCOPE:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_OBSERVATION_SCOPE_INVALID")
    if (
        report.get("schema_version") != 1
        or report.get("status") != "PASS"
        or report.get("failed") != 0
        or report.get("source_commit") != expected_commit
        or _COMMIT.fullmatch(expected_commit) is None
        or not isinstance(report.get("output_sha256"), str)
        or _SHA256.fullmatch(report["output_sha256"]) is None
        or report.get("command") != "scripts/run-release-verification.ps1"
        or report.get("network_actions") != 0
        or report.get("recruiting_sites_visited") != 0
        or report.get("knowledge_write_operations") != 0
    ):
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    categories = report.get("categories")
    if (
        not isinstance(categories, dict)
        or set(categories) != {"unittest_discovery"}
        or not _native_nonnegative_int(categories.get("unittest_discovery"))
        or categories.get("unittest_discovery") != report.get("passed")
    ):
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    tool_identities = report.get("tool_identities")
    if not isinstance(tool_identities, dict) or set(tool_identities) != {"node", "git", "python"}:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    try:
        tool_policies = load_release_toolchain_policy(project)["tools"]
    except ReleaseToolchainError as exc:
        raise ReleaseVerificationError(str(exc)) from exc
    for tool in ("node", "git", "python"):
        identity = tool_identities.get(tool)
        if (
            not isinstance(identity, dict)
            or set(identity)
            != {"status", "tool", "sha256", "signer_subject", "signer_thumbprint"}
            or identity.get("status") != "PASS"
            or identity.get("tool") != tool
            or not isinstance(identity.get("sha256"), str)
            or _SHA256.fullmatch(identity["sha256"]) is None
        ):
            raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
        subject = identity.get("signer_subject")
        thumbprint = identity.get("signer_thumbprint")
        signed = (
            isinstance(subject, str)
            and isinstance(thumbprint, str)
            and _CERTIFICATE_THUMBPRINT.fullmatch(thumbprint) is not None
            and any(
                signer.get("subject") == subject and signer.get("thumbprint") == thumbprint
                for signer in tool_policies[tool]["allowed_signers"]
            )
        )
        unsigned = (
            subject is None
            and thumbprint is None
            and identity["sha256"] in tool_policies[tool]["allowed_unsigned_sha256"]
        )
        if not signed and not unsigned:
            raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    summary = report.get("command_summary")
    if not isinstance(summary, list) or len(summary) != report["command_count"] or not summary:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    seen: set[str] = set()
    for item in summary:
        if not isinstance(item, dict) or set(item) != {"id", "exit_code", "line_count", "output_sha256"}:
            raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
        command_id = item.get("id")
        if (
            not isinstance(command_id, str)
            or _COMMAND_ID.fullmatch(command_id) is None
            or command_id in seen
        ):
            raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
        seen.add(command_id)
        if (
            type(item.get("exit_code")) is not int
            or item.get("exit_code") != 0
            or not _native_nonnegative_int(item.get("line_count"))
            or not isinstance(item.get("output_sha256"), str)
            or _SHA256.fullmatch(item["output_sha256"]) is None
        ):
            raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    required_commands = {
        "git-head-start",
        "git-clean-start",
        "external-actions-baseline",
        "git-tool-identity",
        "node-tool-identity",
        "python-tool-identity",
        "python-unittest-discovery",
        "javascript-package-lock",
        "javascript-dependency-tree",
        "javascript-runtime-version",
        "javascript-runner",
        "python-compileall",
        "runtime-schema-json",
        "public-repository-boundary",
        "git-head-before-report",
        "git-clean-before-report",
        "external-actions-final",
        "git-tool-identity-final",
        "node-tool-identity-final",
        "python-tool-identity-final",
    }
    javascript_commands = {value for value in seen if value.startswith("javascript-e2e-")}
    if not required_commands.issubset(seen) or len(javascript_commands) != report["javascript_e2e_count"]:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    actions = report.get("external_actions")
    if not isinstance(actions, dict):
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    required_actions = {
        "status",
        "baseline_attempt_count",
        "baseline_real_external_actions",
        "final_attempt_count",
        "final_real_external_actions",
        "attempt_delta",
        "real_external_action_delta",
    }
    if set(actions) != required_actions:
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    if any(
        not _native_nonnegative_int(actions.get(name))
        for name in required_actions - {"status"}
    ):
        raise ReleaseVerificationError("RELEASE_TEST_REPORT_INVALID")
    if (
        actions.get("status") != "PASS"
        or actions.get("baseline_attempt_count") != baseline_attempts
        or actions.get("baseline_real_external_actions") != baseline_real
        or actions.get("final_attempt_count") != current_attempts
        or actions.get("final_real_external_actions") != current_real
        or actions.get("attempt_delta") != current_attempts - baseline_attempts
        or actions.get("real_external_action_delta") != current_real - baseline_real
        or current_attempts != baseline_attempts
        or current_real != baseline_real
    ):
        raise ReleaseVerificationError("RELEASE_EXTERNAL_ACTION_DELTA_NONZERO")
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _assert_redacted_report(encoded)
    return report


def _resolved_git_executable(path: Path) -> Path:
    """Resolve a Git-for-Windows command shim to the binary it executes."""

    git = _validated_tool(path, "RELEASE_GIT_UNAVAILABLE")
    if git.name.casefold() != "git.exe":
        raise ReleaseVerificationError("RELEASE_GIT_UNAVAILABLE")
    parent_name = git.parent.name.casefold()
    if parent_name in {"cmd", "bin"}:
        install_root = git.parent.parent
        candidate = install_root / "mingw64" / "bin" / "git.exe"
        if candidate.is_file():
            return _validated_tool(candidate, "RELEASE_GIT_UNAVAILABLE")
    return git


def _git_arguments(project: Path, *arguments: str) -> list[str]:
    git_dir = project / ".git"
    if not git_dir.is_dir() or has_reparse_component(git_dir, project):
        raise ReleaseVerificationError("RELEASE_GIT_DIRECTORY_INVALID")
    return [
        f"--git-dir={git_dir}",
        f"--work-tree={project}",
        "-c",
        "core.hooksPath=NUL",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "protocol.allow=never",
        *arguments,
    ]


def _git(git_path: Path, project: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [str(git_path), *_git_arguments(project, *arguments)],
        cwd=project,
        env=sanitized_command_environment(
            "git",
            executable=git_path,
            project=project,
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise ReleaseVerificationError("RELEASE_GIT_FAILED")
    return completed.stdout.strip()


def _assert_frozen_git(project: Path, git_path: Path, expected_commit: str) -> None:
    if _git(git_path, project, "rev-parse", "HEAD").casefold() != expected_commit:
        raise ReleaseVerificationError("RELEASE_HEAD_CHANGED")
    if _git(
        git_path,
        project,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ):
        raise ReleaseVerificationError("RELEASE_WORKTREE_NOT_CLEAN")


def _database(project: Path) -> JobOpsDB:
    database_path = project / "state" / "jobops.db"
    if not database_path.is_file() or has_reparse_component(database_path, project):
        raise ReleaseVerificationError("RELEASE_DATABASE_UNAVAILABLE")
    return JobOpsDB(database_path)


def action_snapshot(project: Path) -> dict[str, int | str]:
    audit = audit_real_external_actions(_database(project))
    attempts, real = _strict_action_counts(audit)
    return {
        "status": "AUDIT_SNAPSHOT",
        "attempt_count": attempts,
        "real_external_actions": real,
    }


def knowledge_snapshot(project: Path) -> dict[str, object]:
    """Capture the allowlisted knowledge fingerprints for this verification run."""

    location = locate_knowledge_root(project, project / "config" / "knowledge-sources.json")
    return KnowledgeGateway(location).snapshot_collections()


def _validated_tool(path: Path, failure_code: str) -> Path:
    if not path.is_absolute():
        raise ReleaseVerificationError(failure_code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseVerificationError(failure_code) from exc
    if not resolved.is_file():
        raise ReleaseVerificationError(failure_code)
    return resolved


def _report_tool_identity(identity: LockedToolIdentity) -> dict[str, Any]:
    """Keep machine-local file identifiers out of the persisted release report."""

    return {
        "status": identity.status,
        "tool": identity.tool,
        "sha256": identity.sha256,
        "signer_subject": identity.signer_subject,
        "signer_thumbprint": identity.signer_thumbprint,
    }


_PYTHON_CHECK_BOOTSTRAP = """\
import os
import runpy
import subprocess
import sys

if os.name == "nt":
    # Release tests intentionally exercise the Windows installer and runtime
    # from child processes.  Keep those children attached to the captured test
    # streams without opening transient PowerShell or console windows.
    _jobflow_original_popen_init = subprocess.Popen.__init__
    _jobflow_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def _jobflow_hidden_popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | _jobflow_no_window
        return _jobflow_original_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _jobflow_hidden_popen_init

source_root, tests_root, site_packages, mode, *payload = sys.argv[1:]
sys.dont_write_bytecode = True
# Project code is the code under verification. Third-party packages are placed
# after the standard library and no .pth or sitecustomize file is executed.
sys.path[:0] = [source_root, tests_root]
sys.path.append(site_packages)
if mode == "-m" and payload:
    sys.argv = [payload[0], *payload[1:]]
    runpy.run_module(payload[0], run_name="__main__", alter_sys=False)
elif mode == "-c" and payload:
    sys.argv = ["-c", *payload[1:]]
    exec(compile(payload[0], "<jobflow-release-check>", "exec"), {"__name__": "__main__"})
else:
    raise SystemExit(97)
"""


def _isolated_python_arguments(project: Path, arguments: list[str]) -> list[str]:
    if not arguments or arguments[0] not in {"-m", "-c"}:
        raise ReleaseVerificationError("RELEASE_PYTHON_COMMAND_INVALID")
    roots = [
        project / "src",
        project / "tests",
        project / ".venv" / "Lib" / "site-packages",
    ]
    if any(not root.is_dir() or has_reparse_component(root, project) for root in roots):
        raise ReleaseVerificationError("RELEASE_PYTHON_IMPORT_ROOT_UNSAFE")
    return [
        "-I",
        "-P",
        "-S",
        "-B",
        "-X",
        "utf8",
        "-c",
        _PYTHON_CHECK_BOOTSTRAP,
        *(str(root) for root in roots),
        *arguments,
    ]


def _javascript_verification_contract(project: Path, suites: list[Path]) -> dict[str, Any]:
    """Validate the committed npm contract used by the fixed E2E runner."""

    package_path = project / "package.json"
    lock_path = project / "package-lock.json"
    runner_path = project / "scripts" / "run-javascript-e2e.cjs"
    required = [package_path, lock_path, runner_path, *suites]
    if any(not path.is_file() or has_reparse_component(path, project) for path in required):
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_CONTRACT_INVALID")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_CONTRACT_INVALID") from exc
    if not isinstance(package, dict) or not isinstance(lock, dict):
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_CONTRACT_INVALID")
    scripts = package.get("scripts")
    dependencies = package.get("devDependencies")
    playwright = dependencies.get("playwright") if isinstance(dependencies, dict) else None
    packages = lock.get("packages")
    root = packages.get("") if isinstance(packages, dict) else None
    locked_playwright = packages.get("node_modules/playwright") if isinstance(packages, dict) else None
    locked_core = packages.get("node_modules/playwright-core") if isinstance(packages, dict) else None
    if (
        package.get("private") is not True
        or not isinstance(scripts, dict)
        or scripts.get("test:e2e") != "node scripts/run-javascript-e2e.cjs"
        or not isinstance(playwright, str)
        or _SEMVER.fullmatch(playwright) is None
        or type(lock.get("lockfileVersion")) is not int
        or lock.get("lockfileVersion") != 3
        or lock.get("name") != package.get("name")
        or lock.get("version") != package.get("version")
        or not isinstance(root, dict)
        or not isinstance(root.get("devDependencies"), dict)
        or root["devDependencies"].get("playwright") != playwright
        or not isinstance(locked_playwright, dict)
        or locked_playwright.get("version") != playwright
        or not isinstance(locked_core, dict)
        or locked_core.get("version") != playwright
    ):
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_CONTRACT_INVALID")
    return {
        "status": "PASS",
        "playwright_version": playwright,
        "package_sha256": sha256_bytes(package_path.read_bytes()),
        "lock_sha256": sha256_bytes(lock_path.read_bytes()),
        "runner_sha256": sha256_bytes(runner_path.read_bytes()),
        "suites": [path.name for path in suites],
    }


def _validated_node_version(recorder: _CommandRecorder, node: Path) -> str:
    if node.name.casefold() not in {"node", "node.exe"}:
        raise ReleaseVerificationError("RELEASE_NODE_UNTRUSTED")
    output = recorder.run("javascript-runtime-version", node, ["--version"], tool="node")
    match = _NODE_VERSION.fullmatch(output)
    if match is None or int(match.group(1)) < 20:
        raise ReleaseVerificationError("RELEASE_NODE_UNTRUSTED")
    return output


def _validate_javascript_runner_output(output: str, suites: list[Path]) -> list[dict[str, str]]:
    """Require one explicit PASS payload for every suite run by the fixed runner."""

    expected_names = [path.name for path in suites]
    run_pattern = re.compile(r"\[RUN\] ([A-Za-z0-9][A-Za-z0-9._-]*e2e\.cjs)\r?\n")
    run_matches = list(run_pattern.finditer(output))
    observed_names = [match.group(1) for match in run_matches]
    if observed_names != expected_names:
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_EVIDENCE_INVALID")
    final_marker = f"JOBFLOW_JAVASCRIPT_E2E_PASS={len(suites)}"
    final_index = output.rfind(final_marker)
    if (
        not run_matches
        or output[: run_matches[0].start()].strip()
        or output.count(final_marker) != 1
        or final_index <= run_matches[-1].end()
        or output[final_index + len(final_marker) :].strip()
    ):
        raise ReleaseVerificationError("RELEASE_JAVASCRIPT_EVIDENCE_INVALID")
    evidence: list[dict[str, str]] = []
    for position, suite in enumerate(suites):
        start = run_matches[position].end()
        end = run_matches[position + 1].start() if position + 1 < len(run_matches) else final_index
        payloads: list[dict[str, Any]] = []
        for line in output[start:end].splitlines():
            try:
                parsed = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
        if len(payloads) != 1 or payloads[0].get("status") != "PASS":
            raise ReleaseVerificationError("RELEASE_JAVASCRIPT_EVIDENCE_INVALID")
        encoded = json.dumps(payloads[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        evidence.append(
            {
                "status": "PASS",
                "suite": suite.name,
                "output_sha256": sha256_bytes(encoded.encode("utf-8")),
            }
        )
    return evidence


def _assert_frozen_git_recorded(
    recorder: _CommandRecorder,
    git_path: Path,
    expected_commit: str,
    phase: str,
) -> None:
    head = recorder.run(
        f"git-head-{phase}",
        git_path,
        _git_arguments(recorder.project, "rev-parse", "HEAD"),
        tool="git",
    ).strip().casefold()
    if head != expected_commit or _COMMIT.fullmatch(head) is None:
        raise ReleaseVerificationError("RELEASE_HEAD_CHANGED")
    status = recorder.run(
        f"git-clean-{phase}",
        git_path,
        _git_arguments(
            recorder.project,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        tool="git",
    )
    if status:
        raise ReleaseVerificationError("RELEASE_WORKTREE_NOT_CLEAN")


def _publish_report_and_checkpoint(
    project: Path,
    report: dict[str, Any],
    *,
    expected_commit: str,
    baseline_attempts: int,
    baseline_real: int,
    git_path: Path,
    knowledge_baseline: dict[str, object] | None = None,
    require_public_release: bool = False,
    verification_run_id: str = "",
    transaction_nonce_sha256: str | None = None,
) -> dict[str, Any]:
    project = project.resolve(strict=True)
    if require_public_release:
        # This workstation can produce local QA evidence only.  Retain the
        # parameter so older callers fail with a stable, explicit blocker
        # instead of silently falling back to a weaker verification mode.
        raise ReleaseVerificationError("RELEASE_RUNTIME_CLOSURE_UNATTESTED")
    if _COMMIT.fullmatch(expected_commit) is None:
        raise ReleaseVerificationError("RELEASE_COMMIT_INVALID")
    if not git_path.is_absolute() or not git_path.is_file():
        raise ReleaseVerificationError("RELEASE_GIT_UNAVAILABLE")
    if transaction_nonce_sha256 is not None and _SHA256.fullmatch(transaction_nonce_sha256) is None:
        raise ReleaseVerificationError("RELEASE_SIGNING_TRANSACTION_NONCE_INVALID")
    _assert_frozen_git(project, git_path, expected_commit)
    database = _database(project)
    current = audit_real_external_actions(database)
    current_attempts, current_real = _strict_action_counts(current)
    validated = validate_release_test_report(
        report,
        project=project,
        expected_commit=expected_commit,
        baseline_attempts=baseline_attempts,
        baseline_real=baseline_real,
        current_attempts=current_attempts,
        current_real=current_real,
    )
    reports = _safe_reports_root(project)
    report_path = reports / "release-test-results.json"
    checkpoint_path = reports / "checkpoint-final.json"
    previous_report = _existing_regular_unique_file(report_path, reports)
    previous_checkpoint = _existing_regular_unique_file(checkpoint_path, reports)
    report_payload = (json.dumps(validated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _assert_redacted_report(report_payload)
    wrote_report = False
    wrote_checkpoint = False
    try:
        report_sha256 = safe_atomic_write_bytes(report_path, report_payload, project)
        wrote_report = True
        result = verify_release(
            project,
            database,
            require_independent=require_public_release,
            external_action_baseline={
                "attempt_count": baseline_attempts,
                "real_external_actions": baseline_real,
            },
            knowledge_baseline=knowledge_baseline,
            git_path=git_path,
        )
        if (
            result.get("status") != "PASS"
            or result.get("source_commit") != expected_commit
            or result.get("real_external_actions") != 0
            or result.get("tests") != validated
            or result.get("verification_scope") != "LOCAL_DEVELOPMENT"
            or result.get("public_release_ready") is not False
            or result.get("runtime_closure_status") != "UNATTESTED"
            or "RELEASE_RUNTIME_CLOSURE_UNATTESTED"
            not in result.get("public_release_blockers", [])
        ):
            raise ReleaseVerificationError("RELEASE_CHECKPOINT_NOT_PASSING")
        after_verify = audit_real_external_actions(database)
        after_attempts, after_real = _strict_action_counts(after_verify)
        if after_attempts != baseline_attempts or after_real != baseline_real:
            raise ReleaseVerificationError("RELEASE_EXTERNAL_ACTION_DELTA_NONZERO")
        _assert_frozen_git(project, git_path, expected_commit)
        checkpoint_payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _assert_redacted_report(checkpoint_payload)
        checkpoint_sha256 = safe_atomic_write_bytes(checkpoint_path, checkpoint_payload, project)
        wrote_checkpoint = True
        _assert_frozen_git(project, git_path, expected_commit)
        final = audit_real_external_actions(database)
        final_attempts, final_real = _strict_action_counts(final)
        if final_attempts != baseline_attempts or final_real != baseline_real:
            raise ReleaseVerificationError("RELEASE_EXTERNAL_ACTION_DELTA_NONZERO")
        recorded = {
            "status": "RELEASE_VERIFICATION_RECORDED",
            "source_commit": expected_commit,
            "verification_scope": result["verification_scope"],
            "verification_run_id": verification_run_id,
            "public_release_ready": result["public_release_ready"],
            "public_release_blockers": result["public_release_blockers"],
            "independent_qa_fresh": result["independent_qa"].get("fresh_for_current_release") is True,
            "report_sha256": report_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "real_external_actions": 0,
        }
        if transaction_nonce_sha256 is not None:
            # This binding is returned directly to the signer.  It is not read
            # back from either audit file, so replaying those files cannot
            # manufacture an authorization for a different transaction.
            recorded["transaction_nonce_sha256"] = transaction_nonce_sha256
        return recorded
    except Exception:
        if wrote_checkpoint:
            _restore(checkpoint_path, previous_checkpoint, project)
        if wrote_report:
            _restore(report_path, previous_report, project)
        raise


def run_release_verification(
    project: Path,
    *,
    node_path: Path,
    git_path: Path,
    require_public_release: bool = False,
    transaction_nonce_sha256: str | None = None,
) -> dict[str, Any]:
    """Run every release gate and create evidence from this execution only."""

    project = project.resolve(strict=True)
    if require_public_release:
        raise ReleaseVerificationError("RELEASE_RUNTIME_CLOSURE_UNATTESTED")
    verification_run_id = uuid.uuid4().hex
    node = _validated_tool(node_path, "RELEASE_NODE_UNAVAILABLE")
    git = _resolved_git_executable(git_path)
    python = _validated_tool(Path(sys.executable), "RELEASE_PYTHON_UNAVAILABLE")
    try:
        with ExitStack() as stack:
            node_locked = stack.enter_context(locked_authenticated_tool(project, node, "node"))
            git_locked = stack.enter_context(locked_authenticated_tool(project, git, "git"))
            python_locked = stack.enter_context(
                locked_authenticated_tool(project, python, "python")
            )
            dependency_evidence = stack.enter_context(
                locked_javascript_dependency_tree(project)
            )
            identities = {
                "node": _report_tool_identity(node_locked),
                "git": _report_tool_identity(git_locked),
                "python": _report_tool_identity(python_locked),
            }
            recorder = _CommandRecorder(project)
            recorder.record_operation("node-tool-identity", identities["node"])
            recorder.record_operation("git-tool-identity", identities["git"])
            recorder.record_operation("python-tool-identity", identities["python"])

            initial_head = recorder.run(
                "git-head-start",
                git,
                _git_arguments(project, "rev-parse", "HEAD"),
                tool="git",
            ).strip().casefold()
            if _COMMIT.fullmatch(initial_head) is None:
                raise ReleaseVerificationError("RELEASE_COMMIT_INVALID")
            initial_status = recorder.run(
                "git-clean-start",
                git,
                _git_arguments(
                    project,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                ),
                tool="git",
            )
            if initial_status:
                raise ReleaseVerificationError("RELEASE_WORKTREE_NOT_CLEAN")

            baseline = action_snapshot(project)
            baseline_attempts, baseline_real = _strict_action_counts(baseline)
            recorder.record_operation("external-actions-baseline", baseline)
            baseline_knowledge = knowledge_snapshot(project)
            recorder.record_operation("knowledge-baseline", baseline_knowledge)

            unittest_output = recorder.run(
                "python-unittest-discovery",
                python,
                _isolated_python_arguments(
                    project,
                    ["-m", "unittest", "discover", "-s", "tests", "-v"],
                ),
                tool="python",
                # The isolated Python environment intentionally excludes Git
                # from PATH. Tests that create synthetic repositories receive
                # the already resolved release Git as an explicit capability.
                extra_environment={"JOBFLOW_RELEASE_GIT_PATH": str(git)},
            )
            matches = _UNITTEST_COUNT.findall(unittest_output)
            if len(matches) != 1:
                raise ReleaseVerificationError("RELEASE_TEST_COUNT_INVALID")
            python_passed = int(matches[0])

            e2e_suites = sorted((project / "tests").glob("*e2e.cjs"), key=lambda path: path.name)
            if not e2e_suites:
                raise ReleaseVerificationError("RELEASE_E2E_MISSING")
            for suite in e2e_suites:
                if not suite.is_file() or has_reparse_component(suite, project):
                    raise ReleaseVerificationError("RELEASE_E2E_UNSAFE")
            javascript_contract = _javascript_verification_contract(project, e2e_suites)
            recorder.record_operation("javascript-package-lock", javascript_contract)
            recorder.record_operation("javascript-dependency-tree", dependency_evidence)
            _validated_node_version(recorder, node)
            javascript_output = recorder.run(
                "javascript-runner",
                node,
                [str(project / "scripts" / "run-javascript-e2e.cjs")],
                tool="node",
            )
            for suite, evidence in zip(
                e2e_suites,
                _validate_javascript_runner_output(javascript_output, e2e_suites),
                strict=True,
            ):
                recorder.record_operation(f"javascript-e2e-{suite.stem}", evidence)

            recorder.run(
                "python-compileall",
                python,
                _isolated_python_arguments(
                    project,
                    ["-m", "compileall", "-q", "src", "scripts"],
                ),
                tool="python",
            )
            schema_program = """\
import json
from pathlib import Path
paths = sorted(Path("schemas").glob("*.schema.json"))
if not paths:
    raise SystemExit(2)
for path in paths:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SystemExit(3)
print("SCHEMAS_VALID=" + str(len(paths)))
"""
            schema_output = recorder.run(
                "runtime-schema-json",
                python,
                _isolated_python_arguments(project, ["-c", schema_program]),
                tool="python",
            )
            schema_match = re.fullmatch(r"SCHEMAS_VALID=([0-9]+)", schema_output)
            if schema_match is None:
                raise ReleaseVerificationError("RELEASE_SCHEMA_COUNT_INVALID")
            schema_count = int(schema_match.group(1))

            public_output = recorder.run(
                "public-repository-boundary",
                python,
                _isolated_python_arguments(
                    project,
                    [
                        "-m",
                        "jobops.public_release",
                        "--git-path",
                        str(git),
                    ],
                ),
                tool="python",
            )
            try:
                public_result = json.loads(public_output)
            except json.JSONDecodeError as exc:
                raise ReleaseVerificationError("RELEASE_PUBLIC_RESULT_INVALID") from exc
            if not isinstance(public_result, dict) or public_result.get("status") != "PASS":
                raise ReleaseVerificationError("RELEASE_PUBLIC_BOUNDARY_FAILED")

            _assert_frozen_git_recorded(recorder, git, initial_head, "before-report")
            recorder.record_operation("node-tool-identity-final", identities["node"])
            recorder.record_operation("git-tool-identity-final", identities["git"])
            recorder.record_operation("python-tool-identity-final", identities["python"])
            final_audit = action_snapshot(project)
            final_attempts, final_real = _strict_action_counts(final_audit)
            recorder.record_operation("external-actions-final", final_audit)
            if final_attempts != baseline_attempts or final_real != baseline_real:
                raise ReleaseVerificationError("RELEASE_EXTERNAL_ACTION_DELTA_NONZERO")

            report: dict[str, Any] = {
                "schema_version": 1,
                "status": "PASS",
                "source_commit": initial_head,
                "passed": python_passed,
                "failed": 0,
                "schema_count": schema_count,
                "javascript_e2e_count": len(e2e_suites),
                "command_count": len(recorder.summary),
                "network_actions": 0,
                "recruiting_sites_visited": 0,
                "knowledge_write_operations": 0,
                "observation_scope": dict(_LOCAL_OBSERVATION_SCOPE),
                "categories": {"unittest_discovery": python_passed},
                "command": "scripts/run-release-verification.ps1",
                "command_summary": recorder.summary,
                "tool_identities": identities,
                "output_sha256": recorder.output_sha256(),
                "external_actions": {
                    "status": "PASS",
                    "baseline_attempt_count": baseline_attempts,
                    "baseline_real_external_actions": baseline_real,
                    "final_attempt_count": final_attempts,
                    "final_real_external_actions": final_real,
                    "attempt_delta": final_attempts - baseline_attempts,
                    "real_external_action_delta": final_real - baseline_real,
                },
            }
            return _publish_report_and_checkpoint(
                project,
                report,
                expected_commit=initial_head,
                baseline_attempts=baseline_attempts,
                baseline_real=baseline_real,
                git_path=git,
                knowledge_baseline=baseline_knowledge,
                require_public_release=require_public_release,
                verification_run_id=verification_run_id,
                transaction_nonce_sha256=transaction_nonce_sha256,
            )
    except ReleaseToolchainError as exc:
        raise ReleaseVerificationError(str(exc)) from exc


def authenticate_signing_evidence(
    project: Path,
    *,
    expected_commit: str,
    git_path: Path,
) -> dict[str, Any]:
    """Reject the retired filesystem-evidence signing path.

    ``reports/*.json`` are useful audit artifacts, but they live in an ignored,
    same-user-writable directory.  They can never authorize a signature.  This
    callable remains only so older automation fails with a specific safe error
    instead of silently falling back to the historical behavior.
    """

    del project, expected_commit, git_path
    raise ReleaseVerificationError("RELEASE_SIGNING_PERSISTED_EVIDENCE_FORBIDDEN")


def run_signing_transaction_verification(
    project: Path,
    *,
    node_path: Path,
    git_path: Path,
    expected_commit: str,
    transaction_nonce: str,
) -> dict[str, Any]:
    """Run nonce-bound local release QA without authorizing a public signature.

    The current publisher workstation cannot attest the complete executable
    runtime closure that participates in signing.  A fresh local verification
    is still useful evidence, but its result is deliberately shaped as
    local-only and can never be interpreted as signing authorization.
    """

    project = project.resolve(strict=True)
    expected_commit = expected_commit.casefold()
    transaction_nonce = transaction_nonce.casefold()
    if _COMMIT.fullmatch(expected_commit) is None:
        raise ReleaseVerificationError("RELEASE_COMMIT_INVALID")
    if _TRANSACTION_NONCE.fullmatch(transaction_nonce) is None:
        raise ReleaseVerificationError("RELEASE_SIGNING_TRANSACTION_NONCE_INVALID")
    transaction_nonce_sha256 = sha256_bytes(transaction_nonce.encode("ascii"))
    verification = run_release_verification(
        project,
        node_path=node_path,
        git_path=git_path,
        require_public_release=False,
        transaction_nonce_sha256=transaction_nonce_sha256,
    )
    if (
        verification.get("status") != "RELEASE_VERIFICATION_RECORDED"
        or verification.get("source_commit") != expected_commit
        or verification.get("verification_scope") != "LOCAL_DEVELOPMENT"
        or verification.get("real_external_actions") != 0
        or verification.get("transaction_nonce_sha256") != transaction_nonce_sha256
        or not isinstance(verification.get("report_sha256"), str)
        or _SHA256.fullmatch(verification["report_sha256"]) is None
        or not isinstance(verification.get("checkpoint_sha256"), str)
        or _SHA256.fullmatch(verification["checkpoint_sha256"]) is None
        or not isinstance(verification.get("verification_run_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", verification["verification_run_id"]) is None
    ):
        raise ReleaseVerificationError("RELEASE_LOCAL_QA_VERIFICATION_INVALID")
    final_git = _resolved_git_executable(git_path)
    try:
        with locked_authenticated_tool(project, final_git, "git"):
            _assert_frozen_git(project, final_git, expected_commit)
    except ReleaseToolchainError as exc:
        raise ReleaseVerificationError(str(exc)) from exc
    return {
        "schema_version": 1,
        "status": "LOCAL_BEST_EFFORT_VERIFICATION_PASS",
        "source_commit": expected_commit,
        "verification_scope": "LOCAL_RELEASE_QA",
        "verification_run_id": verification["verification_run_id"],
        "transaction_nonce_sha256": transaction_nonce_sha256,
        "report_sha256": verification["report_sha256"],
        "checkpoint_sha256": verification["checkpoint_sha256"],
        "public_release_ready": False,
        "public_release_blockers": ["RELEASE_RUNTIME_CLOSURE_UNATTESTED"],
        "real_external_actions": 0,
    }


def _parse_run_arguments(arguments: list[str]) -> tuple[Path, Path]:
    if len(arguments) != 4:
        raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
    values: dict[str, Path] = {}
    for index in range(0, len(arguments), 2):
        name = arguments[index]
        if name not in {"--node", "--git"} or name in values:
            raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
        values[name] = Path(arguments[index + 1])
    if set(values) != {"--node", "--git"}:
        raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
    return values["--node"], values["--git"]


def _parse_signing_arguments(arguments: list[str]) -> tuple[Path, Path, str, str]:
    if len(arguments) != 8:
        raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
    values: dict[str, str] = {}
    for index in range(0, len(arguments), 2):
        name = arguments[index]
        if name not in {"--node", "--git", "--expected-commit", "--transaction-nonce"} or name in values:
            raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
        values[name] = arguments[index + 1]
    if set(values) != {"--node", "--git", "--expected-commit", "--transaction-nonce"}:
        raise ReleaseVerificationError("RELEASE_VERIFICATION_ARGUMENTS_INVALID")
    return (
        Path(values["--node"]),
        Path(values["--git"]),
        values["--expected-commit"],
        values["--transaction-nonce"],
    )


def main() -> int:
    try:
        project = project_root(Path.cwd())
        command = sys.argv[1] if len(sys.argv) >= 2 else ""
        if command == "run":
            node_path, git_path = _parse_run_arguments(sys.argv[2:])
            value = run_release_verification(project, node_path=node_path, git_path=git_path)
        elif command == "signing-transaction":
            node_path, git_path, expected_commit, transaction_nonce = _parse_signing_arguments(sys.argv[2:])
            value = run_signing_transaction_verification(
                project,
                node_path=node_path,
                git_path=git_path,
                expected_commit=expected_commit,
                transaction_nonce=transaction_nonce,
            )
        elif command == "authenticate-signing-evidence" and len(sys.argv) == 2:
            raise ReleaseVerificationError("RELEASE_SIGNING_PERSISTED_EVIDENCE_FORBIDDEN")
        else:
            raise ReleaseVerificationError("RELEASE_VERIFICATION_COMMAND_INVALID")
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ReleaseVerificationError, json.JSONDecodeError, UnicodeError, OSError) as exc:
        code = str(exc) if isinstance(exc, ReleaseVerificationError) else "RELEASE_VERIFICATION_FAILED"
        print(json.dumps({"status": "FAIL", "code": code}, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps({"status": "FAIL", "code": "RELEASE_VERIFICATION_FAILED"}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
