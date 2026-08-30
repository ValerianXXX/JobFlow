from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .adapters import audit_real_external_actions
from .db import JobOpsDB
from .errors import JobOpsError
from .knowledge import KnowledgeGateway
from .locator import locate_knowledge_root
from .public_release import SAFE_PUBLIC_EMAILS, verify_public_repository
from .release_toolchain import (
    ReleaseToolchainError,
    locked_release_git,
    sanitized_command_environment,
)
from .util import has_reparse_component, load_json, sha256_file, write_json


ABSOLUTE_USER_PATH = re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")
SECRET_PATTERNS = {
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "bearer": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_key": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(r"(?i)\b(?:password|cookie|oauth[_ -]?token|api[_ -]?key)\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]"),
    "verification_code_assignment": re.compile(r"(?i)\b(?:otp|verification[_ -]?code|验证码)\s*[:=]\s*['\"]?[0-9]{4,10}"),
    "username_assignment": re.compile(r"(?i)\b(?:username|user[_ -]?name|用户名)\s*[:=]\s*['\"][^'\"\r\n]{3,}['\"]"),
    "phone": re.compile(r"(?<![0-9])(?:\+?1[ .-]?)?\(?[2-9][0-9]{2}\)?[ .-][0-9]{3}[ .-][0-9]{4}(?![0-9])"),
    "street_address": re.compile(r"(?i)\b[0-9]{1,6}\s+[A-Z0-9.' -]{2,40}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr)\b"),
}
TEXT_EXTENSIONS = {".py", ".js", ".css", ".json", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".xml", ".rels", ".csv"}
RELEASE_INPUT_EXTENSIONS = TEXT_EXTENSIONS | {".bat", ".cmd"}
RELEASE_INPUT_ROOTS = ("src", "schemas", "tests", "config", ".agents", "scripts", "browser-companion", ".github/workflows")
ROOT_LAUNCHER_EXTENSIONS = {".bat", ".cmd", ".ps1"}
RELEASE_RUNTIME_CLOSURE_STATUS = "UNATTESTED"
RELEASE_RUNTIME_CLOSURE_BLOCKER = "RELEASE_RUNTIME_CLOSURE_UNATTESTED"


