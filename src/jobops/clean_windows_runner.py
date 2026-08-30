from __future__ import annotations

import csv
import ctypes
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.parse import urlsplit

from .clean_windows_acceptance import BrowserAcceptanceProbe, _load_browser_policy
from .desktop_update import _resolve_pointer
from .errors import JobOpsError
from .publisher_attestation import EvidenceDocument, validate_clean_windows_acceptance
from .release_attestation import _clean_import_context
from .release_toolchain import (
    ReleaseToolchainError,
    sanitized_command_environment,
    windows_system_directory,
    windows_temp_directory,
)
from .runtime_closure import _open_locked_runtime_file
from .update_manifest import verify_signed_release_bundle, verify_signed_update_bundle
from .util import canonical_json, has_reparse_component, iso_utc, is_relative_to, load_json, parse_iso, sha256_bytes


_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SESSION_PATH = re.compile(r"/session/[A-Za-z0-9_-]{32,128}/")
_ADMINISTRATORS_SID = "S-1-5-32-544"
_MEDIUM_INTEGRITY_SID = "S-1-16-8192"
_TASK_NAME = "JobFlow Authorized Read-Only Discovery"
_NATIVE_HOST = "com.jobflow.browser_companion"
_NATIVE_KEYS = (
    rf"Software\Google\Chrome\NativeMessagingHosts\{_NATIVE_HOST}",
    rf"Software\Microsoft\Edge\NativeMessagingHosts\{_NATIVE_HOST}",
)
_MAX_COMMAND_OUTPUT = 512 * 1024
_MAX_CONTROL_FILE = 2 * 1024 * 1024
_MAX_CONTROL_TOTAL = 32 * 1024 * 1024
_CONTROL_FILES = (
    "jobflow-bootstrap.ps1",
    "start-installed-jobflow.ps1",
    "check-installed-jobflow.ps1",
    "update-installed-jobflow.ps1",
    "rollback-installed-jobflow.ps1",
    "uninstall-installed-jobflow.ps1",
    "jobflow-runtime-locks.ps1",
    "manage-authorized-discovery-task.ps1",
    "run-authorized-discovery-task.ps1",
    "Start JobFlow.cmd",
    "Check JobFlow.cmd",
    "Update JobFlow.cmd",
    "Rollback JobFlow.cmd",
    "Uninstall JobFlow.cmd",
)
_REQUIRED_CONTROL_MEMBERS = (
    ".jobops-root",
    "scripts/install-jobflow-v2.ps1",
    *(f"scripts/windows-runtime/{name}" for name in _CONTROL_FILES),
)


@dataclass(frozen=True)
class ReleaseMaterial:
    version: str
    manifest: Path
    signature: Path
    archive: Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class AcceptanceBackend(Protocol):
    def preflight(self) -> None: ...

    def install(self, control_root: Path, archive: Path) -> None: ...

    def pointer(self, *, version: str, commit: str, previous: bool = False) -> dict[str, Any]: ...

    def health(self, *, version: str) -> None: ...

    def startup(self) -> None: ...

    def native_host(self, project: Path) -> None: ...

    def browsers(self, project: Path, timeout_seconds: int) -> dict[str, Any]: ...

    def rollback(self) -> None: ...

    def uninstall(self) -> None: ...

    def assert_clean(self) -> None: ...


def _fail(code: str, message: str) -> None:
    raise JobOpsError(code, message, automatic_retry=False)


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        _fail("CLEAN_WINDOWS_RELEASE_IDENTITY_INVALID", "A clean-Windows release version is invalid.")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _release_material(value: ReleaseMaterial, *, label: str) -> ReleaseMaterial:
    version = str(value.version)
    _version_tuple(version)
    paths: list[Path] = []
    for candidate in (value.manifest, value.signature, value.archive):
        try:
            path = Path(candidate).resolve(strict=True)
            metadata = path.stat(follow_symlinks=False)
        except OSError as error:
            raise JobOpsError(
                "CLEAN_WINDOWS_SIGNED_INPUT_INVALID",
                "A required signed clean-Windows input is unavailable.",
                input=label,
            ) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(getattr(metadata, "st_nlink", 1)) != 1
            or has_reparse_component(path)
        ):
            _fail("CLEAN_WINDOWS_SIGNED_INPUT_INVALID", "A required signed clean-Windows input has an unsafe identity.")
        paths.append(path)
    if len({os.path.normcase(str(path)) for path in paths}) != 3:
        _fail("CLEAN_WINDOWS_SIGNED_INPUT_INVALID", "Signed clean-Windows inputs must be distinct files.")
    return ReleaseMaterial(version, *paths)


