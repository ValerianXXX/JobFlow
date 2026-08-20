from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .db import JobOpsDB
from .errors import JobOpsError
from .public_release import verify_public_repository
from .release import _latest_release_input_mtime
from .runtime_schema import validate_named
from .util import load_json


def _git(project: Path, *arguments: str) -> str:
    completed = subprocess.run(["git", *arguments], cwd=project, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise JobOpsError("RELEASE_GIT_FAILED", "The local Git status required for release readiness is unavailable.")
    return completed.stdout.strip()


def github_release_gates(project: Path) -> dict[str, str]:
    path = project / "config" / "github-release.json"
    try:
        loaded = load_json(path)
        value = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, TypeError):
        value = {}
    owner = str(value.get("repository_owner", "")).strip()
    name = str(value.get("repository_name", "")).strip()
    visibility = str(value.get("visibility", "")).strip().upper()
    topics = value.get("topics", [])
    metadata_shape_valid = (
        value.get("schema_version") == 1
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner) is not None
        and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name) is not None
        and visibility in {"PUBLIC", "PRIVATE"}
        and bool(str(value.get("description_zh", "")).strip())
        and bool(str(value.get("description_en", "")).strip())
        and isinstance(topics, list)
        and 3 <= len(topics) <= 20
        and len({str(item) for item in topics}) == len(topics)
        and all(re.fullmatch(r"[a-z0-9-]{1,50}", str(item)) for item in topics)
    )
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
        "clean_windows_profile": (
            "PASS" if value.get("clean_windows_profile_tested") is True else "PENDING"
        ),
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
        checks = checkpoint.get("checks", {})
        checkpoint_tests = checkpoint.get("tests", {})
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
            and test_report.get("status") == "PASS"
            and int(test_report.get("failed", 1)) == 0
            and checkpoint_tests.get("output_sha256") == test_report.get("output_sha256")
        )
        if not fresh:
            return "MISSING_OR_STALE", False
        core_pass = (
            checkpoint.get("status") == "PASS"
            and int(checkpoint.get("real_external_actions", 1)) == 0
            and isinstance(checks, dict)
            and all(checks.get(name) is True for name in required_core_checks)
        )
        return ("PASS" if core_pass else "FAIL"), bool(checks.get("independent_qa"))
    except (OSError, ValueError, TypeError, AttributeError):
        return "MISSING_OR_STALE", False


def release_readiness(project: Path, database: JobOpsDB) -> dict[str, Any]:
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata.get("project", {}).get("version", ""))
    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8") if (project / "CHANGELOG.md").is_file() else ""
    version_consistent = version == __version__ and f"## [{version}]" in changelog
    try:
        head_commit = _git(project, "rev-parse", "HEAD")
        git_available = True
    except JobOpsError:
        head_commit = "0" * 40
        git_available = False

    if git_available:
        worktree_clean = not bool(_git(project, "status", "--porcelain", "--untracked-files=all"))
        repository = verify_public_repository(project)
        local_status, independent_fresh = _local_verification_evidence(project, head_commit)

        candidate_path = project / "reports" / "release-candidate.json"
        if not candidate_path.is_file():
            candidate_status = "MISSING"
        else:
            try:
                candidate = load_json(candidate_path)
                if candidate.get("commit") != head_commit:
                    candidate_status = "STALE"
                elif (
                    candidate.get("status") == "RELEASE_CANDIDATE_BUILT"
                    and candidate.get("archive", {}).get("status") == "PASS"
                    and candidate.get("source_smoke", {}).get("status") == "PASS"
                    and candidate.get("uploaded") is False
                ):
                    candidate_status = "PASS"
                else:
                    candidate_status = "FAIL"
            except (OSError, ValueError, TypeError):
                candidate_status = "FAIL"
        tags = {item for item in _git(project, "tag", "--points-at", "HEAD").splitlines() if item}
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
    manual_gates = github_release_gates(project)
    blockers: list[str] = []
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
    if manual_gates["clean_windows_profile"] != "PASS":
        blockers.append("CLEAN_WINDOWS_PROFILE_TEST_REQUIRED")
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
        "INDEPENDENT_QA_STALE_OR_MISSING": "run independent QA on the final frozen clean commit",
        "RELEASE_TAG_MISSING": "create the local signed or annotated release tag after QA",
        "RELEASE_TAG_MISMATCH": "review the local tag and version mismatch",
    }
    result = {
        "schema_version": 1,
        "status": "PUBLIC_RELEASE_READY" if not blockers else "PUBLIC_RELEASE_BLOCKED",
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
        "next_safe_action": next_actions.get(blockers[0], "request explicit GitHub upload authorization") if blockers else "request explicit GitHub upload authorization",
    }
    validate_named("release-readiness", result, project / "schemas")
    return result


def main() -> int:
    project = Path.cwd()
    try:
        database = JobOpsDB(project / "state" / "jobops.db")
        database.initialize()
        result = release_readiness(project, database)
    except JobOpsError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PUBLIC_RELEASE_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
