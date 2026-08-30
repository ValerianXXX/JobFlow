from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .util import has_reparse_component, sha256_bytes


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_THUMBPRINT = re.compile(r"[0-9A-F]{40}")
_TOOLS = {"node", "git", "python"}
_SANITIZED_COMMAND_TOOLS = {*_TOOLS, "powershell"}
_WINDOWS_EXECUTABLE_EXTENSIONS = ".COM;.EXE;.BAT;.CMD"
_PROTECTED_ENVIRONMENT_KEYS = {
    "COMSPEC",
    "HOMEDRIVE",
    "PATH",
    "PATHEXT",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}
_PYTHON_RUNTIME_KEYS = {
    "source_policy",
    "python_tag",
    "maximum_files",
    "maximum_entry_bytes",
    "maximum_uncompressed_bytes",
    "maximum_compression_ratio",
    "required_entries",
    "active_pth_entries",
}


class ReleaseToolchainError(RuntimeError):
    """A fail-closed release-toolchain failure with no private path output."""


def resolve_configured_release_git(git_path: Path | None = None) -> Path:
    """Resolve the explicitly configured release Git without consulting PATH.

    The command-line/API value wins over the environment.  The environment is
    retained only as a non-interactive CI hand-off and must itself contain an
    absolute path.  Tool authentication and the transaction read lock are
    applied separately by :func:`locked_release_git`.
    """

    configured = os.environ.get("JOBFLOW_RELEASE_GIT_PATH", "") if git_path is None else ""
    candidate = git_path if git_path is not None else (Path(configured) if configured else None)
    if candidate is None:
        raise ReleaseToolchainError("RELEASE_GIT_PATH_REQUIRED")
    if not candidate.is_absolute():
        raise ReleaseToolchainError("RELEASE_GIT_PATH_INVALID")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReleaseToolchainError("RELEASE_GIT_PATH_INVALID") from exc
    if not resolved.is_file() or resolved.name.casefold() != "git.exe":
        raise ReleaseToolchainError("RELEASE_GIT_PATH_INVALID")
    return resolved


@dataclass(frozen=True)
class LockedToolIdentity:
    status: str
    tool: str
    sha256: str
    signer_subject: str | None
    signer_thumbprint: str | None
    volume_serial: int
    file_index: int
    file_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _windows_buffer(function: Any, failure: str) -> Path:
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    result = int(function(buffer, size))
    if result <= 0 or result >= size:
        raise ReleaseToolchainError(failure)
    return Path(buffer.value).resolve(strict=True)


