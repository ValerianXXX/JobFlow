from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .errors import JobOpsError
from .publisher_attestation import (
    EvidenceDocument,
    validate_publisher_evidence,
    validate_runtime_build_evidence,
)
from .runtime_schema import validate_named
from .util import canonical_json, load_json, project_root, sha256_bytes, sha256_file


REQUEST_FORMAT = "JOBFLOW_PROTECTED_PUBLISHER_REQUEST_V1"
REQUEST_STATUS = "AWAITING_PROTECTED_PUBLISHER_EVIDENCE"
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 1536 * 1024 * 1024


class _DuplicateJsonKey(ValueError):
    pass


def _fail(code: str, message: str, **details: object) -> None:
    raise JobOpsError(code, message, **details)


def _schema_directory(schema_dir: Path | None) -> Path:
    directory = Path(schema_dir) if schema_dir is not None else project_root() / "schemas"
    if not directory.is_dir() or directory.is_symlink():
        _fail("PROTECTED_PUBLISHER_POLICY_INVALID", "The release schema directory is unavailable.")
    return directory


def _read_regular(path: Path, *, maximum: int, code: str) -> bytes:
    path = Path(path)
    try:
        before = path.stat()
    except OSError as error:
        raise JobOpsError(code, "A required protected-publisher input is unavailable.") from error
    if not path.is_file() or path.is_symlink() or not 1 <= before.st_size <= maximum:
        _fail(code, "A required protected-publisher input is unsafe or outside its size bound.")
    try:
        payload = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise JobOpsError(code, "A required protected-publisher input could not be read.") from error
    if (
        len(payload) != before.st_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    ):
        _fail(code, "A required protected-publisher input changed while it was read.")
    return payload


