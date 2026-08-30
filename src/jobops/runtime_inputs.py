from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import secrets
import ssl
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

from .release_toolchain import (
    ReleaseToolchainError,
    load_python_support_policy,
    load_release_toolchain_policy,
)
from .util import canonical_json, has_reparse_component, project_root, sha256_bytes


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}")
_WHEEL_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,240}\.whl")
_PYTHON_HOST = "www.python.org"
_PYPI_API_HOST = "pypi.org"
_PYPI_FILE_HOST = "files.pythonhosted.org"
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_FETCH_BYTES = 128 * 1024 * 1024
_FETCH_WALL_CLOCK_SECONDS = 180.0
_FETCH_READ_TIMEOUT_SECONDS = 15.0
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_BUNDLE_FILES = 260
_MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_RELATIVE_PATH = 240
_MAX_PATH_DEPTH = 3
_WINDOWS_DEVICE_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class RuntimeInputError(RuntimeError):
    """A fail-closed runtime-input acquisition failure with no private path output."""


if os.name == "nt":
    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]


def _reliable_link_count(descriptor: int, information: os.stat_result) -> int:
    """Return a real link count; CPython 3.11 reports zero for some Windows files."""

    if os.name != "nt":
        return int(information.st_nlink)
    details = _ByHandleFileInformation()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    function = ctypes.windll.kernel32.GetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    function.restype = wintypes.BOOL
    if not function(handle, ctypes.byref(details)):
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    return int(details.number_of_links)


def _stat_identity(descriptor: int, information: os.stat_result) -> tuple[int, ...]:
    if os.name != "nt":
        return (
            int(information.st_dev),
            int(information.st_ino),
            int(information.st_size),
            int(information.st_mtime_ns),
            _reliable_link_count(descriptor, information),
        )
    details = _ByHandleFileInformation()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    function = ctypes.windll.kernel32.GetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    function.restype = wintypes.BOOL
    if not function(handle, ctypes.byref(details)):
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    return (
        int(details.volume_serial_number),
        (int(details.file_index_high) << 32) | int(details.file_index_low),
        (int(details.file_size_high) << 32) | int(details.file_size_low),
        (int(details.last_write_time.dwHighDateTime) << 32)
        | int(details.last_write_time.dwLowDateTime),
        int(details.number_of_links),
    )


if os.name == "nt":
    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ushort),
            ("maximum_length", ctypes.c_ushort),
            ("buffer", ctypes.c_void_p),
        ]


    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(_UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", ctypes.c_void_p),
            ("security_quality_of_service", ctypes.c_void_p),
        ]


    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_void_p)]


def _win_handle_identity(handle: int, failure: str) -> tuple[int, int, int, int]:
    details = _ByHandleFileInformation()
    function = ctypes.windll.kernel32.GetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    function.restype = wintypes.BOOL
    if not function(wintypes.HANDLE(handle), ctypes.byref(details)):
        raise RuntimeInputError(failure)
    return (
        int(details.volume_serial_number),
        (int(details.file_index_high) << 32) | int(details.file_index_low),
        (int(details.file_size_high) << 32) | int(details.file_size_low),
        int(details.number_of_links),
    )


def _win_final_path(handle: int, failure: str) -> Path:
    function = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(function(wintypes.HANDLE(handle), buffer, len(buffer), 0))
    if length < 1 or length >= len(buffer):
        raise RuntimeInputError(failure)
    value = buffer.value
    if value.casefold().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif value.casefold().startswith("\\\\?\\"):
        value = value[4:]
    return Path(os.path.abspath(value))


def _win_close_handle(handle: int) -> None:
    function = ctypes.windll.kernel32.CloseHandle
    function.argtypes = [wintypes.HANDLE]
    function.restype = wintypes.BOOL
    function(wintypes.HANDLE(handle))


def _win_open_directory(path: Path, failure: str) -> int:
    function = ctypes.windll.kernel32.CreateFileW
    function.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    function.restype = wintypes.HANDLE
    raw = function(
        str(path),
        0x00000081,
        0x00000003,
        None,
        3,
        0x02200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    handle = int(raw) if raw is not None else 0
    if handle in {0, invalid}:
        raise RuntimeInputError(failure)
    try:
        details = _ByHandleFileInformation()
        info = ctypes.windll.kernel32.GetFileInformationByHandle
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        info.restype = wintypes.BOOL
        if not info(wintypes.HANDLE(handle), ctypes.byref(details)):
            raise RuntimeInputError(failure)
        if not details.file_attributes & 0x10 or details.file_attributes & 0x400:
            raise RuntimeInputError(failure)
        if _win_final_path(handle, failure) != Path(os.path.abspath(path)):
            raise RuntimeInputError(failure)
        return handle
    except Exception:
        _win_close_handle(handle)
        raise


def _win_relative_handle(
    parent: int,
    name: str,
    *,
    directory: bool,
    create: bool,
    delete_access: bool,
    share_access: int,
    failure: str,
) -> int:
    _validate_windows_segment(name, failure)
    encoded = name.encode("utf-16-le")
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode = _UnicodeString(
        length=len(encoded),
        maximum_length=len(encoded) + 2,
        buffer=ctypes.cast(name_buffer, ctypes.c_void_p),
    )
    attributes = _ObjectAttributes(
        length=ctypes.sizeof(_ObjectAttributes),
        root_directory=wintypes.HANDLE(parent),
        object_name=ctypes.pointer(unicode),
        attributes=0x40,
        security_descriptor=None,
        security_quality_of_service=None,
    )
    io = _IoStatusBlock()
    raw = wintypes.HANDLE()
    function = ctypes.windll.ntdll.NtCreateFile
    function.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    function.restype = ctypes.c_long
    access = (0x00100080 if directory else 0x00100183) | (0x00010000 if delete_access else 0)
    options = 0x21 if directory else 0x60
    status = int(
        function(
            ctypes.byref(raw),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io),
            None,
            0x10 if directory else 0x80,
            share_access,
            2 if create else 1,
            options,
            None,
            0,
        )
    )
    handle = int(raw.value) if raw.value is not None else 0
    if status != 0 or handle in {0, ctypes.c_void_p(-1).value}:
        if handle not in {0, ctypes.c_void_p(-1).value}:
            _win_close_handle(handle)
        raise RuntimeInputError(failure)
    return handle


def _win_create_relative(
    parent: int,
    name: str,
    *,
    directory: bool,
    delete_access: bool = False,
    share_access: int = 0x7,
    failure: str,
) -> int:
    return _win_relative_handle(
        parent,
        name,
        directory=directory,
        create=True,
        delete_access=delete_access,
        share_access=share_access,
        failure=failure,
    )