def _copy_locked(
    source: BinaryIO,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int | None,
    maximum: int,
) -> None:
    if _SHA256.fullmatch(str(expected_sha256)) is None:
        _fail("CLEAN_WINDOWS_SIGNED_INPUT_INVALID", "A signed input digest is invalid.")
    source.seek(0)
    digest = hashlib.sha256()
    total = 0
    try:
        with destination.open("xb") as target:
            while True:
                chunk = source.read(min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    _fail("CLEAN_WINDOWS_SIGNED_INPUT_INVALID", "A signed input exceeds its bounded size.")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except OSError as error:
        raise JobOpsError("CLEAN_WINDOWS_STAGING_FAILED", "Signed clean-Windows inputs could not be staged.") from error
    actual = "sha256:" + digest.hexdigest()
    if actual != expected_sha256 or (expected_bytes is not None and total != expected_bytes):
        destination.unlink(missing_ok=True)
        _fail("CLEAN_WINDOWS_SIGNED_INPUT_CHANGED", "A signed clean-Windows input changed during staging.")


def _extract_control_plane(archive: Path, prefix: str, target: Path) -> None:
    expected = {prefix + relative: relative for relative in _REQUIRED_CONTROL_MEMBERS}
    observed: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    try:
        with zipfile.ZipFile(archive, "r") as package:
            for entry in package.infolist():
                name = entry.filename.replace("\\", "/")
                folded = name.casefold()
                if folded in casefolded:
                    _fail("CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID", "The signed archive contains a duplicate path.")
                casefolded.add(folded)
                if name in expected:
                    if (
                        entry.is_dir()
                        or entry.flag_bits & 0x1
                        or entry.file_size < 1
                        or entry.file_size > _MAX_CONTROL_FILE
                        or (entry.compress_size == 0 and entry.file_size > 0)
                        or entry.file_size > max(entry.compress_size, 1) * 200
                    ):
                        _fail("CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID", "A signed control-plane entry is invalid.")
                    observed[name] = entry
            if set(observed) != set(expected):
                _fail("CLEAN_WINDOWS_ARCHIVE_CONTROL_MISSING", "The signed archive is missing required installer controls.")
            if sum(item.file_size for item in observed.values()) > _MAX_CONTROL_TOTAL:
                _fail("CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID", "The signed control plane exceeds its bounded size.")
            for name, relative in expected.items():
                destination = target / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if has_reparse_component(destination.parent, target):
                    _fail("CLEAN_WINDOWS_STAGING_REPARSE_REJECTED", "The clean-Windows staging boundary is unsafe.")
                with package.open(observed[name], "r") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if destination.stat().st_size != observed[name].file_size:
                    _fail("CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID", "A signed control-plane entry was truncated.")
    except (OSError, zipfile.BadZipFile) as error:
        raise JobOpsError("CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID", "The signed archive control plane is unavailable.") from error


def _stage_release(
    root: Path,
    label: str,
    material: ReleaseMaterial,
    bundle: dict[str, Any],
    handles: tuple[BinaryIO, BinaryIO, BinaryIO],
) -> tuple[Path, Path]:
    package = root / f"{label}-package"
    controls = root / f"{label}-controls"
    package.mkdir()
    controls.mkdir()
    manifest = package / "JobFlow-update-manifest.json"
    signature = package / "JobFlow-update-manifest.sig.json"
    archive = package / str(bundle["asset_name"])
    _copy_locked(handles[0], manifest, expected_sha256=str(bundle["manifest_sha256"]), expected_bytes=None, maximum=64 * 1024)
    _copy_locked(handles[1], signature, expected_sha256=str(bundle["signature_sha256"]), expected_bytes=None, maximum=16 * 1024)
    _copy_locked(
        handles[2],
        archive,
        expected_sha256=str(bundle["asset_sha256"]),
        expected_bytes=int(bundle["asset_bytes"]),
        maximum=1536 * 1024 * 1024,
    )
    # Each staged file is byte-identical to the read-locked input already
    # verified against the project-pinned channel.  Re-running verification on
    # a mutable path here would weaken, rather than strengthen, that binding.
    _extract_control_plane(archive, str(bundle["archive_prefix"]), controls)
    return controls, archive


def _known_folder(identifier: str) -> Path:
    if os.name != "nt":
        _fail("CLEAN_WINDOWS_PLATFORM_REQUIRED", "Clean-Windows acceptance must run on Windows.")

    class GUID(ctypes.Structure):
        _fields_ = (("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8))

    value = uuid.UUID(identifier)
    raw = value.bytes_le
    guid = GUID.from_buffer_copy(raw)
    pointer = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    function = shell32.SHGetKnownFolderPath
    function.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    function.restype = ctypes.c_long
    result = int(function(ctypes.byref(guid), 0, None, ctypes.byref(pointer)))
    if result != 0 or not pointer.value:
        _fail("CLEAN_WINDOWS_KNOWN_FOLDER_UNAVAILABLE", "A required Windows known folder is unavailable.")
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    try:
        return Path(pointer.value).resolve(strict=True)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(pointer, ctypes.c_void_p))


def _read_startup_readiness(path: Path, *, final: bool) -> dict[str, Any] | None:
    """Read a readiness document without rejecting a still-partial UTF-8 write."""

    raw = path.read_bytes()
    if len(raw) > _MAX_COMMAND_OUTPUT:
        _fail("CLEAN_WINDOWS_STARTUP_OUTPUT_EXCEEDED", "JobFlow startup exceeded its bounded output.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        if not final:
            return None
        raise JobOpsError(
            "CLEAN_WINDOWS_STARTUP_OUTPUT_INVALID",
            "JobFlow startup returned invalid UTF-8 output.",
            automatic_retry=False,
        ) from error
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("status") == "ONBOARDING_CENTER_READY":
            return candidate
    return None


def _validated_session_url(value: object):
    try:
        parsed = urlsplit(str(value or ""))
        port = parsed.port
    except ValueError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_STARTUP_BINDING_INVALID",
            "JobFlow startup did not bind to an authorized loopback session.",
            automatic_retry=False,
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _SESSION_PATH.fullmatch(parsed.path) is None
    ):
        _fail(
            "CLEAN_WINDOWS_STARTUP_BINDING_INVALID",
            "JobFlow startup did not bind to an authorized loopback session.",
        )
    return parsed


def _validated_readiness(value: dict[str, Any]):
    locales = value.get("supported_locales")
    if (
        value.get("status") != "ONBOARDING_CENTER_READY"
        or value.get("binding") != "127.0.0.1"
        or value.get("private_values_emitted") != 0
        or value.get("real_external_actions") != 0
        or not isinstance(locales, list)
        or len(locales) != 2
        or set(locales) != {"zh", "en"}
    ):
        _fail(
            "CLEAN_WINDOWS_STARTUP_METADATA_INVALID",
            "JobFlow startup did not report its required local, private, bilingual safety state.",
        )
    return _validated_session_url(value.get("url"))


class WindowsAcceptanceBackend:
    def __init__(self, temporary_root: Path) -> None:
        if os.name != "nt" or platform.machine().upper() != "AMD64":
            _fail("CLEAN_WINDOWS_PLATFORM_REQUIRED", "Clean-Windows acceptance requires Windows AMD64.")
        self.temporary_root = temporary_root
        self.local_app_data = _known_folder("f1b32785-6fba-4fcf-9d55-7b8e7f157091")
        self.roaming_app_data = _known_folder("3eb685db-65f9-4cf6-a03a-e3ef65729f3d")
        self.profile = _known_folder("5e6c858f-0e22-4760-9afe-ea3317b67173")
        self.jobops = self.local_app_data / "JobOps"
        self.installer_state = self.local_app_data / "JobFlowInstaller"
        self.start_menu = self.roaming_app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "JobFlow"
        self.powershell = windows_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        self._command_index = 0

    def _environment(self) -> dict[str, str]:
        try:
            return sanitized_command_environment(
                "powershell",
                extra={
                    "LOCALAPPDATA": str(self.local_app_data),
                    "APPDATA": str(self.roaming_app_data),
                    "USERPROFILE": str(self.profile),
                },
            )
        except ReleaseToolchainError as error:
            raise JobOpsError("CLEAN_WINDOWS_COMMAND_ENVIRONMENT_INVALID", "The clean-Windows command environment is invalid.") from error

    def _run(self, command: list[str], *, timeout: int = 900) -> CommandResult:
        self._command_index += 1
        stdout_path = self.temporary_root / f"command-{self._command_index}.stdout"
        stderr_path = self.temporary_root / f"command-{self._command_index}.stderr"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=self._environment(),
                    close_fds=True,
                    creationflags=creationflags,
                )
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait(timeout=10)
                    raise JobOpsError("CLEAN_WINDOWS_COMMAND_TIMEOUT", "A bounded clean-Windows step timed out.", automatic_retry=False) from error
            if stdout_path.stat().st_size > _MAX_COMMAND_OUTPUT or stderr_path.stat().st_size > _MAX_COMMAND_OUTPUT:
                _fail("CLEAN_WINDOWS_COMMAND_OUTPUT_EXCEEDED", "A clean-Windows step exceeded its bounded output.")
            return CommandResult(returncode, stdout_path.read_bytes(), stderr_path.read_bytes())
        except OSError as error:
            raise JobOpsError("CLEAN_WINDOWS_COMMAND_FAILED", "A clean-Windows command could not run.", automatic_retry=False) from error

    def _powershell(self, script: Path, *arguments: str, timeout: int = 900) -> CommandResult:
        return self._run(
            [
                str(self.powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *arguments,
            ],
            timeout=timeout,
        )

    @staticmethod
    def _json(raw: bytes, *, code: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise JobOpsError(code, "A clean-Windows step returned invalid structured output.") from error
        if not isinstance(value, dict):
            _fail(code, "A clean-Windows step returned invalid structured output.")
        return value

    @staticmethod
    def _ordinary(path: Path, root: Path) -> None:
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as error:
            raise JobOpsError("CLEAN_WINDOWS_INSTALLED_FILE_INVALID", "An installed JobFlow file is missing.") from error
        if (
            not is_relative_to(path, root)
            or has_reparse_component(path, root)
            or not stat.S_ISREG(metadata.st_mode)
            or int(getattr(metadata, "st_nlink", 1)) != 1
        ):
            _fail("CLEAN_WINDOWS_INSTALLED_FILE_INVALID", "An installed JobFlow file has an unsafe identity.")

    def _native_registry_present(self) -> bool:
        import winreg

        for key_name in _NATIVE_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ):
                    return True
            except FileNotFoundError:
                continue
        return False

    def _task_present(self) -> bool:
        executable = windows_system_directory() / "schtasks.exe"
        result = self._run([str(executable), "/Query", "/TN", _TASK_NAME, "/FO", "LIST"], timeout=30)
        if result.returncode == 0:
            return True
        if result.returncode != 1:
            _fail("CLEAN_WINDOWS_SCHEDULED_TASK_CHECK_FAILED", "The JobFlow scheduled-task state could not be verified.")
        # ``schtasks /Query /TN`` uses exit code 1 both for a missing task and
        # for several query failures.  A successful full inventory is the
        # independent proof that the scheduler was readable before treating
        # the exact task as absent.  The task name is ASCII and therefore
        # stable across Windows console code pages.
        inventory = self._run([str(executable), "/Query", "/FO", "CSV", "/NH"], timeout=60)
        if inventory.returncode != 0:
            _fail("CLEAN_WINDOWS_SCHEDULED_TASK_CHECK_FAILED", "The JobFlow scheduled-task state could not be verified.")
        return _TASK_NAME.casefold().encode("ascii") in inventory.stdout.lower()

    def _has_preexisting(self) -> bool:
        return any(path.exists() for path in (self.jobops, self.installer_state, self.start_menu)) or self._native_registry_present() or self._task_present()

    def preflight(self) -> None:
        whoami = windows_system_directory() / "whoami.exe"
        result = self._run([str(whoami), "/groups", "/fo", "csv", "/nh"], timeout=30)
        if result.returncode != 0 or len(result.stdout) > 256 * 1024:
            _fail("CLEAN_WINDOWS_ACCOUNT_CHECK_FAILED", "The Windows account profile could not be verified.")
        try:
            rows = list(csv.reader(result.stdout.decode("utf-8-sig").splitlines()))
        except (UnicodeError, csv.Error) as error:
            raise JobOpsError("CLEAN_WINDOWS_ACCOUNT_CHECK_FAILED", "The Windows account profile could not be verified.") from error
        sids = {column.strip() for row in rows for column in row if column.strip().startswith("S-1-")}
        if _ADMINISTRATORS_SID in sids or _MEDIUM_INTEGRITY_SID not in sids:
            _fail("CLEAN_WINDOWS_FRESH_STANDARD_USER_REQUIRED", "Acceptance requires a fresh standard Windows user at medium integrity.")
        if self._has_preexisting():
            _fail("CLEAN_WINDOWS_PREEXISTING_JOBFLOW", "Acceptance requires a user profile with no pre-existing JobFlow state.")

    def install(self, control_root: Path, archive: Path) -> None:
        script = control_root / "scripts" / "install-jobflow-v2.ps1"
        result = self._powershell(script, "-NoLaunch", "-ArchivePath", str(archive), timeout=1200)
        if result.returncode != 0:
            _fail("CLEAN_WINDOWS_INSTALL_FAILED", "The signed JobFlow package did not install successfully.")

    def pointer(self, *, version: str, commit: str, previous: bool = False) -> dict[str, Any]:
        path = self.jobops / ("previous.json" if previous else "current.json")
        self._ordinary(path, self.jobops)
        try:
            value = load_json(path)
            _resolve_pointer(value)
        except (OSError, UnicodeError, ValueError, JobOpsError) as error:
            raise JobOpsError("CLEAN_WINDOWS_POINTER_INVALID", "An installed JobFlow pointer is invalid.") from error
        if value.get("version") != version or value.get("source_commit") != commit:
            _fail("CLEAN_WINDOWS_POINTER_MISMATCH", "The installed JobFlow pointer does not match the expected signed release.")
        return value

    def health(self, *, version: str) -> None:
        script = self.jobops / "bin" / "check-installed-jobflow.ps1"
        self._ordinary(script, self.jobops)
        result = self._powershell(script, "-Json", timeout=300)
        value = self._json(result.stdout, code="CLEAN_WINDOWS_HEALTH_INVALID")
        if (
            result.returncode != 0
            or value.get("status") != "JOBFLOW_READY"
            or value.get("version") != version
            or value.get("checks_passed") != value.get("checks_total")
            or value.get("private_values_emitted") != 0
            or value.get("network_actions") != 0
            or value.get("real_external_actions") != 0
        ):
            _fail("CLEAN_WINDOWS_HEALTH_FAILED", "The installed JobFlow health check did not pass.")

    def startup(self) -> None:
        script = self.jobops / "bin" / "start-installed-jobflow.ps1"
        self._ordinary(script, self.jobops)
        self._command_index += 1
        stdout_path = self.temporary_root / f"command-{self._command_index}.stdout"
        stderr_path = self.temporary_root / f"command-{self._command_index}.stderr"
        command = [
            str(self.powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-NoBrowser",
            ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process: subprocess.Popen[Any] | None = None
        connection: http.client.HTTPConnection | None = None
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=self._environment(),
                    close_fds=True,
                    creationflags=creationflags,
                )
            deadline = time.monotonic() + 180
            readiness: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                running = process.poll() is None
                if stderr_path.stat().st_size > _MAX_COMMAND_OUTPUT:
                    _fail("CLEAN_WINDOWS_STARTUP_OUTPUT_EXCEEDED", "JobFlow startup exceeded its bounded output.")
                readiness = _read_startup_readiness(stdout_path, final=not running)
                if readiness is not None or not running:
                    break
                time.sleep(0.2)
            if readiness is None:
                _fail("CLEAN_WINDOWS_STARTUP_FAILED", "JobFlow did not reach its local ready state.")
            parsed = _validated_readiness(readiness)
            connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=15)
            connection.request("GET", "/health")
            response = connection.getresponse()
            health = json.loads(response.read().decode("utf-8"))
            if (
                response.status != 200
                or health.get("status") != "READY"
                or health.get("binding") != "127.0.0.1"
                or health.get("real_external_actions") != 0
            ):
                _fail("CLEAN_WINDOWS_STARTUP_HEALTH_FAILED", "The local JobFlow UI health endpoint did not pass.")
            connection.request("GET", parsed.path)
            page = connection.getresponse()
            body = page.read()
            if page.status != 200 or b"JobFlow" not in body:
                _fail("CLEAN_WINDOWS_STARTUP_UI_FAILED", "The local JobFlow interface did not load.")
            shutdown_path = parsed.path.rstrip("/") + "/api/shutdown"
            connection.request("POST", shutdown_path, body=b"{}", headers={"Content-Type": "application/json", "Content-Length": "2"})
            closing = connection.getresponse()
            closing_body = json.loads(closing.read().decode("utf-8"))
            connection.close()
            if closing.status != 200 or closing_body.get("status") != "CLOSING" or closing_body.get("real_external_actions") != 0:
                _fail("CLEAN_WINDOWS_STARTUP_SHUTDOWN_FAILED", "The local JobFlow interface did not close safely.")
            if process.wait(timeout=30) != 0:
                _fail("CLEAN_WINDOWS_STARTUP_EXIT_FAILED", "The local JobFlow process did not exit cleanly.")
            if stdout_path.stat().st_size > _MAX_COMMAND_OUTPUT or stderr_path.stat().st_size > _MAX_COMMAND_OUTPUT:
                _fail("CLEAN_WINDOWS_STARTUP_OUTPUT_EXCEEDED", "JobFlow startup exceeded its bounded output.")
        except subprocess.TimeoutExpired as error:
            raise JobOpsError(
                "CLEAN_WINDOWS_STARTUP_EXIT_TIMEOUT",
                "The local JobFlow process did not stop within its bounded shutdown window.",
                automatic_retry=False,
            ) from error
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, http.client.HTTPException) as error:
            raise JobOpsError("CLEAN_WINDOWS_STARTUP_FAILED", "The installed JobFlow interface failed its bounded startup check.") from error
        finally:
            if connection is not None:
                connection.close()
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def native_host(self, project: Path) -> None:
        import winreg

        host_root = self.jobops / "BrowserCompanionHost"
        manifest_path = host_root / f"{_NATIVE_HOST}.json"
        executable = host_root / "JobFlowBrowserCompanionHost.exe"
        self._ordinary(manifest_path, self.jobops)
        self._ordinary(executable, self.jobops)
        try:
            manifest = load_json(manifest_path)
            stores = load_json(project / "config" / "browser-companion-stores.json")
        except (OSError, UnicodeError, ValueError) as error:
            raise JobOpsError("CLEAN_WINDOWS_NATIVE_HOST_INVALID", "The Browser Companion native host is invalid.") from error
        _version, production_ids, _store_urls = _load_browser_policy(project)
        expected_origins = {f"chrome-extension://{item}/" for item in production_ids.values()}
        if (
            manifest.get("name") != _NATIVE_HOST
            or manifest.get("type") != "stdio"
            or os.path.normcase(str(manifest.get("path", ""))) != os.path.normcase(str(executable))
            or set(manifest.get("allowed_origins", [])) != expected_origins
        ):
            _fail("CLEAN_WINDOWS_NATIVE_HOST_INVALID", "The Browser Companion native host manifest is invalid.")
        for key_name in _NATIVE_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ) as key:
                    value, kind = winreg.QueryValueEx(key, None)
            except OSError as error:
                raise JobOpsError("CLEAN_WINDOWS_NATIVE_HOST_INVALID", "The Browser Companion native host registration is missing.") from error
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or os.path.normcase(str(value)) != os.path.normcase(str(manifest_path)):
                _fail("CLEAN_WINDOWS_NATIVE_HOST_INVALID", "The Browser Companion native host registration is invalid.")

    def browsers(self, project: Path, timeout_seconds: int) -> dict[str, Any]:
        with BrowserAcceptanceProbe(project, local_app_data=self.local_app_data) as probe:
            probe.open_browsers()
            return probe.wait(timeout_seconds)

    def rollback(self) -> None:
        script = self.jobops / "bin" / "rollback-installed-jobflow.ps1"
        self._ordinary(script, self.jobops)
        result = self._powershell(script, "-StartNewRollback", timeout=600)
        if result.returncode != 0:
            _fail("CLEAN_WINDOWS_ROLLBACK_FAILED", "The signed JobFlow installation did not roll back cleanly.")
        value = self._json(result.stdout, code="CLEAN_WINDOWS_ROLLBACK_INVALID")
        if value.get("status") not in {"JOBFLOW_BOOTSTRAP_ROLLED_BACK", "JOBFLOW_ROLLBACK_ALREADY_COMMITTED"} or value.get("real_external_actions") != 0:
            _fail("CLEAN_WINDOWS_ROLLBACK_FAILED", "The signed JobFlow installation did not roll back cleanly.")

    def uninstall(self) -> None:
        script = self.jobops / "bin" / "uninstall-installed-jobflow.ps1"
        self._ordinary(script, self.jobops)
        result = self._powershell(script, "-RemoveUserData", "-UserConfirmed", timeout=600)
        if result.returncode != 0:
            _fail("CLEAN_WINDOWS_UNINSTALL_FAILED", "JobFlow did not uninstall cleanly.")

    def assert_clean(self) -> None:
        if self._has_preexisting():
            _fail("CLEAN_WINDOWS_UNINSTALL_RESIDUE", "JobFlow left product-owned state after uninstall.")


