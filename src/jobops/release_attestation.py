from __future__ import annotations

import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .publisher_attestation import (
    EvidenceDocument,
    validate_clean_windows_acceptance,
    validate_publisher_evidence,
    validate_runtime_build_evidence,
)
from .update_manifest import verify_signed_release_bundle
from .util import has_reparse_component, load_json, sha256_bytes


_MAX_EVIDENCE_BYTES = 256 * 1024
_RUNTIME_EVIDENCE_NAMES = (
    "JobFlow-runtime-build-evidence.json",
    "JobFlow-publisher-evidence.json",
)
_STRICT_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")


def _fail(code: str, message: str) -> None:
    raise JobOpsError(code, message)


def _safe_exact_file(path: Path, *, project: Path, root: Path, code: str) -> Path:
    """Return one exact regular release file without following a reparse path.

    Release-readiness consumes only fixed local output names.  It never scans a
    directory for a plausible substitute, so a stale or attacker-selected file
    cannot become authoritative by ordering or modification time.
    """

    project = Path(os.path.abspath(project))
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        if (
            not project.is_dir()
            or not root.is_dir()
            or not path.is_file()
            or has_reparse_component(root, project)
            or has_reparse_component(path, project)
        ):
            _fail(code, "A required release evidence file is unavailable or unsafe.")
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        if resolved_path.parent != resolved_root:
            _fail(code, "A required release evidence file is outside its fixed directory.")
        return resolved_path
    except OSError as error:
        raise JobOpsError(
            code,
            "A required release evidence file is unavailable or unsafe.",
        ) from error


def _read_evidence(path: Path, *, project: Path, root: Path, code: str) -> bytes:
    resolved = _safe_exact_file(path, project=project, root=root, code=code)
    try:
        with resolved.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail(code, "A release evidence file has an unsafe identity.")
            if before.st_size < 2 or before.st_size > _MAX_EVIDENCE_BYTES:
                _fail(code, "A release evidence file has an invalid size.")
            raw = source.read(_MAX_EVIDENCE_BYTES + 1)
            after = os.fstat(source.fileno())
    except OSError as error:
        raise JobOpsError(code, "A release evidence file is unavailable.") from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_nlink,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_nlink,
    )
    if (
        identity_before != identity_after
        or len(raw) != before.st_size
        or len(raw) > _MAX_EVIDENCE_BYTES
        or has_reparse_component(resolved, project)
    ):
        _fail(code, "A release evidence file changed while it was being read.")
    return raw


def _read_external_evidence(path: Path, *, destination: Path) -> bytes:
    """Read one caller-selected evidence file through a stable local handle.

    Clean-Windows evidence may arrive from removable media or another local
    directory, so it cannot be constrained to the repository.  It must still
    be one ordinary, single-link file with no reparse component and it must not
    alias the authoritative destination.
    """

    source = Path(os.path.abspath(path))
    authoritative = Path(os.path.abspath(destination))
    try:
        if source == authoritative or not source.is_file() or has_reparse_component(source):
            _fail(
                "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE",
                "The selected clean-Windows evidence file is unavailable or unsafe.",
            )
        with source.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail(
                    "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE",
                    "The selected clean-Windows evidence file has an unsafe identity.",
                )
            if before.st_size < 2 or before.st_size > _MAX_EVIDENCE_BYTES:
                _fail(
                    "CLEAN_WINDOWS_EVIDENCE_SOURCE_INVALID",
                    "The selected clean-Windows evidence file has an invalid size.",
                )
            raw = stream.read(_MAX_EVIDENCE_BYTES + 1)
            after = os.fstat(stream.fileno())
    except JobOpsError:
        raise
    except OSError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE",
            "The selected clean-Windows evidence file could not be read safely.",
        ) from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_nlink,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_nlink,
    )
    if (
        identity_before != identity_after
        or len(raw) != before.st_size
        or len(raw) > _MAX_EVIDENCE_BYTES
        or has_reparse_component(source)
    ):
        _fail(
            "CLEAN_WINDOWS_EVIDENCE_SOURCE_CHANGED",
            "The selected clean-Windows evidence file changed while it was being read.",
        )
    return raw


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        _fail(
            "RELEASE_ATTESTATION_BINDING_MISMATCH",
            f"The signed release evidence does not match {field}.",
        )


