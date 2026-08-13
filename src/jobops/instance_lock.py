from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import JobOpsError


def _default_lock_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise JobOpsError("LOCAL_APP_DATA_REQUIRED", "JobFlow needs the current user's local application-data folder.")
    return Path(local_app_data) / "JobOps" / "locks"


@contextmanager
def local_instance_lock(lock_root: Path | None = None) -> Iterator[None]:
    """Hold a non-blocking per-user lock while the interactive service runs."""

    root = (lock_root or _default_lock_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "onboarding-center.lock"
    handle = path.open("a+b")
    locked = False
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise JobOpsError(
                "JOBFLOW_ALREADY_RUNNING",
                "JobFlow is already running for this Windows user. Use the existing page or close it before starting again.",
            ) from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
