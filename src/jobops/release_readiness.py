from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tomllib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .db import JobOpsDB
from .errors import JobOpsError
from .public_release import verify_public_repository
from .release import _latest_release_input_mtime
from .release_attestation import verify_public_release_attestation
from .release_toolchain import (
    ReleaseToolchainError,
    locked_release_git,
    sanitized_command_environment,
)
from .runtime_schema import validate_named
from .util import has_reparse_component, load_json


_STRICT_SEMVER_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
_ASCII_SAFE_PUBLIC_DESCRIPTION_RE = re.compile(r"[\x20-\x7e]+")
_ATTESTATION_MAX_AGE_DAYS = 30


def _is_strict_semver(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and _STRICT_SEMVER_RE.fullmatch(value) is not None
    )


def _today() -> date:
    return date.today()


def _classify_store_verification_date(value: object) -> str:
    """Classify a manual release-attestation date without claiming to detect language.

    Public descriptions are separately constrained to printable ASCII and are
    accepted as English only through the explicit user-confirmation gate.
    Store and clean-Windows attestations must be recent enough to describe the
    current release state rather than a historical candidate.
    """

    if not isinstance(value, str):
        return "INVALID"
    normalized = value
    if normalized != normalized.strip():
        return "INVALID"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) is None:
        return "INVALID"
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError:
        return "INVALID"
    today = _today()
    if parsed.isoformat() != normalized or parsed > today:
        return "INVALID"
    if parsed < today - timedelta(days=_ATTESTATION_MAX_AGE_DAYS):
        return "STALE"
    return "CURRENT"


def _exact_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _exact_zero(value: object) -> bool:
    return type(value) is int and value == 0


def _project_version(project: Path) -> str:
    try:
        metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
        value = metadata.get("project", {}).get("version")
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    return value if _is_strict_semver(value) else ""


_CANDIDATE_KEYS = {
    "schema_version",
    "status",
    "version",
    "commit",
    "artifact_name",
    "artifact_sha256",
    "artifact_bytes",
    "reproducible_builds",
    "archive",
    "source_smoke",
    "repository_content_status",
    "author_identity_status",
    "uploaded",
    "external_network_actions",
    "real_external_actions",
}
_CANDIDATE_ARCHIVE_KEYS = {"status", "file_count", "finding_count", "findings"}
_CANDIDATE_SMOKE_KEYS = {
    "status",
    "binding",
    "supported_locales",
    "offline_discovery",
    "offline_candidates",
    "snapshot_persisted",
    "candidate_queue_mutations",
    "private_values_emitted",
    "external_network_actions",
    "real_external_actions",
    "private_store_health",
    "private_ciphertext_files",
    "loopback_requests",
    "security_headers",
    "project_state_isolated",
    "local_app_data_isolated",
}


def _release_candidate_shape_valid(value: object) -> bool:
    """Validate the complete, closed candidate record before using any nested value."""

    if not isinstance(value, dict) or set(value) != _CANDIDATE_KEYS:
        return False
    archive = value.get("archive")
    smoke = value.get("source_smoke")
    if not isinstance(archive, dict) or set(archive) != _CANDIDATE_ARCHIVE_KEYS:
        return False
    if not isinstance(smoke, dict) or set(smoke) != _CANDIDATE_SMOKE_KEYS:
        return False
    version = value.get("version")
    commit = value.get("commit")
    artifact_name = value.get("artifact_name")
    artifact_sha256 = value.get("artifact_sha256")
    return bool(
        type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("status") == "RELEASE_CANDIDATE_BUILT"
        and _is_strict_semver(version)
        and isinstance(commit, str)
        and re.fullmatch(r"[a-f0-9]{40}", commit) is not None
        and isinstance(artifact_name, str)
        and re.fullmatch(r"JobFlow-v[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{12}-source\.zip", artifact_name) is not None
        and isinstance(artifact_sha256, str)
        and re.fullmatch(r"sha256:[a-f0-9]{64}", artifact_sha256) is not None
        and type(value.get("artifact_bytes")) is int
        and value.get("artifact_bytes", 0) > 0
        and type(value.get("reproducible_builds")) is int
        and value.get("reproducible_builds", 0) >= 2
        and archive.get("status") == "PASS"
        and type(archive.get("file_count")) is int
        and archive.get("file_count", 0) > 0
        and type(archive.get("finding_count")) is int
        and archive.get("finding_count") == 0
        and archive.get("findings") == []
        and smoke.get("status") == "PASS"
        and smoke.get("binding") == "127.0.0.1"
        and smoke.get("supported_locales") == ["zh", "en"]
        and smoke.get("offline_discovery") == "PASS"
        and type(smoke.get("offline_candidates")) is int
        and smoke.get("offline_candidates", 0) > 0
        and smoke.get("snapshot_persisted") is False
        and _exact_zero(smoke.get("candidate_queue_mutations"))
        and _exact_zero(smoke.get("private_values_emitted"))
        and _exact_zero(smoke.get("external_network_actions"))
        and _exact_zero(smoke.get("real_external_actions"))
        and smoke.get("private_store_health") == "PASS"
        and _exact_zero(smoke.get("private_ciphertext_files"))
        and type(smoke.get("loopback_requests")) is int
        and smoke.get("loopback_requests", 0) > 0
        and smoke.get("security_headers") == "PASS"
        and smoke.get("project_state_isolated") is True
        and smoke.get("local_app_data_isolated") is True
        and value.get("repository_content_status") == "PASS"
        and value.get("author_identity_status") == "PASS"
        and value.get("uploaded") is False
        and _exact_zero(value.get("external_network_actions"))
        and _exact_zero(value.get("real_external_actions"))
    )


