from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:12].upper()}"


def project_root(start: Path | None = None) -> Path:
    cursor = (start or Path(__file__)).resolve()
    if cursor.is_file():
        cursor = cursor.parent
    for candidate in (cursor, *cursor.parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise RuntimeError("JOBOPS_PROJECT_ROOT_NOT_FOUND")


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def has_reparse_component(path: Path, stop_at: Path | None = None) -> bool:
    """Reject symlinks and Windows reparse points on an existing path."""
    path = Path(os.path.abspath(path))
    stop = Path(os.path.abspath(stop_at)) if stop_at else None
    chain: list[Path] = []
    cursor = path
    while True:
        chain.append(cursor)
        if stop is not None and cursor == stop:
            break
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for item in reversed(chain):
        if not item.exists():
            continue
        try:
            stat = item.lstat()
        except OSError:
            return True
        attrs = getattr(stat, "st_file_attributes", 0)
        if item.is_symlink() or bool(attrs & 0x400):
            return True
    return False


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def tree_fingerprint(root: Path, files: Iterable[Path]) -> dict[str, object]:
    records: list[str] = []
    count = 0
    for path in sorted(files, key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        records.append(f"{relative}\t{sha256_file(path).removeprefix('sha256:')}\t{path.stat().st_size}")
        count += 1
    payload = ("\n".join(records) + ("\n" if records else "")).encode("utf-8")
    return {"file_count": count, "tree_sha256": sha256_bytes(payload)}
