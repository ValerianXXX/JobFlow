from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import JobOpsError
from .util import has_reparse_component, is_relative_to


VERSION_DIRECTORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
VERSION_DIRECTORY_V2_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
PREFIXED_SHA256_RE = re.compile(r"sha256:[a-f0-9]{64}")
COMMIT_RE = re.compile(r"[a-f0-9]{40}")

POINTER_V1_FIELDS = {
    "schema_version", "version_directory", "version", "source_sha256",
}
POINTER_V2_FIELDS = {
    "schema_version", "product", "version_directory", "version", "source_commit",
    "source_payload_sha256", "runtime_closure_manifest_sha256", "runtime_tree_sha256",
    "release_key_id", "bootstrap_version", "platform",
}


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _ordinary_single_link_file(path: Path, anchor: Path) -> bool:
    """Return true only for a confined, ordinary, single-link local file."""

    try:
        if not is_relative_to(path, anchor) or has_reparse_component(path, anchor):
            return False
        stat = path.stat()
    except OSError:
        return False
    return path.is_file() and int(getattr(stat, "st_nlink", 1)) == 1


def _resolve_pointer(pointer: object) -> tuple[str, str]:
    """Validate an installed pointer without consulting its runtime."""

    if not isinstance(pointer, dict):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    schema_version = pointer.get("schema_version")
    if type(schema_version) is not int:  # bool and JSON floats must fail closed.
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    if schema_version == 1:
        if set(pointer) != POINTER_V1_FIELDS:
            raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
        version_directory = pointer.get("version_directory")
        version = pointer.get("version")
        source_sha256 = pointer.get("source_sha256")
        if (
            not isinstance(version_directory, str)
            or VERSION_DIRECTORY_RE.fullmatch(version_directory) is None
            or not isinstance(version, str)
            or VERSION_RE.fullmatch(version) is None
            or not isinstance(source_sha256, str)
            or SHA256_RE.fullmatch(source_sha256) is None
        ):
            raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
        return version_directory, ""
    if schema_version != 2 or set(pointer) != POINTER_V2_FIELDS:
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    version_directory = pointer.get("version_directory")
    version = pointer.get("version")
    source_payload_sha256 = pointer.get("source_payload_sha256")
    if (
        pointer.get("product") != "JobFlow"
        or not isinstance(version_directory, str)
        or VERSION_DIRECTORY_V2_RE.fullmatch(version_directory) is None
        or not isinstance(version, str)
        or VERSION_RE.fullmatch(version) is None
        or not isinstance(pointer.get("source_commit"), str)
        or COMMIT_RE.fullmatch(str(pointer["source_commit"])) is None
        or not isinstance(source_payload_sha256, str)
        or PREFIXED_SHA256_RE.fullmatch(source_payload_sha256) is None
        or not isinstance(pointer.get("runtime_closure_manifest_sha256"), str)
        or PREFIXED_SHA256_RE.fullmatch(str(pointer["runtime_closure_manifest_sha256"])) is None
        or not isinstance(pointer.get("runtime_tree_sha256"), str)
        or PREFIXED_SHA256_RE.fullmatch(str(pointer["runtime_tree_sha256"])) is None
        or pointer.get("release_key_id") != "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
        or not isinstance(pointer.get("bootstrap_version"), str)
        or VERSION_RE.fullmatch(str(pointer["bootstrap_version"])) is None
        or pointer.get("platform") != "windows-x64"
    ):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    digest = source_payload_sha256.removeprefix("sha256:")
    if version_directory != f"v{version}-{digest[:12]}":
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    return version_directory, "runtime/python.exe"