def _source_candidate_status(project: Path, head_commit: str, version: str) -> str:
    """Bind readiness to the exact on-disk archive described by the candidate report."""

    candidate_path = project / "reports" / "release-candidate.json"
    if not candidate_path.is_file():
        return "MISSING"
    try:
        candidate = load_json(candidate_path)
        if not _release_candidate_shape_valid(candidate):
            return "FAIL"
        commit = candidate["commit"]
        candidate_version = candidate["version"]
        if commit != head_commit:
            return "STALE"
        if candidate_version != version:
            return "FAIL"
        expected_name = f"JobFlow-v{version}-{head_commit[:12]}-source.zip"
        if candidate["artifact_name"] != expected_name:
            return "FAIL"
        dist = project / "dist"
        artifact_path = dist / expected_name
        if (
            not dist.is_dir()
            or has_reparse_component(dist, project)
            or has_reparse_component(artifact_path, project)
            or not artifact_path.is_file()
        ):
            return "FAIL"
        resolved_dist = dist.resolve(strict=True)
        resolved_artifact = artifact_path.resolve(strict=True)
        if resolved_artifact.parent != resolved_dist:
            return "FAIL"
        digest = hashlib.sha256()
        with resolved_artifact.open("rb") as artifact_handle:
            artifact_bytes = os.fstat(artifact_handle.fileno()).st_size
            for chunk in iter(lambda: artifact_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        artifact_sha256 = "sha256:" + digest.hexdigest()
        if artifact_bytes != candidate["artifact_bytes"]:
            return "FAIL"
        if artifact_sha256 != candidate["artifact_sha256"]:
            return "FAIL"
        return "PASS"
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return "FAIL"


def _git(
    project: Path,
    *arguments: str,
    git_path: Path | None = None,
) -> str:
    try:
        with locked_release_git(project, git_path) as executable:
            environment = sanitized_command_environment(
                "git", executable=executable, project=project
            )
            completed = subprocess.run(
                [str(executable), *arguments],
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
            "The trusted absolute Git executable required for release readiness is unavailable.",
        ) from error
    if completed.returncode != 0:
        raise JobOpsError("RELEASE_GIT_FAILED", "The local Git status required for release readiness is unavailable.")
    return completed.stdout.strip()


def github_release_gates(
    project: Path,
    *,
    expected_commit: str | None = None,
    expected_version: str | None = None,
) -> dict[str, str]:
    path = project / "config" / "github-release.json"
    try:
        loaded = load_json(path)
        value = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, TypeError):
        value = {}
    raw_schema_version = value.get("schema_version")
    raw_owner = value.get("repository_owner")
    raw_name = value.get("repository_name")
    raw_visibility = value.get("visibility")
    owner = raw_owner.strip() if isinstance(raw_owner, str) else ""
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    visibility = raw_visibility.strip().upper() if isinstance(raw_visibility, str) else ""
    raw_description = value.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    topics = value.get("topics", [])
    metadata_shape_valid = (
        isinstance(raw_schema_version, int)
        and not isinstance(raw_schema_version, bool)
        and raw_schema_version == 1
        and isinstance(raw_owner, str)
        and isinstance(raw_name, str)
        and isinstance(raw_visibility, str)
        and raw_owner == owner
        and raw_name == name
        and raw_visibility == visibility
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner) is not None
        and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name) is not None
        and visibility == "PUBLIC"
        and isinstance(raw_description, str)
        and raw_description == description
        and bool(description)
        and len(description) <= 350
        and _ASCII_SAFE_PUBLIC_DESCRIPTION_RE.fullmatch(description) is not None
        and re.search(r"[A-Za-z]", description) is not None
        and "description_zh" not in value
        and "description_en" not in value
        and isinstance(topics, list)
        and 3 <= len(topics) <= 20
        and all(isinstance(item, str) for item in topics)
        and len(set(topics)) == len(topics)
        and all(re.fullmatch(r"[a-z0-9-]{1,50}", item) for item in topics)
    )
    try:
        companion_manifest = load_json(project / "browser-companion" / "manifest.json")
        raw_required_companion_version = companion_manifest.get("version")
        required_companion_version = (
            raw_required_companion_version.strip()
            if isinstance(raw_required_companion_version, str)
            else ""
        )
    except (OSError, ValueError, TypeError, AttributeError):
        raw_required_companion_version = None
        required_companion_version = ""
    raw_chrome_published = value.get("browser_companion_chrome_published_version")
    raw_edge_published = value.get("browser_companion_edge_published_version")
    chrome_published = raw_chrome_published.strip() if isinstance(raw_chrome_published, str) else ""
    edge_published = raw_edge_published.strip() if isinstance(raw_edge_published, str) else ""
    store_versions_verified = value.get("browser_companion_store_versions_verified") is True
    raw_verified_at = value.get("browser_companion_store_versions_verified_at")
    verification_date_status = _classify_store_verification_date(raw_verified_at)
    if not _is_strict_semver(raw_required_companion_version):
        browser_companion_stores = "INVALID"
    elif not store_versions_verified:
        browser_companion_stores = "PENDING"
    elif verification_date_status == "INVALID":
        browser_companion_stores = "INVALID"
    elif not _is_strict_semver(raw_chrome_published) or not _is_strict_semver(raw_edge_published):
        browser_companion_stores = "INVALID"
    elif verification_date_status == "STALE":
        browser_companion_stores = "OUTDATED"
    elif chrome_published != required_companion_version or edge_published != required_companion_version:
        browser_companion_stores = "OUTDATED"
    else:
        browser_companion_stores = "PASS"

    clean_windows_tested = value.get("clean_windows_profile_tested") is True
    raw_clean_windows_version = value.get("clean_windows_profile_tested_app_version")
    raw_clean_windows_companion_version = value.get("clean_windows_profile_tested_companion_version")
    raw_clean_windows_commit = value.get("clean_windows_profile_tested_commit")
    raw_clean_windows_tested_at = value.get("clean_windows_profile_tested_at")
    required_app_version = expected_version if expected_version is not None else _project_version(project)
    clean_windows_date_status = _classify_store_verification_date(raw_clean_windows_tested_at)
    clean_windows_shape_valid = (
        _is_strict_semver(raw_clean_windows_version)
        and _is_strict_semver(raw_clean_windows_companion_version)
        and isinstance(raw_clean_windows_commit, str)
        and re.fullmatch(r"[a-f0-9]{40}", raw_clean_windows_commit) is not None
        and clean_windows_date_status != "INVALID"
    )
    if not clean_windows_tested:
        clean_windows_profile = "PENDING"
    elif not clean_windows_shape_valid:
        clean_windows_profile = "INVALID"
    elif clean_windows_date_status == "STALE":
        clean_windows_profile = "OUTDATED"
    elif (
        not _is_strict_semver(required_app_version)
        or raw_clean_windows_version != required_app_version
        or raw_clean_windows_companion_version != required_companion_version
        or expected_commit is None
        or raw_clean_windows_commit != expected_commit
    ):
        clean_windows_profile = "OUTDATED"
    else:
        clean_windows_profile = "PASS"
    return {
        "repository_metadata": (
            "CONFIRMED"
            if metadata_shape_valid and value.get("metadata_confirmed_by_user") is True
            else "PENDING"
        ),
        "private_vulnerability_reporting": (
            "CONFIRMED"
            if value.get("private_vulnerability_reporting_confirmed") is True
            else "PENDING"
        ),
        "sanitized_screenshots": (
            "APPROVED" if value.get("sanitized_screenshots_approved") is True else "PENDING"
        ),
        "clean_windows_profile": clean_windows_profile,
        "browser_companion_stores": browser_companion_stores,
    }