def _companion_version(project: Path) -> str:
    try:
        manifest = load_json(project / "browser-companion" / "manifest.json")
        version = manifest.get("version")
    except (OSError, ValueError, TypeError):
        version = None
    if not isinstance(version, str) or not version:
        _fail(
            "RELEASE_ATTESTATION_POLICY_INVALID",
            "The Browser Companion version policy is unavailable.",
        )
    return version


def _base_result(
    *,
    version: str,
    commit: str,
    release_status: str,
    clean_status: str,
    runtime_status: str,
    failure_code: str | None,
) -> dict[str, Any]:
    ready = release_status == "PASS" and clean_status == "PASS"
    return {
        "schema_version": 1,
        "status": "PASS" if ready else "BLOCKED",
        "release_attestation_status": release_status,
        "clean_windows_evidence_status": clean_status,
        "runtime_closure_status": runtime_status,
        "version": version,
        "source_commit": commit,
        "failure_code": failure_code,
        "signature_verified": release_status == "PASS",
        "external_actions": 0,
        "real_external_actions": 0,
    }


def verify_public_release_attestation(
    project: Path,
    *,
    version: str,
    commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify the exact current public-release evidence chain.

    The runtime closure becomes ``ATTESTED`` only after the pinned RSA
    signature, complete runtime archive, canonical build evidence, canonical
    publisher evidence, and their cross-bindings all verify.  Clean-Windows
    evidence remains a distinct, short-lived observation and cannot substitute
    for the signed runtime chain.
    """

    project = Path(os.path.abspath(project))
    dist = project / "dist"
    manifest_path = dist / "JobFlow-update-manifest.json"
    signature_path = dist / "JobFlow-update-manifest.sig.json"
    archive_path = dist / f"JobFlow-v{version}-windows-x64-complete.zip"
    runtime_path = dist / _RUNTIME_EVIDENCE_NAMES[0]
    publisher_path = dist / _RUNTIME_EVIDENCE_NAMES[1]
    clean_path = dist / "JobFlow-clean-windows-acceptance.json"
    runtime_paths = (
        manifest_path,
        signature_path,
        archive_path,
        runtime_path,
        publisher_path,
    )
    present = [path.is_file() for path in runtime_paths]
    if not any(present):
        return _base_result(
            version=version,
            commit=commit,
            release_status="MISSING",
            clean_status="NOT_CHECKED",
            runtime_status="UNATTESTED",
            failure_code="RELEASE_ATTESTATION_MISSING",
        )
    if not all(present):
        return _base_result(
            version=version,
            commit=commit,
            release_status="INVALID",
            clean_status="NOT_CHECKED",
            runtime_status="UNATTESTED",
            failure_code="RELEASE_ATTESTATION_INCOMPLETE",
        )

    try:
        _safe_exact_file(manifest_path, project=project, root=dist, code="RELEASE_ATTESTATION_INVALID")
        _safe_exact_file(signature_path, project=project, root=dist, code="RELEASE_ATTESTATION_INVALID")
        _safe_exact_file(archive_path, project=project, root=dist, code="RELEASE_ATTESTATION_INVALID")
        runtime_raw = _read_evidence(
            runtime_path,
            project=project,
            root=dist,
            code="RELEASE_RUNTIME_BUILD_EVIDENCE_INVALID",
        )
        publisher_raw = _read_evidence(
            publisher_path,
            project=project,
            root=dist,
            code="RELEASE_PUBLISHER_EVIDENCE_INVALID",
        )
        schemas = project / "schemas"
        runtime_document = validate_runtime_build_evidence(
            runtime_raw,
            now=now,
            schema_dir=schemas,
        )
        publisher_document = validate_publisher_evidence(
            publisher_raw,
            runtime_build=runtime_document,
            now=now,
            schema_dir=schemas,
        )
        bundle = verify_signed_release_bundle(
            manifest_path,
            signature_path,
            archive_path,
            release_version=version,
            channel_path=project / "config" / "update-channel.json",
            schema_dir=schemas,
        )
        build = runtime_document.value
        publisher = publisher_document.value
        release = publisher["release"]
        closure = publisher["runtime_closure"]
        signer = publisher["outer_signing_readiness"]

        _require_equal(bundle["status"], "RELEASE_BUNDLE_VERIFIED", "bundle status")
        _require_equal(bundle["signature_verified"], True, "signature verification")
        _require_equal(bundle["archive_verified"], True, "archive verification")
        _require_equal(bundle["runtime_closure_verified"], True, "runtime closure verification")
        _require_equal(bundle["publisher_attestation_status"], "ATTESTED", "publisher attestation")
        _require_equal(bundle["available_version"], version, "application version")
        _require_equal(bundle["commit"], commit, "source commit")
        _require_equal(bundle["release_platform"], "windows-x64", "release platform")
        _require_equal(build["application_version"], version, "build application version")
        _require_equal(build["source_commit"], commit, "build source commit")
        _require_equal(release["version"], version, "publisher application version")
        _require_equal(release["source_commit"], commit, "publisher source commit")
        _require_equal(bundle["runtime_build_evidence_sha256"], runtime_document.sha256, "runtime evidence digest")
        _require_equal(bundle["publisher_evidence_sha256"], publisher_document.sha256, "publisher evidence digest")
        _require_equal(bundle["publisher_evidence_expires_at_utc"], publisher["expires_at_utc"], "publisher evidence expiry")
        _require_equal(bundle["publisher_attestation_issued_at_utc"], publisher["issued_at_utc"], "publisher evidence issue time")
        _require_equal(bundle["publisher_build_inputs_sha256"], publisher["build_inputs_sha256"], "build inputs digest")
        _require_equal(bundle["publisher_policy_sha256"], signer["provider_policy_sha256"], "signer policy digest")
        _require_equal(bundle["signer_readiness_challenge_sha256"], signer["challenge_sha256"], "signer challenge digest")
        _require_equal(bundle["key_id"], signer["release_key_id"], "release key")

        archive_bindings = (
            (bundle["asset_name"], release["archive_name"], "archive name"),
            (bundle["asset_bytes"], release["archive_bytes"], "archive bytes"),
            (bundle["asset_sha256"], release["archive_sha256"], "archive digest"),
            (bundle["archive_prefix"], release["archive_prefix"], "archive prefix"),
        )
        for actual, expected, field in archive_bindings:
            _require_equal(actual, expected, field)
        expected_build_archive = {
            "name": release["archive_name"],
            "bytes": release["archive_bytes"],
            "sha256": release["archive_sha256"],
            "archive_prefix": release["archive_prefix"],
        }
        _require_equal(build["archive"], expected_build_archive, "runtime archive identity")

        closure_bindings = (
            (bundle["runtime_closure_manifest_sha256"], closure["manifest_sha256"], "closure manifest digest"),
            (bundle["runtime_tree_sha256"], closure["tree_sha256"], "runtime tree digest"),
            (bundle["source_payload_sha256"], closure["source_payload_sha256"], "source payload digest"),
            (bundle["runtime_file_count"], closure["file_count"], "runtime file count"),
            (bundle["runtime_total_bytes"], closure["total_bytes"], "runtime total bytes"),
        )
        for actual, expected, field in closure_bindings:
            _require_equal(actual, expected, field)
        _require_equal(build["runtime_closure"]["manifest_sha256"], closure["manifest_sha256"], "build closure manifest")
        _require_equal(build["runtime_closure"]["tree_sha256"], closure["tree_sha256"], "build runtime tree")
        _require_equal(build["runtime_closure"]["source_payload_sha256"], closure["source_payload_sha256"], "build source payload")
    except (JobOpsError, KeyError, TypeError, ValueError, OSError) as error:
        code = error.code if isinstance(error, JobOpsError) else "RELEASE_ATTESTATION_INVALID"
        return _base_result(
            version=version,
            commit=commit,
            release_status="INVALID",
            clean_status="NOT_CHECKED",
            runtime_status="UNATTESTED",
            failure_code=code,
        )

    if not clean_path.is_file():
        return _base_result(
            version=version,
            commit=commit,
            release_status="PASS",
            clean_status="MISSING",
            runtime_status="ATTESTED",
            failure_code="CLEAN_WINDOWS_EVIDENCE_MISSING",
        )

    try:
        clean_raw = _read_evidence(
            clean_path,
            project=project,
            root=dist,
            code="CLEAN_WINDOWS_EVIDENCE_INVALID",
        )
        clean_document = validate_clean_windows_acceptance(
            clean_raw,
            publisher_evidence=publisher_document,
            now=now,
            schema_dir=project / "schemas",
        )
        clean = clean_document.value
        signed = clean["signed_bundle"]
        companion = clean["browser_companion"]
        required_companion = _companion_version(project)
        _require_equal(clean["release"]["version"], version, "clean Windows application version")
        _require_equal(clean["release"]["source_commit"], commit, "clean Windows source commit")
        _require_equal(clean["publisher_evidence_sha256"], publisher_document.sha256, "clean Windows publisher digest")
        _require_equal(signed["manifest_sha256"], bundle["manifest_sha256"], "clean Windows manifest digest")
        _require_equal(signed["signature_sha256"], bundle["signature_sha256"], "clean Windows signature digest")
        _require_equal(signed["archive_name"], bundle["asset_name"], "clean Windows archive name")
        _require_equal(signed["archive_bytes"], bundle["asset_bytes"], "clean Windows archive bytes")
        _require_equal(signed["archive_sha256"], bundle["asset_sha256"], "clean Windows archive digest")
        _require_equal(signed["release_key_id"], bundle["key_id"], "clean Windows release key")
        _require_equal(clean["runtime_closure"]["manifest_sha256"], bundle["runtime_closure_manifest_sha256"], "clean Windows closure digest")
        _require_equal(clean["runtime_closure"]["tree_sha256"], bundle["runtime_tree_sha256"], "clean Windows tree digest")
        _require_equal(companion["version"], required_companion, "Browser Companion version")
        _require_equal(companion["chrome_store_version"], required_companion, "Chrome store version")
        _require_equal(companion["edge_store_version"], required_companion, "Edge store version")
    except (JobOpsError, KeyError, TypeError, ValueError, OSError) as error:
        code = error.code if isinstance(error, JobOpsError) else "CLEAN_WINDOWS_EVIDENCE_INVALID"
        return _base_result(
            version=version,
            commit=commit,
            release_status="PASS",
            clean_status="INVALID",
            runtime_status="ATTESTED",
            failure_code=code,
        )

    return _base_result(
        version=version,
        commit=commit,
        release_status="PASS",
        clean_status="PASS",
        runtime_status="ATTESTED",
        failure_code=None,
    )


def _clean_import_context(
    project: Path,
    *,
    version: str,
    commit: str,
    now: datetime | None,
) -> tuple[EvidenceDocument, EvidenceDocument, dict[str, Any]]:
    """Re-open the already verified release inputs needed by the importer."""

    initial = verify_public_release_attestation(
        project,
        version=version,
        commit=commit,
        now=now,
    )
    if (
        initial.get("release_attestation_status") != "PASS"
        or initial.get("runtime_closure_status") != "ATTESTED"
    ):
        _fail(
            "CLEAN_WINDOWS_IMPORT_RELEASE_UNATTESTED",
            "Clean-Windows evidence can be imported only for an exact verified signed release.",
        )

    dist = project / "dist"
    runtime_path = dist / _RUNTIME_EVIDENCE_NAMES[0]
    publisher_path = dist / _RUNTIME_EVIDENCE_NAMES[1]
    manifest_path = dist / "JobFlow-update-manifest.json"
    signature_path = dist / "JobFlow-update-manifest.sig.json"
    archive_path = dist / f"JobFlow-v{version}-windows-x64-complete.zip"
    runtime_document = validate_runtime_build_evidence(
        _read_evidence(
            runtime_path,
            project=project,
            root=dist,
            code="RELEASE_RUNTIME_BUILD_EVIDENCE_INVALID",
        ),
        now=now,
        schema_dir=project / "schemas",
    )
    publisher_document = validate_publisher_evidence(
        _read_evidence(
            publisher_path,
            project=project,
            root=dist,
            code="RELEASE_PUBLISHER_EVIDENCE_INVALID",
        ),
        runtime_build=runtime_document,
        now=now,
        schema_dir=project / "schemas",
    )
    bundle = verify_signed_release_bundle(
        manifest_path,
        signature_path,
        archive_path,
        release_version=version,
        channel_path=project / "config" / "update-channel.json",
        schema_dir=project / "schemas",
    )
    return runtime_document, publisher_document, bundle


def _validate_clean_import_candidate(
    raw: bytes,
    *,
    project: Path,
    version: str,
    commit: str,
    now: datetime | None,
) -> EvidenceDocument:
    _runtime, publisher, bundle = _clean_import_context(
        project,
        version=version,
        commit=commit,
        now=now,
    )
    document = validate_clean_windows_acceptance(
        raw,
        publisher_evidence=publisher,
        now=now,
        schema_dir=project / "schemas",
    )
    clean = document.value
    signed = clean["signed_bundle"]
    companion = clean["browser_companion"]
    required_companion = _companion_version(project)
    _require_equal(clean["release"]["version"], version, "clean Windows application version")
    _require_equal(clean["release"]["source_commit"], commit, "clean Windows source commit")
    _require_equal(clean["publisher_evidence_sha256"], publisher.sha256, "clean Windows publisher digest")
    _require_equal(signed["manifest_sha256"], bundle["manifest_sha256"], "clean Windows manifest digest")
    _require_equal(signed["signature_sha256"], bundle["signature_sha256"], "clean Windows signature digest")
    _require_equal(signed["archive_name"], bundle["asset_name"], "clean Windows archive name")
    _require_equal(signed["archive_bytes"], bundle["asset_bytes"], "clean Windows archive bytes")
    _require_equal(signed["archive_sha256"], bundle["asset_sha256"], "clean Windows archive digest")
    _require_equal(signed["release_key_id"], bundle["key_id"], "clean Windows release key")
    _require_equal(
        clean["runtime_closure"]["manifest_sha256"],
        bundle["runtime_closure_manifest_sha256"],
        "clean Windows closure digest",
    )
    _require_equal(
        clean["runtime_closure"]["tree_sha256"],
        bundle["runtime_tree_sha256"],
        "clean Windows tree digest",
    )
    _require_equal(companion["version"], required_companion, "Browser Companion version")
    _require_equal(companion["chrome_store_version"], required_companion, "Chrome store version")
    _require_equal(companion["edge_store_version"], required_companion, "Edge store version")
    return document


def _atomic_replace_evidence(destination: Path, raw: bytes) -> None:
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            staging = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        staged = staging.stat()
        if (
            not stat.S_ISREG(staged.st_mode)
            or staged.st_nlink != 1
            or has_reparse_component(staging, destination.parent)
        ):
            _fail(
                "CLEAN_WINDOWS_EVIDENCE_COMMIT_UNSAFE",
                "The clean-Windows evidence staging file has an unsafe identity.",
            )
        os.replace(staging, destination)
        staging = None
    except JobOpsError:
        raise
    except OSError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_EVIDENCE_COMMIT_FAILED",
            "The validated clean-Windows evidence could not be committed atomically.",
        ) from error
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass


def _restore_clean_evidence(
    destination: Path,
    previous: bytes | None,
    candidate: bytes,
    *,
    project: Path,
    root: Path,
) -> None:
    try:
        if previous is None:
            quarantine: Path | None = None
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.rollback-",
                suffix=".tmp",
                dir=root,
                delete=False,
            ) as stream:
                quarantine = Path(stream.name)
            try:
                # Remove the failed candidate from its authoritative fixed name
                # before inspecting or deleting it.  A concurrent replacement
                # can therefore never make rollback unlink an attacker-selected
                # path at the authoritative destination.
                os.replace(destination, quarantine)
            except OSError:
                quarantine.unlink(missing_ok=True)
                raise
            current = _read_evidence(
                quarantine,
                project=project,
                root=root,
                code="CLEAN_WINDOWS_EVIDENCE_ROLLBACK_FAILED",
            )
            if current != candidate:
                _fail(
                    "CLEAN_WINDOWS_EVIDENCE_ROLLBACK_FAILED",
                    "The clean-Windows evidence changed before rollback.",
                )
            quarantine.unlink()
        else:
            _atomic_replace_evidence(destination, previous)
    except JobOpsError:
        raise
    except OSError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_EVIDENCE_ROLLBACK_FAILED",
            "The previous clean-Windows evidence could not be restored.",
        ) from error


def import_clean_windows_acceptance(
    project: Path,
    source: Path,
    *,
    version: str,
    commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and atomically import one clean-Windows observation.

    The source document never becomes authoritative until every binding has
    passed.  A final full-chain verification runs after the atomic replacement;
    an unexpected failure restores the prior fixed evidence file.
    """

    if not isinstance(version, str) or _STRICT_SEMVER.fullmatch(version) is None:
        _fail("CLEAN_WINDOWS_IMPORT_IDENTITY_INVALID", "The release version is invalid.")
    if not isinstance(commit, str) or _SOURCE_COMMIT.fullmatch(commit) is None:
        _fail("CLEAN_WINDOWS_IMPORT_IDENTITY_INVALID", "The source commit is invalid.")
    project = Path(os.path.abspath(project))
    dist = project / "dist"
    destination = dist / "JobFlow-clean-windows-acceptance.json"
    if (
        not project.is_dir()
        or not (project / ".jobops-root").is_file()
        or not dist.is_dir()
        or has_reparse_component(dist, project)
    ):
        _fail(
            "CLEAN_WINDOWS_EVIDENCE_DESTINATION_UNSAFE",
            "The fixed release evidence directory is unavailable or unsafe.",
        )

    raw = _read_external_evidence(source, destination=destination)
    document = _validate_clean_import_candidate(
        raw,
        project=project,
        version=version,
        commit=commit,
        now=now,
    )
    previous: bytes | None = None
    if destination.exists():
        previous = _read_evidence(
            destination,
            project=project,
            root=dist,
            code="CLEAN_WINDOWS_EVIDENCE_DESTINATION_UNSAFE",
        )

    _atomic_replace_evidence(destination, document.canonical_bytes)
    postverify_error: Exception | None = None
    try:
        final = verify_public_release_attestation(
            project,
            version=version,
            commit=commit,
            now=now,
        )
    except (JobOpsError, KeyError, TypeError, ValueError, OSError) as error:
        final = {}
        postverify_error = error
    if (
        postverify_error is not None
        or final.get("status") != "PASS"
        or final.get("release_attestation_status") != "PASS"
        or final.get("clean_windows_evidence_status") != "PASS"
        or final.get("runtime_closure_status") != "ATTESTED"
    ):
        _restore_clean_evidence(
            destination,
            previous,
            document.canonical_bytes,
            project=project,
            root=dist,
        )
        raise JobOpsError(
            "CLEAN_WINDOWS_IMPORT_POSTVERIFY_FAILED",
            "The imported clean-Windows evidence failed final release-chain verification.",
        ) from postverify_error
    return {
        "schema_version": 1,
        "status": "CLEAN_WINDOWS_EVIDENCE_IMPORTED",
        "version": version,
        "source_commit": commit,
        "evidence_sha256": document.sha256,
        "release_attestation_status": "PASS",
        "clean_windows_evidence_status": "PASS",
        "runtime_closure_status": "ATTESTED",
        "external_actions": 0,
        "real_external_actions": 0,
    }


__all__ = ["import_clean_windows_acceptance", "verify_public_release_attestation"]
