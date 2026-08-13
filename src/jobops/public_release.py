from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


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
TEXT_SUFFIXES = {".py", ".js", ".css", ".json", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".toml", ".in"}
HISTORY_DOCUMENT_SUFFIXES = {".docx", ".pdf"}
MAX_HISTORY_TEXT_BYTES = 5_000_000
REQUIRED_PUBLIC_FILES = {
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "Start JobFlow Demo.cmd",
    "scripts/start-jobflow-demo.ps1",
    "pyproject.toml",
}
PUBLIC_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SAFE_PUBLIC_EMAIL_SUFFIXES = ("@example.test", "@jobops.local", "@users.noreply.github.com")
AUTHOR_POLICIES = {"NOREPLY_ONLY", "PUBLIC_EMAIL_APPROVED"}


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
        findings.extend(validate_public_text(relative, text))
    return findings


def validate_public_text(label: str, text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if ABSOLUTE_USER_PATH.search(text):
        findings.append({"kind": "absolute_user_path", "path": label})
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append({"kind": kind, "path": label})
    for email in PUBLIC_EMAIL.findall(text):
        if not email.casefold().endswith(SAFE_PUBLIC_EMAIL_SUFFIXES):
            findings.append({"kind": "email", "path": label})
    return findings


def _git(project: Path, *arguments: str) -> bytes:
    completed = subprocess.run(["git", *arguments], cwd=project, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("GIT_REPOSITORY_REQUIRED")
    return completed.stdout


def _history_inventory(project: Path) -> tuple[list[str], dict[str, set[str]], list[dict[str, str]], int]:
    commits = [item.decode("ascii") for item in _git(project, "rev-list", "--all").splitlines() if item]
    paths: set[str] = set()
    blob_paths: dict[str, set[str]] = {}
    findings: list[dict[str, str]] = []
    for commit in commits:
        for record in _git(project, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
            if not record:
                continue
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            relative = encoded_path.decode("utf-8", errors="strict")
            paths.add(relative)
            if mode == "120000":
                findings.append({"kind": "historical_symlink", "path": relative})
            if object_type == "blob":
                blob_paths.setdefault(object_id, set()).add(relative)
    return sorted(paths), blob_paths, findings, len(commits)


def _extract_historical_pdf(project: Path, payload: bytes) -> tuple[str, str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, json.dumps(dict(reader.metadata or {}), ensure_ascii=False, default=str)
    except ModuleNotFoundError:
        helper = project / ".agents" / "skills" / "job-application-operator" / "scripts" / "extract-pdf-stdin.py"
        candidates = [
            project / ".venv" / "Scripts" / "python.exe",
            project / ".venv" / "bin" / "python",
            Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe",
        ]
        for interpreter in candidates:
            if not interpreter.is_file() or not helper.is_file():
                continue
            completed = subprocess.run(
                [str(interpreter), str(helper)], input=payload, capture_output=True, timeout=45, check=False
            )
            if completed.returncode != 0:
                continue
            try:
                result = json.loads(completed.stdout.decode("utf-8"))
                import base64

                return (
                    base64.b64decode(result["text_base64"]).decode("utf-8"),
                    base64.b64decode(result["metadata_base64"]).decode("utf-8"),
                )
            except Exception:
                continue
        raise RuntimeError("HISTORICAL_PDF_RUNTIME_UNAVAILABLE")


def _scan_historical_document(project: Path, label: str, suffix: str, payload: bytes) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for name in archive.namelist():
                    if name.endswith((".xml", ".rels")):
                        text = archive.read(name).decode("utf-8", errors="ignore")
                        findings.extend(validate_public_text(f"{label}!{name}", text))
        except zipfile.BadZipFile:
            findings.append({"kind": "invalid_historical_docx", "path": label})
    elif suffix == ".pdf":
        try:
            text, metadata = _extract_historical_pdf(project, payload)
            findings.extend(validate_public_text(f"{label}#text", text))
            findings.extend(validate_public_text(f"{label}#metadata", metadata))
        except Exception:
            findings.append({"kind": "invalid_historical_pdf", "path": label})
    return findings


def verify_public_history(project: Path) -> dict[str, Any]:
    paths, blob_paths, findings, commit_count = _history_inventory(project)
    findings.extend(validate_public_paths(paths))
    scanned_text_blobs = 0
    scanned_document_blobs = 0
    for object_id, aliases in blob_paths.items():
        relevant = sorted(
            alias for alias in aliases
            if PurePosixPath(alias).suffix.casefold() in TEXT_SUFFIXES | HISTORY_DOCUMENT_SUFFIXES
        )
        if not relevant:
            continue
        label = relevant[0] + "@history"
        payload = _git(project, "cat-file", "blob", object_id)
        suffix = PurePosixPath(label.removesuffix("@history")).suffix.casefold()
        if suffix in HISTORY_DOCUMENT_SUFFIXES:
            scanned_document_blobs += 1
            findings.extend(_scan_historical_document(project, label, suffix, payload))
            continue
        scanned_text_blobs += 1
        if len(payload) > MAX_HISTORY_TEXT_BYTES:
            findings.append({"kind": "oversized_historical_text", "path": label})
            continue
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            findings.append({"kind": "undecodable_historical_text", "path": label})
            continue
        findings.extend(validate_public_text(label, text))
    unique = sorted({(item["kind"], item["path"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL",
        "commit_count": commit_count,
        "historical_path_count": len(paths),
        "unique_blob_count": len(blob_paths),
        "scanned_text_blob_count": scanned_text_blobs,
        "scanned_document_blob_count": scanned_document_blobs,
        "finding_count": len(unique),
        "findings": [{"kind": kind, "path": path} for kind, path in unique],
    }


def verify_author_identity(project: Path) -> dict[str, Any]:
    policy_path = project / "config" / "public-release.json"
    policy: dict[str, Any] = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    if set(policy) != {"schema_version", "author_identity_policy"} or policy.get("schema_version") != 1:
        author_policy = "INVALID_OR_MISSING"
    else:
        author_policy = str(policy.get("author_identity_policy"))
    raw = _git(project, "log", "--all", "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1e")
    identities: set[tuple[str, str]] = set()
    for record in raw.split(b"\x1e"):
        values = record.strip().split(b"\x1f")
        if len(values) != 4:
            continue
        identities.add((values[0].decode("utf-8"), values[1].decode("utf-8")))
        identities.add((values[2].decode("utf-8"), values[3].decode("utf-8")))
    non_noreply = sum(1 for _, email in identities if "noreply" not in email.casefold())
    approved = author_policy == "PUBLIC_EMAIL_APPROVED" or (author_policy == "NOREPLY_ONLY" and non_noreply == 0)
    return {
        "status": "PASS" if approved else "REVIEW_REQUIRED",
        "policy": author_policy,
        "identity_count": len(identities),
        "non_noreply_identity_count": non_noreply,
        "private_identity_values_emitted": 0,
    }


def verify_public_tree(project: Path) -> dict[str, object]:
    files = tracked_files(project)
    findings = validate_public_paths(files) + validate_public_contents(project, files)
    for relative in sorted(REQUIRED_PUBLIC_FILES - set(files)):
        findings.append({"kind": "required_public_file_missing", "path": relative})
    unique = sorted({(item["kind"], item["path"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL",
        "tracked_file_count": len(files),
        "finding_count": len(unique),
        "findings": [{"kind": kind, "path": path} for kind, path in unique],
    }


def verify_public_repository(project: Path) -> dict[str, Any]:
    tree = verify_public_tree(project)
    history = verify_public_history(project)
    identity = verify_author_identity(project)
    blockers: list[str] = []
    if tree["status"] != "PASS":
        blockers.append("PUBLIC_TREE_BOUNDARY_FAILED")
    if history["status"] != "PASS":
        blockers.append("PUBLIC_HISTORY_BOUNDARY_FAILED")
    if identity["status"] != "PASS":
        blockers.append("GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED")
    content_pass = tree["status"] == history["status"] == "PASS"
    return {
        "schema_version": 1,
        "status": "PASS" if content_pass else "FAIL",
        "public_release_ready": content_pass and identity["status"] == "PASS",
        "public_release_blockers": blockers,
        "tree": tree,
        "history": history,
        "author_identity": identity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    project = Path.cwd()
    result = verify_public_repository(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = result["public_release_ready"] if args.require_ready else result["status"] == "PASS"
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
