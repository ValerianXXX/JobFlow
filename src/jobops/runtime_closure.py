from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator

from .errors import JobOpsError
from .runtime_schema import validate_named
from .util import canonical_json


_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _windows_stream_names(path: Path) -> tuple[str, ...]:
    if os.name != "nt":
        return ("::$DATA",)

    class FindStreamData(ctypes.Structure):
        _fields_ = [("stream_size", ctypes.c_longlong), ("stream_name", wintypes.WCHAR * 296)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(FindStreamData), wintypes.DWORD]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(FindStreamData)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL

    data = FindStreamData()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        if error == 38:  # ERROR_HANDLE_EOF: the file system exposes no streams.
            return ()
        _fail("RUNTIME_CLOSURE_STREAM_SCAN_FAILED", "A runtime file stream could not be inspected.", error=error)
    names: list[str] = []
    try:
        names.append(str(data.stream_name))
        while find_next(handle, ctypes.byref(data)):
            names.append(str(data.stream_name))
        error = ctypes.get_last_error()
        if error != 38:
            _fail("RUNTIME_CLOSURE_STREAM_SCAN_FAILED", "A runtime file stream scan did not finish cleanly.", error=error)
    finally:
        find_close(handle)
    return tuple(names)


def _fail(code: str, message: str, **details: object) -> None:
    raise JobOpsError(code, message, **details)