def _win_open_relative(
    parent: int,
    name: str,
    *,
    directory: bool,
    delete_access: bool = False,
    share_access: int = 0x7,
    failure: str,
) -> int:
    return _win_relative_handle(
        parent,
        name,
        directory=directory,
        create=False,
        delete_access=delete_access,
        share_access=share_access,
        failure=failure,
    )


def _win_mark_delete(handle: int, failure: str) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.SetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    function.restype = wintypes.BOOL
    disposition = ctypes.c_ubyte(1)
    if not function(wintypes.HANDLE(handle), 4, ctypes.byref(disposition), 1):
        raise RuntimeInputError(failure)


def _win_rename_relative(handle: int, parent: int, name: str, failure: str) -> None:
    _validate_windows_segment(name, failure)
    destination = str(_win_final_path(parent, failure) / name)
    encoded = destination.encode("utf-16-le")
    pointer_offset = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
    length_offset = pointer_offset + ctypes.sizeof(ctypes.c_void_p)
    name_offset = length_offset + 4
    # FileNameLength excludes a terminator, but Windows' rename implementation
    # may still inspect the WCHAR immediately following FileName.  Reserve and
    # zero that WCHAR explicitly so the variable-length FILE_RENAME_INFO never
    # exposes adjacent memory as an extra character in the destination name.
    buffer = ctypes.create_string_buffer(name_offset + len(encoded) + 2)
    buffer[0] = 0
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        struct.pack_into("<Q", buffer, pointer_offset, 0)
    else:
        struct.pack_into("<I", buffer, pointer_offset, 0)
    struct.pack_into("<I", buffer, length_offset, len(encoded))
    ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.SetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    function.restype = wintypes.BOOL
    if not function(wintypes.HANDLE(handle), 3, buffer, len(buffer)):
        raise RuntimeInputError(failure)


@dataclass(frozen=True)
class FetchResponse:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes


