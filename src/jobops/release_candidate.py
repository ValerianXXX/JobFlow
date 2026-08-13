from __future__ import annotations

import argparse
import json
import os
import subprocess
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
    for required in sorted(REQUIRED_PUBLIC_FILES | {"Install JobFlow.cmd", "Start JobFlow.cmd", ".jobops-root"}):
        if required not in present:
            findings.append({"kind": "candidate_required_file_missing", "path": required})
    unique = sorted({(item["kind"], item["path"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL",
        "file_count": len(relative_files),
        "finding_count": len(unique),
        "findings": [{"kind": kind, "path": path} for kind, path in unique],
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
        os.replace(first, destination)
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
        "repository_content_status": repository["status"],
        "author_identity_status": repository["author_identity"]["status"],
        "uploaded": False,
        "network_actions": 0,
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
