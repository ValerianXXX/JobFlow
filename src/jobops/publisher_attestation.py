from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import JobOpsError
from .runtime_schema import validate_application_wheel_provenance, validate_named
from .util import canonical_json, load_json, parse_iso, project_root, sha256_bytes, sha256_file


_MAX_EVIDENCE_BYTES = 256 * 1024
_CLOCK_SKEW = timedelta(minutes=5)
_RUNTIME_BUILD_MAX_LIFETIME = timedelta(hours=24)
_PUBLISHER_MAX_LIFETIME = timedelta(hours=4)
_CLEAN_WINDOWS_MAX_LIFETIME = timedelta(hours=24)
_SIGNER_CHALLENGE_FORMAT = "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1"
_EXPLICIT_UTC = re.compile(r"Z$")

_LOCAL_PATH = re.compile(
    r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\\]|/(?:users|home|private|var|tmp)/|%[a-z][a-z0-9_]*%[\\/]|(?:^|\s)~[\\/])"
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"github_pat_[A-Za-z0-9_]{10,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[:=]"
    r")"
)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceDocument:
    """One canonical, schema-validated evidence document.

    The canonical bytes are the authority. ``value`` reparses them on each use
    so a caller cannot mutate the object later and bypass a cross-binding check.
    The document is evidence only; it is not a signature or an attestation.
    """

    schema_name: str
    canonical_bytes: bytes
    sha256: str

    @property
    def value(self) -> dict[str, Any]:
        value = json.loads(self.canonical_bytes.decode("utf-8"))
        if not isinstance(value, dict):  # Defensive: construction already enforces this.
            raise AssertionError("validated evidence is not an object")
        return value


@dataclass(frozen=True)
class _PinnedPolicy:
    platform: str
    python_version: str
    python_artifact_name: str
    python_artifact_bytes: int
    python_artifact_sha256: str
    sigstore_bundle_name: str
    sigstore_bundle_bytes: int
    sigstore_bundle_sha256: str
    sigstore_identity: str
    sigstore_issuer: str
    runtime_lock_sha256: str
    build_lock_sha256: str
    runtime_wheel_count: int
    build_wheel_count: int
    release_key_id: str


def _fail(code: str, message: str, **details: object) -> None:
    raise JobOpsError(code, message, **details)


def _schema_directory(schema_dir: Path | None) -> Path:
    directory = Path(schema_dir) if schema_dir is not None else project_root() / "schemas"
    if not directory.is_dir() or directory.is_symlink():
        _fail("PUBLISHER_EVIDENCE_POLICY_INVALID", "The evidence schema directory is unavailable.")
    return directory