def _build_evidence(
    *,
    publisher: EvidenceDocument,
    bundle: dict[str, Any],
    version: str,
    commit: str,
    browser: dict[str, Any],
    issued: datetime,
) -> bytes:
    publisher_expires = parse_iso(str(publisher.value["expires_at_utc"]))
    expires = min(issued + timedelta(hours=4), publisher_expires)
    if expires <= issued + timedelta(minutes=1):
        _fail("CLEAN_WINDOWS_EVIDENCE_WINDOW_INVALID", "Publisher evidence expires too soon for clean-Windows acceptance.")
    companion = browser.get("browser_companion", {})
    value = {
        "schema_version": 1,
        "format": "JOBFLOW_CLEAN_WINDOWS_ACCEPTANCE_V1",
        "evidence_kind": "SANITIZED_CLEAN_WINDOWS_OBSERVATION",
        "status": "PASS",
        "issued_at_utc": iso_utc(issued),
        "expires_at_utc": iso_utc(expires),
        "publisher_evidence_sha256": publisher.sha256,
        "release": {"version": version, "source_commit": commit, "platform": "windows-x64"},
        "signed_bundle": {
            "manifest_sha256": bundle["manifest_sha256"],
            "signature_sha256": bundle["signature_sha256"],
            "archive_name": bundle["asset_name"],
            "archive_bytes": bundle["asset_bytes"],
            "archive_sha256": bundle["asset_sha256"],
            "release_key_id": bundle["key_id"],
            "signature_verified_with_pinned_trust": True,
        },
        "runtime_closure": {
            "manifest_sha256": bundle["runtime_closure_manifest_sha256"],
            "tree_sha256": bundle["runtime_tree_sha256"],
            "structural_status": "BUILT_UNATTESTED",
        },
        "environment": {
            "os_family": "Windows",
            "architecture": "AMD64",
            "account_profile": "FRESH_STANDARD_USER",
            "preexisting_jobflow": False,
        },
        "browser_companion": {
            "version": companion["version"],
            "chrome_store_version": companion["version"],
            "edge_store_version": companion["version"],
            "chrome_install_passed": companion["chrome_store_install_observed"],
            "edge_install_passed": companion["edge_store_install_observed"],
            "native_host_registration_passed": companion["native_binding_proof_observed"],
            "chrome_pairing_passed": companion["chrome_pairing_observed"],
            "edge_pairing_passed": companion["edge_pairing_observed"],
        },
        "checks": {
            "install_passed": True,
            "startup_passed": True,
            "health_passed": True,
            "update_passed": True,
            "rollback_passed": True,
            "uninstall_passed": True,
        },
        "safety": {
            "external_actions": 0,
            "real_job_site_visits": 0,
            "final_submit_attempts": 0,
            "secret_material_in_evidence": False,
        },
    }
    return canonical_json(value)