def normalize_runtime_path(value: str) -> str:
    """Return one canonical archive/runtime path or fail closed.

    Runtime manifests use NFC-normalized POSIX paths so Windows case aliases,
    alternate data streams, reserved device names, traversal and separator
    ambiguity cannot describe two different files.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 768
        or value != unicodedata.normalize("NFC", value)
    ):
        _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths must be non-empty NFC strings.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        _fail(
            "RUNTIME_CLOSURE_PATH_INVALID",
            "Runtime paths must use the cross-verifier ASCII path subset.",
            path=value,
        )
    if "\\" in value or ":" in value or value.startswith("/") or value.endswith("/"):
        _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths must be canonical relative POSIX paths.", path=value)
    parsed = PurePosixPath(value)
    parts = parsed.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths cannot traverse or contain dot components.", path=value)
    for part in parts:
        if (
            len(part) > 255
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES
        ):
            _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths cannot use Windows aliases or device names.", path=value)
        if any(ord(character) < 32 or ord(character) > 126 or character in '\"<>|?*' for character in part):
            _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths cannot contain control characters.", path=value)
    canonical = "/".join(parts)
    if canonical != value:
        _fail("RUNTIME_CLOSURE_PATH_INVALID", "Runtime paths must already be canonical.", path=value)
    return canonical


def _assert_safe_root(root: Path) -> Path:
    absolute = Path(os.path.abspath(root))
    if not absolute.is_dir() or absolute.is_symlink():
        _fail("RUNTIME_CLOSURE_ROOT_INVALID", "The runtime root must be an existing ordinary directory.")
    try:
        attributes = getattr(absolute.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError as exc:
        _fail("RUNTIME_CLOSURE_ROOT_INVALID", "The runtime root could not be inspected.", error=type(exc).__name__)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        _fail("RUNTIME_CLOSURE_REPARSE_REJECTED", "The runtime root cannot be a reparse point.")
    return absolute


def _assert_safe_entry(path: Path, relative: str, *, directory: bool) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "A runtime entry could not be inspected.", path=relative, error=type(exc).__name__)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if path.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        _fail("RUNTIME_CLOSURE_REPARSE_REJECTED", "Runtime entries cannot be links or reparse points.", path=relative)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_ENCRYPTED", 0x4000):
        _fail("RUNTIME_CLOSURE_ENCRYPTED_REJECTED", "Runtime entries cannot use filesystem encryption.", path=relative)
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "Runtime entries must be ordinary files or directories.", path=relative)
    if not directory and getattr(metadata, "st_nlink", 1) != 1:
        _fail("RUNTIME_CLOSURE_HARDLINK_REJECTED", "Runtime files must have exactly one hard link.", path=relative)
    if not directory and any(name != "::$DATA" for name in _windows_stream_names(path)):
        _fail("RUNTIME_CLOSURE_ADS_REJECTED", "Runtime files cannot contain alternate data streams.", path=relative)
    return metadata


@contextmanager
def _open_locked_runtime_file(path: Path, relative: str) -> Iterator[BinaryIO]:
    """Open one ordinary file while denying replacement and writes on Windows."""

    if os.name != "nt":
        try:
            with path.open("rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_nlink", 1) != 1:
                    _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "Runtime files must be ordinary single-link files.", path=relative)
                yield source
        except OSError as exc:
            _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "A runtime file could not be locked.", path=relative, error=type(exc).__name__)
        return

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ only: deny writes, deletes and replacement.
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x08000000,  # OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        _fail(
            "RUNTIME_CLOSURE_ENTRY_INVALID",
            "A runtime file could not be locked.",
            path=relative,
            error=ctypes.get_last_error(),
        )
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        handle = None
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = None
            metadata = os.fstat(source.fileno())
            attributes = getattr(metadata, "st_file_attributes", 0)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or getattr(metadata, "st_nlink", 1) != 1
                or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "Runtime files must be ordinary single-link files.", path=relative)
            if any(name != "::$DATA" for name in _windows_stream_names(path)):
                _fail("RUNTIME_CLOSURE_ADS_REJECTED", "Runtime files cannot contain alternate data streams.", path=relative)
            yield source
    except OSError as exc:
        _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "A runtime file could not be locked.", path=relative, error=type(exc).__name__)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif handle is not None:
            close_handle(handle)


def _read_locked_bytes(path: Path, relative: str, *, maximum: int) -> bytes:
    with _open_locked_runtime_file(path, relative) as source:
        before = os.fstat(source.fileno())
        if before.st_size < 1 or before.st_size > maximum:
            _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "A runtime file has an invalid size.", path=relative)
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
        if (
            len(raw) != before.st_size
            or len(raw) > maximum
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            _fail("RUNTIME_CLOSURE_ENTRY_CHANGED", "A runtime file changed during verification.", path=relative)
        return raw


def inventory_runtime_tree(root: Path, *, excluded: Iterable[str] = ("runtime-closure.json",)) -> list[dict[str, Any]]:
    absolute = _assert_safe_root(root)
    excluded_set = {normalize_runtime_path(item).casefold() for item in excluded}
    discovered: list[tuple[str, Path]] = []
    aliases: set[str] = set()

    def visit(directory: Path, prefix: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: unicodedata.normalize("NFC", item.name).casefold())
        except OSError as exc:
            _fail("RUNTIME_CLOSURE_SCAN_FAILED", "The runtime tree could not be enumerated.", error=type(exc).__name__)
        for entry in entries:
            relative = normalize_runtime_path("/".join((*prefix, entry.name)))
            alias = relative.casefold()
            if alias in aliases:
                _fail("RUNTIME_CLOSURE_PATH_COLLISION", "Runtime paths collide under Windows case semantics.", path=relative)
            aliases.add(alias)
            candidate = absolute.joinpath(*PurePosixPath(relative).parts)
            if entry.is_dir(follow_symlinks=False):
                _assert_safe_entry(candidate, relative, directory=True)
                visit(candidate, (*prefix, entry.name))
            elif entry.is_file(follow_symlinks=False):
                _assert_safe_entry(candidate, relative, directory=False)
                if alias not in excluded_set:
                    discovered.append((relative, candidate))
            else:
                _fail("RUNTIME_CLOSURE_ENTRY_INVALID", "Runtime trees cannot contain special entries.", path=relative)

    visit(absolute, ())
    records: list[dict[str, Any]] = []
    for relative, path in discovered:
        digest = hashlib.sha256()
        with _open_locked_runtime_file(path, relative) as source:
            before = os.fstat(source.fileno())
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(source.fileno())
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                _fail("RUNTIME_CLOSURE_ENTRY_CHANGED", "A runtime file changed during verification.", path=relative)
        records.append({"path": relative, "size": after.st_size, "sha256": "sha256:" + digest.hexdigest()})
    return records


def runtime_tree_digest(records: list[dict[str, Any]]) -> str:
    material = canonical_json(records)
    return "sha256:" + hashlib.sha256(material).hexdigest()


def verify_runtime_closure(
    root: Path,
    manifest_path: Path | None = None,
    *,
    schema_dir: Path | None = None,
    require_attested: bool = False,
) -> dict[str, Any]:
    absolute = _assert_safe_root(root)
    manifest = manifest_path or absolute / "runtime-closure.json"
    try:
        manifest_absolute = Path(os.path.abspath(manifest))
        manifest_absolute.relative_to(absolute)
    except (OSError, ValueError):
        _fail("RUNTIME_CLOSURE_MANIFEST_OUTSIDE_ROOT", "The runtime closure manifest must be inside the runtime root.")
    manifest_relative = manifest_absolute.relative_to(absolute).as_posix()
    _assert_safe_entry(manifest_absolute, manifest_relative, directory=False)
    try:
        import json

        value = json.loads(_read_locked_bytes(manifest_absolute, manifest_relative, maximum=16 * 1024 * 1024).decode("utf-8"))
    except Exception as exc:
        if isinstance(exc, JobOpsError):
            raise
        _fail("RUNTIME_CLOSURE_MANIFEST_INVALID", "The runtime closure manifest is not valid JSON.", error=type(exc).__name__)
    schemas = schema_dir or absolute / "schemas"
    validate_named("runtime-closure", value, schemas)
    if require_attested:
        _fail(
            "RUNTIME_CLOSURE_UNATTESTED",
            "A structural runtime closure cannot attest itself; a pinned external signature is required.",
        )

    records = inventory_runtime_tree(
        absolute,
        excluded=(manifest_absolute.relative_to(absolute).as_posix(),),
    )
    expected = value["files"]
    if records != expected:
        _fail("RUNTIME_CLOSURE_INVENTORY_MISMATCH", "The expanded runtime does not match its closure manifest.")
    if value["file_count"] != len(records):
        _fail("RUNTIME_CLOSURE_COUNT_MISMATCH", "The runtime file count does not match its closure manifest.")
    if value["total_bytes"] != sum(record["size"] for record in records):
        _fail("RUNTIME_CLOSURE_SIZE_MISMATCH", "The runtime byte count does not match its closure manifest.")
    if value["tree_sha256"] != runtime_tree_digest(records):
        _fail("RUNTIME_CLOSURE_DIGEST_MISMATCH", "The runtime tree digest does not match its closure manifest.")

    paths = {record["path"] for record in records}
    required_layout = {
        ".jobops-root",
        "runtime/python.exe",
        "runtime/python313.dll",
        "runtime/python313._pth",
        "runtime/python313.zip",
        "app/jobops/__init__.py",
        "app/jobops/cli.py",
        "app/jobops/runtime_health.py",
        "config/windows-cp313-build.lock",
        "config/windows-cp313-runtime.lock",
    }
    if not required_layout.issubset(paths):
        _fail("RUNTIME_CLOSURE_LAYOUT_MISSING", "The complete runtime launcher layout is incomplete.")
    pth_relative = normalize_runtime_path(value["layout"]["python_pth"])
    pth_path = absolute.joinpath(*PurePosixPath(pth_relative).parts)
    try:
        pth_bytes = _read_locked_bytes(pth_path, pth_relative, maximum=1024)
    except JobOpsError:
        raise
    if pth_bytes != b"python313.zip\n.\n../app\n":
        _fail("RUNTIME_CLOSURE_PTH_INVALID", "The embedded Python path file is not isolated.")
    return value