def _policy_file(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        _fail("PUBLISHER_EVIDENCE_POLICY_INVALID", "A pinned policy file reference is invalid.")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("PUBLISHER_EVIDENCE_POLICY_INVALID", "A pinned policy file reference is invalid.")
    candidate = root.joinpath(*relative.parts)
    if not candidate.is_file() or candidate.is_symlink():
        _fail("PUBLISHER_EVIDENCE_POLICY_INVALID", "A pinned policy file is unavailable.")
    return candidate


def _load_pinned_policy(schema_dir: Path) -> _PinnedPolicy:
    root = schema_dir.parent
    try:
        source_path = root / "config" / "windows-runtime-source.json"
        channel_path = root / "config" / "update-channel.json"
        if not source_path.is_file() or source_path.is_symlink() or not channel_path.is_file() or channel_path.is_symlink():
            raise ValueError("missing policy")
        source = load_json(source_path)
        channel = load_json(channel_path)
        python = source["python"]
        builder = source["builder"]
        runtime_lock_path = _policy_file(root, builder["runtime_lock"])
        build_lock_path = _policy_file(root, builder["build_lock"])
        runtime_lock = load_json(runtime_lock_path)
        build_lock = load_json(build_lock_path)
        if (
            source.get("schema_version") != 1
            or source.get("status") != "PINNED_OFFICIAL_SOURCE"
            or channel.get("schema_version") != 1
            or runtime_lock.get("schema_version") != 1
            or runtime_lock.get("lock_type") != "runtime-wheelhouse"
            or build_lock.get("schema_version") != 1
            or build_lock.get("lock_type") != "protected-builder-wheelhouse"
        ):
            raise ValueError("policy shape")
        runtime_lock_sha256 = sha256_file(runtime_lock_path)
        build_lock_sha256 = sha256_file(build_lock_path)
        if runtime_lock_sha256 != builder["runtime_lock_sha256"] or build_lock_sha256 != builder["build_lock_sha256"]:
            raise ValueError("policy digest")
        runtime_packages = runtime_lock["packages"]
        build_packages = build_lock["packages"]
        if not isinstance(runtime_packages, list) or not runtime_packages or not isinstance(build_packages, list) or not build_packages:
            raise ValueError("policy package inventory")
        artifact_name = str(python["artifact_name"])
        return _PinnedPolicy(
            platform=str(source["platform"]),
            python_version=str(python["version"]),
            python_artifact_name=artifact_name,
            python_artifact_bytes=int(python["artifact_bytes"]),
            python_artifact_sha256=str(python["artifact_sha256"]),
            sigstore_bundle_name=artifact_name + ".sigstore",
            sigstore_bundle_bytes=int(python["sigstore_bundle_bytes"]),
            sigstore_bundle_sha256=str(python["sigstore_bundle_sha256"]),
            sigstore_identity=str(python["sigstore_certificate_identity"]),
            sigstore_issuer=str(python["sigstore_certificate_oidc_issuer"]),
            runtime_lock_sha256=runtime_lock_sha256,
            build_lock_sha256=build_lock_sha256,
            runtime_wheel_count=len(runtime_packages),
            build_wheel_count=len(build_packages),
            release_key_id=str(channel["signature"]["key_id"]),
        )
    except JobOpsError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_POLICY_INVALID",
            "The pinned publisher evidence policy is invalid.",
            error=type(error).__name__,
        ) from error


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _reject_non_json_number(value: str) -> None:
    raise ValueError(value)


