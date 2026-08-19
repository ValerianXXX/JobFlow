from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import JobOpsError
from .public_release import (
    HISTORY_DOCUMENT_SUFFIXES,
    REQUIRED_PUBLIC_FILES,
    TEXT_SUFFIXES,
    _scan_historical_document,
    validate_public_paths,
    validate_public_text,
    verify_public_repository,
)
from .util import sha256_file, write_json


WINDOWS_POWERSHELL_UTF8_BOM_FILES = {
    "scripts/check-jobflow.ps1",
    "scripts/check-release-readiness.ps1",
    "scripts/install-jobflow.ps1",
    "scripts/start-jobflow-demo.ps1",
    "scripts/start-jobflow.ps1",
    "scripts/windows-runtime/update-installed-jobflow.ps1",
}


def _git(project: Path, *arguments: str) -> bytes:
    completed = subprocess.run(["git", *arguments], cwd=project, capture_output=True, check=False)
    if completed.returncode != 0:
        raise JobOpsError("RELEASE_GIT_FAILED", "The local Git command required to build the candidate failed.")
    return completed.stdout


def _version(project: Path) -> str:
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata.get("project", {}).get("version", ""))
    if not version or any(character not in "0123456789." for character in version):
        raise JobOpsError("RELEASE_VERSION_INVALID", "The public release version must be a numeric dotted value.")
    return version


