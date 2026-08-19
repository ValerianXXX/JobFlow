from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import JobOpsError
from .util import has_reparse_component, is_relative_to


VERSION_DIRECTORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA256_RE = re.compile(r"[a-f0-9]{64}")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


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

    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobOpsError(
            "JOBFLOW_UPDATE_POINTER_INVALID",
            "The fixed JobFlow installation pointer is invalid.",
        ) from exc
    if not isinstance(pointer, dict) or set(pointer) != {
        "schema_version", "version_directory", "version", "source_sha256"
    }:
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    version_directory = pointer.get("version_directory")
    if (
        pointer.get("schema_version") != 1
        or not isinstance(version_directory, str)
        or VERSION_DIRECTORY_RE.fullmatch(version_directory) is None
        or not isinstance(pointer.get("version"), str)
        or VERSION_RE.fullmatch(str(pointer["version"])) is None
        or not isinstance(pointer.get("source_sha256"), str)
        or SHA256_RE.fullmatch(str(pointer["source_sha256"])) is None
    ):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    active_root = Path(os.path.abspath(versions_root / version_directory))
    if not is_relative_to(active_root, versions_root) or has_reparse_component(active_root, versions_root):
        raise JobOpsError("JOBFLOW_UPDATE_POINTER_INVALID", "The fixed JobFlow installation pointer is invalid.")
    if not _same_path(active_root, project_path):
        return None
    if not (active_root / ".jobops-root").is_file() or not launcher_path.is_file():
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