Fetch = Callable[[str, int], FetchResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _set_response_timeout(response: Any, seconds: float) -> None:
    """Set a bounded socket timeout or fail closed if urllib hides the socket."""

    candidates = (
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
        getattr(getattr(response, "fp", None), "_sock", None),
    )
    socket = next((candidate for candidate in candidates if candidate is not None), None)
    if socket is None or not hasattr(socket, "settimeout"):
        raise RuntimeInputError("RUNTIME_INPUT_FETCH_DEADLINE_UNAVAILABLE")
    socket.settimeout(seconds)


def _default_fetch(url: str, maximum_bytes: int) -> FetchResponse:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= _MAX_FETCH_BYTES:
        raise RuntimeInputError("RUNTIME_INPUT_FETCH_BOUND_INVALID")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, application/octet-stream;q=0.9, */*;q=0.1",
            "User-Agent": "JobFlow-runtime-input-acquirer/1",
        },
        method="GET",
    )
    deadline = time.monotonic() + _FETCH_WALL_CLOCK_SECONDS
    try:
        with opener.open(request, timeout=60) as response:
            status = int(response.status)
            final_url = response.geturl()
            headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
            declared_length: int | None = None
            raw_length = headers.get("content-length")
            if raw_length is not None:
                try:
                    parsed_length = int(raw_length)
                except (TypeError, ValueError):
                    parsed_length = -1
                if parsed_length >= 0:
                    declared_length = parsed_length
                    if declared_length > maximum_bytes:
                        raise RuntimeInputError("RUNTIME_INPUT_FETCH_TOO_LARGE")
            read_target = declared_length if declared_length is not None else maximum_bytes + 1
            chunks: list[bytes] = []
            total = 0
            while total < read_target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeInputError("RUNTIME_INPUT_FETCH_DEADLINE_EXCEEDED")
                # http.client closes and detaches its socket after a bounded
                # read consumes the declared Content-Length.  Treat that as
                # EOF instead of trying to reconfigure a socket that no longer
                # exists; exact length and digest checks still happen in the
                # caller before any fetched bytes become authoritative.
                if callable(getattr(response, "isclosed", None)) and response.isclosed():
                    break
                _set_response_timeout(response, min(_FETCH_READ_TIMEOUT_SECONDS, remaining))
                chunk = response.read(min(1024 * 1024, read_target - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    raise RuntimeInputError("RUNTIME_INPUT_FETCH_TOO_LARGE")
    except RuntimeInputError:
        raise
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeInputError("RUNTIME_INPUT_FETCH_FAILED") from exc
    return FetchResponse(status=status, url=final_url, headers=headers, body=b"".join(chunks))


def _read_project_input(path: Path, project: Path) -> bytes:
    if not path.is_file() or has_reparse_component(path, project):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID") from exc
    if not payload or len(payload) > 4 * 1024 * 1024 or payload.startswith(b"\xef\xbb\xbf"):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
    return payload


def _parse_object(payload: bytes, failure: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeInputError(failure)
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except RuntimeInputError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeInputError(failure) from exc
    if not isinstance(value, dict):
        raise RuntimeInputError(failure)
    return value


def _portable_text_sha256(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID") from exc
    if text.startswith("\ufeff"):
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
    return sha256_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _normalized_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _validate_windows_segment(value: str, failure: str) -> None:
    if (
        not value
        or value.endswith((".", " "))
        or ":" in value
        or "/" in value
        or "\\" in value
        or len(value) > 240
        or value.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES
    ):
        raise RuntimeInputError(failure)


def _validate_https_url(url: str, host: str, *, exact_path_suffix: str | None = None) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeInputError("RUNTIME_INPUT_URL_INVALID") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "//" in parsed.path
        or any(segment in {"", ".", ".."} for segment in parsed.path.split("/")[1:])
    ):
        raise RuntimeInputError("RUNTIME_INPUT_URL_INVALID")
    if exact_path_suffix is not None and not parsed.path.endswith("/" + exact_path_suffix):
        raise RuntimeInputError("RUNTIME_INPUT_URL_INVALID")


def _expected_content_length(headers: Mapping[str, str], expected: int) -> None:
    value = next((raw for key, raw in headers.items() if key.casefold() == "content-length"), None)
    try:
        length = int(value) if value is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeInputError("RUNTIME_INPUT_LENGTH_MISMATCH") from exc
    if length != expected:
        raise RuntimeInputError("RUNTIME_INPUT_LENGTH_MISMATCH")


def _fetch_exact(
    fetch: Fetch,
    url: str,
    *,
    host: str,
    expected_bytes: int,
    expected_sha256: str,
    filename: str | None = None,
    media_types: Sequence[str] | None = None,
) -> bytes:
    _validate_https_url(url, host, exact_path_suffix=filename)
    response = fetch(url, expected_bytes)
    if response.status != 200 or response.url != url:
        raise RuntimeInputError("RUNTIME_INPUT_RESPONSE_INVALID")
    _expected_content_length(response.headers, expected_bytes)
    if media_types is not None:
        if (
            not media_types
            or any(not isinstance(item, str) or not item for item in media_types)
            or len({item.casefold() for item in media_types}) != len(media_types)
        ):
            raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
        content_type = next(
            (value for key, value in response.headers.items() if key.casefold() == "content-type"),
            "",
        )
        actual_media_type = content_type.split(";", 1)[0].strip().casefold()
        if actual_media_type not in {item.casefold() for item in media_types}:
            raise RuntimeInputError("RUNTIME_INPUT_MEDIA_TYPE_MISMATCH")
    if len(response.body) != expected_bytes:
        raise RuntimeInputError("RUNTIME_INPUT_LENGTH_MISMATCH")
    if sha256_bytes(response.body) != expected_sha256:
        raise RuntimeInputError("RUNTIME_INPUT_DIGEST_MISMATCH")
    return response.body


def _load_lock(
    payload: bytes,
    *,
    lock_type: str,
    python_tag: str,
    platform: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = _parse_object(payload, "RUNTIME_INPUT_LOCK_INVALID")
    allowed_top = {"schema_version", "lock_type", "python_tag", "platform", "only_binary", "packages"}
    if lock_type == "runtime-wheelhouse":
        allowed_top.add("abi")
    packages = value.get("packages")
    if (
        set(value) != allowed_top
        or value.get("schema_version") != 1
        or value.get("lock_type") != lock_type
        or value.get("python_tag") != python_tag
        or value.get("platform") != platform
        or value.get("only_binary") is not True
        or not isinstance(packages, list)
        or not packages
        or len(packages) > 128
    ):
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
    if lock_type == "runtime-wheelhouse" and value.get("abi") != "cp313-or-abi3":
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
    names: set[str] = set()
    filenames: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for package in packages:
        if not isinstance(package, dict) or set(package) != {
            "name",
            "version",
            "filename",
            "size",
            "sha256",
        }:
            raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
        name = package.get("name")
        version = package.get("version")
        filename = package.get("filename")
        size = package.get("size")
        digest = package.get("sha256")
        if isinstance(filename, str):
            _validate_windows_segment(filename, "RUNTIME_INPUT_LOCK_INVALID")
        normalized_name = _normalized_package_name(name) if isinstance(name, str) else ""
        if (
            not isinstance(name, str)
            or _PACKAGE_NAME.fullmatch(name) is None
            or not isinstance(version, str)
            or _VERSION.fullmatch(version) is None
            or not isinstance(filename, str)
            or _WHEEL_FILENAME.fullmatch(filename) is None
            or Path(filename).name != filename
            or type(size) is not int
            or not 1 <= size <= 128 * 1024 * 1024
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or normalized_name in names
            or filename.casefold() in filenames
        ):
            raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
        names.add(normalized_name)
        filenames.add(filename.casefold())
        normalized.append(
            {
                "name": name,
                "version": version,
                "filename": filename,
                "size": size,
                "sha256": digest,
            }
        )
    return value, normalized


@dataclass(frozen=True)
class _Policies:
    source_bytes: bytes
    runtime_lock_bytes: bytes
    build_lock_bytes: bytes
    source: dict[str, Any]
    runtime_packages: list[dict[str, Any]]
    build_packages: list[dict[str, Any]]


def _validate_source_policy_shape(source: Mapping[str, Any]) -> None:
    top = {
        "schema_version",
        "status",
        "platform",
        "architecture",
        "python",
        "builder",
        "isolation",
        "attestation_policy",
    }
    python_keys = {
        "version",
        "artifact_name",
        "artifact_url",
        "artifact_bytes",
        "artifact_sha256",
        "release_page_url",
        "sigstore_bundle_url",
        "sigstore_bundle_bytes",
        "sigstore_bundle_sha256",
        "sigstore_transport_media_types",
        "sigstore_media_type",
        "sigstore_certificate_identity",
        "sigstore_certificate_oidc_issuer",
    }
    builder_keys = {
        "python_version",
        "python_architecture",
        "pip_version",
        "runtime_lock",
        "runtime_lock_sha256",
        "build_lock",
        "build_lock_sha256",
        "runtime_schema",
        "verification_script",
    }
    isolation_keys = {
        "python_pth",
        "import_site",
        "end_user_pip",
        "network_during_assembly",
        "network_during_smoke_test",
    }
    attestation_keys = {
        "required_for_attested_status",
        "default_status",
        "public_release_allowed_status",
    }
    python = source.get("python")
    builder = source.get("builder")
    isolation = source.get("isolation")
    attestation = source.get("attestation_policy")
    if (
        set(source) != top
        or not isinstance(python, dict)
        or set(python) != python_keys
        or not isinstance(builder, dict)
        or set(builder) != builder_keys
        or not isinstance(isolation, dict)
        or set(isolation) != isolation_keys
        or not isinstance(attestation, dict)
        or set(attestation) != attestation_keys
        or source.get("schema_version") != 1
        or source.get("status") != "PINNED_OFFICIAL_SOURCE"
        or source.get("platform") != "windows-x64"
        or source.get("architecture") != "AMD64"
        or python.get("version") != "3.13.15"
        or python.get("artifact_name") != "python-3.13.15-embed-amd64.zip"
        or python.get("artifact_url")
        != "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip"
        or type(python.get("artifact_bytes")) is not int
        or not 1 <= python["artifact_bytes"] <= _MAX_FETCH_BYTES
        or not isinstance(python.get("artifact_sha256"), str)
        or _SHA256.fullmatch(python["artifact_sha256"]) is None
        or python.get("release_page_url")
        != "https://www.python.org/downloads/release/python-31315/"
        or python.get("sigstore_bundle_url")
        != "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip.sigstore"
        or type(python.get("sigstore_bundle_bytes")) is not int
        or not 1 <= python["sigstore_bundle_bytes"] <= _MAX_METADATA_BYTES
        or not isinstance(python.get("sigstore_bundle_sha256"), str)
        or _SHA256.fullmatch(python["sigstore_bundle_sha256"]) is None
        or python.get("sigstore_transport_media_types") != ["application/octet-stream"]
        or python.get("sigstore_media_type")
        != "application/vnd.dev.sigstore.bundle.v0.3+json"
        or python.get("sigstore_certificate_identity") != "thomas@python.org"
        or python.get("sigstore_certificate_oidc_issuer") != "https://accounts.google.com"
        or builder.get("python_version") != "3.13.15"
        or builder.get("python_architecture") != "AMD64"
        or builder.get("pip_version") != "26.2.1"
        or builder.get("runtime_lock") != "config/windows-cp313-runtime.lock"
        or builder.get("build_lock") != "config/windows-cp313-build.lock"
        or builder.get("runtime_schema") != "schemas/runtime-closure.schema.json"
        or builder.get("verification_script")
        != "scripts/verify-windows-runtime-closure.ps1"
        or isolation
        != {
            "python_pth": ["python313.zip", ".", "../app"],
            "import_site": False,
            "end_user_pip": False,
            "network_during_assembly": False,
            "network_during_smoke_test": False,
        }
        or attestation
        != {
            "required_for_attested_status": [
                "verified_psf_sigstore_evidence",
                "deterministic_double_build_match",
                "offline_smoke_passed",
                "outer_signing_readiness_evidence",
                "detached_signature_verified_with_pinned_trust",
            ],
            "default_status": "BUILT_UNATTESTED",
            "public_release_allowed_status": "ATTESTED",
        }
    ):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")


def _load_policies(project: Path) -> _Policies:
    project = project.resolve(strict=True)
    try:
        load_release_toolchain_policy(project)
        support = load_python_support_policy(project)
    except ReleaseToolchainError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID") from exc
    complete = support["production_complete_windows_runtime"]
    source_path = project / complete["source_policy"]
    runtime_path = project / complete["runtime_lock"]
    build_path = project / complete["build_lock"]
    source_bytes = _read_project_input(source_path, project)
    runtime_bytes = _read_project_input(runtime_path, project)
    build_bytes = _read_project_input(build_path, project)
    source = _parse_object(source_bytes, "RUNTIME_INPUT_POLICY_INVALID")
    _validate_source_policy_shape(source)
    builder = source.get("builder")
    python = source.get("python")
    if not isinstance(builder, dict) or not isinstance(python, dict):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
    if (
        builder.get("runtime_lock") != complete["runtime_lock"]
        or builder.get("build_lock") != complete["build_lock"]
        or builder.get("runtime_lock_sha256") != _portable_text_sha256(runtime_bytes)
        or builder.get("build_lock_sha256") != _portable_text_sha256(build_bytes)
    ):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
    runtime_lock, runtime_packages = _load_lock(
        runtime_bytes,
        lock_type="runtime-wheelhouse",
        python_tag="cp313",
        platform="win_amd64",
    )
    build_lock, build_packages = _load_lock(
        build_bytes,
        lock_type="protected-builder-wheelhouse",
        python_tag="py3",
        platform="any",
    )
    if runtime_lock.get("abi") != "cp313-or-abi3" or build_lock.get("packages") is None:
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
    combined_names = [
        _normalized_package_name(str(package["name"]))
        for package in runtime_packages + build_packages
    ]
    combined_filenames = [
        str(package["filename"]).casefold() for package in runtime_packages + build_packages
    ]
    if (
        len(combined_names) != len(set(combined_names))
        or len(combined_filenames) != len(set(combined_filenames))
    ):
        raise RuntimeInputError("RUNTIME_INPUT_LOCK_INVALID")
    artifact_name = python.get("artifact_name")
    artifact_url = python.get("artifact_url")
    sigstore_url = python.get("sigstore_bundle_url")
    if (
        not isinstance(artifact_name, str)
        or not isinstance(artifact_url, str)
        or not isinstance(sigstore_url, str)
        or type(python.get("sigstore_bundle_bytes")) is not int
        or not 1 <= python["sigstore_bundle_bytes"] <= 4 * 1024 * 1024
        or not isinstance(python.get("sigstore_bundle_sha256"), str)
        or _SHA256.fullmatch(python["sigstore_bundle_sha256"]) is None
        or python.get("sigstore_transport_media_types") != ["application/octet-stream"]
        or python.get("sigstore_media_type") != "application/vnd.dev.sigstore.bundle.v0.3+json"
        or not isinstance(python.get("sigstore_certificate_identity"), str)
        or not python["sigstore_certificate_identity"]
        or python.get("sigstore_certificate_oidc_issuer") != "https://accounts.google.com"
    ):
        raise RuntimeInputError("RUNTIME_INPUT_POLICY_INVALID")
    _validate_https_url(artifact_url, _PYTHON_HOST, exact_path_suffix=artifact_name)
    _validate_https_url(sigstore_url, _PYTHON_HOST, exact_path_suffix=artifact_name + ".sigstore")
    return _Policies(
        source_bytes=source_bytes,
        runtime_lock_bytes=runtime_bytes,
        build_lock_bytes=build_bytes,
        source=source,
        runtime_packages=runtime_packages,
        build_packages=build_packages,
    )


def _metadata_url(name: str, version: str) -> str:
    encoded_name = urllib.parse.quote(name, safe="._-")
    encoded_version = urllib.parse.quote(version, safe=".!+_-")
    return f"https://{_PYPI_API_HOST}/pypi/{encoded_name}/{encoded_version}/json"


def _resolve_wheel_url(fetch: Fetch, package: Mapping[str, Any]) -> str:
    url = _metadata_url(str(package["name"]), str(package["version"]))
    response = fetch(url, _MAX_METADATA_BYTES)
    if response.status != 200 or response.url != url or len(response.body) > _MAX_METADATA_BYTES:
        raise RuntimeInputError("RUNTIME_INPUT_METADATA_INVALID")
    content_type = next(
        (value for key, value in response.headers.items() if key.casefold() == "content-type"),
        "",
    )
    if not content_type.casefold().startswith("application/json"):
        raise RuntimeInputError("RUNTIME_INPUT_METADATA_INVALID")
    metadata = _parse_object(response.body, "RUNTIME_INPUT_METADATA_INVALID")
    releases = metadata.get("urls")
    if not isinstance(releases, list):
        raise RuntimeInputError("RUNTIME_INPUT_METADATA_INVALID")
    matches: list[str] = []
    for item in releases:
        if not isinstance(item, dict) or item.get("filename") != package["filename"]:
            continue
        digests = item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        candidate = item.get("url")
        if (
            item.get("packagetype") == "bdist_wheel"
            and type(item.get("size")) is int
            and item["size"] == package["size"]
            and digest == str(package["sha256"]).removeprefix("sha256:")
            and isinstance(candidate, str)
        ):
            _validate_https_url(candidate, _PYPI_FILE_HOST, exact_path_suffix=str(package["filename"]))
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeInputError("RUNTIME_INPUT_METADATA_INVALID")
    return matches[0]


def _validate_relative_bundle_path(relative: str) -> None:
    parts = relative.split("/")
    if (
        not relative
        or len(relative) > _MAX_RELATIVE_PATH
        or len(parts) > _MAX_PATH_DEPTH
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    for part in parts:
        _validate_windows_segment(part, "RUNTIME_INPUT_BUNDLE_INVALID")


def _safe_write(root: Path, relative: str, payload: bytes) -> None:
    _validate_relative_bundle_path(relative)
    target = root / Path(relative.replace("/", os.sep))
    if target.exists() or target.is_symlink():
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    target.parent.mkdir(parents=True, exist_ok=True)
    if has_reparse_component(target.parent, root):
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        information = os.fstat(descriptor)
        if (
            information.st_size != len(payload)
            or _reliable_link_count(descriptor, information) != 1
            or int(getattr(information, "st_file_attributes", 0)) & 0x400
        ):
            raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    finally:
        os.close(descriptor)


class _RuntimeInputStaging:
    """Own a staging tree by retained identity, including commit and cleanup."""

    def __init__(self, parent: Path, destination_name: str) -> None:
        _validate_windows_segment(destination_name, "RUNTIME_INPUT_DESTINATION_INVALID")
        self.parent = Path(os.path.abspath(parent))
        self.destination_name = destination_name
        self._committed = False
        self._closed = False
        self._files: dict[str, tuple[int, tuple[int, ...]]] = {}
        self._directories: dict[str, int] = {}
        self._directory_identities: dict[str, tuple[int, int]] = {}
        self._parent_handle: int | None = None
        if os.name == "nt":
            self._parent_handle = _win_open_directory(
                self.parent, "RUNTIME_INPUT_DESTINATION_INVALID"
            )
            prefix = f".{destination_name}.jfi-"
            if len(prefix) + 16 > 240:
                self.release()
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            root_handle: int | None = None
            root_name = ""
            try:
                for _ in range(16):
                    root_name = prefix + secrets.token_hex(8)
                    try:
                        root_handle = _win_create_relative(
                            self._parent_handle,
                            root_name,
                            directory=True,
                            delete_access=True,
                            share_access=0x3,
                            failure="RUNTIME_INPUT_DESTINATION_INVALID",
                        )
                        break
                    except RuntimeInputError:
                        continue
                if root_handle is None:
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                self.path = self.parent / root_name
                identity = _win_handle_identity(root_handle, "RUNTIME_INPUT_DESTINATION_INVALID")
                if identity[3] != 1 or _win_final_path(
                    root_handle, "RUNTIME_INPUT_DESTINATION_INVALID"
                ) != self.path:
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                self._directories[""] = root_handle
                self._directory_identities[""] = identity[:2]
            except Exception:
                if root_handle is not None:
                    try:
                        _win_mark_delete(root_handle, "RUNTIME_INPUT_DESTINATION_INVALID")
                    except RuntimeInputError:
                        pass
                    _win_close_handle(root_handle)
                self.release()
                raise
        else:
            self.path = Path(
                tempfile.mkdtemp(prefix=f".{destination_name}.jfi-", dir=self.parent)
            )

    def _expected_path(self, relative: str) -> Path:
        return self.path / Path(relative.replace("/", os.sep)) if relative else self.path

    def _ensure_directory(self, relative: str) -> int:
        if os.name != "nt":
            path = self._expected_path(relative)
            path.mkdir(parents=True, exist_ok=True)
            return -1
        if relative in self._directories:
            return self._directories[relative]
        parts = relative.split("/")
        parent_relative = "/".join(parts[:-1])
        parent_handle = self._ensure_directory(parent_relative)
        handle = _win_create_relative(
            parent_handle,
            parts[-1],
            directory=True,
            failure="RUNTIME_INPUT_DESTINATION_INVALID",
        )
        try:
            identity = _win_handle_identity(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
            if identity[3] != 1 or _win_final_path(
                handle, "RUNTIME_INPUT_DESTINATION_INVALID"
            ) != self._expected_path(relative):
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            self._directories[relative] = handle
            self._directory_identities[relative] = identity[:2]
            return handle
        except Exception:
            try:
                _win_mark_delete(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
            except RuntimeInputError:
                pass
            _win_close_handle(handle)
            raise

    def write(self, relative: str, payload: bytes) -> None:
        if self._closed or self._committed or relative in self._files:
            raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
        _validate_relative_bundle_path(relative)
        parent_relative, leaf = relative.rsplit("/", 1) if "/" in relative else ("", relative)
        if os.name != "nt":
            self._ensure_directory(parent_relative)
            _safe_write(self.path, relative, payload)
            self._files[relative] = (-1, ())
            return
        parent_handle = self._ensure_directory(parent_relative)
        raw: int | None = None
        descriptor: int | None = None
        try:
            raw = _win_create_relative(
                parent_handle,
                leaf,
                directory=False,
                failure="RUNTIME_INPUT_DESTINATION_INVALID",
            )
            if _win_final_path(raw, "RUNTIME_INPUT_DESTINATION_INVALID") != self._expected_path(
                relative
            ):
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            descriptor = msvcrt.open_osfhandle(raw, os.O_RDWR | getattr(os, "O_BINARY", 0))
            raw = None
            view = memoryview(payload)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written : written + 1024 * 1024])
                if count < 1:
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                written += count
            os.fsync(descriptor)
            information = os.fstat(descriptor)
            identity = _stat_identity(descriptor, information)
            if identity[2] != len(payload) or identity[4] != 1:
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            self._files[relative] = (descriptor, identity)
            descriptor = None
        except Exception:
            if descriptor is not None:
                try:
                    _win_mark_delete(
                        msvcrt.get_osfhandle(descriptor), "RUNTIME_INPUT_DESTINATION_INVALID"
                    )
                except RuntimeInputError:
                    pass
                os.close(descriptor)
            elif raw is not None:
                try:
                    _win_mark_delete(raw, "RUNTIME_INPUT_DESTINATION_INVALID")
                except RuntimeInputError:
                    pass
                _win_close_handle(raw)
            raise

    def assert_intact(self) -> None:
        if self._closed:
            raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
        if os.name != "nt":
            if not self.path.is_dir() or has_reparse_component(self.path, self.parent):
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            return
        for relative, handle in self._directories.items():
            identity = _win_handle_identity(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
            if (
                identity[:2] != self._directory_identities[relative]
                or identity[3] != 1
                or _win_final_path(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
                != self._expected_path(relative)
            ):
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
        for relative, (descriptor, expected) in self._files.items():
            if descriptor < 0:
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            information = os.fstat(descriptor)
            if (
                _stat_identity(descriptor, information) != expected
                or _win_final_path(
                    msvcrt.get_osfhandle(descriptor), "RUNTIME_INPUT_DESTINATION_INVALID"
                )
                != self._expected_path(relative)
            ):
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")

    def _close_children_for_transition(self) -> None:
        if os.name != "nt":
            return
        for relative, (descriptor, expected) in list(self._files.items()):
            if descriptor >= 0:
                os.close(descriptor)
                self._files[relative] = (-1, expected)
        for relative in sorted(
            (item for item in self._directories if item),
            key=lambda item: (item.count("/"), len(item)),
            reverse=True,
        ):
            _win_close_handle(self._directories.pop(relative))

    def _reopen_children(self) -> None:
        if os.name != "nt":
            return
        for relative in sorted(
            (item for item in self._directory_identities if item),
            key=lambda item: (item.count("/"), len(item)),
        ):
            if relative in self._directories:
                continue
            parent_relative, leaf = (
                relative.rsplit("/", 1) if "/" in relative else ("", relative)
            )
            handle = _win_open_relative(
                self._directories[parent_relative],
                leaf,
                directory=True,
                failure="RUNTIME_INPUT_DESTINATION_INVALID",
            )
            try:
                identity = _win_handle_identity(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
                if (
                    identity[:2] != self._directory_identities[relative]
                    or identity[3] != 1
                    or _win_final_path(handle, "RUNTIME_INPUT_DESTINATION_INVALID")
                    != self._expected_path(relative)
                ):
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                self._directories[relative] = handle
            except Exception:
                _win_close_handle(handle)
                raise
        for relative, (descriptor, expected) in list(self._files.items()):
            if descriptor >= 0:
                continue
            parent_relative, leaf = (
                relative.rsplit("/", 1) if "/" in relative else ("", relative)
            )
            raw = _win_open_relative(
                self._directories[parent_relative],
                leaf,
                directory=False,
                failure="RUNTIME_INPUT_DESTINATION_INVALID",
            )
            reopened: int | None = None
            try:
                if _win_final_path(raw, "RUNTIME_INPUT_DESTINATION_INVALID") != self._expected_path(
                    relative
                ):
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                reopened = msvcrt.open_osfhandle(
                    raw, os.O_RDWR | getattr(os, "O_BINARY", 0)
                )
                raw = 0
                if _stat_identity(reopened, os.fstat(reopened)) != expected:
                    raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
                self._files[relative] = (reopened, expected)
                reopened = None
            except Exception:
                if reopened is not None:
                    os.close(reopened)
                elif raw:
                    _win_close_handle(raw)
                raise

    def commit(self, destination: Path) -> None:
        if self._committed or destination.parent != self.parent or destination.name != self.destination_name:
            raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
        self.assert_intact()
        if destination.exists() or destination.is_symlink():
            raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
        if os.name == "nt":
            if self._parent_handle is None:
                raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
            self._close_children_for_transition()
            _win_rename_relative(
                self._directories[""],
                self._parent_handle,
                destination.name,
                "RUNTIME_INPUT_DESTINATION_INVALID",
            )
        else:
            os.rename(self.path, destination)
        self.path = destination
        self._committed = True
        self._reopen_children()
        self.assert_intact()

    def cleanup(self) -> None:
        if self._closed:
            return
        failed = False
        if os.name == "nt":
            try:
                self._reopen_children()
            except RuntimeInputError:
                failed = True
            for relative, (descriptor, expected) in sorted(self._files.items(), reverse=True):
                parent_relative, leaf = (
                    relative.rsplit("/", 1) if "/" in relative else ("", relative)
                )
                anchor: int | None = None
                deleter: int | None = None
                try:
                    if parent_relative not in self._directories:
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    anchor = _win_open_relative(
                        self._directories[parent_relative], leaf, directory=False,
                        failure="RUNTIME_INPUT_STAGING_CLEANUP_FAILED",
                    )
                    anchor_identity = _win_handle_identity(
                        anchor, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED"
                    )
                    if (
                        anchor_identity
                        != (expected[0], expected[1], expected[2], expected[4])
                        or _win_final_path(anchor, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                        != self._expected_path(relative)
                    ):
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    if descriptor >= 0:
                        os.close(descriptor)
                        descriptor = -1
                    deleter = _win_open_relative(
                        self._directories[parent_relative],
                        leaf,
                        directory=False,
                        delete_access=True,
                        failure="RUNTIME_INPUT_STAGING_CLEANUP_FAILED",
                    )
                    if _win_handle_identity(
                        deleter, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED"
                    ) != anchor_identity:
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    _win_mark_delete(deleter, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                except (OSError, RuntimeInputError):
                    failed = True
                finally:
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            failed = True
                    if deleter is not None:
                        _win_close_handle(deleter)
                    if anchor is not None:
                        _win_close_handle(anchor)
            self._files.clear()
            child_directories = sorted(
                (relative for relative in self._directories if relative),
                key=lambda item: (item.count("/"), len(item)),
                reverse=True,
            )
            for relative in child_directories:
                handle = self._directories[relative]
                parent_relative, leaf = (
                    relative.rsplit("/", 1) if "/" in relative else ("", relative)
                )
                deleter = None
                try:
                    identity = _win_handle_identity(handle, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    if (
                        identity[:2] != self._directory_identities[relative]
                        or _win_final_path(handle, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                        != self._expected_path(relative)
                    ):
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    deleter = _win_open_relative(
                        self._directories[parent_relative],
                        leaf,
                        directory=True,
                        delete_access=True,
                        failure="RUNTIME_INPUT_STAGING_CLEANUP_FAILED",
                    )
                    if _win_handle_identity(
                        deleter, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED"
                    )[:2] != identity[:2]:
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    _win_mark_delete(deleter, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                except RuntimeInputError:
                    failed = True
                finally:
                    if deleter is not None:
                        _win_close_handle(deleter)
                    _win_close_handle(handle)
                    del self._directories[relative]
            root_handle = self._directories.get("")
            if root_handle is not None:
                try:
                    identity = _win_handle_identity(
                        root_handle, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED"
                    )
                    if identity[:2] != self._directory_identities[""]:
                        raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                    _win_mark_delete(root_handle, "RUNTIME_INPUT_STAGING_CLEANUP_FAILED")
                except RuntimeInputError:
                    failed = True
                _win_close_handle(root_handle)
            self._directories.clear()
            if self._parent_handle is not None:
                _win_close_handle(self._parent_handle)
                self._parent_handle = None
        else:
            for relative in sorted(self._files, reverse=True):
                try:
                    self._expected_path(relative).unlink(missing_ok=True)
                except OSError:
                    failed = True
            directories = {
                "/".join(relative.split("/")[:depth])
                for relative in self._files
                for depth in range(1, len(relative.split("/")))
            }
            for relative in sorted(
                directories, key=lambda item: (item.count("/"), len(item)), reverse=True
            ):
                try:
                    self._expected_path(relative).rmdir()
                except OSError:
                    failed = True
            try:
                self.path.rmdir()
            except OSError:
                failed = True
            self._files.clear()
        self._closed = True
        if failed:
            raise RuntimeInputError("RUNTIME_INPUT_STAGING_CLEANUP_FAILED")

    def release(self) -> None:
        if self._closed:
            return
        if os.name == "nt":
            for descriptor, _ in self._files.values():
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for handle in self._directories.values():
                _win_close_handle(handle)
            if self._parent_handle is not None:
                _win_close_handle(self._parent_handle)
                self._parent_handle = None
        self._files.clear()
        self._directories.clear()
        self._closed = True


def _inventory(
    root: Path,
    expected: Mapping[str, tuple[int, str]],
) -> list[dict[str, Any]]:
    if not expected or len(expected) > _MAX_BUNDLE_FILES:
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    expected_paths = frozenset(expected)
    expected_dirs = {
        "/".join(path.split("/")[:depth])
        for path in expected_paths
        for depth in range(1, len(path.split("/")))
    }
    for relative, (size, digest) in expected.items():
        _validate_relative_bundle_path(relative)
        if (
            type(size) is not int
            or not 0 <= size <= _MAX_FETCH_BYTES
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    expected_total = sum(size for size, _ in expected.values())
    if expected_total > _MAX_BUNDLE_BYTES:
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack: list[Path] = [root]
    scanned_entries = 0
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID") from exc
        for entry in entries:
            scanned_entries += 1
            if scanned_entries > len(expected_paths) + len(expected_dirs):
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            _validate_relative_bundle_path(relative)
            try:
                information = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID") from exc
            if entry.is_symlink() or int(getattr(information, "st_file_attributes", 0)) & 0x400:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            if entry.is_dir(follow_symlinks=False):
                if relative not in expected_dirs:
                    raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
                stack.append(path)
                continue
            if not entry.is_file(follow_symlinks=False) or relative not in expected_paths:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            folded = relative.casefold()
            if folded in seen:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            seen.add(folded)
            expected_size, expected_digest = expected[relative]
            if information.st_size != expected_size:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            digest = hashlib.sha256()
            total = 0
            try:
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (
                        opened.st_size != expected_size
                        or _reliable_link_count(stream.fileno(), opened) != 1
                        or int(getattr(opened, "st_file_attributes", 0)) & 0x400
                    ):
                        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > expected_size:
                            raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
            except OSError as exc:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID") from exc
            identity = lambda item: (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
            )
            if total != expected_size or identity(opened) != identity(after):
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            actual_digest = "sha256:" + digest.hexdigest()
            if actual_digest != expected_digest:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            records.append({"path": relative, "bytes": total, "sha256": actual_digest})
    if seen != {path.casefold() for path in expected_paths}:
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    return sorted(records, key=lambda item: str(item["path"]).casefold())


def _expected_file_map(policies: _Policies) -> dict[str, tuple[int, str]]:
    python = policies.source["python"]
    expected = {
        "policy/windows-runtime-source.json": (len(policies.source_bytes), sha256_bytes(policies.source_bytes)),
        "locks/windows-cp313-runtime.lock": (
            len(policies.runtime_lock_bytes),
            sha256_bytes(policies.runtime_lock_bytes),
        ),
        "locks/windows-cp313-build.lock": (len(policies.build_lock_bytes), sha256_bytes(policies.build_lock_bytes)),
        f"python/{python['artifact_name']}": (python["artifact_bytes"], python["artifact_sha256"]),
        f"python/{python['artifact_name']}.sigstore": (
            python["sigstore_bundle_bytes"],
            python["sigstore_bundle_sha256"],
        ),
    }
    for package in policies.runtime_packages + policies.build_packages:
        expected[f"wheelhouse/{package['filename']}"] = (package["size"], package["sha256"])
    return expected


def _manifest(policies: _Policies, inventory: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    python = policies.source["python"]
    return {
        "format": "JOBFLOW_RUNTIME_INPUT_BUNDLE_V1",
        "schema_version": 1,
        "platform": "windows-x64",
        "architecture": "AMD64",
        "python_version": python["version"],
        "source_policy_sha256": sha256_bytes(policies.source_bytes),
        "runtime_lock_sha256": _portable_text_sha256(policies.runtime_lock_bytes),
        "build_lock_sha256": _portable_text_sha256(policies.build_lock_bytes),
        "network_policy": {
            "explicit_opt_in_required": True,
            "redirects_allowed": False,
            "proxy_environment_used": False,
            "allowed_hosts": [_PYTHON_HOST, _PYPI_API_HOST, _PYPI_FILE_HOST],
            "expected_request_count": 2
            + 2 * (len(policies.runtime_packages) + len(policies.build_packages)),
        },
        "files": list(inventory),
    }


def _read_bounded_regular(path: Path, root: Path, maximum: int) -> bytes:
    if (
        type(maximum) is not int
        or maximum < 1
        or not path.is_file()
        or has_reparse_component(path, root)
    ):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    try:
        before = path.stat(follow_symlinks=False)
        if not 1 <= before.st_size <= maximum:
            raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _reliable_link_count(stream.fileno(), opened) != 1:
                raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    except RuntimeInputError:
        raise
    except OSError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID") from exc
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
    )
    if (
        len(payload) != before.st_size
        or identity(before) != identity(opened)
        or identity(opened) != identity(after)
    ):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    return payload


def verify_runtime_inputs(project: Path, bundle: Path) -> dict[str, Any]:
    policies = _load_policies(project)
    try:
        bundle = bundle.resolve(strict=True)
    except OSError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID") from exc
    if not bundle.is_dir() or has_reparse_component(bundle):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    manifest_path = bundle / "runtime-inputs.json"
    if not manifest_path.is_file() or has_reparse_component(manifest_path, bundle):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    manifest_bytes = _read_bounded_regular(manifest_path, bundle, _MAX_MANIFEST_BYTES)
    manifest = _parse_object(manifest_bytes, "RUNTIME_INPUT_BUNDLE_INVALID")
    expected_map = _expected_file_map(policies)
    full_expected = {
        **expected_map,
        "runtime-inputs.json": (len(manifest_bytes), sha256_bytes(manifest_bytes)),
    }
    full_inventory = _inventory(bundle, full_expected)
    actual = [item for item in full_inventory if item["path"] != "runtime-inputs.json"]
    actual_map = {str(item["path"]): (item["bytes"], item["sha256"]) for item in actual}
    expected_manifest = canonical_json(_manifest(policies, actual)) + b"\n"
    if (
        actual_map != expected_map
        or manifest_bytes != expected_manifest
        or manifest != _manifest(policies, actual)
    ):
        raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
    return {
        "status": "PASS",
        "format": manifest["format"],
        "file_count": len(actual),
        "tree_sha256": sha256_bytes(canonical_json(actual)),
        "bundle_sha256": sha256_bytes(canonical_json(full_inventory)),
        "recruitment_external_actions": 0,
    }


def acquire_runtime_inputs(
    project: Path,
    destination: Path,
    *,
    allow_network: bool,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    if allow_network is not True:
        raise RuntimeInputError("RUNTIME_INPUT_NETWORK_OPT_IN_REQUIRED")
    project = project.resolve(strict=True)
    policies = _load_policies(project)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    try:
        parent = destination.parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID") from exc
    if not parent.is_dir() or has_reparse_component(parent):
        raise RuntimeInputError("RUNTIME_INPUT_DESTINATION_INVALID")
    simulated_fetch = fetch is not None
    transport = fetch or _default_fetch
    request_hosts: list[str] = []

    def tracked_fetch(url: str, maximum_bytes: int) -> FetchResponse:
        try:
            host = urllib.parse.urlsplit(url).hostname
        except ValueError as exc:
            raise RuntimeInputError("RUNTIME_INPUT_URL_INVALID") from exc
        if not isinstance(host, str):
            raise RuntimeInputError("RUNTIME_INPUT_URL_INVALID")
        request_hosts.append(host)
        return transport(url, maximum_bytes)

    staging_guard = _RuntimeInputStaging(parent, destination.name)
    staging = staging_guard.path
    try:
        python = policies.source["python"]
        artifact = _fetch_exact(
            tracked_fetch,
            python["artifact_url"],
            host=_PYTHON_HOST,
            expected_bytes=python["artifact_bytes"],
            expected_sha256=python["artifact_sha256"],
            filename=python["artifact_name"],
        )
        sigstore = _fetch_exact(
            tracked_fetch,
            python["sigstore_bundle_url"],
            host=_PYTHON_HOST,
            expected_bytes=python["sigstore_bundle_bytes"],
            expected_sha256=python["sigstore_bundle_sha256"],
            filename=python["artifact_name"] + ".sigstore",
            media_types=python["sigstore_transport_media_types"],
        )
        sigstore_value = _parse_object(sigstore, "RUNTIME_INPUT_SIGSTORE_INVALID")
        if sigstore_value.get("mediaType") != python["sigstore_media_type"]:
            raise RuntimeInputError("RUNTIME_INPUT_SIGSTORE_INVALID")
        staging_guard.write("policy/windows-runtime-source.json", policies.source_bytes)
        staging_guard.write("locks/windows-cp313-runtime.lock", policies.runtime_lock_bytes)
        staging_guard.write("locks/windows-cp313-build.lock", policies.build_lock_bytes)
        staging_guard.write(f"python/{python['artifact_name']}", artifact)
        staging_guard.write(f"python/{python['artifact_name']}.sigstore", sigstore)
        for package in policies.runtime_packages + policies.build_packages:
            wheel_url = _resolve_wheel_url(tracked_fetch, package)
            wheel = _fetch_exact(
                tracked_fetch,
                wheel_url,
                host=_PYPI_FILE_HOST,
                expected_bytes=package["size"],
                expected_sha256=package["sha256"],
                filename=package["filename"],
            )
            staging_guard.write(f"wheelhouse/{package['filename']}", wheel)
        staging_guard.assert_intact()
        expected_map = _expected_file_map(policies)
        inventory = _inventory(staging, expected_map)
        actual_map = {str(item["path"]): (item["bytes"], item["sha256"]) for item in inventory}
        if actual_map != expected_map:
            raise RuntimeInputError("RUNTIME_INPUT_BUNDLE_INVALID")
        staging_guard.write(
            "runtime-inputs.json", canonical_json(_manifest(policies, inventory)) + b"\n"
        )
        staging_guard.assert_intact()
        verify_runtime_inputs(project, staging)
        expected_requests = 2 + 2 * (len(policies.runtime_packages) + len(policies.build_packages))
        if len(request_hosts) != expected_requests:
            raise RuntimeInputError("RUNTIME_INPUT_NETWORK_EVIDENCE_INVALID")
        staging_guard.commit(destination)
        result = verify_runtime_inputs(project, destination)
        staging_guard.assert_intact()
        response = {
            **result,
            "network_opt_in": True,
            "engineering_network_used": not simulated_fetch,
            "network_transport": "SIMULATED_FETCH" if simulated_fetch else "DEFAULT_PINNED_HTTPS",
            "network_request_count": len(request_hosts),
            "network_hosts": sorted(set(request_hosts)),
        }
        staging_guard.release()
        return response
    except Exception as original:
        try:
            staging_guard.cleanup()
        except RuntimeInputError as cleanup_error:
            raise cleanup_error from original
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or verify pinned JobFlow Windows runtime inputs.")
    parser.add_argument("--project", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire = subparsers.add_parser("acquire", help="Download the exact pinned public inputs.")
    acquire.add_argument("--destination", required=True, type=Path)
    acquire.add_argument("--allow-network", action="store_true")
    verify = subparsers.add_parser("verify", help="Verify an existing input bundle without network.")
    verify.add_argument("--bundle", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = args.project.resolve(strict=True) if args.project is not None else project_root()
        if args.command == "acquire":
            result = acquire_runtime_inputs(
                project,
                args.destination,
                allow_network=args.allow_network,
            )
        else:
            result = verify_runtime_inputs(project, args.bundle)
    except RuntimeInputError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                {"status": "FAIL", "reason": "RUNTIME_INPUT_OPERATION_FAILED"},
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
