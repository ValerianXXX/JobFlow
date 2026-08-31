from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator

from .release_toolchain import (
    ReleaseToolchainError,
    locked_release_git,
    resolve_configured_release_git,
    sanitized_command_environment,
)


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
FORBIDDEN_BINDING_FILENAMES = {
    "browser-companion-binding.json",
}
ABSOLUTE_USER_PATH = re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")
SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_key": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "browser_companion_binding_secret": re.compile(
        r'''(?i)["']?secret_b64url["']?\s*:\s*["'][A-Za-z0-9_-]{43}["']'''
    ),
}
TEXT_SUFFIXES = {".py", ".js", ".css", ".json", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".toml", ".in"}
HISTORY_DOCUMENT_SUFFIXES = {".docx", ".pdf"}
MAX_HISTORY_TEXT_BYTES = 5_000_000
MAX_HISTORY_DOCUMENT_BYTES = 100_000_000
MAX_GIT_BATCH_HEADER_BYTES = 192
REQUIRED_PUBLIC_FILES = {
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "Check JobFlow.cmd",
    "Check Release Readiness.cmd",
    "Install JobFlow.cmd",
    "Start JobFlow Demo.cmd",
    "Install JobFlow Browser Companion.cmd",
    "browser-companion/manifest.json",
    "browser-companion/service-worker.js",
    "browser-companion/dom.js",
    "scripts/check-jobflow.ps1",
    "scripts/check-release-readiness.ps1",
    "scripts/install-jobflow-v2.ps1",
    "scripts/install-jobflow-browser-companion.ps1",
    "scripts/start-jobflow-demo.ps1",
    "pyproject.toml",
}
PUBLIC_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SAFE_PUBLIC_EMAIL_SUFFIXES = ("@example.test", "@jobops.local", "@users.noreply.github.com")
# Public certificate identities are provenance, not applicant or maintainer data.
# Keep this allowlist exact so ordinary addresses at the same domain still fail.
SAFE_PUBLIC_EMAILS = frozenset({"noreply@github.com", "thomas@python.org"})
AUTHOR_POLICIES = {"NOREPLY_ONLY", "PUBLIC_EMAIL_APPROVED"}
GITHUB_NOREPLY_DOMAIN = "users.noreply.github.com"


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
        folded_parts = tuple(part.casefold() for part in path.parts)
        folded_name = path.name.casefold()
        if (
            folded_name in FORBIDDEN_BINDING_FILENAMES
            or folded_name.startswith(".browser-companion-binding-")
            or (
                len(folded_parts) >= 2
                and folded_parts[-1] == "binding.json"
                and folded_parts[-2] in {"browser-companion", "browsercompanion"}
            )
        ):
            findings.append({"kind": "browser_companion_binding_tracked", "path": normalized})
    return findings


def _git_executable(git_path: Path | None = None) -> str:
    try:
        return str(resolve_configured_release_git(git_path))
    except (OSError, ReleaseToolchainError) as error:
        code = str(error) if isinstance(error, ReleaseToolchainError) else "RELEASE_GIT_UNTRUSTED"
        raise RuntimeError(code) from error


def _git_environment(project: Path, git_path: Path) -> dict[str, str]:
    try:
        return sanitized_command_environment(
            "git", executable=git_path, project=project
        )
    except (OSError, ReleaseToolchainError) as error:
        raise RuntimeError("RELEASE_GIT_ENVIRONMENT_INVALID") from error


