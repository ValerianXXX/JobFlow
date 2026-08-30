from __future__ import annotations

import sys as _sys


_WRITE_OPEN_FLAG_MASK = 0x001 | 0x002 | 0x008 | 0x100 | 0x200 | 0x400
_DENIED_AUDIT_EVENTS = {
        "os.chmod",
        "os.chown",
        "os.link",
        "os.mkdir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.symlink",
        "os.system",
        "os.truncate",
        "os.utime",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.move",
        "shutil.rmtree",
}


def _runtime_health_audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if isinstance(mode, str) and any(token in mode for token in ("w", "a", "x", "+")):
            raise RuntimeError("JOBFLOW_RUNTIME_HEALTH_AUDIT_DENIED")
        if isinstance(flags, int) and flags & _WRITE_OPEN_FLAG_MASK:
            raise RuntimeError("JOBFLOW_RUNTIME_HEALTH_AUDIT_DENIED")
    if event == "ctypes.dlopen":
        library = args[0] if args else None
        if not isinstance(library, str) or library.lower() not in {"kernel32", "kernel32.dll"}:
            raise RuntimeError("JOBFLOW_RUNTIME_HEALTH_AUDIT_DENIED")
        return
    if (
        event in _DENIED_AUDIT_EVENTS
        or event.startswith("socket.")
        or event.startswith("subprocess.")
        or event.startswith("os.spawn")
        or event.startswith("os.exec")
    ):
        raise RuntimeError("JOBFLOW_RUNTIME_HEALTH_AUDIT_DENIED")


_sys.addaudithook(_runtime_health_audit)


import json
import os
from pathlib import Path
import sqlite3
import stat


SUCCESS_BYTES = b"JOBFLOW_RUNTIME_HEALTH_OK_V1\n"
FAILURE_BYTES = b"JOBFLOW_RUNTIME_HEALTH_FAILED_V1\n"
CURRENT_DATABASE_SCHEMA = "15"
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024

_REQUIRED_CONFIG_FILES = frozenset(
    {
        "browser-companion-stores.json",
        "github-release.json",
        "knowledge-sources.json",
        "policy.json",
        "python-support-policy.json",
        "public-release.json",
        "release-toolchain.json",
        "update-channel.json",
        "windows-runtime-source.json",
        "windows-cp313-build.lock",
        "windows-cp313-runtime.lock",
    }
)
_REQUIRED_SCHEMA_FILES = frozenset(
    {
        "application-readiness.schema.json",
        "candidate-profile.schema.json",
        "external-claim-set.schema.json",
        "installed-pointer-v2.schema.json",
        "onboarding-answer-bank.schema.json",
        "onboarding-completion.schema.json",
        "python-support-policy.schema.json",
        "release-readiness.schema.json",
        "resume-tailoring-manifest.schema.json",
        "review-packet.schema.json",
        "runtime-closure.schema.json",
        "update-manifest-v2.schema.json",
    }
)
_EXPECTED_RUNTIME_PACKAGES = frozenset(
    {
        "cffi",
        "charset-normalizer",
        "cryptography",
        "lxml",
        "packaging",
        "pdfminer.six",
        "pdfplumber",
        "pillow",
        "pycparser",
        "pypdf",
        "pypdfium2",
        "python-docx",
        "typing-extensions",
    }
)
_PACKAGE_SENTINELS = {
    "cffi": ("app/cffi/__init__.py", "app/_cffi_backend.pyd"),
    "charset-normalizer": ("app/charset_normalizer/__init__.py",),
    "cryptography": ("app/cryptography/__init__.py",),
    "lxml": ("app/lxml/__init__.py",),
    "packaging": ("app/packaging/__init__.py",),
    "pdfminer.six": ("app/pdfminer/__init__.py",),
    "pdfplumber": ("app/pdfplumber/__init__.py",),
    "pillow": ("app/PIL/__init__.py",),
    "pycparser": ("app/pycparser/__init__.py",),
    "pypdf": ("app/pypdf/__init__.py",),
    "pypdfium2": ("app/pypdfium2/__init__.py",),
    "python-docx": ("app/docx/__init__.py",),
    "typing-extensions": ("app/typing_extensions.py",),
}


