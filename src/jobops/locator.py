from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import LocationError
from .security import assert_safe_path
from .util import has_reparse_component, load_json


@dataclass(frozen=True)
class LocatedKnowledge:
    root: Path
    discovery_method: str
    manifest_path: Path
    sources: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "RESOLVED",
            "code": "UNIQUE_KNOWLEDGE_ROOT_FOUND",
            "knowledge_root": str(self.root),
            "discovery_method": self.discovery_method,
            "manifest_path": str(self.manifest_path),
            "sources": list(self.sources),
        }


def _validate_manifest(manifest: dict[str, object], path: Path) -> None:
    if manifest.get("schema_version") != 1:
        raise LocationError("UNSUPPORTED_MANIFEST", "Knowledge manifest schema_version must be 1.", manifest=str(path))
    markers = manifest.get("candidate_root_markers")
    sources = manifest.get("sources")
    if not isinstance(markers, list) or not markers or not isinstance(sources, list) or not sources:
        raise LocationError("INVALID_MANIFEST", "Knowledge manifest is missing markers or sources.", manifest=str(path))


def _read_location_document(path: Path) -> Path:
    try:
        document = load_json(path)
        raw = document["knowledge_root"]
    except Exception as exc:
        raise LocationError("PRIVATE_LOCATION_INVALID", "The configured knowledge location document is invalid.", path=str(path), error=type(exc).__name__) from exc
    if not isinstance(raw, str) or not raw.strip():
        raise LocationError("PRIVATE_LOCATION_INVALID", "knowledge_root must be a non-empty path.", path=str(path))
    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = path.parent / candidate
    return candidate


def _validate_candidate(candidate: Path, manifest: dict[str, object]) -> tuple[bool, str]:
    try:
        candidate = candidate.absolute()
        if not candidate.is_dir():
            return False, "directory_not_found"
        if has_reparse_component(candidate):
            return False, "reparse_point_disallowed"
        for marker in manifest["candidate_root_markers"]:  # type: ignore[index]
            marker_path = assert_safe_path(
                candidate / str(marker),
                candidate,
                manifest.get("hard_excluded_segments", []),  # type: ignore[arg-type]
                manifest.get("hard_excluded_filenames", []),  # type: ignore[arg-type]
            )
            if not marker_path.exists():
                return False, f"marker_missing:{marker}"
        return True, "validated"
    except Exception as exc:
        return False, getattr(exc, "code", type(exc).__name__)


def _nearby_candidates(start: Path, max_ancestor_depth: int) -> list[Path]:
    names = ("AI计划", "AI工作站", "AI工作站工作区")
    cursor = start.absolute()
    if cursor.is_file():
        cursor = cursor.parent
    candidates: list[Path] = []
    for depth in range(max_ancestor_depth + 1):
        candidates.append(cursor)
        for name in names:
            candidates.append(cursor / name)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return candidates


def _resolve_sources(root: Path, manifest: dict[str, object]) -> tuple[dict[str, object], ...]:
    resolved: list[dict[str, object]] = []
    for source in manifest["sources"]:  # type: ignore[index]
        if not isinstance(source, dict):
            raise LocationError("INVALID_SOURCE_DEFINITION", "Every source must be an object.")
        source_root = assert_safe_path(
            root / str(source.get("root_subpath", ".")),
            root,
            manifest.get("hard_excluded_segments", []),  # type: ignore[arg-type]
            manifest.get("hard_excluded_filenames", []),  # type: ignore[arg-type]
        )
        missing: list[str] = []
        for marker in source.get("markers", []):
            marker_path = source_root / str(marker)
            try:
                assert_safe_path(
                    marker_path,
                    source_root,
                    manifest.get("hard_excluded_segments", []),  # type: ignore[arg-type]
                    manifest.get("hard_excluded_filenames", []),  # type: ignore[arg-type]
                )
            except Exception:
                missing.append(str(marker))
        if missing:
            raise LocationError("SOURCE_MARKERS_MISSING", "A configured knowledge source failed marker validation.", source_id=source.get("id"), missing=missing)
        resolved.append({
            "source_id": source.get("id"),
            "classification": source.get("classification"),
            "resolved_path": str(source_root),
            "allowed_prefixes": source.get("allowed_prefixes", ["."]),
            "external_claim_policy": source.get("external_claim_policy", "context_only"),
            "status": "READ_ONLY_RESOLVED",
        })
    return tuple(resolved)


def locate_knowledge_root(
    start: Path,
    manifest_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
    local_config_path: Path | None = None,
    max_ancestor_depth: int = 4,
) -> LocatedKnowledge:
    environment = environment if environment is not None else os.environ
    manifest_path = manifest_path.resolve(strict=True)
    manifest = load_json(manifest_path)
    _validate_manifest(manifest, manifest_path)

    env_location = environment.get("JOBOPS_KNOWLEDGE_MANIFEST")
    if env_location is not None:
        location_path = Path(os.path.expandvars(env_location)).expanduser()
        if not location_path.is_file():
            raise LocationError("ENV_MANIFEST_INVALID", "JOBOPS_KNOWLEDGE_MANIFEST was set but does not name a readable file; fallback is disabled.", path=str(location_path))
        candidate = _read_location_document(location_path)
        valid, reason = _validate_candidate(candidate, manifest)
        if not valid:
            raise LocationError("ENV_CANDIDATE_INVALID", "The environment-selected knowledge root failed validation; fallback is disabled.", path=str(candidate), reason=reason)
        root = candidate.resolve(strict=True)
        return LocatedKnowledge(root, "environment_manifest", manifest_path, _resolve_sources(root, manifest))

    if local_config_path is None:
        local_base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
        local_config_path = local_base / "JobOps" / "knowledge-location.json"
    if local_config_path.exists():
        candidate = _read_location_document(local_config_path)
        valid, reason = _validate_candidate(candidate, manifest)
        if not valid:
            raise LocationError("PRIVATE_CANDIDATE_INVALID", "The private knowledge location failed validation; fallback is disabled.", path=str(candidate), reason=reason)
        root = candidate.resolve(strict=True)
        return LocatedKnowledge(root, "private_location_config", manifest_path, _resolve_sources(root, manifest))

    attempts: list[dict[str, str]] = []
    valid_roots: dict[str, Path] = {}
    for candidate in _nearby_candidates(start, max_ancestor_depth):
        key = os.path.normcase(os.path.abspath(candidate))
        if key in {entry["path"] for entry in attempts}:
            continue
        valid, reason = _validate_candidate(candidate, manifest)
        attempts.append({"path": key, "reason": reason})
        if valid:
            root = candidate.resolve(strict=True)
            valid_roots[os.path.normcase(str(root))] = root
    if not valid_roots:
        raise LocationError("KNOWLEDGE_ROOT_NOT_FOUND", "No knowledge root passed all marker checks within the bounded search.", attempts=attempts)
    if len(valid_roots) > 1:
        raise LocationError("MULTIPLE_KNOWLEDGE_ROOTS", "More than one knowledge root passed validation; user selection is required.", candidates=sorted(str(path) for path in valid_roots.values()))
    root = next(iter(valid_roots.values()))
    return LocatedKnowledge(root, "bounded_nearby_search", manifest_path, _resolve_sources(root, manifest))