def tracked_files(project: Path, *, git_path: Path | None = None) -> list[str]:
    executable = Path(_git_executable(git_path))
    completed = subprocess.run(
        [str(executable), "ls-files", "-z"],
        cwd=project,
        env=_git_environment(project, executable),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        normalized = email.casefold()
        if normalized not in SAFE_PUBLIC_EMAILS and not normalized.endswith(SAFE_PUBLIC_EMAIL_SUFFIXES):
            findings.append({"kind": "email", "path": label})
    return findings


def _git(project: Path, *arguments: str, git_path: Path | None = None) -> bytes:
    executable = Path(_git_executable(git_path))
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=project,
        env=_git_environment(project, executable),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError("GIT_REPOSITORY_REQUIRED")
    return completed.stdout


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    payload = bytearray()
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise RuntimeError("GIT_HISTORY_OBJECT_TRUNCATED")
        payload.extend(chunk)
        remaining -= len(chunk)
    return bytes(payload)


def _discard_exact(stream: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise RuntimeError("GIT_HISTORY_OBJECT_TRUNCATED")
        remaining -= len(chunk)


def _iter_git_blobs(
    project: Path,
    requests: Iterable[tuple[str, int]],
    *,
    git_path: Path | None = None,
) -> Iterator[tuple[str, int, bytes | None]]:
    """Read validated historical blobs through one locked Git batch process.

    Starting one Git process per historical blob made the public-history gate
    exceed the one-click check's bounded runtime once the repository accumulated
    a few thousand unique objects.  The batch protocol retains the same complete
    scan while validating every response before its bytes are used.
    """

    normalized: list[tuple[str, int]] = []
    seen: set[str] = set()
    for object_id, maximum_bytes in requests:
        if (
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id) is None
            or object_id in seen
            or type(maximum_bytes) is not int
            or maximum_bytes < 0
        ):
            raise RuntimeError("GIT_HISTORY_OBJECT_REQUEST_INVALID")
        seen.add(object_id)
        normalized.append((object_id, maximum_bytes))
    if not normalized:
        return

    executable = Path(_git_executable(git_path))
    process = subprocess.Popen(
        [str(executable), "cat-file", "--batch"],
        cwd=project,
        env=_git_environment(project, executable),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        process.wait()
        raise RuntimeError("GIT_HISTORY_BATCH_UNAVAILABLE")
    completed_normally = False
    try:
        for object_id, maximum_bytes in normalized:
            encoded_id = object_id.encode("ascii")
            process.stdin.write(encoded_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline(MAX_GIT_BATCH_HEADER_BYTES + 1)
            if (
                not header.endswith(b"\n")
                or len(header) > MAX_GIT_BATCH_HEADER_BYTES
            ):
                raise RuntimeError("GIT_HISTORY_BATCH_HEADER_INVALID")
            parts = header[:-1].split(b" ")
            if (
                len(parts) != 3
                or parts[0] != encoded_id
                or parts[1] != b"blob"
                or re.fullmatch(rb"(?:0|[1-9][0-9]*)", parts[2]) is None
            ):
                raise RuntimeError("GIT_HISTORY_BATCH_HEADER_INVALID")
            payload_size = int(parts[2])
            if payload_size > maximum_bytes:
                _discard_exact(process.stdout, payload_size)
                payload = None
            else:
                payload = _read_exact(process.stdout, payload_size)
            if process.stdout.read(1) != b"\n":
                raise RuntimeError("GIT_HISTORY_BATCH_SEPARATOR_INVALID")
            yield object_id, payload_size, payload
        process.stdin.close()
        if process.wait(timeout=30) != 0:
            raise RuntimeError("GIT_REPOSITORY_REQUIRED")
        completed_normally = True
    finally:
        if not completed_normally and process.poll() is None:
            process.kill()
            process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def _history_inventory(
    project: Path, *, git_path: Path | None = None
) -> tuple[list[str], dict[str, set[str]], list[dict[str, str]], int]:
    commits = [
        item.decode("ascii")
        for item in _git(project, "rev-list", "--all", git_path=git_path).splitlines()
        if item
    ]
    paths: set[str] = set()
    blob_paths: dict[str, set[str]] = {}
    findings: list[dict[str, str]] = []
    for commit in commits:
        for record in _git(
            project, "ls-tree", "-r", "-z", "--full-tree", commit, git_path=git_path
        ).split(b"\0"):
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
                [str(interpreter), str(helper)],
                input=payload,
                capture_output=True,
                timeout=45,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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


def verify_public_history(project: Path, *, git_path: Path | None = None) -> dict[str, Any]:
    paths, blob_paths, findings, commit_count = _history_inventory(project, git_path=git_path)
    findings.extend(validate_public_paths(paths))
    scanned_text_blobs = 0
    scanned_document_blobs = 0
    requests: list[tuple[str, int]] = []
    request_metadata: dict[str, tuple[str, str]] = {}
    for object_id, aliases in blob_paths.items():
        relevant = sorted(
            alias for alias in aliases
            if PurePosixPath(alias).suffix.casefold() in TEXT_SUFFIXES | HISTORY_DOCUMENT_SUFFIXES
        )
        if not relevant:
            continue
        label = relevant[0] + "@history"
        suffix = PurePosixPath(label.removesuffix("@history")).suffix.casefold()
        if suffix in HISTORY_DOCUMENT_SUFFIXES:
            scanned_document_blobs += 1
            maximum_bytes = MAX_HISTORY_DOCUMENT_BYTES
        else:
            scanned_text_blobs += 1
            maximum_bytes = MAX_HISTORY_TEXT_BYTES
        requests.append((object_id, maximum_bytes))
        request_metadata[object_id] = (label, suffix)
    for object_id, _payload_size, payload in _iter_git_blobs(
        project,
        requests,
        git_path=git_path,
    ):
        label, suffix = request_metadata[object_id]
        if payload is None:
            kind = (
                "oversized_historical_document"
                if suffix in HISTORY_DOCUMENT_SUFFIXES
                else "oversized_historical_text"
            )
            findings.append({"kind": kind, "path": label})
            continue
        if suffix in HISTORY_DOCUMENT_SUFFIXES:
            findings.extend(_scan_historical_document(project, label, suffix, payload))
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


def _author_identity_policy(project: Path) -> tuple[str, frozenset[str]]:
    policy_path = project / "config" / "public-release.json"
    policy: dict[str, Any] = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    if policy.get("schema_version") != 1:
        return "INVALID_OR_MISSING", frozenset()
    author_policy = str(policy.get("author_identity_policy"))
    if author_policy == "NOREPLY_ONLY" and set(policy) == {"schema_version", "author_identity_policy"}:
        return author_policy, frozenset()
    if author_policy != "PUBLIC_EMAIL_APPROVED" or set(policy) != {
        "schema_version",
        "author_identity_policy",
        "approved_public_emails",
    }:
        return "INVALID_OR_MISSING", frozenset()
    configured = policy.get("approved_public_emails")
    if not isinstance(configured, list) or not configured:
        return "INVALID_OR_MISSING", frozenset()
    normalized: set[str] = set()
    for value in configured:
        if not isinstance(value, str) or PUBLIC_EMAIL.fullmatch(value) is None:
            return "INVALID_OR_MISSING", frozenset()
        email = value.casefold()
        if email in normalized:
            return "INVALID_OR_MISSING", frozenset()
        normalized.add(email)
    return author_policy, frozenset(normalized)


def _is_github_noreply_email(email: str) -> bool:
    if PUBLIC_EMAIL.fullmatch(email) is None:
        return False
    if email.casefold() == "noreply@github.com":
        return True
    local, separator, domain = email.rpartition("@")
    return bool(local and separator and domain.casefold() == GITHUB_NOREPLY_DOMAIN)


def verify_author_identity(project: Path, *, git_path: Path | None = None) -> dict[str, Any]:
    author_policy, approved_public_emails = _author_identity_policy(project)
    raw = _git(
        project,
        "log",
        "--all",
        "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1e",
        git_path=git_path,
    )
    identities: set[tuple[str, str]] = set()
    malformed_records = 0
    for record in raw.split(b"\x1e"):
        if not record.strip(b"\r\n"):
            continue
        values = record.strip(b"\r\n").split(b"\x1f")
        if len(values) != 4:
            malformed_records += 1
            continue
        try:
            decoded = tuple(value.decode("utf-8", errors="strict") for value in values)
        except UnicodeDecodeError:
            malformed_records += 1
            continue
        author_name, author_email, committer_name, committer_email = decoded
        if (
            not author_name.strip()
            or not committer_name.strip()
            or PUBLIC_EMAIL.fullmatch(author_email) is None
            or PUBLIC_EMAIL.fullmatch(committer_email) is None
        ):
            malformed_records += 1
            continue
        identities.add((author_name, author_email))
        identities.add((committer_name, committer_email))
    non_noreply = sum(1 for _, email in identities if not _is_github_noreply_email(email))
    unapproved = sum(1 for _, email in identities if email.casefold() not in approved_public_emails)
    approved = bool(
        identities
        and malformed_records == 0
        and (
            (author_policy == "NOREPLY_ONLY" and non_noreply == 0)
            or (author_policy == "PUBLIC_EMAIL_APPROVED" and unapproved == 0)
        )
    )
    return {
        "status": "PASS" if approved else "REVIEW_REQUIRED",
        "policy": author_policy,
        "identity_count": len(identities),
        "non_noreply_identity_count": non_noreply,
        "unapproved_identity_count": unapproved,
        "approved_public_email_count": len(approved_public_emails),
        "malformed_record_count": malformed_records,
        "private_identity_values_emitted": 0,
    }


def verify_public_tree(project: Path, *, git_path: Path | None = None) -> dict[str, object]:
    files = tracked_files(project, git_path=git_path)
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


def verify_public_repository(project: Path, *, git_path: Path | None = None) -> dict[str, Any]:
    try:
        with locked_release_git(project, git_path) as trusted_git:
            tree = verify_public_tree(project, git_path=trusted_git)
            history = verify_public_history(project, git_path=trusted_git)
            identity = verify_author_identity(project, git_path=trusted_git)
    except (OSError, ReleaseToolchainError) as error:
        code = str(error) if isinstance(error, ReleaseToolchainError) else "RELEASE_GIT_UNTRUSTED"
        raise RuntimeError(code) from error
    blockers: list[str] = []
    if tree["status"] != "PASS":
        blockers.append("PUBLIC_TREE_BOUNDARY_FAILED")
    if history["status"] != "PASS":
        blockers.append("PUBLIC_HISTORY_BOUNDARY_FAILED")
    if identity["status"] != "PASS":
        blockers.append("GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED")
    content_pass = tree["status"] == history["status"] == "PASS"
    repository_ready = content_pass and identity["status"] == "PASS"
    return {
        "schema_version": 1,
        "status": "PASS" if content_pass else "FAIL",
        "readiness_scope": "PUBLIC_REPOSITORY_BOUNDARY_ONLY",
        "public_repository_ready": repository_ready,
        # Repository hygiene is only one input to the authoritative release
        # gate.  Keep this literal false so the standalone scanner can never
        # be mistaken for signing, tagging, upload, or publication approval.
        "public_release_ready": False,
        "public_release_blockers": blockers,
        "tree": tree,
        "history": history,
        "author_identity": identity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--git-path", type=Path)
    args = parser.parse_args()
    project = Path.cwd()
    try:
        result = verify_public_repository(project, git_path=args.git_path)
    except RuntimeError as error:
        print(
            json.dumps(
                {"status": "FAIL", "code": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = result["public_repository_ready"] if args.require_ready else result["status"] == "PASS"
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