def _source_commit(project: Path, *, git_path: Path | None = None) -> str:
    try:
        with locked_release_git(project, git_path) as executable:
            environment = sanitized_command_environment(
                "git", executable=executable, project=project
            )
            completed = subprocess.run(
                [str(executable), "rev-parse", "HEAD"],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
    except (OSError, ReleaseToolchainError) as error:
        code = str(error) if isinstance(error, ReleaseToolchainError) else "RELEASE_GIT_UNTRUSTED"
        if code not in {
            "RELEASE_GIT_PATH_REQUIRED",
            "RELEASE_GIT_PATH_INVALID",
            "RELEASE_GIT_UNTRUSTED",
            "RELEASE_GIT_CHANGED",
        }:
            code = "RELEASE_GIT_UNTRUSTED"
        raise JobOpsError(
            code,
            "The trusted absolute Git executable required for release verification is unavailable.",
        ) from error
    commit = completed.stdout.strip().casefold()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise JobOpsError(
            "RELEASE_GIT_FAILED",
            "The exact source commit required for the release checkpoint is unavailable.",
        )
    return commit


def _latest_release_input_mtime(project: Path) -> float:
    latest = 0.0
    for root_name in RELEASE_INPUT_ROOTS:
        root = project / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in RELEASE_INPUT_EXTENSIONS:
                continue
            if any(part in {"__pycache__", ".tmp"} for part in path.relative_to(root).parts):
                continue
            latest = max(latest, path.stat().st_mtime)
    for path in project.iterdir():
        if path.is_file() and path.suffix.casefold() in ROOT_LAUNCHER_EXTENSIONS:
            latest = max(latest, path.stat().st_mtime)
    return latest


def _release_test_report_matches_source(tests: object, source_commit: str) -> bool:
    return bool(
        isinstance(tests, dict)
        and tests.get("status") == "PASS"
        and type(tests.get("failed")) is int
        and tests.get("failed") == 0
        and tests.get("source_commit") == source_commit
    )


def _skill_validation_passes(report: object) -> bool:
    return bool(
        isinstance(report, dict)
        and report.get("status") == "PASS"
        and type(report.get("returncode")) is int
        and report.get("returncode") == 0
        and report.get("validator") == "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py"
        and isinstance(report.get("output"), str)
        and report["output"].strip() == "Skill is valid!"
    )


def _normalized_sha256(value: object) -> str:
    if not isinstance(value, str):
        return ""
    material = value.strip().casefold()
    if not material.startswith("sha256:"):
        material = "sha256:" + material
    return material if re.fullmatch(r"sha256:[0-9a-f]{64}", material) else ""


def _qa_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _qa_integer(value: object, default: int = -1) -> int:
    return value if type(value) is int else default


def _independent_qa_matches_release(
    independent: dict[str, Any],
    candidate: dict[str, Any],
    tests: dict[str, Any],
) -> bool:
    """Bind independent QA to the exact frozen commit and deterministic archive."""
    archive = _qa_mapping(independent.get("archive"))
    source_freeze = _qa_mapping(independent.get("source_freeze"))
    qa_tests = _qa_mapping(independent.get("tests"))
    schemas = _qa_mapping(independent.get("schemas"))
    knowledge = _qa_mapping(independent.get("knowledge"))
    actions = _qa_mapping(independent.get("external_actions"))
    security = _qa_mapping(independent.get("security_scan"))
    candidate_archive = _qa_mapping(candidate.get("archive"))
    candidate_commit = str(candidate.get("commit") or "").casefold()
    candidate_sha256 = _normalized_sha256(candidate.get("artifact_sha256"))
    qa_sha256 = _normalized_sha256(archive.get("sha256"))

    return bool(
        independent.get("status") == "PASS"
        and "ISOLATED_READ_ONLY" in str(independent.get("qa_mode") or "")
        and independent.get("source_tree_modified_by_qa") is False
        and candidate.get("status") == "RELEASE_CANDIDATE_BUILT"
        and re.fullmatch(r"[0-9a-f]{40}", candidate_commit)
        and candidate_sha256
        and _qa_integer(candidate.get("reproducible_builds"), 0) >= 2
        and candidate_archive.get("status") == "PASS"
        and _qa_integer(candidate_archive.get("finding_count")) == 0
        and _qa_integer(candidate.get("external_network_actions")) == 0
        and _qa_integer(candidate.get("real_external_actions")) == 0
        and archive.get("status") == "PASS"
        and str(archive.get("commit") or "").casefold() == candidate_commit
        and qa_sha256 == candidate_sha256
        and archive.get("byte_reproducible_from_frozen_clone") is True
        and _qa_integer(archive.get("candidate_findings")) == 0
        and source_freeze.get("status") == "UNCHANGED"
        and str(source_freeze.get("head") or "").casefold() == candidate_commit
        and source_freeze.get("product_code_changed_since_full_isolated_regression") is False
        and qa_tests.get("status") == "PASS"
        and _qa_integer(qa_tests.get("passed")) == _qa_integer(tests.get("passed"), -2)
        and _qa_integer(qa_tests.get("failed")) == 0
        and schemas.get("status") == "PASS"
        and _qa_integer(schemas.get("valid")) == _qa_integer(tests.get("schema_count"), -2)
        and _qa_integer(schemas.get("total")) == _qa_integer(tests.get("schema_count"), -2)
        and knowledge.get("status") == "UNCHANGED"
        and _qa_integer(knowledge.get("write_operations")) == 0
        and actions.get("status") == "PASS"
        and _qa_integer(actions.get("qa_real_external_actions")) == 0
        and _qa_integer(actions.get("isolated_candidate_real_external_actions")) == 0
        and _qa_integer(actions.get("isolated_candidate_external_network_actions")) == 0
        and _qa_integer(actions.get("real_recruiting_sites_visited")) == 0
        and security.get("status") == "PASS"
        and _qa_integer(security.get("isolated_candidate_findings")) == 0
        and _qa_integer(security.get("public_tree_findings")) == 0
        and _qa_integer(security.get("public_history_findings")) == 0
        and _qa_integer(security.get("private_staging_files")) == 0
        and _qa_integer(security.get("private_ciphertext_integrity_failures")) == 0
        and _qa_integer(independent.get("p0_open")) == 0
        and _qa_integer(independent.get("p1_open")) == 0
        and _qa_integer(independent.get("must_fix_open")) == 0
    )


def _scan_text(label: str, text: str, findings: list[dict[str, str]]) -> None:
    if ABSOLUTE_USER_PATH.search(text):
        findings.append({"kind": "absolute_user_path", "location": label})
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append({"kind": kind, "location": label})
    for email in re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text):
        normalized = email.casefold()
        if normalized not in SAFE_PUBLIC_EMAILS and not normalized.endswith(("@example.test", "@jobops.local", "@users.noreply.github.com")):
            findings.append({"kind": "email", "location": label})


