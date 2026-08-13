from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


ALLOWED_RUNTIME_SENTINELS = {
    "state/.gitkeep",
    "reports/.gitkeep",
    "workspace/inbox/.gitkeep",
    "workspace/jobs/.gitkeep",
    "workspace/review-packets/.gitkeep",
}
PRIVATE_ROOTS = {"state", "reports", "workspace"}
FORBIDDEN_SUFFIXES = {".db", ".dpapi", ".sqlite", ".sqlite3", ".pyc", ".zip", ".7z", ".rar", ".log"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build"}
ABSOLUTE_USER_PATH = re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")
SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_key": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
TEXT_SUFFIXES = {".py", ".js", ".css", ".json", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".toml"}


def validate_public_paths(paths: Iterable[str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for raw in paths:
        normalized = PurePosixPath(raw.replace("\\", "/")).as_posix().lstrip("./")
        path = PurePosixPath(normalized)
        if not normalized or normalized.startswith("../"):
            findings.append({"kind": "invalid_path", "path": normalized or raw})
            continue
        if path.parts and path.parts[0] in PRIVATE_ROOTS and normalized not in ALLOWED_RUNTIME_SENTINELS:
            findings.append({"kind": "runtime_state_tracked", "path": normalized})
        if any(part in FORBIDDEN_PARTS for part in path.parts):
            findings.append({"kind": "generated_path_tracked", "path": normalized})
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append({"kind": "private_or_generated_file_tracked", "path": normalized})
        if path.name.casefold().startswith(".env") and path.name != ".env.example":
            findings.append({"kind": "environment_file_tracked", "path": normalized})
    return findings


def tracked_files(project: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=project, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError("GIT_REPOSITORY_REQUIRED")
    return [item.decode("utf-8", errors="strict") for item in completed.stdout.split(b"\0") if item]


def validate_public_contents(project: Path, paths: Iterable[str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative in paths:
        path = project / relative
        if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            findings.append({"kind": "undecodable_text", "path": relative})
            continue
        if ABSOLUTE_USER_PATH.search(text):
            findings.append({"kind": "absolute_user_path", "path": relative})
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"kind": kind, "path": relative})
    return findings


def verify_public_tree(project: Path) -> dict[str, object]:
    files = tracked_files(project)
    findings = validate_public_paths(files) + validate_public_contents(project, files)
    unique = sorted({(item["kind"], item["path"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL",
        "tracked_file_count": len(files),
        "finding_count": len(unique),
        "findings": [{"kind": kind, "path": path} for kind, path in unique],
    }


def main() -> int:
    project = Path.cwd()
    result = verify_public_tree(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
