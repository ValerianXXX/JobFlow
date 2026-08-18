from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from .errors import SecurityBoundaryError
from .security import assert_project_io_path, path_has_hard_excluded_name
from .util import has_reparse_component, is_relative_to


RUNTIME_DATA_ENV = "JOBFLOW_DATA_ROOT"
RUNTIME_DATA_MARKER = ".jobflow-data-root"
RUNTIME_DATA_MARKER_VALUE = {"schema_version": 1, "kind": "JOBFLOW_RUNTIME_DATA"}
RUNTIME_AREAS = frozenset({"state", "workspace", "reports"})


def runtime_data_root(
    project: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the optional installed-runtime data root without trusting links.

    Source checkouts keep their historical project-local state layout.  A fixed
    Windows installation opts into a separate data root through one process
    environment variable and a non-secret marker created by the installer.
    """

    project = project.resolve(strict=True)
    environment = os.environ if environ is None else environ
    raw = str(environment.get(RUNTIME_DATA_ENV, "")).strip()
    if not raw:
        return project
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise SecurityBoundaryError(
            "RUNTIME_DATA_ROOT_INVALID",
            "The installed JobFlow data root must be an absolute local path.",
        )
    absolute = Path(os.path.abspath(candidate))
    if not absolute.is_dir() or has_reparse_component(absolute):
        raise SecurityBoundaryError(
            "RUNTIME_DATA_ROOT_UNTRUSTED",
            "The installed JobFlow data root is missing or crosses a link or reparse point.",
        )
    resolved = absolute.resolve(strict=True)
    if resolved == project or is_relative_to(resolved, project):
        raise SecurityBoundaryError(
            "RUNTIME_DATA_ROOT_NOT_SEPARATE",
            "Installed JobFlow data must be stored outside the immutable application version.",
        )
    marker = resolved / RUNTIME_DATA_MARKER
    if not marker.is_file() or has_reparse_component(marker, resolved):
        raise SecurityBoundaryError(
            "RUNTIME_DATA_MARKER_MISSING",
            "The installed JobFlow data root marker is missing or unsafe.",
        )
    try:
        value = json.loads(marker.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SecurityBoundaryError(
            "RUNTIME_DATA_MARKER_INVALID",
            "The installed JobFlow data root marker is invalid.",
        ) from exc
    if value != RUNTIME_DATA_MARKER_VALUE:
        raise SecurityBoundaryError(
            "RUNTIME_DATA_MARKER_INVALID",
            "The installed JobFlow data root marker is invalid.",
        )
    return resolved


def runtime_path(
    project: Path,
    area: str,
    *parts: str | Path,
    operation: str,
) -> Path:
    """Return a bounded runtime path in source or installed mode."""

    if operation not in {"read", "write"}:
        raise ValueError(operation)
    if area not in RUNTIME_AREAS:
        raise SecurityBoundaryError(
            "RUNTIME_DATA_AREA_INVALID",
            "JobFlow runtime data is limited to state, workspace, and reports.",
        )
    relative = Path(area)
    for part in parts:
        value = Path(part)
        if value.is_absolute() or ".." in value.parts:
            raise SecurityBoundaryError(
                "RUNTIME_DATA_PATH_INVALID",
                "A JobFlow runtime path must remain relative to its approved data area.",
            )
        relative /= value
    project = project.resolve(strict=True)
    root = runtime_data_root(project)
    candidate = Path(os.path.abspath(root / relative))
    if root == project:
        return assert_project_io_path(candidate, project, operation=operation)
    if path_has_hard_excluded_name(candidate, (), ()) or not is_relative_to(candidate, root):
        raise SecurityBoundaryError(
            "RUNTIME_DATA_PATH_INVALID",
            "The requested JobFlow runtime path is outside its approved data root.",
        )
    existing_parent = candidate if candidate.exists() else candidate.parent
    if has_reparse_component(existing_parent, root):
        raise SecurityBoundaryError(
            "REPARSE_POINT_DISALLOWED",
            "JobFlow runtime data cannot cross a link or reparse point.",
        )
    if operation == "read" and candidate.exists() and has_reparse_component(candidate, root):
        raise SecurityBoundaryError(
            "REPARSE_POINT_DISALLOWED",
            "JobFlow runtime data cannot cross a link or reparse point.",
        )
    return candidate


def runtime_relative_path(project: Path, path: Path) -> str:
    """Serialize a runtime artifact without persisting a machine path."""

    root = runtime_data_root(project)
    absolute = Path(path).resolve(strict=False)
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise SecurityBoundaryError(
            "RUNTIME_DATA_PATH_INVALID",
            "Only a JobFlow runtime artifact may be stored as a runtime-relative path.",
        ) from exc
    return relative.as_posix()
