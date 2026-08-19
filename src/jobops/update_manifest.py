from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .release_candidate import verify_candidate_archive
from .util import canonical_json, load_json, project_root, sha256_file


MAX_UPDATE_MANIFEST_BYTES = 64 * 1024
MAX_UPDATE_SIGNATURE_BYTES = 16 * 1024
MAX_UPDATE_ARCHIVE_BYTES = 1024 * 1024 * 1024
UPDATE_SIGNATURE_ALGORITHM = "RSA-PKCS1-v1_5-SHA256"
TRUSTED_RELEASE_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ASSET = re.compile(r"^JobFlow-v([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{12})-source\.zip$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")


def _b64url_decode(value: str, *, label: str, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise JobOpsError("UPDATE_SIGNATURE_FORMAT_INVALID", f"The {label} encoding is invalid.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as error:
        raise JobOpsError("UPDATE_SIGNATURE_FORMAT_INVALID", f"The {label} encoding is invalid.") from error
    if not minimum <= len(decoded) <= maximum:
        raise JobOpsError("UPDATE_SIGNATURE_FORMAT_INVALID", f"The {label} length is invalid.")
    return decoded


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise JobOpsError("UPDATE_VERSION_INVALID", "The update version is invalid.")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _read_bounded_json(path: Path, *, maximum: int, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise JobOpsError(code, "A required update metadata file is unavailable.") from error
    if size < 2 or size > maximum:
        raise JobOpsError(code, "A required update metadata file has an invalid size.")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JobOpsError(code, "A required update metadata file is invalid.") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise JobOpsError(code, "Update metadata must use the canonical JSON encoding.")
    return value, raw


def _public_key_material(signature: dict[str, Any]) -> dict[str, str]:
    return {
        "algorithm": str(signature.get("algorithm", "")),
        "n": str(signature.get("modulus_b64url", "")),
        "e": str(signature.get("exponent_b64url", "")),
    }


def release_key_id(signature: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(_public_key_material(signature))).hexdigest()


def validate_update_channel(
    value: dict[str, Any], *, trusted_key_id: str = TRUSTED_RELEASE_KEY_ID
) -> dict[str, Any]:
    expected = {
        "schema_version", "product", "channel", "repository", "latest_release_api_url",
        "manifest_asset_name", "signature_asset_name", "allowed_download_hosts", "signature",
    }
    if set(value) != expected or value.get("schema_version") != 1:
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update channel is invalid.")
    if value.get("product") != "JobFlow" or value.get("channel") != "stable":
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update channel identity is invalid.")
    repository = value.get("repository")
    if repository != "ValerianXXX/JobFlow":
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update repository is invalid.")
    if value.get("latest_release_api_url") != "https://api.github.com/repos/ValerianXXX/JobFlow/releases/latest":
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed release endpoint is invalid.")
    if value.get("manifest_asset_name") != "JobFlow-update-manifest.json" or value.get("signature_asset_name") != "JobFlow-update-manifest.sig.json":
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update asset names are invalid.")
    hosts = value.get("allowed_download_hosts")
    if (
        not isinstance(hosts, list)
        or not 1 <= len(hosts) <= 8
        or hosts != sorted(set(hosts))
        or any(not isinstance(host, str) or _HOST.fullmatch(host) is None for host in hosts)
        or "github.com" not in hosts
    ):
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update host allowlist is invalid.")
    signature = value.get("signature")
    if not isinstance(signature, dict) or set(signature) != {
        "algorithm", "key_id", "modulus_b64url", "exponent_b64url"
    }:
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update public key is invalid.")
    if signature.get("algorithm") != UPDATE_SIGNATURE_ALGORITHM:
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update signature algorithm is invalid.")
    if not isinstance(signature.get("key_id"), str) or _KEY_ID.fullmatch(signature["key_id"]) is None:
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update key identifier is invalid.")
    if not hmac.compare_digest(signature["key_id"], trusted_key_id):
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update key is not the pinned JobFlow release key.")
    modulus = _b64url_decode(str(signature.get("modulus_b64url", "")), label="modulus", minimum=256, maximum=512)
    exponent = _b64url_decode(str(signature.get("exponent_b64url", "")), label="exponent", minimum=1, maximum=8)
    if len(modulus) * 8 < 2048 or int.from_bytes(exponent, "big") < 3:
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update public key is too weak.")
    if not hmac.compare_digest(signature["key_id"], release_key_id(signature)):
        raise JobOpsError("UPDATE_CHANNEL_INVALID", "The installed update key identifier does not match its public key.")
    return value


def validate_update_manifest(value: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "product", "channel", "repository", "version", "tag_name", "commit",
        "asset_name", "asset_sha256", "asset_bytes", "archive_prefix", "installer_relative_path",
    }
    if set(value) != expected or value.get("schema_version") != 1:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed update manifest is invalid.")
    if any(value.get(key) != channel[key] for key in ("product", "channel", "repository")):
        raise JobOpsError("UPDATE_MANIFEST_IDENTITY_MISMATCH", "The signed update identity does not match this installation.")
    version = str(value.get("version", ""))
    _version_tuple(version)
    if value.get("tag_name") != f"v{version}":
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed release tag does not match its version.")
    commit = str(value.get("commit", ""))
    if _COMMIT.fullmatch(commit) is None:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed release commit is invalid.")
    asset_name = str(value.get("asset_name", ""))
    asset_match = _ASSET.fullmatch(asset_name)
    if asset_match is None or asset_match.group(1) != version or asset_match.group(2) != commit[:12]:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed release asset name is invalid.")
    if not isinstance(value.get("asset_sha256"), str) or _SHA256.fullmatch(value["asset_sha256"]) is None:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed release digest is invalid.")
    asset_bytes = value.get("asset_bytes")
    if not isinstance(asset_bytes, int) or not 1 <= asset_bytes <= MAX_UPDATE_ARCHIVE_BYTES:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed release size is invalid.")
    if value.get("archive_prefix") != f"JobFlow-v{version}/":
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed archive prefix is invalid.")
    if value.get("installer_relative_path") != "scripts/install-jobflow.ps1":
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The signed installer entry is invalid.")
    return value


def _verify_rsa_pkcs1_sha256(message: bytes, signature_bytes: bytes, public_key: dict[str, Any]) -> None:
    modulus_bytes = _b64url_decode(str(public_key.get("modulus_b64url", "")), label="modulus", minimum=256, maximum=512)
    exponent_bytes = _b64url_decode(str(public_key.get("exponent_b64url", "")), label="exponent", minimum=1, maximum=8)
    if len(signature_bytes) != len(modulus_bytes):
        raise JobOpsError("UPDATE_SIGNATURE_INVALID", "The update signature has the wrong length.")
    modulus = int.from_bytes(modulus_bytes, "big")
    exponent = int.from_bytes(exponent_bytes, "big")
    signature_number = int.from_bytes(signature_bytes, "big")
    if signature_number <= 0 or signature_number >= modulus:
        raise JobOpsError("UPDATE_SIGNATURE_INVALID", "The update signature is outside the public-key range.")
    encoded = pow(signature_number, exponent, modulus).to_bytes(len(modulus_bytes), "big")
    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(message).digest()
    padding_length = len(encoded) - len(digest_info) - 3
    if padding_length < 8:
        raise JobOpsError("UPDATE_SIGNATURE_INVALID", "The update signature padding is invalid.")
    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise JobOpsError("UPDATE_SIGNATURE_INVALID", "The update manifest signature is invalid.")


def inspect_signed_update(
    manifest_path: Path,
    signature_path: Path,
    *,
    current_version: str,
    channel_path: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
) -> dict[str, Any]:
    channel = validate_update_channel(
        load_json(channel_path or (project_root() / "config" / "update-channel.json")),
        trusted_key_id=trusted_key_id,
    )
    manifest, raw_manifest = _read_bounded_json(
        manifest_path, maximum=MAX_UPDATE_MANIFEST_BYTES, code="UPDATE_MANIFEST_INVALID"
    )
    signed = validate_update_manifest(manifest, channel)
    envelope, _ = _read_bounded_json(
        signature_path, maximum=MAX_UPDATE_SIGNATURE_BYTES, code="UPDATE_SIGNATURE_FORMAT_INVALID"
    )
    if set(envelope) != {"schema_version", "algorithm", "key_id", "signature_b64url"} or envelope.get("schema_version") != 1:
        raise JobOpsError("UPDATE_SIGNATURE_FORMAT_INVALID", "The update signature envelope is invalid.")
    public_key = channel["signature"]
    if envelope.get("algorithm") != public_key["algorithm"] or envelope.get("key_id") != public_key["key_id"]:
        raise JobOpsError("UPDATE_SIGNATURE_KEY_MISMATCH", "The update was not signed by the installed release key.")
    signature_bytes = _b64url_decode(
        str(envelope.get("signature_b64url", "")), label="signature", minimum=256, maximum=512
    )
    _verify_rsa_pkcs1_sha256(raw_manifest, signature_bytes, public_key)
    current = _version_tuple(current_version)
    available = _version_tuple(str(signed["version"]))
    status = "UPDATE_AVAILABLE" if available > current else "UPDATE_CURRENT"
    return {
        "schema_version": 1,
        "status": status,
        "current_version": current_version,
        "available_version": signed["version"],
        "tag_name": signed["tag_name"],
        "asset_name": signed["asset_name"],
        "asset_sha256": signed["asset_sha256"],
        "asset_bytes": signed["asset_bytes"],
        "archive_prefix": signed["archive_prefix"],
        "installer_relative_path": signed["installer_relative_path"],
        "commit": signed["commit"],
        "signature_verified": True,
        "key_id": public_key["key_id"],
    }


def verify_signed_update_bundle(
    manifest_path: Path,
    signature_path: Path,
    archive_path: Path,
    *,
    current_version: str,
    channel_path: Path | None = None,
    project: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
) -> dict[str, Any]:
    result = inspect_signed_update(
        manifest_path,
        signature_path,
        current_version=current_version,
        channel_path=channel_path,
        trusted_key_id=trusted_key_id,
    )
    if result["status"] != "UPDATE_AVAILABLE":
        return result
    try:
        archive_size = archive_path.stat().st_size
    except OSError as error:
        raise JobOpsError("UPDATE_ARCHIVE_MISSING", "The signed update archive is unavailable.") from error
    if archive_path.name != result["asset_name"] or archive_size != result["asset_bytes"]:
        raise JobOpsError("UPDATE_ARCHIVE_IDENTITY_MISMATCH", "The downloaded update archive does not match its signed identity.")
    if not hmac.compare_digest(sha256_file(archive_path), str(result["asset_sha256"])):
        raise JobOpsError("UPDATE_ARCHIVE_DIGEST_MISMATCH", "The downloaded update archive failed its signed SHA-256 check.")
    verification = verify_candidate_archive(
        project or project_root(), archive_path, prefix=str(result["archive_prefix"])
    )
    if verification["status"] != "PASS":
        raise JobOpsError(
            "UPDATE_ARCHIVE_BOUNDARY_FAILED",
            "The downloaded update archive failed the public source boundary.",
            finding_count=verification["finding_count"],
        )
    return {
        **result,
        "status": "UPDATE_BUNDLE_VERIFIED",
        "archive_verified": True,
        "archive_file_count": verification["file_count"],
        "finding_count": 0,
    }


def build_update_manifest(*, archive_path: Path, version: str, commit: str) -> dict[str, Any]:
    _version_tuple(version)
    if _COMMIT.fullmatch(commit) is None:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The release commit is invalid.")
    expected_name = f"JobFlow-v{version}-{commit[:12]}-source.zip"
    if archive_path.name != expected_name:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The release archive name does not match its version and commit.")
    size = archive_path.stat().st_size
    if not 1 <= size <= MAX_UPDATE_ARCHIVE_BYTES:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The release archive size is invalid.")
    return {
        "schema_version": 1,
        "product": "JobFlow",
        "channel": "stable",
        "repository": "ValerianXXX/JobFlow",
        "version": version,
        "tag_name": f"v{version}",
        "commit": commit,
        "asset_name": expected_name,
        "asset_sha256": sha256_file(archive_path),
        "asset_bytes": size,
        "archive_prefix": f"JobFlow-v{version}/",
        "installer_relative_path": "scripts/install-jobflow.ps1",
    }


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify a signed JobFlow update manifest.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--manifest", type=Path, required=True)
    inspect_parser.add_argument("--signature", type=Path, required=True)
    inspect_parser.add_argument("--current-version", required=True)
    inspect_parser.add_argument("--channel", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--signature", type=Path, required=True)
    verify_parser.add_argument("--archive", type=Path, required=True)
    verify_parser.add_argument("--current-version", required=True)
    verify_parser.add_argument("--channel", type=Path)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--archive", type=Path, required=True)
    build_parser.add_argument("--version", required=True)
    build_parser.add_argument("--commit", required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "inspect":
            result = inspect_signed_update(
                arguments.manifest,
                arguments.signature,
                current_version=arguments.current_version,
                channel_path=arguments.channel,
            )
        elif arguments.command == "verify":
            result = verify_signed_update_bundle(
                arguments.manifest,
                arguments.signature,
                arguments.archive,
                current_version=arguments.current_version,
                channel_path=arguments.channel,
            )
        else:
            result = build_update_manifest(
                archive_path=arguments.archive,
                version=arguments.version,
                commit=arguments.commit,
            )
            arguments.output.write_bytes(canonical_json(result))
            result = {"schema_version": 1, "status": "UPDATE_MANIFEST_BUILT", **result}
    except (JobOpsError, OSError) as error:
        if isinstance(error, JobOpsError):
            _emit(error.as_dict())
        else:
            _emit(JobOpsError("UPDATE_IO_FAILED", "The local update operation could not finish.").as_dict())
        return 2
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