def _assert_sanitized(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_sanitized(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_sanitized(item, f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    if any(ord(character) < 32 for character in value) or _LOCAL_PATH.search(value) or _SECRET_VALUE.search(value):
        _fail(
            "PUBLISHER_EVIDENCE_SANITIZATION_FAILED",
            "Evidence cannot contain local paths, control characters, or secret material.",
            path=path,
        )


def _parse_canonical(raw: bytes, schema_name: str, schema_dir: Path) -> EvidenceDocument:
    if type(raw) is not bytes or len(raw) < 2 or len(raw) > _MAX_EVIDENCE_BYTES:
        _fail("PUBLISHER_EVIDENCE_INVALID", "Evidence must be bounded canonical JSON bytes.", schema=schema_name)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_non_json_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_INVALID",
            "Evidence is not valid duplicate-free UTF-8 JSON.",
            schema=schema_name,
        ) from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        _fail("PUBLISHER_EVIDENCE_INVALID", "Evidence must use the canonical JSON encoding.", schema=schema_name)
    validate_named(schema_name, value, schema_dir)
    _assert_sanitized(value)
    return EvidenceDocument(schema_name=schema_name, canonical_bytes=raw, sha256=sha256_bytes(raw))


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        _fail("PUBLISHER_EVIDENCE_TIME_INVALID", "Evidence validation requires a timezone-aware clock.")
    return value.astimezone(timezone.utc)


def _parse_evidence_time(value: object) -> datetime:
    """Parse one evidence timestamp without silently assuming a timezone."""

    if not isinstance(value, str) or _EXPLICIT_UTC.search(value) is None:
        _fail(
            "PUBLISHER_EVIDENCE_TIME_INVALID",
            "Evidence timestamps must contain an explicit UTC zone.",
        )
    try:
        parsed = parse_iso(value)
    except (TypeError, ValueError) as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_TIME_INVALID",
            "An evidence timestamp is invalid.",
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(
            "PUBLISHER_EVIDENCE_TIME_INVALID",
            "Evidence timestamps must contain an explicit UTC zone.",
        )
    return parsed.astimezone(timezone.utc)


def _validate_window(value: dict[str, Any], *, now: datetime, maximum_lifetime: timedelta) -> None:
    issued = _parse_evidence_time(value["issued_at_utc"])
    expires = _parse_evidence_time(value["expires_at_utc"])
    if issued >= expires or expires - issued > maximum_lifetime:
        _fail("PUBLISHER_EVIDENCE_TIME_INVALID", "The evidence validity window is invalid.")
    if issued > now + _CLOCK_SKEW:
        _fail("PUBLISHER_EVIDENCE_TIME_INVALID", "The evidence issue time is in the future.")
    if now >= expires:
        _fail("PUBLISHER_EVIDENCE_STALE", "The evidence validity window has expired.")


def _reparse_parent_document(
    value: object,
    *,
    schema_name: str,
    schema_dir: Path,
) -> EvidenceDocument:
    """Re-establish a parent document's canonical bytes, schema and digest.

    ``EvidenceDocument`` is a transport object, not a capability.  Callers can
    construct dataclass instances themselves, so every validation boundary must
    derive authority from the raw canonical bytes rather than trusting its
    public fields.
    """

    if not isinstance(value, EvidenceDocument) or value.schema_name != schema_name:
        _fail(
            "PUBLISHER_EVIDENCE_INPUT_INVALID",
            "Evidence requires the expected validated parent document.",
        )
    try:
        reparsed = _parse_canonical(value.canonical_bytes, schema_name, schema_dir)
    except (JobOpsError, TypeError, ValueError) as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_INPUT_INVALID",
            "The parent evidence bytes failed canonical schema validation.",
        ) from error
    if value.sha256 != reparsed.sha256:
        _fail(
            "PUBLISHER_EVIDENCE_INPUT_INVALID",
            "The parent evidence digest does not match its canonical bytes.",
        )
    return reparsed


def _require_equal(actual: object, expected: object, binding: str) -> None:
    if actual != expected:
        _fail(
            "PUBLISHER_EVIDENCE_BINDING_MISMATCH",
            "Evidence does not match its independently validated input.",
            binding=binding,
        )


def signer_readiness_challenge_sha256(
    *,
    runtime_build_evidence_sha256: str,
    archive_sha256: str,
    source_commit: str,
    provider_policy_sha256: str,
    release_key_id: str,
) -> str:
    """Return the digest a protected signer readiness proof must bind.

    This function creates no signature and receives no credential material.
    """

    material = {
        "format": _SIGNER_CHALLENGE_FORMAT,
        "runtime_build_evidence_sha256": runtime_build_evidence_sha256,
        "archive_sha256": archive_sha256,
        "source_commit": source_commit,
        "provider_policy_sha256": provider_policy_sha256,
        "release_key_id": release_key_id,
    }
    return sha256_bytes(canonical_json(material))


def validate_runtime_build_evidence(
    raw: bytes,
    *,
    now: datetime | None = None,
    schema_dir: Path | None = None,
) -> EvidenceDocument:
    schemas = _schema_directory(schema_dir)
    policy = _load_pinned_policy(schemas)
    document = _parse_canonical(raw, "runtime-build-evidence-v1", schemas)
    value = document.value
    _validate_window(value, now=_utc_now(now), maximum_lifetime=_RUNTIME_BUILD_MAX_LIFETIME)

    archive = value["archive"]
    closure = value["runtime_closure"]
    python_source = value["python_source"]
    inputs = value["build_inputs"]
    deterministic = value["deterministic_build"]
    verifier = value["independent_verification"]
    smoke = value["offline_smoke"]
    version = value["application_version"]

    expected_archive_name = f"JobFlow-v{version}-windows-x64-complete.zip"
    expected_archive_prefix = f"JobFlow-v{version}-windows-x64/"
    _require_equal(archive["name"], expected_archive_name, "archive.name")
    _require_equal(archive["archive_prefix"], expected_archive_prefix, "archive.archive_prefix")
    _require_equal(value["platform"], policy.platform, "platform")
    _require_equal(closure["platform"], policy.platform, "runtime_closure.platform")
    _require_equal(closure["python_version"], policy.python_version, "runtime_closure.python_version")
    _require_equal(closure["source_payload_sha256"], archive["sha256"], "runtime_closure.source_payload_sha256")

    pinned_python = {
        "version": policy.python_version,
        "artifact_name": policy.python_artifact_name,
        "artifact_bytes": policy.python_artifact_bytes,
        "artifact_sha256": policy.python_artifact_sha256,
        "sigstore_bundle_name": policy.sigstore_bundle_name,
        "sigstore_bundle_bytes": policy.sigstore_bundle_bytes,
        "sigstore_bundle_sha256": policy.sigstore_bundle_sha256,
    }
    _require_equal(python_source, pinned_python, "python_source")
    _require_equal(inputs["runtime_wheel_lock_sha256"], policy.runtime_lock_sha256, "build_inputs.runtime_wheel_lock_sha256")
    _require_equal(inputs["build_wheel_lock_sha256"], policy.build_lock_sha256, "build_inputs.build_wheel_lock_sha256")
    validate_application_wheel_provenance(
        inputs.get("application_wheel_provenance"),
        application_wheel_sha256=inputs.get("application_wheel_sha256"),
        source_commit=value.get("source_commit"),
        build_lock_sha256=policy.build_lock_sha256,
    )
    _require_equal(inputs["runtime_wheel_count"], policy.runtime_wheel_count, "build_inputs.runtime_wheel_count")
    _require_equal(inputs["build_wheel_count"], policy.build_wheel_count, "build_inputs.build_wheel_count")
    _require_equal(value["build_inputs_sha256"], sha256_bytes(canonical_json(inputs)), "build_inputs_sha256")

    for field in ("pass_a_archive_sha256", "pass_b_archive_sha256"):
        _require_equal(deterministic[field], archive["sha256"], f"deterministic_build.{field}")
    for field in ("pass_a_tree_sha256", "pass_b_tree_sha256"):
        _require_equal(deterministic[field], closure["tree_sha256"], f"deterministic_build.{field}")
    _require_equal(verifier["archive_sha256"], archive["sha256"], "independent_verification.archive_sha256")
    _require_equal(verifier["closure_manifest_sha256"], closure["manifest_sha256"], "independent_verification.closure_manifest_sha256")
    _require_equal(verifier["tree_sha256"], closure["tree_sha256"], "independent_verification.tree_sha256")
    _require_equal(smoke["archive_sha256"], archive["sha256"], "offline_smoke.archive_sha256")
    _require_equal(smoke["closure_manifest_sha256"], closure["manifest_sha256"], "offline_smoke.closure_manifest_sha256")
    _require_equal(smoke["tree_sha256"], closure["tree_sha256"], "offline_smoke.tree_sha256")
    return document


def validate_publisher_evidence(
    raw: bytes,
    *,
    runtime_build: EvidenceDocument,
    now: datetime | None = None,
    schema_dir: Path | None = None,
) -> EvidenceDocument:
    schemas = _schema_directory(schema_dir)
    validation_now = _utc_now(now)
    runtime_build = _reparse_parent_document(
        runtime_build,
        schema_name="runtime-build-evidence-v1",
        schema_dir=schemas,
    )
    try:
        # Re-run the complete pinned-policy and time validation.  A public
        # dataclass instance with plausible fields is not a trust token.
        runtime_build = validate_runtime_build_evidence(
            runtime_build.canonical_bytes,
            now=validation_now,
            schema_dir=schemas,
        )
    except JobOpsError as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_INPUT_INVALID",
            "Publisher evidence requires currently valid runtime build evidence.",
        ) from error
    policy = _load_pinned_policy(schemas)
    document = _parse_canonical(raw, "publisher-evidence-v1", schemas)
    value = document.value
    build = runtime_build.value
    _validate_window(value, now=validation_now, maximum_lifetime=_PUBLISHER_MAX_LIFETIME)

    release = value["release"]
    closure = value["runtime_closure"]
    sigstore = value["psf_sigstore"]
    deterministic = value["deterministic_rebuild"]
    verifier = value["independent_verification"]
    smoke = value["offline_smoke"]
    signer = value["outer_signing_readiness"]

    _require_equal(value["runtime_build_evidence_sha256"], runtime_build.sha256, "runtime_build_evidence_sha256")
    expected_release = {
        "version": build["application_version"],
        "source_commit": build["source_commit"],
        "platform": build["platform"],
        "archive_name": build["archive"]["name"],
        "archive_bytes": build["archive"]["bytes"],
        "archive_sha256": build["archive"]["sha256"],
        "archive_prefix": build["archive"]["archive_prefix"],
    }
    _require_equal(release, expected_release, "release")
    expected_closure = {
        "manifest_sha256": build["runtime_closure"]["manifest_sha256"],
        "tree_sha256": build["runtime_closure"]["tree_sha256"],
        "source_payload_sha256": build["runtime_closure"]["source_payload_sha256"],
        "file_count": build["runtime_closure"]["file_count"],
        "total_bytes": build["runtime_closure"]["total_bytes"],
        "structural_status": build["structural_status"],
    }
    _require_equal(closure, expected_closure, "runtime_closure")
    _require_equal(value["build_inputs_sha256"], build["build_inputs_sha256"], "build_inputs_sha256")
    _require_equal(sigstore["python_artifact_sha256"], build["python_source"]["artifact_sha256"], "psf_sigstore.python_artifact_sha256")
    _require_equal(sigstore["sigstore_bundle_sha256"], build["python_source"]["sigstore_bundle_sha256"], "psf_sigstore.sigstore_bundle_sha256")
    _require_equal(sigstore["certificate_identity"], policy.sigstore_identity, "psf_sigstore.certificate_identity")
    _require_equal(sigstore["certificate_oidc_issuer"], policy.sigstore_issuer, "psf_sigstore.certificate_oidc_issuer")

    expected_deterministic = {
        "verified": True,
        "pass_a_archive_sha256": build["deterministic_build"]["pass_a_archive_sha256"],
        "pass_b_archive_sha256": build["deterministic_build"]["pass_b_archive_sha256"],
        "pass_a_tree_sha256": build["deterministic_build"]["pass_a_tree_sha256"],
        "pass_b_tree_sha256": build["deterministic_build"]["pass_b_tree_sha256"],
    }
    _require_equal(deterministic, expected_deterministic, "deterministic_rebuild")
    _require_equal(verifier["runtime_build_evidence_sha256"], runtime_build.sha256, "independent_verification.runtime_build_evidence_sha256")
    _require_equal(verifier["verifier_sha256"], build["independent_verification"]["verifier_sha256"], "independent_verification.verifier_sha256")
    _require_equal(verifier["archive_sha256"], build["archive"]["sha256"], "independent_verification.archive_sha256")
    _require_equal(verifier["closure_manifest_sha256"], build["runtime_closure"]["manifest_sha256"], "independent_verification.closure_manifest_sha256")
    _require_equal(verifier["tree_sha256"], build["runtime_closure"]["tree_sha256"], "independent_verification.tree_sha256")
    _require_equal(smoke["runtime_build_evidence_sha256"], runtime_build.sha256, "offline_smoke.runtime_build_evidence_sha256")
    _require_equal(signer["release_key_id"], policy.release_key_id, "outer_signing_readiness.release_key_id")
    expected_challenge = signer_readiness_challenge_sha256(
        runtime_build_evidence_sha256=runtime_build.sha256,
        archive_sha256=build["archive"]["sha256"],
        source_commit=build["source_commit"],
        provider_policy_sha256=signer["provider_policy_sha256"],
        release_key_id=policy.release_key_id,
    )
    _require_equal(signer["challenge_sha256"], expected_challenge, "outer_signing_readiness.challenge_sha256")
    publisher_issued = _parse_evidence_time(value["issued_at_utc"])
    runtime_issued = _parse_evidence_time(build["issued_at_utc"])
    runtime_expires = _parse_evidence_time(build["expires_at_utc"])
    if not runtime_issued <= publisher_issued < runtime_expires:
        _fail(
            "PUBLISHER_EVIDENCE_TIME_INVALID",
            "Publisher evidence must be issued inside its runtime build evidence validity window.",
        )
    return document