def _file_identity(path: Path, *, maximum: int, code: str) -> tuple[int, str]:
    path = Path(path)
    try:
        before = path.stat()
    except OSError as error:
        raise JobOpsError(code, "A required protected-publisher input is unavailable.") from error
    if not path.is_file() or path.is_symlink() or not 1 <= before.st_size <= maximum:
        _fail(code, "A required protected-publisher input is unsafe or outside its size bound.")
    try:
        digest = sha256_file(path)
        after = path.stat()
    except OSError as error:
        raise JobOpsError(code, "A required protected-publisher input could not be hashed.") from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    ):
        _fail(code, "A required protected-publisher input changed while it was hashed.")
    return before.st_size, digest


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _parse_request(raw: bytes, *, schema_dir: Path) -> dict[str, Any]:
    if type(raw) is not bytes or not 2 <= len(raw) <= MAX_EVIDENCE_BYTES:
        _fail("PROTECTED_PUBLISHER_REQUEST_INVALID", "The publisher request must be bounded canonical JSON bytes.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as error:
        raise JobOpsError(
            "PROTECTED_PUBLISHER_REQUEST_INVALID",
            "The publisher request is not valid duplicate-free UTF-8 JSON.",
        ) from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        _fail("PROTECTED_PUBLISHER_REQUEST_INVALID", "The publisher request must use canonical JSON encoding.")
    validate_named("protected-publisher-request-v1", value, schema_dir)
    return value


def _pinned_policy(schema_dir: Path) -> dict[str, str]:
    root = schema_dir.parent
    source_path = root / "config" / "windows-runtime-source.json"
    channel_path = root / "config" / "update-channel.json"
    try:
        source = load_json(source_path)
        channel = load_json(channel_path)
        python = source["python"]
        policy = {
            "windows_runtime_source_sha256": sha256_file(source_path),
            "update_channel_sha256": sha256_file(channel_path),
            "release_key_id": str(channel["signature"]["key_id"]),
            "sigstore_certificate_identity": str(python["sigstore_certificate_identity"]),
            "sigstore_certificate_oidc_issuer": str(python["sigstore_certificate_oidc_issuer"]),
        }
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise JobOpsError(
            "PROTECTED_PUBLISHER_POLICY_INVALID",
            "The pinned protected-publisher policy is invalid.",
        ) from error
    return policy


def validate_protected_publisher_request(
    raw: bytes,
    *,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    schemas = _schema_directory(schema_dir)
    value = _parse_request(raw, schema_dir=schemas)
    if value["pinned_policy"] != _pinned_policy(schemas):
        _fail(
            "PROTECTED_PUBLISHER_POLICY_MISMATCH",
            "The publisher request does not bind the current pinned release policy.",
        )
    return value


def build_protected_publisher_request(
    *,
    archive_path: Path,
    runtime_build_evidence_path: Path,
    now: datetime | None = None,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    schemas = _schema_directory(schema_dir)
    runtime_raw = _read_regular(
        runtime_build_evidence_path,
        maximum=MAX_EVIDENCE_BYTES,
        code="PROTECTED_PUBLISHER_RUNTIME_EVIDENCE_INVALID",
    )
    runtime = validate_runtime_build_evidence(runtime_raw, now=now, schema_dir=schemas)
    archive_bytes, archive_sha256 = _file_identity(
        archive_path,
        maximum=MAX_ARCHIVE_BYTES,
        code="PROTECTED_PUBLISHER_ARCHIVE_INVALID",
    )
    build = runtime.value
    archive = build["archive"]
    observed_archive = {
        "name": Path(archive_path).name,
        "bytes": archive_bytes,
        "sha256": archive_sha256,
        "archive_prefix": archive["archive_prefix"],
    }
    if observed_archive != archive:
        _fail(
            "PROTECTED_PUBLISHER_ARCHIVE_BINDING_MISMATCH",
            "The supplied complete runtime archive does not match its validated build evidence.",
        )
    closure = build["runtime_closure"]
    python = build["python_source"]
    request = {
        "schema_version": 1,
        "format": REQUEST_FORMAT,
        "status": REQUEST_STATUS,
        "release": {
            "version": build["application_version"],
            "source_commit": build["source_commit"],
            "platform": build["platform"],
        },
        "archive": observed_archive,
        "runtime_build_evidence": {
            "name": "JobFlow-runtime-build-evidence.json",
            "bytes": len(runtime_raw),
            "sha256": runtime.sha256,
            "issued_at_utc": build["issued_at_utc"],
            "expires_at_utc": build["expires_at_utc"],
        },
        "runtime_closure": {
            "manifest_sha256": closure["manifest_sha256"],
            "tree_sha256": closure["tree_sha256"],
            "source_payload_sha256": closure["source_payload_sha256"],
            "file_count": closure["file_count"],
            "total_bytes": closure["total_bytes"],
        },
        "build_inputs_sha256": build["build_inputs_sha256"],
        "python_source": {
            "artifact_name": python["artifact_name"],
            "artifact_bytes": python["artifact_bytes"],
            "artifact_sha256": python["artifact_sha256"],
            "sigstore_bundle_name": python["sigstore_bundle_name"],
            "sigstore_bundle_bytes": python["sigstore_bundle_bytes"],
            "sigstore_bundle_sha256": python["sigstore_bundle_sha256"],
        },
        "pinned_policy": _pinned_policy(schemas),
        "required_response": {
            "name": "JobFlow-publisher-evidence.json",
            "schema_name": "publisher-evidence-v1",
            "format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
            "status": "READY_FOR_PROTECTED_SIGNING",
            "maximum_bytes": MAX_EVIDENCE_BYTES,
            "signer_challenge_format": "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1",
        },
        "safety": {
            "request_contains_local_paths": False,
            "request_contains_secret_material": False,
            "protected_key_required_by_request": False,
            "external_actions": 0,
        },
    }
    canonical = canonical_json(request)
    validated = validate_protected_publisher_request(canonical, schema_dir=schemas)
    if validated != request:
        raise AssertionError("protected publisher request validation changed its value")
    return request


def validate_protected_publisher_response(
    *,
    request_raw: bytes,
    archive_path: Path,
    runtime_build_evidence_path: Path,
    publisher_evidence_path: Path,
    now: datetime | None = None,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    schemas = _schema_directory(schema_dir)
    request = validate_protected_publisher_request(request_raw, schema_dir=schemas)
    rebuilt = build_protected_publisher_request(
        archive_path=archive_path,
        runtime_build_evidence_path=runtime_build_evidence_path,
        now=now,
        schema_dir=schemas,
    )
    if canonical_json(rebuilt) != request_raw:
        _fail(
            "PROTECTED_PUBLISHER_REQUEST_BINDING_MISMATCH",
            "The publisher request does not match the exact current release inputs.",
        )
    runtime_raw = _read_regular(
        runtime_build_evidence_path,
        maximum=MAX_EVIDENCE_BYTES,
        code="PROTECTED_PUBLISHER_RUNTIME_EVIDENCE_INVALID",
    )
    runtime = validate_runtime_build_evidence(runtime_raw, now=now, schema_dir=schemas)
    publisher_raw = _read_regular(
        publisher_evidence_path,
        maximum=MAX_EVIDENCE_BYTES,
        code="PROTECTED_PUBLISHER_RESPONSE_INVALID",
    )
    publisher: EvidenceDocument = validate_publisher_evidence(
        publisher_raw,
        runtime_build=runtime,
        now=now,
        schema_dir=schemas,
    )
    value = publisher.value
    if request["runtime_build_evidence"]["sha256"] != runtime.sha256:
        _fail(
            "PROTECTED_PUBLISHER_RESPONSE_BINDING_MISMATCH",
            "The returned publisher evidence is not bound to the requested runtime evidence.",
        )
    return {
        "schema_version": 1,
        "status": "PROTECTED_PUBLISHER_RESPONSE_VERIFIED",
        "release": request["release"],
        "request_sha256": sha256_bytes(request_raw),
        "runtime_build_evidence_sha256": runtime.sha256,
        "publisher_evidence_sha256": publisher.sha256,
        "publisher_evidence_expires_at_utc": value["expires_at_utc"],
        "outer_signing_readiness": {
            "release_key_id": value["outer_signing_readiness"]["release_key_id"],
            "provider_policy_sha256": value["outer_signing_readiness"]["provider_policy_sha256"],
            "challenge_sha256": value["outer_signing_readiness"]["challenge_sha256"],
        },
        "ready_for_presign": True,
        "secret_material_read": 0,
        "external_actions": 0,
    }


def write_protected_publisher_request(
    output_path: Path,
    request: dict[str, Any],
    *,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    schemas = _schema_directory(schema_dir)
    payload = canonical_json(request)
    validate_protected_publisher_request(payload, schema_dir=schemas)
    output = Path(os.path.abspath(output_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temporary:
            staging = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(staging, output)
        staging = None
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    committed = _read_regular(
        output,
        maximum=MAX_EVIDENCE_BYTES,
        code="PROTECTED_PUBLISHER_REQUEST_WRITE_FAILED",
    )
    validate_protected_publisher_request(committed, schema_dir=schemas)
    if committed != payload:
        _fail("PROTECTED_PUBLISHER_REQUEST_WRITE_FAILED", "The committed publisher request changed after validation.")
    return {
        "schema_version": 1,
        "status": "PROTECTED_PUBLISHER_REQUEST_READY",
        "output_name": output.name,
        "bytes": len(committed),
        "sha256": sha256_bytes(committed),
        "release": request["release"],
        "secret_material_written": 0,
        "external_actions": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or verify the pathless JobFlow protected-publisher handoff."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--archive", type=Path, required=True)
    prepare.add_argument("--runtime-build-evidence", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--request", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--runtime-build-evidence", type=Path, required=True)
    verify.add_argument("--publisher-evidence", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "prepare":
        request = build_protected_publisher_request(
            archive_path=arguments.archive,
            runtime_build_evidence_path=arguments.runtime_build_evidence,
        )
        result = write_protected_publisher_request(arguments.output, request)
    else:
        request_raw = _read_regular(
            arguments.request,
            maximum=MAX_EVIDENCE_BYTES,
            code="PROTECTED_PUBLISHER_REQUEST_INVALID",
        )
        result = validate_protected_publisher_response(
            request_raw=request_raw,
            archive_path=arguments.archive,
            runtime_build_evidence_path=arguments.runtime_build_evidence,
            publisher_evidence_path=arguments.publisher_evidence,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