def _commit_candidate_archive(source: Path, destination: Path) -> None:
    """Copy into the destination volume before the final atomic replacement."""
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as staging_file:
            staging = Path(staging_file.name)
            with source.open("rb") as source_file:
                shutil.copyfileobj(source_file, staging_file)
            staging_file.flush()
            os.fsync(staging_file.fileno())
        os.replace(staging, destination)
        staging = None
    except OSError as error:
        raise JobOpsError(
            "RELEASE_ARCHIVE_COMMIT_FAILED",
            "The validated release archive could not be committed atomically.",
        ) from error
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def verify_candidate_archive(project: Path, archive_path: Path, *, prefix: str) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    relative_files: list[str] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            corrupt = archive.testzip()
            if corrupt:
                findings.append({"kind": "corrupt_archive_member", "path": PurePosixPath(corrupt).name})
            for info in archive.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                if not name.startswith(prefix):
                    findings.append({"kind": "archive_prefix_mismatch", "path": PurePosixPath(name).name})
                    continue
                relative = name[len(prefix):]
                path = PurePosixPath(relative)
                if not relative or path.is_absolute() or ".." in path.parts:
                    findings.append({"kind": "unsafe_archive_path", "path": path.name or "<empty>"})
                    continue
                relative_files.append(relative)
                payload = archive.read(info)
                if relative in WINDOWS_POWERSHELL_UTF8_BOM_FILES and not payload.startswith(b"\xef\xbb\xbf"):
                    findings.append({"kind": "windows_powershell_utf8_bom_missing", "path": relative})
                suffix = path.suffix.casefold()
                if suffix in TEXT_SUFFIXES or path.name in {"LICENSE", ".jobops-root"}:
                    try:
                        findings.extend(validate_public_text(relative, payload.decode("utf-8-sig")))
                    except UnicodeDecodeError:
                        findings.append({"kind": "undecodable_archive_text", "path": relative})
                elif suffix in HISTORY_DOCUMENT_SUFFIXES:
                    findings.extend(_scan_historical_document(project, relative, suffix, payload))
    except zipfile.BadZipFile:
        findings.append({"kind": "invalid_candidate_zip", "path": archive_path.name})
    findings.extend(validate_public_paths(relative_files))
    present = set(relative_files)
    for required in sorted(REQUIRED_PUBLIC_FILES | {
        "Install JobFlow.cmd", "Start JobFlow.cmd", "Update JobFlow.cmd", ".jobops-root"
    }):
        if required not in present:
            findings.append({"kind": "candidate_required_file_missing", "path": required})
    unique = sorted({(item["kind"], item["path"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL",
        "file_count": len(relative_files),
        "finding_count": len(unique),
        "findings": [{"kind": kind, "path": path} for kind, path in unique],
    }


def run_source_candidate_smoke(archive_path: Path, *, prefix: str, temporary: Path) -> dict[str, Any]:
    extracted = temporary / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)
    candidate = extracted / prefix.rstrip("/")
    entry = candidate / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"
    smoke = candidate / ".agents" / "skills" / "job-application-operator" / "scripts" / "smoke-source-candidate.py"
    if not entry.is_file() or not smoke.is_file():
        raise JobOpsError("RELEASE_SMOKE_ENTRY_MISSING", "The extracted source candidate is missing its local startup smoke entry.")
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(temporary / "local-app-data")
    help_result = subprocess.run(
        [sys.executable, str(entry), "--help"], cwd=candidate, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )
    if help_result.returncode != 0 or not all(command in help_result.stdout for command in ("onboarding-center", "demo", "check-private-store")):
        raise JobOpsError("RELEASE_SMOKE_CLI_FAILED", "The extracted source candidate public CLI did not start correctly.")
    private_check = subprocess.run(
        [sys.executable, str(entry), "check-private-store"], cwd=candidate, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        private_result = json.loads(private_check.stdout)
    except json.JSONDecodeError as error:
        raise JobOpsError("RELEASE_SMOKE_PRIVATE_CHECK_INVALID", "The extracted source candidate private-store check was invalid.") from error
    if (
        private_check.returncode != 0
        or private_result.get("status") != "PRIVATE_STORE_HEALTHY"
        or private_result.get("expected_ciphertext_files") != 0
        or private_result.get("ciphertext_files") != 0
        or private_result.get("private_values_read") != 0
        or private_result.get("private_values_emitted") != 0
        or private_result.get("network_actions") != 0
        or private_result.get("real_external_actions") != 0
    ):
        raise JobOpsError("RELEASE_SMOKE_PRIVATE_CHECK_FAILED", "The extracted source candidate private-store boundary failed.")
    service_result = subprocess.run(
        [sys.executable, str(smoke)], cwd=candidate, env=environment,
        capture_output=True, text=True, timeout=45, check=False,
    )
    if service_result.returncode != 0:
        raise JobOpsError("RELEASE_SMOKE_UI_FAILED", "The extracted source candidate local UI smoke failed.")
    try:
        result = json.loads(service_result.stdout)
    except json.JSONDecodeError as error:
        raise JobOpsError("RELEASE_SMOKE_OUTPUT_INVALID", "The extracted source candidate smoke result was invalid.") from error
    required = {
        "status": "PASS", "binding": "127.0.0.1", "supported_locales": ["zh", "en"],
        "offline_discovery": "PASS", "offline_candidates": 2, "snapshot_persisted": False,
        "candidate_queue_mutations": 0,
        "private_values_emitted": 0, "external_network_actions": 0, "real_external_actions": 0,
    }
    if any(result.get(key) != value for key, value in required.items()):
        raise JobOpsError("RELEASE_SMOKE_BOUNDARY_FAILED", "The extracted source candidate crossed a startup boundary.")
    return {
        **required,
        "private_store_health": "PASS",
        "private_ciphertext_files": 0,
        "loopback_requests": int(result.get("loopback_requests", 0)),
        "security_headers": result.get("security_headers"),
        "project_state_isolated": bool(result.get("project_state_isolated")),
        "local_app_data_isolated": bool(result.get("local_app_data_isolated")),
    }


def build_release_candidate(project: Path) -> dict[str, Any]:
    repository = verify_public_repository(project)
    if repository["status"] != "PASS":
        raise JobOpsError("PUBLIC_REPOSITORY_BOUNDARY_FAILED", "The public repository content boundary is not passing.")
    dirty = _git(project, "status", "--porcelain", "--untracked-files=all").decode("utf-8").strip()
    if dirty:
        raise JobOpsError("RELEASE_WORKTREE_NOT_CLEAN", "Commit or remove local source changes before building a release candidate.")
    version = _version(project)
    commit = _git(project, "rev-parse", "HEAD").decode("ascii").strip()
    prefix = f"JobFlow-v{version}/"
    artifact_name = f"JobFlow-v{version}-{commit[:12]}-source.zip"
    destination = project / "dist" / artifact_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jobflow-release-") as raw_temp:
        temporary = Path(raw_temp)
        first = temporary / "first.zip"
        second = temporary / "second.zip"
        for output in (first, second):
            completed = subprocess.run(
                ["git", "archive", "--format=zip", f"--prefix={prefix}", f"--output={output}", "HEAD"],
                cwd=project, capture_output=True, check=False,
            )
            if completed.returncode != 0:
                raise JobOpsError("RELEASE_ARCHIVE_FAILED", "Git could not build the local source archive.")
        first_hash, second_hash = sha256_file(first), sha256_file(second)
        if first_hash != second_hash:
            raise JobOpsError("RELEASE_ARCHIVE_NOT_REPRODUCIBLE", "Two builds from the same commit produced different hashes.")
        verification = verify_candidate_archive(project, first, prefix=prefix)
        if verification["status"] != "PASS":
            raise JobOpsError("RELEASE_ARCHIVE_UNSAFE", "The local release candidate failed its content boundary.", findings=verification["findings"])
        source_smoke = run_source_candidate_smoke(first, prefix=prefix, temporary=temporary)
        _commit_candidate_archive(first, destination)
    result = {
        "schema_version": 1,
        "status": "RELEASE_CANDIDATE_BUILT",
        "version": version,
        "commit": commit,
        "artifact_name": artifact_name,
        "artifact_sha256": sha256_file(destination),
        "artifact_bytes": destination.stat().st_size,
        "reproducible_builds": 2,
        "archive": verification,
        "source_smoke": source_smoke,
        "repository_content_status": repository["status"],
        "author_identity_status": repository["author_identity"]["status"],
        "uploaded": False,
        "external_network_actions": 0,
        "real_external_actions": 0,
    }
    write_json(project / "reports" / "release-candidate.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic local-only JobFlow source candidate.")
    parser.parse_args()
    project = Path.cwd()
    try:
        result = build_release_candidate(project)
    except JobOpsError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