def validate_clean_windows_acceptance(
    raw: bytes,
    *,
    publisher_evidence: EvidenceDocument,
    now: datetime | None = None,
    schema_dir: Path | None = None,
) -> EvidenceDocument:
    schemas = _schema_directory(schema_dir)
    validation_now = _utc_now(now)
    publisher_evidence = _reparse_parent_document(
        publisher_evidence,
        schema_name="publisher-evidence-v1",
        schema_dir=schemas,
    )
    policy = _load_pinned_policy(schemas)
    document = _parse_canonical(raw, "clean-windows-acceptance-v1", schemas)
    value = document.value
    publisher = publisher_evidence.value
    try:
        _validate_window(
            publisher,
            now=validation_now,
            maximum_lifetime=_PUBLISHER_MAX_LIFETIME,
        )
    except JobOpsError as error:
        raise JobOpsError(
            "PUBLISHER_EVIDENCE_INPUT_INVALID",
            "Clean Windows evidence requires currently valid publisher evidence.",
        ) from error
    _validate_window(value, now=validation_now, maximum_lifetime=_CLEAN_WINDOWS_MAX_LIFETIME)

    _require_equal(value["publisher_evidence_sha256"], publisher_evidence.sha256, "publisher_evidence_sha256")
    expected_release = {
        "version": publisher["release"]["version"],
        "source_commit": publisher["release"]["source_commit"],
        "platform": publisher["release"]["platform"],
    }
    _require_equal(value["release"], expected_release, "release")
    signed_bundle = value["signed_bundle"]
    _require_equal(signed_bundle["archive_name"], publisher["release"]["archive_name"], "signed_bundle.archive_name")
    _require_equal(signed_bundle["archive_bytes"], publisher["release"]["archive_bytes"], "signed_bundle.archive_bytes")
    _require_equal(signed_bundle["archive_sha256"], publisher["release"]["archive_sha256"], "signed_bundle.archive_sha256")
    _require_equal(signed_bundle["release_key_id"], policy.release_key_id, "signed_bundle.release_key_id")
    expected_closure = {
        "manifest_sha256": publisher["runtime_closure"]["manifest_sha256"],
        "tree_sha256": publisher["runtime_closure"]["tree_sha256"],
        "structural_status": publisher["runtime_closure"]["structural_status"],
    }
    _require_equal(value["runtime_closure"], expected_closure, "runtime_closure")
    clean_issued = _parse_evidence_time(value["issued_at_utc"])
    publisher_issued = _parse_evidence_time(publisher["issued_at_utc"])
    publisher_expires = _parse_evidence_time(publisher["expires_at_utc"])
    if not publisher_issued <= clean_issued < publisher_expires:
        _fail(
            "PUBLISHER_EVIDENCE_TIME_INVALID",
            "Clean Windows evidence must be issued inside its publisher evidence validity window.",
        )
    return document


__all__ = [
    "EvidenceDocument",
    "signer_readiness_challenge_sha256",
    "validate_clean_windows_acceptance",
    "validate_publisher_evidence",
    "validate_runtime_build_evidence",
]