def _write_evidence(output: Path, raw: bytes, *, project: Path) -> None:
    if not output.is_absolute():
        _fail("CLEAN_WINDOWS_OUTPUT_INVALID", "The clean-Windows evidence output must be an absolute external path.")
    absolute = Path(os.path.abspath(output))
    parent = absolute.parent
    try:
        parent = parent.resolve(strict=True)
    except OSError as error:
        raise JobOpsError("CLEAN_WINDOWS_OUTPUT_INVALID", "The clean-Windows evidence output directory is unavailable.") from error
    if is_relative_to(absolute, project) or has_reparse_component(parent) or absolute.exists():
        _fail("CLEAN_WINDOWS_OUTPUT_INVALID", "The clean-Windows evidence output must be a new file outside the project.")
    created = False
    try:
        descriptor = os.open(absolute, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except OSError as error:
        if created:
            try:
                absolute.unlink(missing_ok=True)
            except OSError:
                pass
        raise JobOpsError("CLEAN_WINDOWS_OUTPUT_INVALID", "The clean-Windows evidence output could not be created.") from error


def _orchestrate(
    project: Path,
    *,
    current: ReleaseMaterial,
    predecessor: ReleaseMaterial,
    commit: str,
    output: Path,
    browser_timeout_seconds: int,
    backend_factory: type[WindowsAcceptanceBackend] = WindowsAcceptanceBackend,
    now: datetime | None = None,
) -> dict[str, Any]:
    if _COMMIT.fullmatch(commit) is None or _version_tuple(predecessor.version) >= _version_tuple(current.version):
        _fail("CLEAN_WINDOWS_RELEASE_IDENTITY_INVALID", "The clean-Windows release sequence is invalid.")
    if not isinstance(browser_timeout_seconds, int) or not 30 <= browser_timeout_seconds <= 900:
        _fail("CLEAN_WINDOWS_BROWSER_TIMEOUT_INVALID", "Browser acceptance timeout must be between 30 and 900 seconds.")
    project = project.resolve(strict=True)
    issued = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current = _release_material(current, label="current")
    predecessor = _release_material(predecessor, label="predecessor")
    dist = project / "dist"
    current_expected = ReleaseMaterial(
        current.version,
        dist / "JobFlow-update-manifest.json",
        dist / "JobFlow-update-manifest.sig.json",
        dist / f"JobFlow-v{current.version}-windows-x64-complete.zip",
    )
    if any(os.path.normcase(str(left)) != os.path.normcase(str(right.resolve(strict=True))) for left, right in zip((current.manifest, current.signature, current.archive), (current_expected.manifest, current_expected.signature, current_expected.archive))):
        _fail("CLEAN_WINDOWS_CURRENT_INPUT_INVALID", "Current acceptance inputs must be the fixed protected release files.")

    runtime_path = dist / "JobFlow-runtime-build-evidence.json"
    publisher_path = dist / "JobFlow-publisher-evidence.json"
    with ExitStack() as stack:
        current_handles = tuple(
            stack.enter_context(_open_locked_runtime_file(path, f"current-{index}"))
            for index, path in enumerate((current.manifest, current.signature, current.archive))
        )
        predecessor_handles = tuple(
            stack.enter_context(_open_locked_runtime_file(path, f"predecessor-{index}"))
            for index, path in enumerate((predecessor.manifest, predecessor.signature, predecessor.archive))
        )
        stack.enter_context(_open_locked_runtime_file(runtime_path, "runtime-build-evidence"))
        stack.enter_context(_open_locked_runtime_file(publisher_path, "publisher-evidence"))
        runtime, publisher, current_bundle = _clean_import_context(project, version=current.version, commit=commit, now=issued)
        predecessor_bundle = verify_signed_release_bundle(
            predecessor.manifest,
            predecessor.signature,
            predecessor.archive,
            release_version=predecessor.version,
            channel_path=project / "config" / "update-channel.json",
            schema_dir=project / "schemas",
        )
        update_bundle = verify_signed_update_bundle(
            current.manifest,
            current.signature,
            current.archive,
            current_version=predecessor.version,
            channel_path=project / "config" / "update-channel.json",
            schema_dir=project / "schemas",
        )
        if update_bundle.get("status") != "UPDATE_BUNDLE_VERIFIED" or update_bundle.get("commit") != commit:
            _fail("CLEAN_WINDOWS_UPDATE_PATH_INVALID", "The signed current release does not authorize this predecessor update.")
        if current_bundle.get("commit") != commit or current_bundle.get("publisher_evidence_sha256") != publisher.sha256:
            _fail("CLEAN_WINDOWS_RELEASE_BINDING_INVALID", "The current signed release is not bound to its publisher evidence.")

        temporary_base = windows_temp_directory() if os.name == "nt" else Path(tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(prefix="jobflow-clean-windows-", dir=temporary_base) as temporary_name:
            temporary_root = Path(temporary_name).resolve(strict=True)
            if has_reparse_component(temporary_root, temporary_base):
                _fail("CLEAN_WINDOWS_STAGING_REPARSE_REJECTED", "The clean-Windows staging boundary is unsafe.")
            predecessor_controls, predecessor_archive = _stage_release(
                temporary_root, "predecessor", predecessor, predecessor_bundle, predecessor_handles
            )
            current_controls, current_archive = _stage_release(
                temporary_root, "current", current, current_bundle, current_handles
            )
            backend = backend_factory(temporary_root)
            backend.preflight()
            backend.install(predecessor_controls, predecessor_archive)
            backend.pointer(version=predecessor.version, commit=str(predecessor_bundle["commit"]))
            backend.health(version=predecessor.version)
            backend.install(current_controls, current_archive)
            backend.pointer(version=current.version, commit=commit)
            backend.pointer(version=predecessor.version, commit=str(predecessor_bundle["commit"]), previous=True)
            backend.health(version=current.version)
            backend.startup()
            backend.native_host(project)
            browser = backend.browsers(project, browser_timeout_seconds)
            backend.rollback()
            backend.pointer(version=predecessor.version, commit=str(predecessor_bundle["commit"]))
            backend.health(version=predecessor.version)
            backend.install(current_controls, current_archive)
            backend.pointer(version=current.version, commit=commit)
            backend.pointer(version=predecessor.version, commit=str(predecessor_bundle["commit"]), previous=True)
            backend.health(version=current.version)
            backend.uninstall()
            backend.assert_clean()

        final_runtime, final_publisher, final_bundle = _clean_import_context(project, version=current.version, commit=commit, now=issued)
        if (
            final_runtime.sha256 != runtime.sha256
            or final_publisher.sha256 != publisher.sha256
            or any(final_bundle.get(key) != current_bundle.get(key) for key in ("manifest_sha256", "signature_sha256", "asset_sha256", "asset_bytes", "commit"))
        ):
            _fail("CLEAN_WINDOWS_RELEASE_CHANGED", "Protected release inputs changed during clean-Windows acceptance.")
        raw = _build_evidence(
            publisher=publisher,
            bundle=current_bundle,
            version=current.version,
            commit=commit,
            browser=browser,
            issued=issued,
        )
        document = validate_clean_windows_acceptance(
            raw,
            publisher_evidence=publisher,
            now=issued,
            schema_dir=project / "schemas",
        )
        sensitive_fragments = [str(project), str(runtime_path), str(publisher_path)]
        raw_lower = raw.lower()
        if any(fragment.casefold().encode("utf-8") in raw_lower for fragment in sensitive_fragments):
            _fail("CLEAN_WINDOWS_EVIDENCE_LEAK", "Clean-Windows evidence contains a local path.")
        _write_evidence(output, document.canonical_bytes, project=project)
        return {
            "schema_version": 1,
            "status": "CLEAN_WINDOWS_ACCEPTANCE_PASS",
            "version": current.version,
            "source_commit": commit,
            "evidence_sha256": document.sha256,
            "external_actions": 0,
            "real_job_site_visits": 0,
            "final_submit_attempts": 0,
        }


def run_clean_windows_acceptance(
    project: Path,
    *,
    version: str,
    commit: str,
    predecessor_version: str,
    predecessor_manifest: Path,
    predecessor_signature: Path,
    predecessor_archive: Path,
    output: Path,
    browser_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run the only production clean-Windows acceptance path.

    The public entry point always constructs the real Windows backend.  Tests
    replace the backend class at the module boundary; callers cannot submit
    booleans or observations and have them converted into release evidence.
    """

    project = Path(project).resolve(strict=True)
    current = ReleaseMaterial(
        version,
        project / "dist" / "JobFlow-update-manifest.json",
        project / "dist" / "JobFlow-update-manifest.sig.json",
        project / "dist" / f"JobFlow-v{version}-windows-x64-complete.zip",
    )
    predecessor = ReleaseMaterial(
        predecessor_version,
        predecessor_manifest,
        predecessor_signature,
        predecessor_archive,
    )
    return _orchestrate(
        project,
        current=current,
        predecessor=predecessor,
        commit=commit,
        output=output,
        browser_timeout_seconds=browser_timeout_seconds,
    )


__all__ = ["run_clean_windows_acceptance"]