def _local_verification_evidence(project: Path, head_commit: str) -> tuple[str, bool]:
    """Read the release proof produced against an isolated candidate state.

    Release readiness must not re-run the release verifier against the user's
    operational database.  That database intentionally retains audited,
    user-authorized website activity and is not evidence about the release
    candidate.  The checked proof is instead bound to the current source
    commit and must be newer than every release input.
    """

    checkpoint_path = project / "reports" / "checkpoint-final.json"
    test_report_path = project / "reports" / "release-test-results.json"
    if not checkpoint_path.is_file() or not test_report_path.is_file():
        return "MISSING_OR_STALE", False
    try:
        checkpoint = load_json(checkpoint_path)
        test_report = load_json(test_report_path)
        if not isinstance(checkpoint, dict) or not isinstance(test_report, dict):
            return "MISSING_OR_STALE", False
        checks = checkpoint.get("checks", {})
        checkpoint_tests = checkpoint.get("tests", {})
        output_sha256 = test_report.get("output_sha256")
        reports_well_formed = (
            isinstance(checks, dict)
            and isinstance(checkpoint_tests, dict)
            and isinstance(output_sha256, str)
            and re.fullmatch(r"sha256:[a-f0-9]{64}", output_sha256) is not None
            and test_report.get("status") == "PASS"
            and _exact_nonnegative_int(test_report.get("passed"))
            and _exact_zero(test_report.get("failed"))
            and _exact_nonnegative_int(test_report.get("schema_count"))
            and checkpoint_tests.get("status") == "PASS"
            and _exact_zero(checkpoint_tests.get("failed"))
            and checkpoint_tests.get("output_sha256") == output_sha256
        )
        required_core_checks = (
            "tests",
            "skill",
            "knowledge",
            "security",
            "external_actions",
            "database",
            "synthetic_private_purged",
            "private_store_consistent",
            "public_repository",
        )
        fresh = (
            checkpoint_path.stat().st_mtime >= _latest_release_input_mtime(project)
            and checkpoint.get("source_commit") == head_commit
            and checkpoint.get("verification_scope") in {"LOCAL_DEVELOPMENT", "PUBLIC_RELEASE"}
            and reports_well_formed
        )
        if not fresh:
            return "MISSING_OR_STALE", False
        core_pass = (
            checkpoint.get("status") == "PASS"
            and _exact_zero(checkpoint.get("real_external_actions"))
            and isinstance(checks, dict)
            and all(checks.get(name) is True for name in required_core_checks)
        )
        return ("PASS" if core_pass else "FAIL"), checks.get("independent_qa") is True
    except (OSError, ValueError, TypeError, AttributeError):
        return "MISSING_OR_STALE", False