def security_scan(project: Path, database: JobOpsDB) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    private_reference_rows: list[tuple[str, str, str]] = []
    roots = [
        project / "src", project / "config", project / "schemas", project / ".agents",
        project / "state", project / "reports", project / "workspace", project / "tests",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix.casefold() in {".db", ".png", ".pdf", ".docx", ".pyc"}:
                continue
            if path.suffix.casefold() in TEXT_EXTENSIONS or path.name in {"SKILL.md"}:
                try:
                    _scan_text(path.relative_to(project).as_posix(), path.read_text(encoding="utf-8-sig"), findings)
                except UnicodeDecodeError:
                    findings.append({"kind": "undecodable_text", "location": path.relative_to(project).as_posix()})
    artifact_files = {path for root in roots if root.exists() for path in root.rglob("*") if path.is_file()}
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".docx"), key=str):
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith((".xml", ".rels")):
                        _scan_text(f"{path.relative_to(project).as_posix()}!{name}", archive.read(name).decode("utf-8", errors="ignore"), findings)
        except zipfile.BadZipFile:
            findings.append({"kind": "invalid_docx", "location": path.relative_to(project).as_posix()})
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".pdf"), key=str):
        try:
            from .document_qa import extract_pdf_text
            pdf_text, _ = extract_pdf_text(path)
            _scan_text(path.relative_to(project).as_posix() + "#text", pdf_text, findings)
            _scan_text(path.relative_to(project).as_posix() + "#metadata", path.read_bytes().decode("latin-1", errors="ignore"), findings)
        except Exception:
            findings.append({"kind": "invalid_or_unreadable_pdf", "location": path.relative_to(project).as_posix()})
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".png"), key=str):
        try:
            from PIL import Image
            with Image.open(path) as image:
                _scan_text(path.relative_to(project).as_posix() + "#metadata", json.dumps(dict(image.info), default=str), findings)
        except Exception:
            findings.append({"kind": "invalid_png", "location": path.relative_to(project).as_posix()})
    if database.path.is_file():
        with database.connect() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                for row in connection.execute(f'SELECT * FROM "{table}"'):
                    _scan_text(f"state/jobops.db#{table}", json.dumps(list(row), ensure_ascii=False, default=str), findings)
            if "private_refs" in tables:
                private_reference_rows = [
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in connection.execute(
                        "SELECT secure_ref,ciphertext_sha256,status FROM private_refs"
                    )
                ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    private_root = Path(local_app_data) / "JobOps" / "private" if local_app_data else None
    staging_residue = []
    ciphertext_residue = []
    private_temporary_residue = []
    private_ciphertext_integrity_failures = 0
    retained_private_rows = [row for row in private_reference_rows if row[2] != "DELETED"]
    if private_root and has_reparse_component(private_root):
        findings.append({"kind": "private_store_reparse_forbidden", "location": "$LOCALAPPDATA/JobOps/private"})
        private_ciphertext_integrity_failures += max(1, len(retained_private_rows))
    elif private_root and private_root.is_dir():
        staging = private_root / "staging"
        if has_reparse_component(staging, private_root):
            findings.append({"kind": "private_staging_reparse_forbidden", "location": "$LOCALAPPDATA/JobOps/private/staging"})
        elif staging.is_dir():
            staging_entries = list(staging.rglob("*"))
            unsafe_staging = [path for path in staging_entries if has_reparse_component(path, staging)]
            if unsafe_staging:
                findings.append({"kind": "private_staging_reparse_forbidden", "location": "$LOCALAPPDATA/JobOps/private/staging"})
            staging_residue = [path for path in staging_entries if path not in unsafe_staging and path.is_file()]
        ciphertext_residue = [
            path for path in private_root.iterdir()
            if path.name.casefold().endswith(".dpapi")
        ]
        private_temporary_residue = [
            path for path in private_root.iterdir()
            if path.name.startswith(".jobflow-write-")
        ]
        findings.extend({"kind": "private_staging_residue", "location": "$LOCALAPPDATA/JobOps/private/staging"} for _ in staging_residue)
        findings.extend({"kind": "private_atomic_write_residue", "location": "$LOCALAPPDATA/JobOps/private"} for _ in private_temporary_residue)
        expected: dict[str, str] = {}
        for reference, ciphertext_sha256, status in retained_private_rows:
            match = re.fullmatch(r"secure-ref:([A-Za-z0-9_-]{8,128})", reference)
            if match is None:
                findings.append({"kind": "invalid_private_reference_metadata", "location": "state/jobops.db#private_refs"})
                private_ciphertext_integrity_failures += 1
                continue
            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", ciphertext_sha256) is None:
                findings.append({"kind": "invalid_private_ciphertext_hash", "location": "state/jobops.db#private_refs"})
                private_ciphertext_integrity_failures += 1
                continue
            expected[match.group(1) + ".dpapi"] = ciphertext_sha256.casefold()
            if status == "CORRUPT":
                findings.append({"kind": "corrupt_private_reference", "location": "state/jobops.db#private_refs"})
                private_ciphertext_integrity_failures += 1

        discovered = {path.name: path for path in ciphertext_residue}
        unsafe_names: set[str] = set()
        for name, path in discovered.items():
            if has_reparse_component(path, private_root):
                findings.append({"kind": "private_ciphertext_reparse_forbidden", "location": "$LOCALAPPDATA/JobOps/private"})
                private_ciphertext_integrity_failures += 1
                unsafe_names.add(name)
                continue
            if not path.is_file():
                findings.append({"kind": "invalid_private_ciphertext_type", "location": "$LOCALAPPDATA/JobOps/private"})
                private_ciphertext_integrity_failures += 1
                continue
            if name not in expected:
                findings.append({"kind": "orphan_private_ciphertext", "location": "$LOCALAPPDATA/JobOps/private"})
                private_ciphertext_integrity_failures += 1
                continue
            try:
                actual_hash = sha256_file(path)
            except OSError:
                actual_hash = ""
            if actual_hash != expected[name]:
                findings.append({"kind": "private_ciphertext_hash_mismatch", "location": "$LOCALAPPDATA/JobOps/private"})
                private_ciphertext_integrity_failures += 1
        for name in expected:
            if name not in discovered and name not in unsafe_names:
                findings.append({"kind": "missing_private_ciphertext", "location": "$LOCALAPPDATA/JobOps/private"})
                private_ciphertext_integrity_failures += 1
    elif private_root and private_root.exists():
        findings.append({"kind": "invalid_private_store_type", "location": "$LOCALAPPDATA/JobOps/private"})
        private_ciphertext_integrity_failures += max(1, len(retained_private_rows))
    elif retained_private_rows:
        findings.append({"kind": "missing_private_ciphertext", "location": "$LOCALAPPDATA/JobOps/private"})
        private_ciphertext_integrity_failures += len(retained_private_rows)
    unique = sorted({(item["kind"], item["location"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL", "finding_count": len(unique),
        "findings": [{"kind": kind, "location": location} for kind, location in unique],
        "scanned_zones": [
            "source", "config", "schemas", "skill", "tests", "sqlite", "state", "reports",
            "docx_xml", "pdf_text_and_metadata", "png_metadata", "workspace", "private_staging",
            "private_ciphertext_integrity",
        ],
        "private_staging_file_count": len(staging_residue),
        "private_temporary_file_count": len(private_temporary_residue),
        "private_ciphertext_file_count": len(ciphertext_residue),
        "private_expected_ciphertext_file_count": len(retained_private_rows),
        "private_ciphertext_integrity_failure_count": private_ciphertext_integrity_failures,
    }


def _external_action_verification_window(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline_value = baseline or {"attempt_count": 0, "real_external_actions": 0}
    raw_baseline_attempts = baseline_value.get("attempt_count")
    raw_baseline_real = baseline_value.get("real_external_actions")
    raw_current_attempts = current.get("attempt_count")
    raw_current_real = current.get("real_external_actions")
    counts_have_native_types = all(
        type(value) is int
        for value in (raw_baseline_attempts, raw_baseline_real, raw_current_attempts, raw_current_real)
    )
    baseline_attempts = raw_baseline_attempts if type(raw_baseline_attempts) is int else 0
    baseline_real = raw_baseline_real if type(raw_baseline_real) is int else 0
    current_attempts = raw_current_attempts if type(raw_current_attempts) is int else 0
    current_real = raw_current_real if type(raw_current_real) is int else 0
    baseline_valid = (
        counts_have_native_types
        and baseline_attempts >= 0
        and baseline_real >= 0
        and current_attempts >= 0
        and current_real >= 0
        and baseline_attempts <= current_attempts
        and baseline_real <= current_real
    )
    attempt_delta = current_attempts - baseline_attempts if baseline_valid else current_attempts
    real_delta = current_real - baseline_real if baseline_valid else current_real
    return {
        "attempt_count": attempt_delta,
        "real_external_actions": real_delta,
        "status": "PASS" if baseline_valid and real_delta == 0 else "FAIL",
        "baseline_valid": baseline_valid,
        "baseline_attempt_count": baseline_attempts,
        "baseline_real_external_actions": baseline_real,
        "lifetime_attempt_count": current_attempts,
        "lifetime_real_external_actions": current_real,
        "evidence": "append-only external_action_attempts bounded by verification-start baseline",
    }


def _release_truth(
    *,
    core_pass: bool,
    independent_fresh: bool,
    public_repository: dict[str, Any],
) -> dict[str, Any]:
    """Separate repository hygiene from authoritative public-release readiness.

    The local verifier can establish a clean public repository boundary and a
    passing local QA checkpoint.  It cannot attest the protected publisher
    runtime closure, so none of those local facts can authorize a public
    release.  Keep the two readiness concepts explicit in every checkpoint.
    """

    blockers = [RELEASE_RUNTIME_CLOSURE_BLOCKER]
    if not core_pass:
        blockers.append("CORE_RELEASE_CHECK_FAILED")
    blockers.extend(public_repository.get("public_release_blockers", []))
    if not independent_fresh:
        blockers.append("INDEPENDENT_QA_STALE_OR_MISSING")
    return {
        "public_repository_ready": public_repository.get("public_repository_ready") is True,
        "runtime_closure_status": RELEASE_RUNTIME_CLOSURE_STATUS,
        "public_release_ready": False,
        "public_release_blockers": list(dict.fromkeys(blockers)),
    }


def verify_release(
    project: Path,
    database: JobOpsDB,
    *,
    require_independent: bool = False,
    external_action_baseline: dict[str, Any] | None = None,
    knowledge_baseline: dict[str, object] | None = None,
    git_path: Path | None = None,
) -> dict[str, Any]:
    source_commit = _source_commit(project, git_path=git_path)
    test_report_path = project / "reports" / "release-test-results.json"
    if not test_report_path.is_file():
        raise JobOpsError("RELEASE_TEST_REPORT_MISSING", "Run the checked-in release verification script before verify-release.")
    tests = load_json(test_report_path)
    if (
        not isinstance(tests, dict)
        or tests.get("status") != "PASS"
        or type(tests.get("failed")) is not int
        or tests.get("failed") != 0
    ):
        raise JobOpsError("RELEASE_TESTS_FAILED", "The most recent full regression report is not passing.")
    if not _release_test_report_matches_source(tests, source_commit):
        raise JobOpsError("RELEASE_TEST_REPORT_SOURCE_MISMATCH", "The full regression report is not bound to the current source commit.")
    latest_input_mtime = _latest_release_input_mtime(project)
    if test_report_path.stat().st_mtime < latest_input_mtime:
        raise JobOpsError("RELEASE_TEST_REPORT_STALE", "Run the checked-in release verification script after the latest source changes.")
    location = locate_knowledge_root(project, project / "config" / "knowledge-sources.json")
    gateway = KnowledgeGateway(location)
    current_knowledge = gateway.snapshot_collections()
    baseline_knowledge = (
        knowledge_baseline
        if knowledge_baseline is not None
        else load_json(project / "state" / "knowledge-baseline.json")
    )
    knowledge = gateway.compare_snapshots(baseline_knowledge, current_knowledge)
    knowledge["collections"] = current_knowledge["collections"]
    knowledge["write_operations"] = 0
    lifetime_actions = audit_real_external_actions(database)
    actions = _external_action_verification_window(lifetime_actions, external_action_baseline)
    scan = security_scan(project, database)
    public_repository = verify_public_repository(project, git_path=git_path)
    with database.connect() as connection:
        active_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE'").fetchone()[0])
        active_real_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=0").fetchone()[0])
        active_synthetic_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=1").fetchone()[0])
        retained_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status!='DELETED'").fetchone()[0])
        retained_synthetic_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status!='DELETED' AND synthetic=1").fetchone()[0])
        revoked_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='REVOKED'").fetchone()[0])
        corrupt_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='CORRUPT'").fetchone()[0])
        active_onboarding_packets = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=0 AND kind='onboarding_review_packet'").fetchone()[0])
        deleted_synthetic = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='DELETED' AND synthetic=1").fetchone()[0])
        schema_version = database.schema_version()
        dry_run_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()[0]
    skill_validation_path = project / "reports" / "skill-validation.json"
    skill_validation = load_json(skill_validation_path) if skill_validation_path.is_file() else {"status": "MISSING"}
    skill_validation_valid = bool(
        skill_validation_path.is_file() and _skill_validation_passes(skill_validation)
    )
    independent_path = project / "reports" / "independent-qa.json"
    independent = load_json(independent_path) if independent_path.is_file() else {"status": "PENDING"}
    candidate_path = project / "reports" / "release-candidate.json"
    candidate = load_json(candidate_path) if candidate_path.is_file() else {"status": "MISSING"}
    independent_fresh = bool(
        independent_path.is_file()
        and candidate_path.is_file()
        and independent_path.stat().st_mtime >= latest_input_mtime
        and independent_path.stat().st_mtime >= candidate_path.stat().st_mtime
        and _independent_qa_matches_release(independent, candidate, tests)
    )
    independent_view = {**independent, "fresh_for_current_release": independent_fresh}
    independent_counts = {
        "p0_open": independent.get("p0_open"),
        "p1_open": independent.get("p1_open"),
        "must_fix_open": independent.get("must_fix_open"),
    }
    onboarding_index_path = project / "state" / "onboarding-center-index.json"
    onboarding_in_progress = bool(
        onboarding_index_path.is_file()
        and load_json(onboarding_index_path).get("status") != "ONBOARDING_COMPLETE"
    )
    checks = {
        "tests": tests.get("status") == "PASS",
        "skill": skill_validation_valid,
        "knowledge": knowledge.get("status") == "UNCHANGED" and knowledge["write_operations"] == 0,
        "security": scan["status"] == "PASS",
        "external_actions": actions["status"] == "PASS",
        "database": schema_version == JobOpsDB.LATEST_SCHEMA_VERSION and "CHECK(dry_run = 1)" in dry_run_sql,
        "synthetic_private_purged": retained_synthetic_private == 0,
        "private_store_consistent": (
            scan["private_expected_ciphertext_file_count"] == scan["private_ciphertext_file_count"]
            and scan["private_temporary_file_count"] == 0
            and scan["private_staging_file_count"] == 0
            and scan["private_ciphertext_integrity_failure_count"] == 0
        ),
        "public_repository": public_repository["status"] == "PASS",
        "independent_qa": independent_fresh,
    }
    if require_independent and not checks["independent_qa"]:
        raise JobOpsError("INDEPENDENT_QA_REQUIRED", "Final release requires a passing independent read-only QA report.")
    core_pass = all(value for key, value in checks.items() if key != "independent_qa")
    status = "PASS" if core_pass and (checks["independent_qa"] or not require_independent) else "FAIL"
    release_truth = _release_truth(
        core_pass=core_pass,
        independent_fresh=independent_fresh,
        public_repository=public_repository,
    )
    result = {
        "schema_version": 1, "status": status,
        "source_commit": source_commit,
        "verification_scope": "PUBLIC_RELEASE" if require_independent else "LOCAL_DEVELOPMENT",
        **release_truth,
        "final_states": [
            "PHASE_0_4_HARDENED", "PHASE_4_5_SECURE_ONBOARDING_READY",
            "BILINGUAL_ONBOARDING_CENTER_READY",
            "PHASE_5_USER_PRESENT_MULTI_PAGE_COMPANY_ATS_ASSIST_READY",
            "FINAL_SUBMIT_USER_ONLY_AUTOMATIC_RETRY_DISABLED",
            "PHASE_6_UNATTENDED_AUTOMATION_NOT_OPERATIONAL",
        ],
        "phase_5_6_authorization": "PER_APPLICATION_USER_PRESENT_PREFILL_UPLOAD_AND_SCOPED_FORWARD_NAVIGATION_ONLY",
        "real_external_actions": actions["real_external_actions"],
        "checks": checks, "tests": tests, "skill_validation": skill_validation,
        "knowledge": knowledge, "database": {"schema_version": schema_version, "dry_run_constraint": True},
        "security_scan": scan,
        "public_repository": public_repository,
        "external_action_audit": actions,
        "lifetime_external_action_audit": lifetime_actions,
        "private_data": {
            "active_references": active_private, "active_real_references": active_real_private,
            "active_synthetic_references": active_synthetic_private,
            "retained_references": retained_private,
            "revoked_references": revoked_private,
            "corrupt_references": corrupt_private,
            "retained_synthetic_references": retained_synthetic_private,
            "deleted_synthetic_metadata_records": deleted_synthetic,
            "synthetic_ciphertext_residue": retained_synthetic_private,
            "private_ciphertext_file_count": scan["private_ciphertext_file_count"],
            "private_expected_ciphertext_file_count": scan["private_expected_ciphertext_file_count"],
            "private_staging_file_count": scan["private_staging_file_count"],
            "private_temporary_file_count": scan["private_temporary_file_count"],
            "private_ciphertext_integrity_failure_count": scan["private_ciphertext_integrity_failure_count"],
        },
        "independent_qa": independent_view,
        "document_visual_review": load_json(project / "reports" / "validation-artifacts" / "complex-resume-visual-review.json"),
        **independent_counts,
        "closed_capabilities": [
            "final submit", "automatic retry", "login or account creation",
            "cross-origin forms or unattended ATS automation", "email", "recruiter contact",
            "unattended real scheduler",
        ],
        "next_safe_action": (
            "onboarding-center" if status == "PASS" and onboarding_in_progress
            else "review-onboarding --latest" if status == "PASS" and active_onboarding_packets
            else "secure-onboard with user-selected private files" if status == "PASS"
            else "review failed release checks"
        ),
    }
    return result


def write_release_reports(project: Path, result: dict[str, Any]) -> None:
    write_json(project / "reports" / "checkpoint-final.json", result)
    tests = result["tests"]
    lines = [
        "# JobFlow verification checkpoint", "",
        f"Local verification status: **{result['status']}**", "",
        f"Public repository boundary: **{'READY' if result.get('public_repository_ready') else 'BLOCKED'}**", "",
        f"Public release readiness: **{'READY' if result.get('public_release_ready') else 'BLOCKED'}**", "",
        f"Publisher runtime closure: **{result.get('runtime_closure_status', 'UNATTESTED')}**", "",
        *[f"- Blocker: `{value}`" for value in result.get("public_release_blockers", [])],
        "" if result.get("public_release_blockers") else "",
        *[f"- `{value}`" for value in result["final_states"]],
        f"- `REAL_EXTERNAL_ACTIONS={result['real_external_actions']}`",
        f"- `PHASE_5_6_AUTHORIZATION={result['phase_5_6_authorization']}`", "",
        "## Verification", "",
        f"- Tests: {tests['passed']} passed, {tests['failed']} failed across {len(tests.get('categories', {}))} categories.",
        f"- Runtime Schemas: {tests.get('schema_count', 0)} strict schemas.",
        f"- Database migration: version {result['database']['schema_version']}; dry-run CHECK retained.",
        f"- Knowledge: {result['knowledge']['status']}; writes {result['knowledge']['write_operations']}.",
        *[
            f"  - `{name}`: {item['file_count']} files; `{item['tree_sha256']}`"
            for name, item in result["knowledge"].get("collections", {}).items()
        ],
        f"- Security findings: {result['security_scan']['finding_count']}.",
        f"- Public repository: {result['public_repository']['status']}; current-tree findings {result['public_repository']['tree']['finding_count']}; full-history findings {result['public_repository']['history']['finding_count']}; author identity {result['public_repository']['author_identity']['status']}.",
        f"- Verification-window external-action audit: {result['external_action_audit']['attempt_count']} new attempts; {result['external_action_audit']['real_external_actions']} real side effects.",
        f"- Lifetime retained external-action audit: {result['lifetime_external_action_audit']['attempt_count']} recorded attempts; {result['lifetime_external_action_audit']['real_external_actions']} real side effects.",
        f"- Private references: {result['private_data']['active_references']} active, {result['private_data']['revoked_references']} revoked, {result['private_data']['corrupt_references']} corrupt, {result['private_data']['retained_references']} retained total (real active: {result['private_data']['active_real_references']}; synthetic retained: {result['private_data']['retained_synthetic_references']}); deleted synthetic metadata: {result['private_data']['deleted_synthetic_metadata_records']}; total/expected ciphertext files: {result['private_data']['private_ciphertext_file_count']}/{result['private_data']['private_expected_ciphertext_file_count']}; ciphertext integrity failures: {result['private_data']['private_ciphertext_integrity_failure_count']}; staging files: {result['private_data']['private_staging_file_count']}; atomic-write temporary files: {result['private_data']['private_temporary_file_count']}.",
        f"- P0/P1/must-fix open: {result['p0_open']}/{result['p1_open']}/{result['must_fix_open']}.",
        f"- Independent QA: {'PASS (fresh)' if result['checks']['independent_qa'] else 'STALE OR MISSING'}.", "",
        "## Implemented", "",
        "- Strict runtime schemas, versioned SQLite migrations, atomic state/audit transitions and one-time content-bound approvals.",
        "- Read-only Knowledge Gateway, evidence-verified Claim lifecycle, hardened path exclusions and DPAPI-backed secure references.",
        "- Transactional bounded intake, idempotent/revisable orchestration, safe recovery and a complete offline chain to `AWAITING_APPROVAL`.",
        "- Compound JD/eligibility/Fit analysis, local research evidence, master-resume copy tailoring, document QA and fail-closed bilingual form classification.",
        "- A fixed-ID, loopback-only Browser Companion for separately authorized user-present inspection, approved prefill/material attachment and one-use non-final Next/Continue navigation; final Submit and automatic retry are absent.",
        "- Fully content-bound synthetic Greenhouse, Lever, Ashby, SmartRecruiters and representative three-page Workday verticals through encrypted answers, per-page session rotation, human login/CAPTCHA handoff, later-page material attachment and trusted-user receipt observation, with zero public-site access.",
        "- Ordered Workday page analysis with dynamic-control logical deduplication, repeated-page detection, cross-origin rejection and bounded navigation.",
        "- File-byte-bound ATS capability disclosure with complete provider-specific verified/unverified stage partitions and separately scoped shared browser-runtime evidence; every provider explicitly remains unverified against live sites.",
        "- Manual-tick-only continuous intake with strict local manifests, FIFO deferred promotion, same-process automatic capacity refill and one-use DPAPI retention for UI-deferred evidence; zero background or external actions are registered.",
        "- Existing Skill, references, deterministic scripts, fixtures, quick start and release verification were updated in place.", "",
        "## Public CLI", "",
        "`status`, `audit`, `locate`, `init-db`, `migrate-db`, `secure-onboard`, `secure-onboard-resume`, `finalize-resume-onboarding`, `review-onboarding`, `secure-import-master-resume`, `secure-import-answer-bank`, `secure-store-status`, `purge-synthetic-private-data`, `propose-claims`, `list-claim-proposals`, `approve-claim`, `reject-claim`, `revoke-claim`, `revalidate-claims`, `ats-capabilities`, `product-capabilities`, `live-acceptance`, `discover-official-jobs`, `verify-route`, `analyze-ats-form`, `analyze-ats-sequence`, `import-jd`, `analyze-job`, `run-to-awaiting-approval`, `plan-continuous-intake`, `run-queue`, `list-pending`, `show-review-packet`, `revise-application`, `approve-review-packet`, `reject-review-packet`, `resume-blocked`, `retry-safe-step`, `explain`, `verify-release`.", "",
        "## Document QA", "",
        f"- Reviewer: {result['document_visual_review']['reviewer_type']}; rendered pages: {len(result['document_visual_review']['pages'])}; failed pages: {sum(page['result'] != 'PASS' for page in result['document_visual_review']['pages'])}.",
        "- The complex synthetic master and tailored DOCX/PDF were rendered; page hashes, timestamps and per-page reasons are retained in the visual record.", "",
        "## Closed capabilities", "",
        *[f"- {item}" for item in result["closed_capabilities"]], "",
        "## Next safe action", "", result["next_safe_action"], "",
        "Any real trial requires a secure Candidate Profile, Answer Bank, editable master DOCX, approved Claims, a current review packet and separate user-present authorization for one application. Site terms and fresh route evidence still apply. JobFlow may assist only on the bound company/ATS origin; login and verification stay with the user, and the user alone clicks final Submit. No public recruiting site, account creation, email, recruiter contact or real scheduler was used in this release.",
    ]
    (project / "reports" / "checkpoint-final.md").write_text("\n".join(lines), encoding="utf-8")