def windows_system_directory() -> Path:
    """Return the real Windows system directory without consulting environment variables."""

    if os.name != "nt":
        raise ReleaseToolchainError("RELEASE_WINDOWS_REQUIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetSystemDirectoryW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    function.restype = ctypes.c_uint
    return _windows_buffer(function, "RELEASE_SYSTEM_DIRECTORY_UNAVAILABLE")


def windows_directory() -> Path:
    if os.name != "nt":
        raise ReleaseToolchainError("RELEASE_WINDOWS_REQUIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetWindowsDirectoryW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    function.restype = ctypes.c_uint
    return _windows_buffer(function, "RELEASE_WINDOWS_DIRECTORY_UNAVAILABLE")


def windows_temp_directory() -> Path:
    if os.name != "nt":
        raise ReleaseToolchainError("RELEASE_WINDOWS_REQUIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetTempPathW
    function.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    function.restype = ctypes.c_uint
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    result = int(function(size, buffer))
    if result <= 0 or result >= size:
        raise ReleaseToolchainError("RELEASE_TEMP_DIRECTORY_UNAVAILABLE")
    return Path(buffer.value).resolve(strict=True)


def sanitized_command_environment(
    tool: str,
    *,
    executable: Path | None = None,
    project: Path | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal command environment instead of inheriting caller injection state."""

    if tool not in _SANITIZED_COMMAND_TOOLS:
        raise ReleaseToolchainError("RELEASE_TOOL_KIND_INVALID")
    system = windows_system_directory()
    windows = windows_directory()
    system_drive = windows.drive
    if len(system_drive) != 2 or system_drive[1] != ":" or not system_drive[0].isalpha():
        raise ReleaseToolchainError("RELEASE_WINDOWS_DRIVE_INVALID")
    temporary = windows_temp_directory()
    path_entries = [system, windows, system / "WindowsPowerShell" / "v1.0"]
    if executable is not None:
        executable = executable.resolve(strict=True)
        path_entries.insert(0, executable.parent)
        if tool == "git":
            if (
                executable.parent.name.casefold() == "bin"
                and executable.parent.parent.name.casefold() == "mingw64"
            ):
                root = executable.parent.parent.parent
            else:
                root = executable.parent.parent
            path_entries.extend([root / "mingw64" / "bin", root / "usr" / "bin"])
    environment = {
        "SystemRoot": str(windows),
        "SystemDrive": system_drive,
        # Playwright resolves the system Edge installation from these standard
        # Windows locations. Derive them from the authenticated Windows drive
        # instead of inheriting caller-controlled environment values.
        "HOMEDRIVE": system_drive,
        "PROGRAMFILES": str(Path(system_drive + "\\") / "Program Files"),
        "PROGRAMFILES(X86)": str(Path(system_drive + "\\") / "Program Files (x86)"),
        "WINDIR": str(windows),
        "COMSPEC": str(system / "cmd.exe"),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "PATH": os.pathsep.join(str(path) for path in path_entries if path.is_dir()),
        # Windows PowerShell consults PATHEXT even for an explicitly resolved
        # native executable. Without a deterministic .EXE entry, a trusted
        # tool such as icacls.exe is found but silently not launched.
        "PATHEXT": _WINDOWS_EXECUTABLE_EXTENSIONS,
    }
    if tool == "git":
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "NUL",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
        if project is not None:
            environment["GIT_CEILING_DIRECTORIES"] = str(project.resolve(strict=True).parent)
    elif tool == "python":
        environment.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
    if extra:
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key + value:
                raise ReleaseToolchainError("RELEASE_TOOL_ENVIRONMENT_INVALID")
            upper = key.upper()
            if upper in _PROTECTED_ENVIRONMENT_KEYS:
                raise ReleaseToolchainError("RELEASE_TOOL_ENVIRONMENT_INVALID")
            forbidden = (
                upper.startswith("NODE_")
                or upper.startswith("NPM_CONFIG_")
                or upper.startswith("PYTHON")
                or upper.startswith("GIT_")
            )
            safe_override = upper in {
                "GIT_CONFIG_NOSYSTEM",
                "GIT_CONFIG_GLOBAL",
                "GIT_TERMINAL_PROMPT",
                "GIT_OPTIONAL_LOCKS",
                "GIT_CEILING_DIRECTORIES",
                "PYTHONNOUSERSITE",
                "PYTHONSAFEPATH",
                "PYTHONDONTWRITEBYTECODE",
            }
            if forbidden and not safe_override:
                raise ReleaseToolchainError("RELEASE_TOOL_ENVIRONMENT_INVALID")
            environment[key] = value
    return environment


def load_python_support_policy(project: Path) -> dict[str, Any]:
    """Load the intentional Python role split and bind it to pinned runtime inputs."""

    project = project.resolve(strict=True)
    path = project / "config" / "python-support-policy.json"
    if not path.is_file() or has_reparse_component(path, project):
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID") from exc
    expected_keys = {
        "schema_version",
        "status",
        "source_package",
        "legacy_windows_source_installer",
        "production_complete_windows_runtime",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema_version") != 1
        or value.get("status") != "ROLE_SPLIT_INTENTIONAL"
    ):
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID")
    source_package = value.get("source_package")
    legacy = value.get("legacy_windows_source_installer")
    complete = value.get("production_complete_windows_runtime")
    if (
        source_package
        != {
            "requires_python": ">=3.11,<3.14",
            "tested_minors": ["3.11", "3.12", "3.13"],
        }
        or legacy
        != {
            "allowed_minors": ["3.11", "3.12"],
            "distribution_policy": "PYTHON_SOFTWARE_FOUNDATION_SIGNED_SYSTEM_INSTALLATION",
        }
        or not isinstance(complete, dict)
        or set(complete)
        != {
            "exact_version",
            "python_tag",
            "runtime_tag",
            "architecture",
            "source_policy",
            "runtime_lock",
            "build_lock",
        }
        or complete.get("architecture") != "AMD64"
        or complete.get("source_policy") != "config/windows-runtime-source.json"
        or complete.get("runtime_lock") != "config/windows-cp313-runtime.lock"
        or complete.get("build_lock") != "config/windows-cp313-build.lock"
    ):
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID")
    try:
        runtime_source = json.loads(
            (project / complete["source_policy"]).read_text(encoding="utf-8")
        )
        runtime_lock = json.loads(
            (project / complete["runtime_lock"]).read_text(encoding="utf-8")
        )
        build_lock = json.loads(
            (project / complete["build_lock"]).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID") from exc
    runtime_python = runtime_source.get("python") if isinstance(runtime_source, dict) else None
    if (
        not isinstance(runtime_python, dict)
        or complete.get("exact_version") != runtime_python.get("version")
        or complete.get("python_tag") != runtime_lock.get("python_tag")
        or complete.get("runtime_tag")
        != "python" + str(complete.get("python_tag"))[2:]
        or runtime_lock.get("lock_type") != "runtime-wheelhouse"
        or build_lock.get("lock_type") != "protected-builder-wheelhouse"
    ):
        raise ReleaseToolchainError("PYTHON_SUPPORT_POLICY_INVALID")
    return value


def load_release_toolchain_policy(project: Path) -> dict[str, Any]:
    project = project.resolve(strict=True)
    path = project / "config" / "release-toolchain.json"
    if not path.is_file() or has_reparse_component(path, project):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    tools = value.get("tools")
    python_runtime = value.get("python_execution_runtime")
    dependencies = value.get("javascript_dependencies")
    if (
        set(value) != {
            "schema_version",
            "tools",
            "python_execution_runtime",
            "javascript_dependencies",
        }
        or not isinstance(tools, dict)
        or set(tools) != _TOOLS
        or not isinstance(python_runtime, dict)
        or set(python_runtime) != _PYTHON_RUNTIME_KEYS
        or not isinstance(dependencies, dict)
    ):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    for tool, policy in tools.items():
        expected_policy_keys = {
            "file_names",
            "allowed_signers",
            "allowed_unsigned_sha256",
            *(("runtime_tree_sha256",) if tool == "git" else ()),
        }
        if not isinstance(policy, dict) or set(policy) != expected_policy_keys:
            raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
        names = policy.get("file_names")
        signers = policy.get("allowed_signers")
        unsigned = policy.get("allowed_unsigned_sha256")
        if (
            not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
            or not isinstance(signers, list)
            or not isinstance(unsigned, list)
            or any(not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in unsigned)
        ):
            raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
        if tool == "git" and (
            not isinstance(policy.get("runtime_tree_sha256"), str)
            or _SHA256.fullmatch(policy["runtime_tree_sha256"]) is None
        ):
            raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
        for signer in signers:
            if (
                not isinstance(signer, dict)
                or set(signer) != {"subject", "thumbprint"}
                or not isinstance(signer.get("subject"), str)
                or not signer["subject"]
                or not isinstance(signer.get("thumbprint"), str)
                or _THUMBPRINT.fullmatch(signer["thumbprint"]) is None
            ):
                raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    source_policy = python_runtime.get("source_policy")
    python_tag = python_runtime.get("python_tag")
    required_entries = python_runtime.get("required_entries")
    active_pth_entries = python_runtime.get("active_pth_entries")
    bounded_integers = {
        "maximum_files": (8, 256),
        "maximum_entry_bytes": (1024 * 1024, 128 * 1024 * 1024),
        "maximum_uncompressed_bytes": (8 * 1024 * 1024, 256 * 1024 * 1024),
        "maximum_compression_ratio": (1, 500),
    }
    if (
        source_policy != "config/windows-runtime-source.json"
        or not isinstance(python_tag, str)
        or re.fullmatch(r"python[0-9]{3}", python_tag) is None
        or not isinstance(required_entries, list)
        or len(required_entries) < 8
        or any(
            not isinstance(entry, str)
            or not entry
            or re.fullmatch(r"[A-Za-z0-9._-]+", entry) is None
            for entry in required_entries
        )
        or len({entry.casefold() for entry in required_entries}) != len(required_entries)
        or active_pth_entries != [f"{python_tag}.zip", "."]
        or any(
            type(python_runtime.get(name)) is not int
            or not minimum <= python_runtime[name] <= maximum
            for name, (minimum, maximum) in bounded_integers.items()
        )
    ):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    mandatory = {
        "python.exe",
        "python3.dll",
        f"{python_tag}.dll",
        f"{python_tag}.zip",
        f"{python_tag}._pth",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "_hashlib.pyd",
        "unicodedata.pyd",
        "select.pyd",
    }
    if not mandatory.issubset({entry.casefold() for entry in required_entries}):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    source_path = project / source_policy
    if not source_path.is_file() or has_reparse_component(source_path, project):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID") from exc
    source_python = source.get("python") if isinstance(source, dict) else None
    builder = source.get("builder") if isinstance(source, dict) else None
    isolation = source.get("isolation") if isinstance(source, dict) else None
    if (
        not isinstance(source, dict)
        or source.get("schema_version") != 1
        or source.get("status") != "PINNED_OFFICIAL_SOURCE"
        or source.get("platform") != "windows-x64"
        or source.get("architecture") != "AMD64"
        or not isinstance(source_python, dict)
        or not isinstance(source_python.get("version"), str)
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", source_python["version"]) is None
        or source_python.get("artifact_name")
        != f"python-{source_python['version']}-embed-amd64.zip"
        or type(source_python.get("artifact_bytes")) is not int
        or not 1 <= source_python["artifact_bytes"] <= 128 * 1024 * 1024
        or not isinstance(source_python.get("artifact_sha256"), str)
        or _SHA256.fullmatch(source_python["artifact_sha256"]) is None
        or not isinstance(builder, dict)
        or builder.get("python_version") != source_python.get("version")
        or not isinstance(isolation, dict)
        or isolation.get("import_site") is not False
        or not isinstance(isolation.get("python_pth"), list)
        or isolation["python_pth"][:2] != [f"{python_tag}.zip", "."]
    ):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    parts = source_python["version"].split(".")
    if python_tag != f"python{parts[0]}{parts[1]}":
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    packages = dependencies.get("packages")
    if (
        set(dependencies) != {"packages", "file_count", "total_bytes", "tree_sha256"}
        or not isinstance(packages, list)
        or not packages
        or any(not isinstance(item, str) or not item or "/" in item or "\\" in item for item in packages)
        or type(dependencies.get("file_count")) is not int
        or dependencies["file_count"] < 1
        or type(dependencies.get("total_bytes")) is not int
        or dependencies["total_bytes"] < 1
        or not isinstance(dependencies.get("tree_sha256"), str)
        or _SHA256.fullmatch(dependencies["tree_sha256"]) is None
    ):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    try:
        support = load_python_support_policy(project)
    except ReleaseToolchainError as exc:
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID") from exc
    complete_runtime = support["production_complete_windows_runtime"]
    if (
        complete_runtime["exact_version"] != source_python["version"]
        or complete_runtime["runtime_tag"] != python_tag
    ):
        raise ReleaseToolchainError("RELEASE_TOOLCHAIN_POLICY_INVALID")
    return value


def _windows_signature_valid(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class _WintrustFileInfo(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(_Guid)),
            ]

        class _WintrustData(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", wintypes.LPVOID),
                ("pSIPClientData", wintypes.LPVOID),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pInfoStruct", wintypes.LPVOID),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
            ]

        action = _Guid(
            0x00AAC56B,
            0xCD44,
            0x11D0,
            (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
        )
        file_info = _WintrustFileInfo(ctypes.sizeof(_WintrustFileInfo), str(path), None, None)
        trust_data = _WintrustData()
        trust_data.cbStruct = ctypes.sizeof(_WintrustData)
        trust_data.dwUIChoice = 2
        trust_data.fdwRevocationChecks = 0
        trust_data.dwUnionChoice = 1
        trust_data.pInfoStruct = ctypes.cast(ctypes.pointer(file_info), wintypes.LPVOID)
        trust_data.dwStateAction = 1
        trust_data.dwProvFlags = 0x1000
        verify = ctypes.WinDLL("wintrust", use_last_error=True).WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(_Guid), ctypes.POINTER(_WintrustData)]
        verify.restype = ctypes.c_long
        result = verify(None, ctypes.byref(action), ctypes.byref(trust_data))
        trust_data.dwStateAction = 2
        verify(None, ctypes.byref(action), ctypes.byref(trust_data))
        return result == 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _embedded_signer_identity(path: Path) -> tuple[str, str]:
    system = windows_system_directory()
    powershell = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise ReleaseToolchainError("RELEASE_TOOL_IDENTITY_UNAVAILABLE")
    program = (
        "$ErrorActionPreference='Stop';"
        "$raw=[System.Security.Cryptography.X509Certificates.X509Certificate]::"
        "CreateFromSignedFile($env:JOBFLOW_IDENTITY_TOOL);"
        "$cert=[System.Security.Cryptography.X509Certificates.X509Certificate2]::new($raw);"
        "[pscustomobject]@{subject=$cert.Subject;thumbprint=$cert.Thumbprint} | "
        "ConvertTo-Json -Compress"
    )
    environment = sanitized_command_environment(
        "python",
        executable=powershell,
        extra={"JOBFLOW_IDENTITY_TOOL": str(path)},
    )
    completed = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            program,
        ],
        cwd=system,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        value = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise ReleaseToolchainError("RELEASE_TOOL_IDENTITY_UNAVAILABLE") from exc
    subject = value.get("subject") if isinstance(value, dict) else None
    thumbprint = value.get("thumbprint") if isinstance(value, dict) else None
    if (
        completed.returncode != 0
        or not isinstance(subject, str)
        or not subject
        or not isinstance(thumbprint, str)
        or _THUMBPRINT.fullmatch(thumbprint.upper()) is None
    ):
        raise ReleaseToolchainError("RELEASE_TOOL_IDENTITY_UNAVAILABLE")
    return subject, thumbprint.upper()


def _has_absolute_reparse_component(path: Path) -> bool:
    current = path
    while current != current.parent:
        try:
            value = current.lstat()
        except OSError:
            return True
        attributes = getattr(value, "st_file_attributes", 0)
        if current.is_symlink() or attributes & 0x400:
            return True
        current = current.parent
    return False


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_ulong),
        ("ftCreationTimeLow", ctypes.c_ulong),
        ("ftCreationTimeHigh", ctypes.c_ulong),
        ("ftLastAccessTimeLow", ctypes.c_ulong),
        ("ftLastAccessTimeHigh", ctypes.c_ulong),
        ("ftLastWriteTimeLow", ctypes.c_ulong),
        ("ftLastWriteTimeHigh", ctypes.c_ulong),
        ("dwVolumeSerialNumber", ctypes.c_ulong),
        ("nFileSizeHigh", ctypes.c_ulong),
        ("nFileSizeLow", ctypes.c_ulong),
        ("nNumberOfLinks", ctypes.c_ulong),
        ("nFileIndexHigh", ctypes.c_ulong),
        ("nFileIndexLow", ctypes.c_ulong),
    ]


def _open_locked_read(path: Path) -> int:
    if os.name != "nt":
        raise ReleaseToolchainError("RELEASE_WINDOWS_REQUIRED")
    from ctypes import wintypes

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 0x1, None, 3, 0x02000080, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise ReleaseToolchainError("RELEASE_TOOL_LOCK_FAILED")
    return int(handle)


def _open_locked_directory(path: Path) -> int:
    if os.name != "nt":
        raise ReleaseToolchainError("RELEASE_WINDOWS_REQUIRED")
    from ctypes import wintypes

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0, 0x1, None, 3, 0x02200000, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise ReleaseToolchainError("RELEASE_TOOL_LOCK_FAILED")
    return int(handle)


def _handle_information(handle: int) -> tuple[int, int, int]:
    from ctypes import wintypes

    info = _ByHandleFileInformation()
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    function.restype = wintypes.BOOL
    if (
        not function(handle, ctypes.byref(info))
        or info.dwFileAttributes & 0x400
        or int(info.nNumberOfLinks) != 1
    ):
        raise ReleaseToolchainError("RELEASE_TOOL_IDENTITY_UNAVAILABLE")
    index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
    return int(info.dwVolumeSerialNumber), index, size


def _close_handle(handle: int) -> None:
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    function.argtypes = [wintypes.HANDLE]
    function.restype = wintypes.BOOL
    function(handle)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@contextmanager
def locked_authenticated_tool(project: Path, path: Path, tool: str) -> Iterator[LockedToolIdentity]:
    """Authenticate and keep a tool read-locked for the complete caller transaction."""

    if tool not in _TOOLS or not path.is_absolute():
        raise ReleaseToolchainError("RELEASE_TOOL_INVALID")
    project = project.resolve(strict=True)
    policy = load_release_toolchain_policy(project)["tools"][tool]
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseToolchainError("RELEASE_TOOL_INVALID") from exc
    if not path.is_file() or path.name.casefold() not in {name.casefold() for name in policy["file_names"]}:
        raise ReleaseToolchainError("RELEASE_TOOL_INVALID")
    if _has_absolute_reparse_component(path):
        raise ReleaseToolchainError("RELEASE_TOOL_REPARSE_POINT")
    ancestor_handles: list[int] = []
    handle = -1
    try:
        ancestors = list(path.parents)
        for ancestor in reversed(ancestors):
            ancestor_handles.append(_open_locked_directory(ancestor))
        handle = _open_locked_read(path)
        first = _handle_information(handle)
        digest = _file_sha256(path)
        signer_subject: str | None = None
        signer_thumbprint: str | None = None
        trusted = False
        if _windows_signature_valid(path):
            signer_subject, signer_thumbprint = _embedded_signer_identity(path)
            trusted = any(
                signer.get("subject") == signer_subject
                and signer.get("thumbprint") == signer_thumbprint
                for signer in policy["allowed_signers"]
            )
        if not trusted and digest in policy["allowed_unsigned_sha256"]:
            trusted = True
        if not trusted:
            raise ReleaseToolchainError(f"RELEASE_{tool.upper()}_UNTRUSTED")
        identity = LockedToolIdentity(
            status="PASS",
            tool=tool,
            sha256=digest,
            signer_subject=signer_subject,
            signer_thumbprint=signer_thumbprint,
            volume_serial=first[0],
            file_index=first[1],
            file_size=first[2],
        )
        yield identity
        if _handle_information(handle) != first or _file_sha256(path) != digest:
            raise ReleaseToolchainError(f"RELEASE_{tool.upper()}_CHANGED")
    finally:
        if handle >= 0:
            _close_handle(handle)
        for ancestor_handle in reversed(ancestor_handles):
            _close_handle(ancestor_handle)


@contextmanager
def locked_release_git(
    project: Path, git_path: Path | None = None
) -> Iterator[Path]:
    """Resolve, authenticate, and read-lock the selected release Git."""

    resolved = resolve_configured_release_git(git_path)
    with locked_authenticated_tool(project, resolved, "git"):
        yield resolved


def _javascript_dependency_evidence(project: Path, expected: dict[str, Any]) -> dict[str, Any]:
    root = project / "node_modules"
    rows: list[str] = []
    total = 0
    for package in expected["packages"]:
        package_root = root / package
        files = sorted(
            (path for path in package_root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        )
        for path in files:
            if has_reparse_component(path, project):
                raise ReleaseToolchainError("RELEASE_JAVASCRIPT_DEPENDENCIES_INVALID")
            payload = path.read_bytes()
            relative = path.relative_to(root).as_posix()
            rows.append(f"{relative}\t{len(payload)}\t{hashlib.sha256(payload).hexdigest()}")
            total += len(payload)
    digest = "sha256:" + hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    if (
        len(rows) != expected["file_count"]
        or total != expected["total_bytes"]
        or digest != expected["tree_sha256"]
    ):
        raise ReleaseToolchainError("RELEASE_JAVASCRIPT_DEPENDENCIES_INVALID")
    return {
        "status": "PASS",
        "packages": list(expected["packages"]),
        "file_count": len(rows),
        "total_bytes": total,
        "tree_sha256": digest,
    }


@contextmanager
def locked_javascript_dependency_tree(project: Path) -> Iterator[dict[str, Any]]:
    """Read-lock every installed JS dependency byte for the complete Node check."""

    project = project.resolve(strict=True)
    expected = load_release_toolchain_policy(project)["javascript_dependencies"]
    root = project / "node_modules"
    if not root.is_dir() or has_reparse_component(root, project):
        raise ReleaseToolchainError("RELEASE_JAVASCRIPT_DEPENDENCIES_INVALID")
    directory_handles: list[int] = []
    file_handles: list[int] = []
    try:
        directory_handles.append(_open_locked_directory(root))
        for package in expected["packages"]:
            package_root = root / package
            if not package_root.is_dir() or has_reparse_component(package_root, project):
                raise ReleaseToolchainError("RELEASE_JAVASCRIPT_DEPENDENCIES_INVALID")
            directory_handles.append(_open_locked_directory(package_root))
            for path in sorted(
                (item for item in package_root.rglob("*") if item.is_file()),
                key=lambda item: item.as_posix(),
            ):
                file_handles.append(_open_locked_read(path))
        evidence = _javascript_dependency_evidence(project, expected)
        yield evidence
        if _javascript_dependency_evidence(project, expected) != evidence:
            raise ReleaseToolchainError("RELEASE_JAVASCRIPT_DEPENDENCIES_CHANGED")
    finally:
        for file_handle in reversed(file_handles):
            _close_handle(file_handle)
        for directory_handle in reversed(directory_handles):
            _close_handle(directory_handle)


def verify_javascript_dependency_tree(project: Path) -> dict[str, Any]:
    """Verify installed JS bytes and close the temporary read locks."""

    with locked_javascript_dependency_tree(project) as evidence:
        return evidence