class _HealthFailure(Exception):
    pass


def _fail() -> None:
    raise _HealthFailure()


def _absolute_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        _fail()
    candidate = Path(raw)
    if not candidate.is_absolute():
        _fail()
    absolute = Path(os.path.abspath(raw))
    drive, tail = os.path.splitdrive(str(absolute))
    del drive
    if ":" in tail:
        _fail()
    return absolute


def _is_reparse_or_link(path: Path) -> bool:
    item = os.lstat(path)
    attributes = int(getattr(item, "st_file_attributes", 0))
    return stat.S_ISLNK(item.st_mode) or bool(attributes & 0x400)


def _assert_existing_chain_is_ordinary(path: Path) -> None:
    absolute = _absolute_path(str(path))
    parts = absolute.parts
    if not parts:
        _fail()
    cursor = Path(parts[0])
    if os.path.lexists(cursor) and _is_reparse_or_link(cursor):
        _fail()
    for part in parts[1:]:
        cursor = cursor / part
        if os.path.lexists(cursor) and _is_reparse_or_link(cursor):
            _fail()


def _assert_directory(path: Path) -> None:
    _assert_existing_chain_is_ordinary(path)
    if not os.path.isdir(path) or os.path.islink(path):
        _fail()


def _assert_no_alternate_streams(path: Path) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class _StreamData(ctypes.Structure):
        _fields_ = (("size", ctypes.c_longlong), ("name", wintypes.WCHAR * (260 + 36)))

    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = (wintypes.LPCWSTR, wintypes.INT, ctypes.POINTER(_StreamData), wintypes.DWORD)
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_StreamData))
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = (wintypes.HANDLE,)
    find_close.restype = wintypes.BOOL

    record = _StreamData()
    handle = find_first(str(path), 0, ctypes.byref(record), 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        _fail()
    try:
        while True:
            if record.name != "::$DATA":
                _fail()
            if not find_next(handle, ctypes.byref(record)):
                error = ctypes.get_last_error()
                if error != 38:
                    _fail()
                break
    finally:
        if not find_close(handle):
            _fail()


def _assert_file(path: Path, *, single_link: bool = True) -> os.stat_result:
    _assert_existing_chain_is_ordinary(path)
    if not os.path.isfile(path) or os.path.islink(path):
        _fail()
    item = os.lstat(path)
    if not stat.S_ISREG(item.st_mode):
        _fail()
    if single_link and int(item.st_nlink) != 1:
        _fail()
    _assert_no_alternate_streams(path)
    return item


def _file_identity(item: os.stat_result) -> tuple[int, ...]:
    return (
        int(item.st_dev),
        int(item.st_ino),
        int(item.st_mode),
        int(item.st_nlink),
        int(item.st_size),
        int(item.st_mtime_ns),
        int(item.st_ctime_ns),
        int(getattr(item, "st_file_attributes", 0)),
        int(getattr(item, "st_reparse_tag", 0)),
    )


def _assert_same_file(path: Path, expected: tuple[int, ...]) -> None:
    if _file_identity(_assert_file(path)) != expected:
        _fail()


def _assert_stable_file(path: Path) -> None:
    before = _file_identity(_assert_file(path))
    _assert_same_file(path, before)


def _read_bounded(path: Path, *, maximum: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    item = _assert_file(path)
    before = _file_identity(item)
    if item.st_size < 1 or item.st_size > maximum:
        _fail()
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    if len(value) != item.st_size or len(value) > maximum:
        _fail()
    _assert_same_file(path, before)
    return value


def _read_json_object(path: Path, *, maximum: int = MAX_CONTROL_FILE_BYTES) -> dict[str, object]:
    try:
        value = json.loads(_read_bounded(path, maximum=maximum).decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        _fail()
    if not isinstance(value, dict):
        _fail()
    return value


def _validate_runtime_marker(runtime_root: Path) -> None:
    path = runtime_root / ".jobops-root"
    before = _file_identity(_assert_file(path))
    marker = _read_bounded(path, maximum=128)
    if marker.strip() != b"jobops-root-v1":
        _fail()
    _assert_same_file(path, before)


def _validate_policy(value: dict[str, object]) -> None:
    required = {
        "schema_version": 3,
        "user_present_browser_assist_enabled": True,
        "external_actions_enabled": False,
        "final_submit_implementation_present": False,
        "unattended_submission_enabled": False,
        "account_creation_enabled": False,
        "submission_unknown_auto_retry": False,
        "phase_5_6_authorization": "PER_APPLICATION_USER_PRESENT_PREFILL_UPLOAD_AND_SCOPED_FORWARD_NAVIGATION_ONLY",
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        _fail()
    bounds = value.get("pending_approval_limit_bounds")
    if not isinstance(bounds, dict) or bounds.get("minimum") != 1 or bounds.get("maximum") != 1000:
        _fail()


def _validate_runtime_source(value: dict[str, object]) -> None:
    if (
        value.get("schema_version") != 1
        or value.get("status") != "PINNED_OFFICIAL_SOURCE"
        or value.get("platform") != "windows-x64"
    ):
        _fail()
    isolation = value.get("isolation")
    if not isinstance(isolation, dict):
        _fail()
    if isolation.get("network_during_assembly") is not False or isolation.get("network_during_smoke_test") is not False:
        _fail()
    if isolation.get("import_site") is not False or isolation.get("end_user_pip") is not False:
        _fail()


def _validate_python_support_policy(
    value: dict[str, object],
    runtime_source: dict[str, object],
    runtime_lock: dict[str, object],
) -> None:
    if (
        value.get("schema_version") != 1
        or value.get("status") != "ROLE_SPLIT_INTENTIONAL"
        or value.get("source_package")
        != {
            "requires_python": ">=3.11,<3.14",
            "tested_minors": ["3.11", "3.12", "3.13"],
        }
        or value.get("legacy_windows_source_installer")
        != {
            "allowed_minors": ["3.11", "3.12"],
            "distribution_policy": "PYTHON_SOFTWARE_FOUNDATION_SIGNED_SYSTEM_INSTALLATION",
        }
    ):
        _fail()
    complete = value.get("production_complete_windows_runtime")
    source_python = runtime_source.get("python")
    if (
        not isinstance(complete, dict)
        or complete
        != {
            "exact_version": "3.13.15",
            "python_tag": "cp313",
            "runtime_tag": "python313",
            "architecture": "AMD64",
            "source_policy": "config/windows-runtime-source.json",
            "runtime_lock": "config/windows-cp313-runtime.lock",
            "build_lock": "config/windows-cp313-build.lock",
        }
        or not isinstance(source_python, dict)
        or source_python.get("version") != complete["exact_version"]
        or runtime_lock.get("python_tag") != complete["python_tag"]
    ):
        _fail()


def _validate_update_channel(value: dict[str, object]) -> None:
    if value.get("schema_version") != 1 or value.get("product") != "JobFlow" or value.get("channel") != "stable":
        _fail()
    signature = value.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "RSA-PKCS1-v1_5-SHA256":
        _fail()


def _validate_runtime_lock(value: dict[str, object], runtime_root: Path) -> None:
    if (
        value.get("schema_version") != 1
        or value.get("lock_type") != "runtime-wheelhouse"
        or value.get("python_tag") != "cp313"
        or value.get("platform") != "win_amd64"
        or value.get("only_binary") is not True
    ):
        _fail()
    packages = value.get("packages")
    if not isinstance(packages, list):
        _fail()
    names: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            _fail()
        name = package.get("name")
        filename = package.get("filename")
        digest = package.get("sha256")
        if not isinstance(name, str) or not isinstance(filename, str) or not isinstance(digest, str):
            _fail()
        if Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename:
            _fail()
        if not isinstance(package.get("version"), str) or not package.get("version"):
            _fail()
        if type(package.get("size")) is not int or int(package["size"]) < 1:
            _fail()
        if len(digest) != 71 or not digest.startswith("sha256:"):
            _fail()
        try:
            int(digest[7:], 16)
        except ValueError:
            _fail()
        names.append(name)
    if len(names) != len(set(names)) or frozenset(names) != _EXPECTED_RUNTIME_PACKAGES:
        _fail()
    for package_name in sorted(_EXPECTED_RUNTIME_PACKAGES):
        for relative in _PACKAGE_SENTINELS[package_name]:
            _assert_stable_file(runtime_root / Path(relative))


def _validate_runtime(runtime_root: Path) -> None:
    _assert_directory(runtime_root)
    if not os.path.samefile(Path.cwd(), runtime_root):
        _fail()
    _validate_runtime_marker(runtime_root)

    expected_module = runtime_root / "app" / "jobops" / "runtime_health.py"
    _assert_stable_file(expected_module)
    _assert_stable_file(runtime_root / "app" / "jobops" / "__init__.py")
    expected_python = runtime_root / "runtime" / "python.exe"
    _assert_stable_file(expected_python)
    _assert_stable_file(runtime_root / "runtime" / "python313.zip")
    pth_path = runtime_root / "runtime" / "python313._pth"
    pth_before = _file_identity(_assert_file(pth_path))
    pth = _read_bounded(pth_path, maximum=128)
    if pth != b"python313.zip\n.\n../app\n":
        _fail()
    _assert_same_file(pth_path, pth_before)
    if not os.path.samefile(Path(__file__), expected_module):
        _fail()
    if not os.path.samefile(Path(_sys.executable), expected_python):
        _fail()

    config_root = runtime_root / "config"
    schema_root = runtime_root / "schemas"
    _assert_directory(config_root)
    _assert_directory(schema_root)
    present_configs = {item.name for item in config_root.iterdir() if item.is_file()}
    if not _REQUIRED_CONFIG_FILES.issubset(present_configs):
        _fail()
    config_values: dict[str, dict[str, object]] = {}
    config_identities: dict[str, tuple[int, ...]] = {}
    for name in sorted(_REQUIRED_CONFIG_FILES):
        config_path = config_root / name
        config_identities[name] = _file_identity(_assert_file(config_path))
        config_values[name] = _read_json_object(config_path)
    _validate_policy(config_values["policy.json"])
    _validate_runtime_source(config_values["windows-runtime-source.json"])
    _validate_python_support_policy(
        config_values["python-support-policy.json"],
        config_values["windows-runtime-source.json"],
        config_values["windows-cp313-runtime.lock"],
    )
    _validate_update_channel(config_values["update-channel.json"])
    _validate_runtime_lock(config_values["windows-cp313-runtime.lock"], runtime_root)
    for name in sorted(_REQUIRED_CONFIG_FILES):
        _assert_same_file(config_root / name, config_identities[name])

    present_schemas = {item.name for item in schema_root.iterdir() if item.is_file() and item.name.endswith(".schema.json")}
    if not _REQUIRED_SCHEMA_FILES.issubset(present_schemas):
        _fail()
    for name in sorted(present_schemas):
        schema_path = schema_root / name
        schema_before = _file_identity(_assert_file(schema_path))
        schema = _read_json_object(schema_path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or schema.get("type") != "object":
            _fail()
        _assert_same_file(schema_path, schema_before)


def _validate_data_marker(data_root: Path) -> None:
    path = data_root / ".jobflow-data-root"
    before = _file_identity(_assert_file(path))
    marker = _read_json_object(path, maximum=4096)
    if marker != {"schema_version": 1, "kind": "JOBFLOW_RUNTIME_DATA"}:
        _fail()
    _assert_same_file(path, before)


def _validate_queue_settings(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT singleton_id,pending_approval_limit,continue_after_awaiting_approval,updated_at "
        "FROM queue_settings ORDER BY singleton_id"
    ).fetchall()
    if len(rows) != 1:
        _fail()
    singleton_id, limit, continue_flag, updated_at = rows[0]
    if type(singleton_id) is not int or singleton_id != 1:
        _fail()
    if type(limit) is not int or limit < 1 or limit > 1000:
        _fail()
    if type(continue_flag) is not int or continue_flag != 1:
        _fail()
    if not isinstance(updated_at, str) or not updated_at.strip() or len(updated_at) > 128:
        _fail()


def _validate_database(database_path: Path) -> None:
    database_before = _file_identity(_assert_file(database_path))
    uri = database_path.as_uri() + "?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        trusted_schema = connection.execute("PRAGMA trusted_schema").fetchone()
        if query_only != (1,) or trusted_schema != (0,):
            _fail()
        quick_check = connection.execute("PRAGMA quick_check").fetchall()
        if quick_check != [("ok",)]:
            _fail()
        version_rows = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchall()
        if version_rows != [(CURRENT_DATABASE_SCHEMA,)]:
            _fail()
        _validate_queue_settings(connection)
    except (sqlite3.Error, ValueError, TypeError):
        _fail()
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            _assert_same_file(database_path, database_before)


def _assert_no_database_sidecars(state_root: Path, database_name: str) -> None:
    normalized_database_name = os.path.normcase(database_name)
    transient_names = {
        normalized_database_name + suffix for suffix in ("-journal", "-wal", "-shm")
    }
    master_journal_prefix = normalized_database_name + "-mj"
    for entry in os.scandir(state_root):
        normalized_name = os.path.normcase(entry.name)
        if normalized_name in transient_names or normalized_name.startswith(master_journal_prefix):
            _fail()


def _validate_data(data_root: Path) -> None:
    _assert_directory(data_root)
    _validate_data_marker(data_root)
    state_root = data_root / "state"
    database_path = state_root / "jobops.db"
    if os.path.lexists(state_root):
        _assert_directory(state_root)
        _assert_no_database_sidecars(state_root, database_path.name)
    if not os.path.lexists(database_path):
        return
    _validate_database(database_path)
    _assert_no_database_sidecars(state_root, database_path.name)


def _fixed_roots(argv: list[str]) -> tuple[Path, Path]:
    if argv:
        _fail()
    runtime_root = _absolute_path(str(Path.cwd()))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not isinstance(local_appdata, str) or not local_appdata:
        _fail()
    local_root = _absolute_path(local_appdata)
    data_root = _absolute_path(str(local_root / "JobOps" / "Data"))
    override = os.environ.get("JOBFLOW_DATA_ROOT")
    if override is not None:
        override_root = _absolute_path(override)
        if os.path.normcase(str(override_root)) != os.path.normcase(str(data_root)):
            _fail()
    try:
        common = Path(os.path.commonpath((str(runtime_root), str(data_root))))
    except ValueError:
        common = Path()
    if common == runtime_root or common == data_root:
        _fail()
    return runtime_root, data_root


def _entry() -> int:
    try:
        runtime_root, data_root = _fixed_roots(_sys.argv[1:])
        _validate_runtime(runtime_root)
        _validate_data(data_root)
    except BaseException:
        _sys.stderr.buffer.write(FAILURE_BYTES)
        _sys.stderr.buffer.flush()
        return 1
    _sys.stdout.buffer.write(SUCCESS_BYTES)
    _sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_entry())
