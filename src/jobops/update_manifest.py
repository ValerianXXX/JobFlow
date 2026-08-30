from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import JobOpsError
from .publisher_attestation import (
    validate_publisher_evidence,
    validate_runtime_build_evidence,
)
from .release_candidate import verify_candidate_archive
from .release_toolchain import ReleaseToolchainError, load_release_toolchain_policy
from .runtime_schema import validate_named
from .util import canonical_json, parse_iso, project_root, sha256_bytes, sha256_file


MAX_UPDATE_MANIFEST_BYTES = 64 * 1024
MAX_UPDATE_SIGNATURE_BYTES = 16 * 1024
MAX_UPDATE_ARCHIVE_BYTES = 1536 * 1024 * 1024
UPDATE_SIGNATURE_ALGORITHM = "RSA-PKCS1-v1_5-SHA256"
TRUSTED_RELEASE_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ASSET = re.compile(r"^JobFlow-v([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{12})-source\.zip$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEGACY_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", "conin$", "conout$", "clock$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
MAX_UPDATE_EXTRACTED_BYTES = 1024 * 1024 * 1024


def _payload_error(message: str) -> JobOpsError:
    return JobOpsError("UPDATE_ARCHIVE_PAYLOAD_INVALID", message)


def _normalize_payload_relative(value: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or "\\" in value
        or ":" in value
        or re.search(r'[<>"|?*]', value)
    ):
        raise _payload_error("The update archive contains an unsafe Windows path.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise _payload_error("The update archive path must use the cross-verifier ASCII subset.") from error
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise _payload_error("The update archive path must use printable cross-verifier ASCII.")
    if value.startswith("/") or value.startswith("//") or re.match(r"^[A-Za-z]:", value):
        raise _payload_error("The update archive contains an absolute path.")
    if value.endswith("/"):
        value = value[:-1]
    if not value:
        if allow_empty:
            return ""
        raise _payload_error("The update archive contains an empty payload path.")
    if unicodedata.normalize("NFC", value) != value:
        raise _payload_error("The update archive contains a non-canonical Unicode path.")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise _payload_error("The update archive contains a path traversal or empty component.")
    for part in parts:
        if part[-1] in {" ", "."}:
            raise _payload_error("The update archive contains a Windows-normalized path alias.")
        base = part.split(".", 1)[0].casefold()
        if base in _WINDOWS_RESERVED_NAMES:
            raise _payload_error("The update archive contains a reserved Windows device path.")
    return PurePosixPath(*parts).as_posix()


def _payload_path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _archive_payload_records_with_capture(
    archive_path: Path,
    archive_prefix: str,
    *,
    capture_relative: str | None = None,
) -> tuple[list[str], list[dict[str, Any]], bytes | None]:
    if not isinstance(archive_prefix, str) or not archive_prefix.endswith("/"):
        raise _payload_error("The signed archive prefix is invalid.")
    prefix = _normalize_payload_relative(archive_prefix, allow_empty=False) + "/"
    explicit_keys: set[str] = set()
    file_keys: set[str] = set()
    directory_keys: set[str] = set()
    directories: set[str] = set()
    records: list[dict[str, Any]] = []
    captured: bytes | None = None
    total_bytes = 0
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise _payload_error("The update archive is not a readable ZIP file.") from error
    try:
        for info in archive.infolist():
            raw_name = info.filename
            if not isinstance(raw_name, str) or not raw_name.startswith(prefix):
                raise _payload_error("The update archive contains an entry outside its signed prefix.")
            relative_raw = raw_name[len(prefix):]
            is_directory = info.is_dir() or raw_name.endswith("/")
            relative = _normalize_payload_relative(relative_raw, allow_empty=is_directory)
            if not relative:
                if raw_name != prefix or not is_directory:
                    raise _payload_error("The update archive prefix entry is invalid.")
                key = ""
            else:
                key = _payload_path_key(relative)
            if key in explicit_keys:
                raise _payload_error("The update archive contains a duplicate or case-aliased path.")
            explicit_keys.add(key)
            if info.flag_bits & 0x1:
                raise _payload_error("Encrypted update archive entries are forbidden.")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            dos_attributes = info.external_attr & 0xFFFF
            if dos_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise _payload_error("Reparse-point archive entries are forbidden.")
            if file_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)):
                raise _payload_error("Link and special-file archive entries are forbidden.")
            if relative:
                parts = relative.split("/")
                for index in range(1, len(parts)):
                    parent = "/".join(parts[:index])
                    parent_key = _payload_path_key(parent)
                    if parent_key in file_keys:
                        raise _payload_error("The update archive contains a file-directory collision.")
                    directory_keys.add(parent_key)
                    directories.add(parent)
            if is_directory:
                if relative:
                    if key in file_keys:
                        raise _payload_error("The update archive contains a file-directory collision.")
                    directory_keys.add(key)
                    directories.add(relative)
                if info.file_size != 0:
                    raise _payload_error("The update archive contains a non-empty directory record.")
                continue
            if key in directory_keys:
                raise _payload_error("The update archive contains a file-directory collision.")
            if info.file_size < 0 or info.file_size > MAX_UPDATE_EXTRACTED_BYTES:
                raise _payload_error("An update archive entry has an invalid extracted size.")
            total_bytes += info.file_size
            if total_bytes > MAX_UPDATE_EXTRACTED_BYTES:
                raise _payload_error("The update archive expands beyond the allowed payload size.")
            digest = hashlib.sha256()
            extracted = 0
            captured_chunks: list[bytes] | None = [] if relative == capture_relative else None
            try:
                with archive.open(info, "r") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        extracted += len(chunk)
                        if extracted > info.file_size or extracted > MAX_UPDATE_EXTRACTED_BYTES:
                            raise _payload_error("An update archive entry expanded beyond its declared size.")
                        digest.update(chunk)
                        if captured_chunks is not None:
                            captured_chunks.append(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise _payload_error("An update archive entry could not be read safely.") from error
            if extracted != info.file_size:
                raise _payload_error("An update archive entry size does not match its ZIP record.")
            if captured_chunks is not None:
                if captured is not None:
                    raise _payload_error("The update archive contains a duplicate captured payload.")
                captured = b"".join(captured_chunks)
            file_keys.add(key)
            records.append({"relative": relative, "length": extracted, "sha256": digest.hexdigest()})
    finally:
        archive.close()
    if not records:
        raise _payload_error("The update archive contains no payload files.")
    return sorted(directories, key=lambda item: (_payload_path_key(item), item)), sorted(
        records, key=lambda item: (_payload_path_key(str(item["relative"])), str(item["relative"]))
    ), captured


def _archive_payload_records(
    archive_path: Path, archive_prefix: str
) -> tuple[list[str], list[dict[str, Any]]]:
    directories, records, _ = _archive_payload_records_with_capture(
        archive_path, archive_prefix
    )
    return directories, records


def inventory_archive_payload(archive_path: Path, archive_prefix: str) -> dict[str, Any]:
    directories, records = _archive_payload_records(archive_path, archive_prefix)
    inventory_sha256 = hashlib.sha256(
        canonical_json({"directories": directories, "records": records})
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "UPDATE_ARCHIVE_PAYLOAD_INVENTORIED",
        "archive_sha256": sha256_file(archive_path),
        "archive_prefix": archive_prefix,
        "directory_count": len(directories),
        "file_count": len(records),
        "directories": directories,
        "records": records,
        "inventory_sha256": inventory_sha256,
    }


def _extracted_payload_records(extracted_root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        root = Path(os.path.abspath(extracted_root))
        root_stat = root.lstat()
    except OSError as error:
        raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update root is unavailable.") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink() or getattr(root_stat, "st_file_attributes", 0) & reparse_flag:
        raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update root is linked or invalid.")
    directories: list[str] = []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending = [root]
    total_bytes = 0
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update tree is unreadable.") from error
        for entry in entries:
            path = Path(entry.path)
            try:
                item_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update entry is unreadable.") from error
            try:
                relative = _normalize_payload_relative(path.relative_to(root).as_posix())
            except (ValueError, JobOpsError) as error:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update path is unsafe.") from error
            key = _payload_path_key(relative)
            if key in seen:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update contains an aliased path.")
            seen.add(key)
            if entry.is_symlink() or getattr(item_stat, "st_file_attributes", 0) & reparse_flag:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update contains a link or reparse point.")
            if entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                pending.append(path)
                continue
            # Windows network and sync providers can report st_nlink as zero
            # through DirEntry.stat even for ordinary files.  Reparse/symlink
            # checks are reliable here; the installer separately enforces a
            # single-link Win32 file identity while holding each source open.
            if not entry.is_file(follow_symlinks=False):
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update contains a linked or special file.")
            if item_stat.st_size < 0 or item_stat.st_size > MAX_UPDATE_EXTRACTED_BYTES:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update file has an invalid size.")
            total_bytes += item_stat.st_size
            if total_bytes > MAX_UPDATE_EXTRACTED_BYTES:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "The extracted update exceeds the allowed payload size.")
            digest = hashlib.sha256()
            extracted = 0
            try:
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        extracted += len(chunk)
                        if extracted > item_stat.st_size:
                            raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update file changed during attestation.")
                        digest.update(chunk)
            except OSError as error:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update file could not be attested.") from error
            if extracted != item_stat.st_size:
                raise JobOpsError("UPDATE_EXTRACTED_PAYLOAD_MISMATCH", "An extracted update file changed during attestation.")
            records.append({"relative": relative, "length": extracted, "sha256": digest.hexdigest()})
    return sorted(directories, key=lambda item: (_payload_path_key(item), item)), sorted(
        records, key=lambda item: (_payload_path_key(str(item["relative"])), str(item["relative"]))
    )


def attest_extracted_payload(archive_path: Path, archive_prefix: str, extracted_root: Path) -> dict[str, Any]:
    expected_directories, expected_records = _archive_payload_records(archive_path, archive_prefix)
    actual_directories, actual_records = _extracted_payload_records(extracted_root)
    if actual_directories != expected_directories or actual_records != expected_records:
        raise JobOpsError(
            "UPDATE_EXTRACTED_PAYLOAD_MISMATCH",
            "The extracted update payload does not exactly match the verified archive.",
        )
    inventory_sha256 = hashlib.sha256(
        canonical_json({"directories": expected_directories, "records": expected_records})
    ).hexdigest()
    extracted_root_sha256 = hashlib.sha256(
        canonical_json({"directories": actual_directories, "records": actual_records})
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "UPDATE_EXTRACTED_PAYLOAD_ATTESTED",
        "archive_sha256": sha256_file(archive_path),
        "archive_prefix": archive_prefix,
        "directory_count": len(expected_directories),
        "file_count": len(expected_records),
        "directories": expected_directories,
        "records": expected_records,
        "inventory_sha256": inventory_sha256,
        "extracted_root_sha256": extracted_root_sha256,
    }


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


class _DuplicateJsonKey(ValueError):
    pass


def _canonical_object_from_bytes(raw: bytes, *, code: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise _DuplicateJsonKey(key)
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise JobOpsError(code, "A required update metadata file is invalid.") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise JobOpsError(code, "Update metadata must use the canonical JSON encoding.")
    return value


def _json_object_from_bytes(raw: bytes, *, code: str) -> dict[str, Any]:
    """Parse a bounded trusted configuration document without duplicate keys.

    Generated release artifacts must remain canonical byte-for-byte.  The
    checked-in update-channel configuration is human-formatted, so its trusted
    default may contain insignificant whitespace while still receiving the
    same duplicate-key and schema validation as an artifact.
    """

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise _DuplicateJsonKey(key)
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise JobOpsError(code, "A required update metadata file is invalid.") from error
    if not isinstance(value, dict):
        raise JobOpsError(code, "A required update metadata file is invalid.")
    return value


def _read_bounded_bytes(path: Path, *, maximum: int, code: str) -> bytes:
    try:
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise JobOpsError(code, "A required update metadata file has an unsafe identity.")
            if before.st_size < 2 or before.st_size > maximum:
                raise JobOpsError(code, "A required update metadata file has an invalid size.")
            raw = source.read(maximum + 1)
            after = os.fstat(source.fileno())
    except OSError as error:
        raise JobOpsError(code, "A required update metadata file is unavailable.") from error
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
    if identity_before != identity_after or len(raw) != before.st_size or len(raw) > maximum:
        raise JobOpsError(code, "A required update metadata file changed while it was being read.")
    return raw


def _read_bounded_json(path: Path, *, maximum: int, code: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded_bytes(path, maximum=maximum, code=code)
    return _canonical_object_from_bytes(raw, code=code), raw


def _read_update_channel(channel_path: Path | None) -> dict[str, Any]:
    path = channel_path or (project_root() / "config" / "update-channel.json")
    raw = _read_bounded_bytes(path, maximum=MAX_UPDATE_MANIFEST_BYTES, code="UPDATE_CHANNEL_INVALID")
    if channel_path is None:
        return _json_object_from_bytes(raw, code="UPDATE_CHANNEL_INVALID")
    return _canonical_object_from_bytes(raw, code="UPDATE_CHANNEL_INVALID")


def _read_bounded_file_identity(
    path: Path, *, maximum: int, code: str
) -> tuple[int, str]:
    try:
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise JobOpsError(code, "The signed update archive has an unsafe identity.")
            if before.st_size < 1 or before.st_size > maximum:
                raise JobOpsError(code, "The signed update archive has an invalid size.")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = source.read(min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise JobOpsError(code, "The signed update archive exceeds its size limit.")
                digest.update(chunk)
            after = os.fstat(source.fileno())
    except OSError as error:
        raise JobOpsError(code, "The signed update archive is unavailable.") from error
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
    if identity_before != identity_after or total != before.st_size:
        raise JobOpsError(code, "The signed update archive changed while it was being read.")
    return total, "sha256:" + digest.hexdigest()


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


def validate_legacy_update_manifest_v1(value: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
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


def inspect_legacy_signed_update_v1(
    manifest_path: Path,
    signature_path: Path,
    *,
    current_version: str,
    channel_path: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
) -> dict[str, Any]:
    channel_value = _read_update_channel(channel_path)
    channel = validate_update_channel(channel_value, trusted_key_id=trusted_key_id)
    manifest, raw_manifest = _read_bounded_json(
        manifest_path, maximum=MAX_UPDATE_MANIFEST_BYTES, code="UPDATE_MANIFEST_INVALID"
    )
    signed = validate_legacy_update_manifest_v1(manifest, channel)
    envelope, raw_signature = _read_bounded_json(
        signature_path, maximum=MAX_UPDATE_SIGNATURE_BYTES, code="UPDATE_SIGNATURE_FORMAT_INVALID"
    )
    if envelope.get("scope") == "development-fixture":
        raise JobOpsError(
            "UPDATE_DEVELOPMENT_SIGNATURE_FORBIDDEN",
            "A development-fixture signature cannot authorize a production update.",
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


def verify_legacy_signed_update_bundle_v1(
    manifest_path: Path,
    signature_path: Path,
    archive_path: Path,
    *,
    current_version: str,
    channel_path: Path | None = None,
    project: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
) -> dict[str, Any]:
    result = inspect_legacy_signed_update_v1(
        manifest_path,
        signature_path,
        current_version=current_version,
        channel_path=channel_path,
        trusted_key_id=trusted_key_id,
    )
    if result["status"] != "UPDATE_AVAILABLE":
        return result
    archive_size, archive_sha256 = _read_bounded_file_identity(
        archive_path,
        maximum=MAX_UPDATE_ARCHIVE_BYTES,
        code="UPDATE_ARCHIVE_MISSING",
    )
    if archive_path.name != result["asset_name"] or archive_size != result["asset_bytes"]:
        raise JobOpsError("UPDATE_ARCHIVE_IDENTITY_MISMATCH", "The downloaded update archive does not match its signed identity.")
    if not hmac.compare_digest(archive_sha256, str(result["asset_sha256"])):
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


def _v2_schema_dir(schema_dir: Path | None = None) -> Path:
    return schema_dir or (project_root() / "schemas")


def _release_python_identity(schema_dir: Path | None = None) -> dict[str, str | int]:
    """Load the Python identity bound to the same project as the schemas.

    JSON Schema constrains only the public syntax.  Production truth is the
    locked ``windows-runtime-source.json`` plus the release-toolchain runtime
    policy.  The protected PowerShell handoff stages and holds both files, so
    this read cannot drift from the clean candidate while Python runs.
    """

    schemas = _v2_schema_dir(schema_dir)
    root = schemas.parent
    try:
        policy = load_release_toolchain_policy(root)
        runtime = policy["python_execution_runtime"]
        source_path = root / str(runtime["source_policy"])
        source = _json_object_from_bytes(
            _read_bounded_bytes(
                source_path,
                maximum=256 * 1024,
                code="UPDATE_RUNTIME_POLICY_INVALID",
            ),
            code="UPDATE_RUNTIME_POLICY_INVALID",
        )
        python = source["python"]
        return {
            "version": str(python["version"]),
            "tag": str(runtime["python_tag"]),
            "artifact_name": str(python["artifact_name"]),
            "artifact_bytes": int(python["artifact_bytes"]),
            "artifact_sha256": str(python["artifact_sha256"]),
        }
    except (ReleaseToolchainError, JobOpsError, KeyError, TypeError, ValueError, OSError) as error:
        if isinstance(error, JobOpsError) and error.code == "UPDATE_RUNTIME_POLICY_INVALID":
            raise
        raise JobOpsError(
            "UPDATE_RUNTIME_POLICY_INVALID",
            "The signed update runtime policy is unavailable or invalid.",
        ) from error


def validate_update_manifest(
    value: dict[str, Any],
    channel: dict[str, Any],
    *,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate one production v2 complete-runtime manifest.

    This is deliberately v2-only.  The old source-package contract remains
    available only through ``validate_legacy_update_manifest_v1`` so a caller
    cannot silently downgrade to the migration format.
    """

    validate_named("update-manifest-v2", value, _v2_schema_dir(schema_dir))
    runtime_identity = _release_python_identity(schema_dir)
    runtime_summary = value["runtime_closure"]
    if (
        runtime_summary.get("python_version") != runtime_identity["version"]
        or runtime_summary.get("build_inputs", {}).get("python_artifact_sha256")
        != runtime_identity["artifact_sha256"]
    ):
        raise JobOpsError(
            "UPDATE_RUNTIME_POLICY_MISMATCH",
            "The signed update runtime summary does not match the pinned runtime policy.",
        )
    if value.get("product") != channel.get("product") or value.get("channel") != channel.get("channel"):
        raise JobOpsError(
            "UPDATE_MANIFEST_IDENTITY_MISMATCH",
            "The signed update identity does not match this installation.",
        )
    attestation = value["publisher_attestation"]
    channel_key_id = channel["signature"]["key_id"]
    if not hmac.compare_digest(str(attestation["release_key_id"]), str(channel_key_id)):
        raise JobOpsError(
            "UPDATE_MANIFEST_ATTESTATION_KEY_MISMATCH",
            "The publisher attestation is not bound to the installed release key.",
        )
    return value


def _validate_signature_envelope(
    envelope: dict[str, Any], public_key: dict[str, Any]
) -> bytes:
    if envelope.get("scope") == "development-fixture":
        raise JobOpsError(
            "UPDATE_DEVELOPMENT_SIGNATURE_FORBIDDEN",
            "A development-fixture signature cannot authorize a production update.",
        )
    if (
        set(envelope) != {"schema_version", "algorithm", "key_id", "signature_b64url"}
        or type(envelope.get("schema_version")) is not int
        or envelope.get("schema_version") != 1
    ):
        raise JobOpsError("UPDATE_SIGNATURE_FORMAT_INVALID", "The update signature envelope is invalid.")
    if envelope.get("algorithm") != public_key["algorithm"] or envelope.get("key_id") != public_key["key_id"]:
        raise JobOpsError(
            "UPDATE_SIGNATURE_KEY_MISMATCH",
            "The update was not signed by the installed release key.",
        )
    return _b64url_decode(
        str(envelope.get("signature_b64url", "")), label="signature", minimum=256, maximum=512
    )


def inspect_signed_update(
    manifest_path: Path,
    signature_path: Path,
    *,
    current_version: str,
    channel_path: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify and inspect the v2 manifest; v1 is rejected by construction."""

    channel_value = _read_update_channel(channel_path)
    channel = validate_update_channel(channel_value, trusted_key_id=trusted_key_id)
    envelope, raw_signature = _read_bounded_json(
        signature_path, maximum=MAX_UPDATE_SIGNATURE_BYTES, code="UPDATE_SIGNATURE_FORMAT_INVALID"
    )
    signature_bytes = _validate_signature_envelope(envelope, channel["signature"])
    # Trust the manifest only after verifying the exact raw bytes.  Canonical
    # JSON and schema/semantic validation are intentionally later gates.
    raw_manifest = _read_bounded_bytes(
        manifest_path, maximum=MAX_UPDATE_MANIFEST_BYTES, code="UPDATE_MANIFEST_INVALID"
    )
    _verify_rsa_pkcs1_sha256(raw_manifest, signature_bytes, channel["signature"])
    manifest = _canonical_object_from_bytes(raw_manifest, code="UPDATE_MANIFEST_INVALID")
    signed = validate_update_manifest(manifest, channel, schema_dir=schema_dir)
    current = _version_tuple(current_version)
    available = _version_tuple(str(signed["release"]["version"]))
    minimum = _version_tuple(str(signed["predecessor"]["minimum_version"]))
    maximum = _version_tuple(str(signed["predecessor"]["maximum_version_exclusive"]))
    if available > current and not minimum <= current < maximum:
        raise JobOpsError(
            "UPDATE_PREDECESSOR_UNSUPPORTED",
            "The installed version is outside this update's signed predecessor range.",
        )
    status = "UPDATE_AVAILABLE" if available > current else "UPDATE_CURRENT"
    asset = signed["asset"]
    closure = signed["runtime_closure"]
    attestation = signed["publisher_attestation"]
    return {
        "schema_version": 2,
        "status": status,
        "current_version": current_version,
        "available_version": signed["release"]["version"],
        "asset_name": asset["name"],
        "asset_sha256": asset["sha256"],
        "asset_bytes": asset["bytes"],
        "archive_prefix": asset["archive_prefix"],
        "commit": signed["release"]["source_commit"],
        "release_platform": signed["release"]["platform"],
        "runtime_closure_manifest_sha256": closure["manifest_sha256"],
        "runtime_tree_sha256": closure["tree_sha256"],
        "source_payload_sha256": closure["source_payload_sha256"],
        "runtime_file_count": closure["file_count"],
        "runtime_total_bytes": closure["total_bytes"],
        "manifest_sha256": sha256_bytes(raw_manifest),
        "signature_sha256": sha256_bytes(raw_signature),
        "runtime_build_evidence_sha256": attestation["runtime_build_evidence_sha256"],
        "publisher_evidence_sha256": attestation["publisher_evidence_sha256"],
        "publisher_evidence_expires_at_utc": attestation["evidence_expires_at_utc"],
        "publisher_attestation_issued_at_utc": attestation["issued_at_utc"],
        "publisher_build_inputs_sha256": attestation["build_inputs_sha256"],
        "publisher_policy_sha256": attestation["policy_sha256"],
        "signer_readiness_challenge_sha256": attestation[
            "signer_readiness_challenge_sha256"
        ],
        "publisher_attestation_status": attestation["status"],
        "signature_verified": True,
        "key_id": channel["signature"]["key_id"],
    }


def _archive_runtime_closure_record(
    archive_path: Path, archive_prefix: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    directories, records, raw_closure = _archive_payload_records_with_capture(
        archive_path, archive_prefix, capture_relative="runtime-closure.json"
    )
    inventory = {
        "schema_version": 1,
        "status": "UPDATE_ARCHIVE_PAYLOAD_INVENTORIED",
        "archive_sha256": sha256_file(archive_path),
        "archive_prefix": archive_prefix,
        "directory_count": len(directories),
        "file_count": len(records),
        "directories": directories,
        "records": records,
        "inventory_sha256": hashlib.sha256(
            canonical_json({"directories": directories, "records": records})
        ).hexdigest(),
    }
    records = [item for item in inventory["records"] if item.get("relative") == "runtime-closure.json"]
    if len(records) != 1 or raw_closure is None or len(raw_closure) > 16 * 1024 * 1024:
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_MISSING",
            "The complete runtime archive must contain exactly one runtime closure manifest.",
        )
    closure = _canonical_object_from_bytes(
        raw_closure, code="UPDATE_RUNTIME_CLOSURE_INVALID"
    )
    return inventory, records[0], closure, raw_closure


def _assert_archive_matches_runtime_closure(
    inventory: dict[str, Any], closure: dict[str, Any]
) -> None:
    archived_records = [
        {
            "path": str(record["relative"]),
            "size": int(record["length"]),
            "sha256": "sha256:" + str(record["sha256"]),
        }
        for record in inventory["records"]
        if record.get("relative") != "runtime-closure.json"
    ]
    expected_records = closure.get("files")
    if (
        not isinstance(expected_records, list)
        or archived_records != expected_records
        or int(closure.get("file_count", -1)) != len(archived_records)
        or int(closure.get("total_bytes", -1))
        != sum(int(record["size"]) for record in archived_records)
        or str(closure.get("tree_sha256", ""))
        != sha256_bytes(canonical_json(archived_records))
        or int(inventory.get("file_count", -1)) != len(archived_records) + 1
    ):
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_INVENTORY_MISMATCH",
            "The complete runtime archive payload does not exactly match its runtime closure.",
        )


def _verify_signed_archive(
    result: dict[str, Any],
    archive_path: Path,
    *,
    verified_status: str,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify one held complete-runtime archive against signed metadata.

    The updater may intentionally skip this work when no newer version is
    available.  Release readiness must not: it verifies the exact current
    candidate before publication, so the archive check lives in this shared
    helper instead of being coupled to version ordering.
    """

    archive_size, archive_sha256 = _read_bounded_file_identity(
        archive_path,
        maximum=MAX_UPDATE_ARCHIVE_BYTES,
        code="UPDATE_ARCHIVE_MISSING",
    )
    if archive_path.name != result["asset_name"] or archive_size != result["asset_bytes"]:
        raise JobOpsError(
            "UPDATE_ARCHIVE_IDENTITY_MISMATCH",
            "The downloaded update archive does not match its signed identity.",
        )
    if not hmac.compare_digest(archive_sha256, str(result["asset_sha256"])):
        raise JobOpsError(
            "UPDATE_ARCHIVE_DIGEST_MISMATCH",
            "The downloaded update archive failed its signed SHA-256 check.",
        )
    inventory, closure_record, closure, raw_closure = _archive_runtime_closure_record(
        archive_path, str(result["archive_prefix"])
    )
    record_digest = "sha256:" + str(closure_record["sha256"])
    if not hmac.compare_digest(record_digest, str(result["runtime_closure_manifest_sha256"])):
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_DIGEST_MISMATCH",
            "The archived runtime closure does not match the signed manifest.",
        )
    validate_named("runtime-closure", closure, _v2_schema_dir(schema_dir))
    if sha256_bytes(raw_closure) != str(result["runtime_closure_manifest_sha256"]):
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_DIGEST_MISMATCH",
            "The archived runtime closure does not match the signed manifest.",
        )
    _assert_archive_matches_runtime_closure(inventory, closure)
    final_size, final_sha256 = _read_bounded_file_identity(
        archive_path,
        maximum=MAX_UPDATE_ARCHIVE_BYTES,
        code="UPDATE_ARCHIVE_MISSING",
    )
    if final_size != archive_size or not hmac.compare_digest(final_sha256, archive_sha256):
        raise JobOpsError(
            "UPDATE_ARCHIVE_CHANGED",
            "The downloaded update archive changed during verification.",
        )
    return {
        **result,
        "status": verified_status,
        "archive_verified": True,
        "archive_file_count": inventory["file_count"],
        "runtime_closure_verified": True,
        "finding_count": 0,
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
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    del project  # v2 is a complete runtime, not the legacy public-source package.
    result = inspect_signed_update(
        manifest_path,
        signature_path,
        current_version=current_version,
        channel_path=channel_path,
        trusted_key_id=trusted_key_id,
        schema_dir=schema_dir,
    )
    if result["status"] != "UPDATE_AVAILABLE":
        return result
    return _verify_signed_archive(
        result,
        archive_path,
        verified_status="UPDATE_BUNDLE_VERIFIED",
        schema_dir=schema_dir,
    )


def verify_signed_release_bundle(
    manifest_path: Path,
    signature_path: Path,
    archive_path: Path,
    *,
    release_version: str,
    channel_path: Path | None = None,
    trusted_key_id: str = TRUSTED_RELEASE_KEY_ID,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify the exact signed bundle selected for public release.

    Unlike the updater entry point, this function always verifies the archive
    payload even when the signed release is the same version as the source
    tree being assessed.
    """

    result = inspect_signed_update(
        manifest_path,
        signature_path,
        current_version=release_version,
        channel_path=channel_path,
        trusted_key_id=trusted_key_id,
        schema_dir=schema_dir,
    )
    if result["available_version"] != release_version:
        raise JobOpsError(
            "RELEASE_BUNDLE_VERSION_MISMATCH",
            "The signed release bundle does not match the version selected for publication.",
        )
    return _verify_signed_archive(
        result,
        archive_path,
        verified_status="RELEASE_BUNDLE_VERIFIED",
        schema_dir=schema_dir,
    )


def _load_required_build_input(
    path: Path | None, *, maximum: int, code: str, missing_code: str
) -> tuple[dict[str, Any], bytes]:
    if path is None:
        raise JobOpsError(missing_code, "A separately produced release input is required.")
    return _read_bounded_json(path, maximum=maximum, code=code)


def _load_legacy_v1_predecessors(path: Path | None) -> list[dict[str, Any]] | None:
    """Load an exact, bounded legacy-v1 authorization set for outer signing."""

    if path is None:
        return None
    code = "UPDATE_LEGACY_V1_PREDECESSORS_INVALID"
    value, _ = _read_bounded_json(path, maximum=MAX_UPDATE_MANIFEST_BYTES, code=code)
    if (
        set(value) != {"schema_version", "product", "predecessors"}
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("product") != "JobFlow"
        or type(value.get("predecessors")) is not list
        or not 1 <= len(value["predecessors"]) <= 64
    ):
        raise JobOpsError(code, "The legacy-v1 predecessor authorization file is invalid.")

    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    directories: set[str] = set()
    for item in value["predecessors"]:
        if (
            type(item) is not dict
            or set(item) != {"schema_version", "version", "source_sha256", "version_directory"}
            or type(item.get("schema_version")) is not int
            or item.get("schema_version") != 1
            or type(item.get("version")) is not str
            or _VERSION.fullmatch(item["version"]) is None
            or type(item.get("source_sha256")) is not str
            or _LEGACY_SHA256.fullmatch(item["source_sha256"]) is None
            or type(item.get("version_directory")) is not str
        ):
            raise JobOpsError(code, "A legacy-v1 predecessor identity is malformed.")
        version = item["version"]
        source_sha256 = item["source_sha256"]
        version_directory = item["version_directory"]
        expected_directory = f"v{version}-{source_sha256[:12]}"
        identity = (version, source_sha256, version_directory)
        if (
            version_directory != expected_directory
            or identity in identities
            or version_directory in directories
        ):
            raise JobOpsError(
                code,
                "A legacy-v1 predecessor identity is inconsistent, duplicated, or ambiguous.",
            )
        identities.add(identity)
        directories.add(version_directory)
        normalized.append(
            {
                "schema_version": 1,
                "version": version,
                "source_sha256": source_sha256,
                "version_directory": version_directory,
            }
        )
    normalized.sort(
        key=lambda item: (
            _version_tuple(item["version"]),
            item["source_sha256"],
            item["version_directory"],
        )
    )
    return normalized


def build_update_manifest(
    *,
    archive_path: Path,
    version: str,
    commit: str,
    runtime_closure_path: Path | None = None,
    runtime_build_evidence_path: Path | None = None,
    publisher_evidence_path: Path | None = None,
    legacy_v1_predecessors_path: Path | None = None,
    predecessor_minimum_version: str | None = None,
    minimum_updater_version: str | None = None,
    minimum_bootstrap_version: str | None = None,
    issued_at_utc: str | None = None,
    validation_time_utc: str | None = None,
    channel_path: Path | None = None,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the exact manifest body that a protected publisher will sign.

    A structural closure remains ``BUILT_UNATTESTED``.  The signed manifest's
    publisher-attestation projection is derived only from independently
    validated, canonical build and publisher evidence; callers cannot supply
    an arbitrary object already labelled ``ATTESTED``.
    """

    _version_tuple(version)
    channel_value = _read_update_channel(channel_path)
    channel = validate_update_channel(
        channel_value,
        trusted_key_id=TRUSTED_RELEASE_KEY_ID,
    )
    if _COMMIT.fullmatch(commit) is None:
        raise JobOpsError("UPDATE_MANIFEST_INVALID", "The release commit is invalid.")
    required_versions = {
        "predecessor_minimum_version": predecessor_minimum_version,
        "minimum_updater_version": minimum_updater_version,
        "minimum_bootstrap_version": minimum_bootstrap_version,
    }
    if (
        any(value is None for value in required_versions.values())
        or issued_at_utc is None
        or validation_time_utc is None
    ):
        raise JobOpsError(
            "UPDATE_MANIFEST_V2_INPUT_REQUIRED",
            "The complete-runtime release policy, issuance time, and evidence validation time are required.",
        )
    for value in required_versions.values():
        _version_tuple(str(value))
    try:
        manifest_issued_at = parse_iso(str(issued_at_utc))
        evidence_validation_time = parse_iso(str(validation_time_utc))
    except (TypeError, ValueError) as error:
        raise JobOpsError(
            "UPDATE_MANIFEST_INVALID",
            "The update manifest issuance time is invalid.",
        ) from error
    if manifest_issued_at > evidence_validation_time:
        raise JobOpsError(
            "UPDATE_MANIFEST_TIME_INVALID",
            "The update manifest cannot be issued after the trusted evidence-validation time.",
        )
    expected_name = f"JobFlow-v{version}-windows-x64-complete.zip"
    if archive_path.name != expected_name:
        raise JobOpsError(
            "UPDATE_MANIFEST_INVALID",
            "The complete runtime archive name does not match its version.",
        )
    archive_size, archive_sha256 = _read_bounded_file_identity(
        archive_path,
        maximum=MAX_UPDATE_ARCHIVE_BYTES,
        code="UPDATE_ARCHIVE_MISSING",
    )
    archive_prefix = f"JobFlow-v{version}-windows-x64/"
    inventory, archived_closure, archive_closure_value, archive_closure_raw = (
        _archive_runtime_closure_record(archive_path, archive_prefix)
    )
    closure, raw_closure = _load_required_build_input(
        runtime_closure_path,
        maximum=16 * 1024 * 1024,
        code="UPDATE_RUNTIME_CLOSURE_INVALID",
        missing_code="UPDATE_RUNTIME_CLOSURE_REQUIRED",
    )
    validate_named("runtime-closure", closure, _v2_schema_dir(schema_dir))
    validate_named("runtime-closure", archive_closure_value, _v2_schema_dir(schema_dir))
    if closure.get("application_version") != version or closure.get("source_commit") != commit:
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_IDENTITY_MISMATCH",
            "The structural runtime closure does not match the release version and commit.",
        )
    runtime_identity = _release_python_identity(schema_dir)
    closure_python = closure.get("python", {})
    if (
        closure_python.get("version") != runtime_identity["version"]
        or closure_python.get("artifact_name") != runtime_identity["artifact_name"]
        or closure_python.get("artifact_sha256") != runtime_identity["artifact_sha256"]
        or closure.get("layout", {}).get("python_pth")
        != f"runtime/{runtime_identity['tag']}._pth"
    ):
        raise JobOpsError(
            "UPDATE_RUNTIME_POLICY_MISMATCH",
            "The runtime closure does not match the pinned execution runtime identity.",
        )
    for record in closure.get("files", []):
        _normalize_payload_relative(str(record.get("path", "")))
    closure_sha256 = sha256_bytes(raw_closure)
    if (
        str(archived_closure["sha256"]) != closure_sha256.removeprefix("sha256:")
        or int(archived_closure["length"]) != len(raw_closure)
        or archive_closure_raw != raw_closure
    ):
        raise JobOpsError(
            "UPDATE_RUNTIME_CLOSURE_DIGEST_MISMATCH",
            "The supplied runtime closure is not the one embedded in the complete archive.",
        )
    _assert_archive_matches_runtime_closure(inventory, closure)
    policy = {
        "minimum_updater_version": str(minimum_updater_version),
        "minimum_bootstrap_version": str(minimum_bootstrap_version),
        "required_structural_status": "BUILT_UNATTESTED",
        "publisher_attestation_required": True,
        "final_submit_user_only": True,
        "automatic_retry_submission_unknown": False,
        "external_actions_during_update": 0,
    }
    build_inputs = closure["build_inputs"]
    projected_build_inputs = {
        "python_artifact_sha256": closure["python"]["artifact_sha256"],
        "wheel_lock_sha256": build_inputs["wheel_lock_sha256"],
        "wheelhouse_tree_sha256": build_inputs["wheelhouse_tree_sha256"],
        "application_wheel_sha256": build_inputs["application_wheel_sha256"],
        "application_wheel_provenance": build_inputs["application_wheel_provenance"],
        "builder_toolchain_sha256": build_inputs["builder_toolchain_sha256"],
        "wheel_count": len(build_inputs["wheels"]),
    }
    if runtime_build_evidence_path is None:
        raise JobOpsError(
            "UPDATE_RUNTIME_BUILD_EVIDENCE_REQUIRED",
            "Canonical runtime build evidence is required for protected signing.",
        )
    runtime_build_raw = _read_bounded_bytes(
        runtime_build_evidence_path,
        maximum=256 * 1024,
        code="UPDATE_RUNTIME_BUILD_EVIDENCE_INVALID",
    )
    runtime_build_document = validate_runtime_build_evidence(
        runtime_build_raw,
        now=evidence_validation_time,
        schema_dir=_v2_schema_dir(schema_dir),
    )
    if publisher_evidence_path is None:
        raise JobOpsError(
            "UPDATE_PUBLISHER_EVIDENCE_REQUIRED",
            "Canonical publisher evidence is required for protected signing.",
        )
    publisher_raw = _read_bounded_bytes(
        publisher_evidence_path,
        maximum=256 * 1024,
        code="UPDATE_PUBLISHER_EVIDENCE_INVALID",
    )
    publisher_document = validate_publisher_evidence(
        publisher_raw,
        runtime_build=runtime_build_document,
        now=evidence_validation_time,
        schema_dir=_v2_schema_dir(schema_dir),
    )
    runtime_build = runtime_build_document.value
    publisher_evidence = publisher_document.value
    expected_runtime_archive = {
        "name": expected_name,
        "bytes": archive_size,
        "sha256": archive_sha256,
        "archive_prefix": archive_prefix,
    }
    expected_runtime_closure = {
        "manifest_sha256": closure_sha256,
        "tree_sha256": closure["tree_sha256"],
        "source_payload_sha256": archive_sha256,
        "file_count": closure["file_count"],
        "total_bytes": closure["total_bytes"],
        "python_version": closure["python"]["version"],
        "platform": closure["platform"],
    }
    evidence_inputs = runtime_build["build_inputs"]
    expected_input_bindings = {
        "runtime_wheel_lock_sha256": build_inputs["wheel_lock_sha256"],
        "wheelhouse_tree_sha256": build_inputs["wheelhouse_tree_sha256"],
        "application_wheel_sha256": build_inputs["application_wheel_sha256"],
        "application_wheel_provenance": build_inputs["application_wheel_provenance"],
        "builder_toolchain_sha256": build_inputs["builder_toolchain_sha256"],
        "runtime_wheel_count": len(build_inputs["wheels"]),
    }
    if evidence_inputs.get("application_wheel_provenance") != build_inputs.get("application_wheel_provenance"):
        raise JobOpsError(
            "UPDATE_APPLICATION_WHEEL_PROVENANCE_MISMATCH",
            "Runtime evidence and closure bind different application wheel provenance.",
        )
    evidence_identity_matches = (
        runtime_build.get("application_version") == version
        and runtime_build.get("source_commit") == commit
        and runtime_build.get("platform") == closure.get("platform")
        and runtime_build.get("structural_status") == closure.get("status")
        and runtime_build.get("archive") == expected_runtime_archive
        and runtime_build.get("runtime_closure") == expected_runtime_closure
        and closure.get("python", {}).get("version")
        == runtime_build.get("python_source", {}).get("version")
        and closure.get("python", {}).get("artifact_name")
        == runtime_build.get("python_source", {}).get("artifact_name")
        and runtime_build.get("python_source", {}).get("artifact_sha256")
        == closure.get("python", {}).get("artifact_sha256")
        and all(evidence_inputs.get(key) == expected for key, expected in expected_input_bindings.items())
    )
    if not evidence_identity_matches:
        raise JobOpsError(
            "UPDATE_PUBLISHER_EVIDENCE_BINDING_MISMATCH",
            "The validated publisher evidence does not bind the exact archive and runtime closure.",
        )
    signer = publisher_evidence["outer_signing_readiness"]
    attestation = {
        "status": "ATTESTED",
        "format": "JOBFLOW_PUBLISHER_ATTESTATION_V2",
        "release_key_id": signer["release_key_id"],
        "evidence_format": publisher_evidence["format"],
        "runtime_build_evidence_sha256": runtime_build_document.sha256,
        "publisher_evidence_sha256": publisher_document.sha256,
        "evidence_expires_at_utc": publisher_evidence["expires_at_utc"],
        "signer_readiness_challenge_sha256": signer["challenge_sha256"],
        "runtime_closure_manifest_sha256": closure_sha256,
        "runtime_tree_sha256": closure["tree_sha256"],
        "build_inputs_sha256": sha256_bytes(canonical_json(projected_build_inputs)),
        "source_commit": commit,
        "source_payload_sha256": archive_sha256,
        "file_count": closure["file_count"],
        "total_bytes": closure["total_bytes"],
        "policy_sha256": sha256_bytes(canonical_json(policy)),
        "issued_at_utc": publisher_evidence["issued_at_utc"],
    }
    legacy_v1_predecessors = _load_legacy_v1_predecessors(legacy_v1_predecessors_path)
    value = {
        "schema_version": 2,
        "product": "JobFlow",
        "channel": "stable",
        "release": {
            "version": version,
            "source_commit": commit,
            "platform": "windows-x64",
        },
        "predecessor": {
            "minimum_version": str(predecessor_minimum_version),
            "maximum_version_exclusive": version,
            "disallow_downgrade": True,
            "require_current_runtime_closure": True,
        },
        **(
            {"legacy_v1_predecessors": legacy_v1_predecessors}
            if legacy_v1_predecessors is not None
            else {}
        ),
        "asset": {
            "name": expected_name,
            "bytes": archive_size,
            "sha256": archive_sha256,
            "archive_prefix": archive_prefix,
        },
        "runtime_closure": {
            "manifest_sha256": closure_sha256,
            "tree_sha256": closure["tree_sha256"],
            "structural_status": closure["status"],
            "source_commit": closure["source_commit"],
            "source_payload_sha256": archive_sha256,
            "file_count": closure["file_count"],
            "total_bytes": closure["total_bytes"],
            "python_version": closure["python"]["version"],
            "platform": closure["platform"],
            "build_inputs": projected_build_inputs,
        },
        "publisher_attestation": attestation,
        "policy": policy,
        "issued_at_utc": issued_at_utc,
    }
    validate_update_manifest(value, channel, schema_dir=schema_dir)
    # Inventory was fully materialized above; this assertion makes it explicit
    # that the source package was not accepted as an empty or partial ZIP.
    if inventory["file_count"] < 1:
        raise JobOpsError("UPDATE_ARCHIVE_PAYLOAD_INVALID", "The complete runtime archive is empty.")
    final_archive_size, final_archive_sha256 = _read_bounded_file_identity(
        archive_path,
        maximum=MAX_UPDATE_ARCHIVE_BYTES,
        code="UPDATE_ARCHIVE_MISSING",
    )
    if final_archive_size != archive_size or not hmac.compare_digest(
        final_archive_sha256, archive_sha256
    ):
        raise JobOpsError(
            "UPDATE_ARCHIVE_CHANGED",
            "The complete runtime archive changed while its manifest was being built.",
        )
    return value


UPDATE_SIGNING_REQUEST_FORMAT = "JOBFLOW_UPDATE_SIGNING_REQUEST_V2"


def build_update_signing_request(
    *,
    manifest_path: Path,
    runtime_closure_path: Path,
    runtime_build_evidence_path: Path,
    publisher_evidence_path: Path,
    legacy_v1_predecessors_path: Path | None = None,
    channel_path: Path | None = None,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the canonical, pathless request passed to protected signing.

    The request contains only public digests and release bindings.  It is not a
    signature and cannot authorize an update by itself.
    """

    channel_value = _read_update_channel(channel_path)
    channel = validate_update_channel(channel_value, trusted_key_id=TRUSTED_RELEASE_KEY_ID)
    manifest, raw_manifest = _read_bounded_json(
        manifest_path,
        maximum=MAX_UPDATE_MANIFEST_BYTES,
        code="UPDATE_MANIFEST_INVALID",
    )
    signed = validate_update_manifest(manifest, channel, schema_dir=schema_dir)

    closure, raw_closure = _load_required_build_input(
        runtime_closure_path,
        maximum=16 * 1024 * 1024,
        code="UPDATE_RUNTIME_CLOSURE_INVALID",
        missing_code="UPDATE_RUNTIME_CLOSURE_REQUIRED",
    )
    validate_named("runtime-closure", closure, _v2_schema_dir(schema_dir))
    runtime_build_raw = _read_bounded_bytes(
        runtime_build_evidence_path,
        maximum=256 * 1024,
        code="UPDATE_RUNTIME_BUILD_EVIDENCE_INVALID",
    )
    publisher_raw = _read_bounded_bytes(
        publisher_evidence_path,
        maximum=256 * 1024,
        code="UPDATE_PUBLISHER_EVIDENCE_INVALID",
    )
    attestation = signed["publisher_attestation"]
    closure_binding = signed["runtime_closure"]
    if (
        sha256_bytes(raw_closure) != closure_binding["manifest_sha256"]
        or closure["tree_sha256"] != closure_binding["tree_sha256"]
        or sha256_bytes(runtime_build_raw) != attestation["runtime_build_evidence_sha256"]
        or sha256_bytes(publisher_raw) != attestation["publisher_evidence_sha256"]
    ):
        raise JobOpsError(
            "UPDATE_PRESIGN_BINDING_MISMATCH",
            "The signing request inputs do not match the exact presign manifest.",
        )

    manifest_legacy = signed.get("legacy_v1_predecessors")
    supplied_legacy = _load_legacy_v1_predecessors(legacy_v1_predecessors_path)
    if supplied_legacy != manifest_legacy:
        raise JobOpsError(
            "UPDATE_PRESIGN_BINDING_MISMATCH",
            "The legacy predecessor authorization does not match the presign manifest.",
        )
    legacy_raw: bytes | None = None
    if legacy_v1_predecessors_path is not None:
        legacy_raw = _read_bounded_bytes(
            legacy_v1_predecessors_path,
            maximum=MAX_UPDATE_MANIFEST_BYTES,
            code="UPDATE_LEGACY_V1_PREDECESSORS_INVALID",
        )

    version_policy = {
        "predecessor_minimum_version": signed["predecessor"]["minimum_version"],
        "minimum_updater_version": signed["policy"]["minimum_updater_version"],
        "minimum_bootstrap_version": signed["policy"]["minimum_bootstrap_version"],
    }
    public_key = channel["signature"]
    return {
        "schema_version": 1,
        "format": UPDATE_SIGNING_REQUEST_FORMAT,
        "status": "AWAITING_PROTECTED_SIGNATURE",
        "signature": {
            "algorithm": public_key["algorithm"],
            "key_id": public_key["key_id"],
            "manifest_schema_version": 2,
            "manifest_bytes": len(raw_manifest),
            "manifest_sha256": sha256_bytes(raw_manifest),
        },
        "release": {
            "version": signed["release"]["version"],
            "source_commit": signed["release"]["source_commit"],
            "platform": signed["release"]["platform"],
        },
        "asset": {
            "name": signed["asset"]["name"],
            "bytes": signed["asset"]["bytes"],
            "sha256": signed["asset"]["sha256"],
        },
        "runtime_closure": {
            "manifest_sha256": closure_binding["manifest_sha256"],
            "tree_sha256": closure_binding["tree_sha256"],
        },
        "evidence": {
            "runtime_build_evidence_sha256": attestation["runtime_build_evidence_sha256"],
            "publisher_evidence_sha256": attestation["publisher_evidence_sha256"],
        },
        "version_policy": {
            **version_policy,
            "sha256": sha256_bytes(canonical_json(version_policy)),
        },
        "legacy_v1_predecessors": {
            "included": legacy_raw is not None,
            "sha256": sha256_bytes(legacy_raw) if legacy_raw is not None else None,
            "count": len(supplied_legacy) if supplied_legacy is not None else 0,
        },
        "external_actions": 0,
    }


def build_legacy_update_manifest_v1(*, archive_path: Path, version: str, commit: str) -> dict[str, Any]:
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


def _emit_canonical_bytes(value: dict[str, Any]) -> None:
    """Write one canonical JSON document and no status wrapper or newline."""

    payload = canonical_json(value)
    if len(payload) > MAX_UPDATE_MANIFEST_BYTES:
        raise JobOpsError(
            "UPDATE_MANIFEST_INVALID",
            "The canonical update document exceeds the bounded output size.",
        )
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


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
    build_parser.add_argument("--runtime-closure", type=Path, required=True)
    build_parser.add_argument("--runtime-build-evidence", type=Path, required=True)
    build_parser.add_argument("--publisher-evidence", type=Path, required=True)
    build_parser.add_argument("--legacy-v1-predecessors", type=Path)
    build_parser.add_argument("--predecessor-minimum-version", required=True)
    build_parser.add_argument("--minimum-updater-version", required=True)
    build_parser.add_argument("--minimum-bootstrap-version", required=True)
    build_parser.add_argument("--issued-at-utc", required=True)
    build_parser.add_argument("--validation-time-utc", required=True)
    build_parser.add_argument("--channel", type=Path)
    build_parser.add_argument("--schema-dir", type=Path)
    build_output = build_parser.add_mutually_exclusive_group(required=True)
    build_output.add_argument("--output", type=Path)
    build_output.add_argument("--emit-canonical-stdout", action="store_true")
    presign_parser = commands.add_parser("presign-request")
    presign_parser.add_argument("--manifest", type=Path, required=True)
    presign_parser.add_argument("--runtime-closure", type=Path, required=True)
    presign_parser.add_argument("--runtime-build-evidence", type=Path, required=True)
    presign_parser.add_argument("--publisher-evidence", type=Path, required=True)
    presign_parser.add_argument("--legacy-v1-predecessors", type=Path)
    presign_parser.add_argument("--channel", type=Path)
    presign_parser.add_argument("--schema-dir", type=Path)
    presign_output = presign_parser.add_mutually_exclusive_group(required=True)
    presign_output.add_argument("--output", type=Path)
    presign_output.add_argument("--emit-canonical-stdout", action="store_true")
    legacy_inspect_parser = commands.add_parser("inspect-legacy-v1")
    legacy_inspect_parser.add_argument("--manifest", type=Path, required=True)
    legacy_inspect_parser.add_argument("--signature", type=Path, required=True)
    legacy_inspect_parser.add_argument("--current-version", required=True)
    legacy_inspect_parser.add_argument("--channel", type=Path)
    legacy_verify_parser = commands.add_parser("verify-legacy-v1")
    legacy_verify_parser.add_argument("--manifest", type=Path, required=True)
    legacy_verify_parser.add_argument("--signature", type=Path, required=True)
    legacy_verify_parser.add_argument("--archive", type=Path, required=True)
    legacy_verify_parser.add_argument("--current-version", required=True)
    legacy_verify_parser.add_argument("--channel", type=Path)
    legacy_build_parser = commands.add_parser("build-legacy-v1")
    legacy_build_parser.add_argument("--archive", type=Path, required=True)
    legacy_build_parser.add_argument("--version", required=True)
    legacy_build_parser.add_argument("--commit", required=True)
    legacy_build_parser.add_argument("--output", type=Path, required=True)
    inventory_parser = commands.add_parser("inventory-payload")
    inventory_parser.add_argument("--archive", type=Path, required=True)
    inventory_parser.add_argument("--archive-prefix", required=True)
    attest_parser = commands.add_parser("attest-extracted")
    attest_parser.add_argument("--archive", type=Path, required=True)
    attest_parser.add_argument("--archive-prefix", required=True)
    attest_parser.add_argument("--extracted-root", type=Path, required=True)
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
        elif arguments.command == "inventory-payload":
            result = inventory_archive_payload(arguments.archive, arguments.archive_prefix)
        elif arguments.command == "attest-extracted":
            result = attest_extracted_payload(
                arguments.archive, arguments.archive_prefix, arguments.extracted_root
            )
        elif arguments.command == "presign-request":
            result = build_update_signing_request(
                manifest_path=arguments.manifest,
                runtime_closure_path=arguments.runtime_closure,
                runtime_build_evidence_path=arguments.runtime_build_evidence,
                publisher_evidence_path=arguments.publisher_evidence,
                legacy_v1_predecessors_path=arguments.legacy_v1_predecessors,
                channel_path=arguments.channel,
                schema_dir=arguments.schema_dir,
            )
            if arguments.emit_canonical_stdout:
                _emit_canonical_bytes(result)
                return 0
            with arguments.output.open("xb") as output_stream:
                output_stream.write(canonical_json(result))
                output_stream.flush()
                os.fsync(output_stream.fileno())
            result = {
                "schema_version": 1,
                "status": "UPDATE_SIGNING_REQUEST_BUILT",
                "manifest_sha256": result["signature"]["manifest_sha256"],
                "key_id": result["signature"]["key_id"],
                "external_actions": 0,
            }
        elif arguments.command == "inspect-legacy-v1":
            result = inspect_legacy_signed_update_v1(
                arguments.manifest,
                arguments.signature,
                current_version=arguments.current_version,
                channel_path=arguments.channel,
            )
        elif arguments.command == "verify-legacy-v1":
            result = verify_legacy_signed_update_bundle_v1(
                arguments.manifest,
                arguments.signature,
                arguments.archive,
                current_version=arguments.current_version,
                channel_path=arguments.channel,
            )
        else:
            if arguments.command == "build-legacy-v1":
                result = build_legacy_update_manifest_v1(
                    archive_path=arguments.archive,
                    version=arguments.version,
                    commit=arguments.commit,
                )
                result_schema = 1
            else:
                result = build_update_manifest(
                    archive_path=arguments.archive,
                    version=arguments.version,
                    commit=arguments.commit,
                    runtime_closure_path=arguments.runtime_closure,
                    runtime_build_evidence_path=arguments.runtime_build_evidence,
                    publisher_evidence_path=arguments.publisher_evidence,
                    legacy_v1_predecessors_path=arguments.legacy_v1_predecessors,
                    predecessor_minimum_version=arguments.predecessor_minimum_version,
                    minimum_updater_version=arguments.minimum_updater_version,
                    minimum_bootstrap_version=arguments.minimum_bootstrap_version,
                    issued_at_utc=arguments.issued_at_utc,
                    validation_time_utc=arguments.validation_time_utc,
                    channel_path=arguments.channel,
                    schema_dir=arguments.schema_dir,
                )
                result_schema = 2
            # The signed-bundle orchestrator supplies an unpredictable path
            # that must not already exist.  Exclusive creation prevents a
            # raced hardlink from being opened and truncated.
            if arguments.command == "build" and arguments.emit_canonical_stdout:
                _emit_canonical_bytes(result)
                return 0
            with arguments.output.open("xb") as output_stream:
                output_stream.write(canonical_json(result))
                output_stream.flush()
                os.fsync(output_stream.fileno())
            result = {"schema_version": result_schema, "status": "UPDATE_MANIFEST_BUILT", **result}
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