def _installed_layout(
    project: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path] | None:
    """Resolve the active fixed installation without exposing its path."""

    environment = os.environ if environ is None else environ
    raw_local_app_data = str(environment.get("LOCALAPPDATA", "")).strip()
    if not raw_local_app_data:
        return None
    local_app_data = Path(os.path.abspath(raw_local_app_data))
    local_root = Path(os.path.abspath(local_app_data / "JobOps"))
    versions_root = Path(os.path.abspath(local_root / "Application" / "versions"))
    pointer_path = Path(os.path.abspath(local_root / "current.json"))
    launcher_path = Path(os.path.abspath(local_root / "Update JobFlow.cmd"))
    project_path = Path(os.path.abspath(project))

    if not all(path.exists() for path in (local_app_data, local_root, versions_root, pointer_path, launcher_path)):
        return None
    if any(
        has_reparse_component(path, local_app_data)
        for path in (local_root, versions_root, pointer_path, launcher_path)
    ):
        raise JobOpsError(
            "JOBFLOW_UPDATE_INSTALL_UNTRUSTED",
            "The fixed JobFlow update installation crosses a link or reparse point.",
        )
    if not is_relative_to(project_path, versions_root):
        return None

    if not _ordinary_single_link_file(pointer_path, local_root):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    if not _ordinary_single_link_file(launcher_path, local_root):
        raise JobOpsError("JOBFLOW_UPDATE_INSTALL_INCOMPLETE", "The fixed JobFlow update installation is incomplete.")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobOpsError(
            "JOBFLOW_UPDATE_POINTER_INVALID",
            "The fixed JobFlow installation pointer is invalid.",
        ) from exc
    version_directory, runtime_relative = _resolve_pointer(pointer)
    active_root = Path(os.path.abspath(versions_root / version_directory))
    if not is_relative_to(active_root, versions_root) or has_reparse_component(active_root, versions_root):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    if not _same_path(active_root, project_path):
        return None
    marker_path = active_root / ".jobops-root"
    runtime_path = active_root / runtime_relative if runtime_relative else None
    if (
        not _ordinary_single_link_file(marker_path, active_root)
        or (runtime_path is not None and not _ordinary_single_link_file(runtime_path, active_root))
        or not launcher_path.is_file()
    ):
        raise JobOpsError(
            "JOBFLOW_UPDATE_INSTALL_INCOMPLETE",
            "The fixed JobFlow update installation is incomplete.",
        )
    return local_root, launcher_path


def update_availability(
    project: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    installed = _installed_layout(project, environ=environ) is not None
    return {
        "status": "AVAILABLE" if installed else "INSTALL_REQUIRED",
        "available": installed,
        "mode": "USER_INITIATED_SIGNED_UPDATE" if installed else "FIXED_INSTALL_REQUIRED",
        "automatic_check": False,
        "automatic_install": False,
        "rollback_on_failed_health_check": True,
    }


def launch_installed_update(
    project: Path,
    *,
    user_confirmed: bool,
    environ: Mapping[str, str] | None = None,
    shell_launcher: Callable[[str], object] | None = None,
) -> dict[str, object]:
    if user_confirmed is not True:
        raise JobOpsError(
            "EXPLICIT_CONFIRMATION_REQUIRED",
            "Checking for a desktop update requires an explicit user action.",
        )
    layout = _installed_layout(project, environ=environ)
    if layout is None:
        raise JobOpsError(
            "JOBFLOW_UPDATE_INSTALL_REQUIRED",
            "Install JobFlow in its fixed current-user directory before using one-click updates.",
        )
    _, launcher_path = layout
    launch = shell_launcher or getattr(os, "startfile", None)
    if launch is None:
        raise JobOpsError(
            "JOBFLOW_UPDATE_PLATFORM_UNSUPPORTED",
            "The one-click JobFlow updater is available only on Windows.",
        )
    try:
        launch(str(launcher_path))
    except OSError as exc:
        raise JobOpsError(
            "JOBFLOW_UPDATE_LAUNCH_FAILED",
            "The signed update window could not be opened. The current version was not changed.",
        ) from exc
    return {
        "status": "JOBFLOW_UPDATE_WINDOW_OPENED",
        "user_initiated": True,
        "signed_manifest_required": True,
        "health_check_required": True,
        "rollback_on_failure": True,
        "restart_required_after_update": True,
        "automatic_update": False,
        "real_recruitment_actions": 0,
    }