def release_readiness(
    project: Path,
    database: JobOpsDB,
    *,
    git_path: Path | None = None,
) -> dict[str, Any]:
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata.get("project", {}).get("version", ""))
    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8") if (project / "CHANGELOG.md").is_file() else ""
    version_consistent = version == __version__ and f"## [{version}]" in changelog

    def run_git(*arguments: str) -> str:
        if git_path is None:
            return _git(project, *arguments)
        return _git(project, *arguments, git_path=git_path)

    try:
        head_commit = run_git("rev-parse", "HEAD")
        git_available = True
    except JobOpsError:
        head_commit = "0" * 40
        git_available = False

    if git_available:
        try:
            worktree_clean = not bool(
                run_git(
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                )
            )
            repository = verify_public_repository(project, git_path=git_path)
            local_status, independent_fresh = _local_verification_evidence(project, head_commit)

            candidate_status = _source_candidate_status(project, head_commit, version)
            tags = {
                item
                for item in run_git(
                    "tag",
                    "--points-at",
                    "HEAD",
                ).splitlines()
                if item
            }
        except (JobOpsError, RuntimeError):
            git_available = False
            worktree_clean = False
            repository = {
                "status": "FAIL",
                "author_identity": {"status": "REVIEW_REQUIRED"},
                "public_release_blockers": [],
            }
            local_status = "MISSING_OR_STALE"
            independent_fresh = False
            candidate_status = "MISSING"
            tags = set()
    else:
        worktree_clean = False
        repository = {
            "status": "FAIL",
            "author_identity": {"status": "REVIEW_REQUIRED"},
            "public_release_blockers": [],
        }
        local_status = "MISSING_OR_STALE"
        independent_fresh = False
        candidate_status = "MISSING"
        tags = set()
    expected_tag = f"v{version}"
    release_tag_status = "PRESENT" if expected_tag in tags else "MISMATCH" if tags else "MISSING"
    manual_gates = github_release_gates(
        project,
        expected_commit=head_commit if git_available else None,
        expected_version=version,
    )
    release_attestation = verify_public_release_attestation(
        project,
        version=version,
        commit=head_commit,
    )
    runtime_closure_status = str(release_attestation["runtime_closure_status"])
    release_attestation_status = str(release_attestation["release_attestation_status"])
    clean_windows_evidence_status = str(
        release_attestation["clean_windows_evidence_status"]
    )
    # Clean-Windows proof is deliberately an ignored, short-lived evidence
    # artifact bound to the frozen commit.  Writing that commit into a tracked
    # config file after testing would create a new commit and invalidate the
    # proof forever, so the evidence chain—not the legacy manual flag—is the
    # authority for this compatibility field.
    manual_gates["clean_windows_profile"] = {
        "PASS": "PASS",
        "MISSING": "PENDING",
        "INVALID": "INVALID",
        "NOT_CHECKED": "PENDING",
    }.get(clean_windows_evidence_status, "INVALID")

    blockers: list[str] = []
    if release_attestation_status == "MISSING":
        blockers.append("RELEASE_ATTESTATION_MISSING")
    elif release_attestation_status != "PASS":
        blockers.append("RELEASE_ATTESTATION_INVALID")
    if runtime_closure_status != "ATTESTED":
        blockers.append("RELEASE_RUNTIME_CLOSURE_UNATTESTED")
    if not git_available:
        blockers.append("GIT_REPOSITORY_REQUIRED")
    elif not worktree_clean:
        blockers.append("GIT_WORKTREE_NOT_CLEAN")
    if not version_consistent:
        blockers.append("VERSION_METADATA_MISMATCH")
    if local_status != "PASS":
        blockers.append("LOCAL_RELEASE_VERIFICATION_NOT_PASSING")
    if repository["status"] != "PASS":
        blockers.append("PUBLIC_REPOSITORY_CONTENT_FAILED")
    blockers.extend(repository.get("public_release_blockers", []))
    if candidate_status != "PASS":
        blockers.append(f"SOURCE_CANDIDATE_{candidate_status}")
    if manual_gates["repository_metadata"] != "CONFIRMED":
        blockers.append("GITHUB_REPOSITORY_METADATA_REQUIRED")
    if manual_gates["private_vulnerability_reporting"] != "CONFIRMED":
        blockers.append("PRIVATE_VULNERABILITY_REPORTING_UNCONFIRMED")
    if manual_gates["sanitized_screenshots"] != "APPROVED":
        blockers.append("SANITIZED_SCREENSHOTS_NOT_APPROVED")
    if manual_gates["clean_windows_profile"] == "PENDING":
        blockers.append("CLEAN_WINDOWS_EVIDENCE_MISSING")
    elif manual_gates["clean_windows_profile"] == "OUTDATED":
        blockers.append("CLEAN_WINDOWS_PROFILE_TEST_OUTDATED")
    elif manual_gates["clean_windows_profile"] == "INVALID":
        blockers.append("CLEAN_WINDOWS_EVIDENCE_INVALID")
    if manual_gates["browser_companion_stores"] != "PASS":
        blockers.append(f"BROWSER_COMPANION_STORES_{manual_gates['browser_companion_stores']}")
    if not independent_fresh:
        blockers.append("INDEPENDENT_QA_STALE_OR_MISSING")
    if release_tag_status != "PRESENT":
        blockers.append(f"RELEASE_TAG_{release_tag_status}")
    blockers = list(dict.fromkeys(blockers))
    next_actions = {
        "GIT_REPOSITORY_REQUIRED": "initialize or restore the local Git repository before checking public-release readiness",
        "GIT_WORKTREE_NOT_CLEAN": "commit the verified local changes",
        "VERSION_METADATA_MISMATCH": "align pyproject, package version and changelog",
        "LOCAL_RELEASE_VERIFICATION_NOT_PASSING": "run the full local release verification",
        "PUBLIC_REPOSITORY_CONTENT_FAILED": "review the public repository content findings",
        "GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED": "confirm a public GitHub noreply author identity policy",
        "SOURCE_CANDIDATE_MISSING": "build the deterministic local source candidate",
        "SOURCE_CANDIDATE_STALE": "rebuild the deterministic local source candidate from HEAD",
        "SOURCE_CANDIDATE_FAIL": "review the local source candidate failure",
        "GITHUB_REPOSITORY_METADATA_REQUIRED": "confirm the public repository owner, name, description, topics and visibility",
        "PRIVATE_VULNERABILITY_REPORTING_UNCONFIRMED": "confirm private vulnerability reporting for the future repository",
        "SANITIZED_SCREENSHOTS_NOT_APPROVED": "capture and approve synthetic Chinese and English screenshots",
        "CLEAN_WINDOWS_PROFILE_TEST_REQUIRED": "test the candidate on a clean supported Windows user profile",
        "CLEAN_WINDOWS_PROFILE_TEST_OUTDATED": "repeat the clean Windows profile test against the exact current commit and Browser Companion version",
        "CLEAN_WINDOWS_PROFILE_EVIDENCE_INVALID": "repair the clean Windows profile attestation before release",
        "BROWSER_COMPANION_STORES_PENDING": "verify the published Chrome and Edge extension versions against the required Browser Companion manifest version",
        "BROWSER_COMPANION_STORES_OUTDATED": "publish and verify the required Browser Companion version in both official stores before releasing JobFlow",
        "BROWSER_COMPANION_STORES_INVALID": "repair the Browser Companion manifest or store-version release attestation",
        "INDEPENDENT_QA_STALE_OR_MISSING": "run independent QA on the final frozen clean commit",
        "RELEASE_TAG_MISSING": "create the local signed or annotated release tag after QA",
        "RELEASE_TAG_MISMATCH": "review the local tag and version mismatch",
        "RELEASE_ATTESTATION_MISSING": (
            "produce the protected signed runtime bundle and canonical publisher evidence"
        ),
        "RELEASE_ATTESTATION_INVALID": (
            "repair the signed runtime or publisher evidence binding before release"
        ),
        "RELEASE_RUNTIME_CLOSURE_UNATTESTED": (
            "attest the complete publisher runtime closure in a protected external signing environment"
        ),
        "CLEAN_WINDOWS_EVIDENCE_MISSING": (
            "run the exact signed candidate on a fresh supported Windows profile and export canonical acceptance evidence"
        ),
        "CLEAN_WINDOWS_EVIDENCE_INVALID": (
            "repeat clean Windows acceptance against the exact signed bundle and current Browser Companion version"
        ),
    }
    public_release_ready = not blockers
    result = {
        "schema_version": 1,
        "status": (
            "PUBLIC_RELEASE_READY" if public_release_ready else "PUBLIC_RELEASE_BLOCKED"
        ),
        "public_release_ready": public_release_ready,
        "runtime_closure_status": runtime_closure_status,
        "release_attestation_status": release_attestation_status,
        "clean_windows_evidence_status": clean_windows_evidence_status,
        "release_attestation_failure_code": release_attestation.get("failure_code"),
        "version": version,
        "head_commit": head_commit,
        "worktree_clean": worktree_clean,
        "version_consistent": version_consistent,
        "local_verification_status": local_status,
        "public_repository_status": repository["status"],
        "source_candidate_status": candidate_status,
        "independent_qa_fresh": independent_fresh,
        "author_identity_status": repository["author_identity"]["status"],
        "release_tag_status": release_tag_status,
        "manual_release_gates": manual_gates,
        "blockers": blockers,
        "upload_performed": False,
        "network_actions": 0,
        "real_external_actions": 0,
        "next_safe_action": (
            "publish only the exact verified artifacts after explicit external-release authorization"
            if public_release_ready
            else next_actions[blockers[0]]
        ),
    }
    validate_named("release-readiness", result, project / "schemas")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-path", type=Path)
    args = parser.parse_args()
    project = Path.cwd()
    try:
        database = JobOpsDB(project / "state" / "jobops.db")
        database.initialize()
        result = release_readiness(project, database, git_path=args.git_path)
    except JobOpsError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["public_release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
