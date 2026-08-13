from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

from .db import JobOpsDB
from .errors import JobOpsError
from .security import assert_no_plaintext_secret
from .sourcing import _canonical_url, url_has_sensitive_query
from .util import iso_utc, sha256_bytes, stable_id


MAX_COLLECTED_JD_CHARACTERS = 4_000_000
MAX_JOB_METADATA_CHARACTERS = 512


class JobCollector:
    def __init__(self, database: JobOpsDB, jobs_workspace: Path, project_root: Path | None = None) -> None:
        self.database = database
        self.jobs_workspace = jobs_workspace
        self.project_root = project_root

    def _stored_path(self, path: Path) -> str:
        if self.project_root is not None:
            try:
                return path.resolve().relative_to(self.project_root.resolve()).as_posix()
            except ValueError:
                pass
        return path.name

    def collect_text(
        self,
        content: str,
        *,
        source_type: str = "manual",
        source_locator: str = "manual-paste",
        company: str = "UNKNOWN",
        title: str = "UNKNOWN",
        official_url: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(content, str) or not content.strip():
            raise JobOpsError("JOB_SNAPSHOT_CONTENT_INVALID", "A job snapshot must contain non-empty text.")
        if len(content) > MAX_COLLECTED_JD_CHARACTERS:
            raise JobOpsError(
                "JOB_SNAPSHOT_CONTENT_TOO_LARGE",
                "The normalized job snapshot exceeds the safe storage limit.",
                maximum_characters=MAX_COLLECTED_JD_CHARACTERS,
            )
        for field_name, value in (("source_type", source_type), ("company", company), ("title", title)):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MAX_JOB_METADATA_CHARACTERS
                or any(ord(character) < 32 for character in value)
            ):
                raise JobOpsError(
                    "JOB_METADATA_INVALID",
                    "Job metadata must be bounded, non-empty display text without control characters.",
                    field=field_name,
                )
        normalized_locator = source_locator.replace("\\", "/")
        locator_path = PurePosixPath(normalized_locator)
        if (
            not source_locator
            or len(source_locator) > 512
            or "\x00" in source_locator
            or ":" in source_locator
            or locator_path.is_absolute()
            or ".." in locator_path.parts
            or any(ord(character) < 32 for character in source_locator)
        ):
            raise JobOpsError(
                "JOB_SOURCE_LOCATOR_INVALID",
                "The job source locator must be a bounded project-relative display value.",
            )
        if official_url is not None:
            official_url = _canonical_url(official_url)
            if url_has_sensitive_query(official_url):
                raise JobOpsError(
                    "JOB_SOURCE_URL_SENSITIVE_QUERY",
                    "The official job URL cannot contain authentication or private query parameters.",
                )
        assert_no_plaintext_secret(content)
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        content_hash = sha256_bytes(normalized.encode("utf-8"))
        job_id = stable_id("JOB", content_hash, official_url or source_locator)
        snapshot_id = stable_id("JDS", content_hash)
        job_dir = self.jobs_workspace / job_id / "raw"
        snapshot_path = job_dir / f"{snapshot_id}.txt"
        created = False
        temporary: Path | None = None
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT job_id, snapshot_path FROM jd_snapshots WHERE content_hash=?", (content_hash,)).fetchone()
                if existing:
                    return {"status": "DUPLICATE", "job_id": existing["job_id"], "snapshot_hash": content_hash, "snapshot_path": existing["snapshot_path"]}
                now = iso_utc()
                connection.execute(
                    "INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (job_id, source_type, source_locator, official_url, company, title, None, "DISCOVERED", now, now),
                )
                job_dir.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{snapshot_id}-", suffix=".tmp", dir=job_dir)
                os.close(descriptor)
                temporary = Path(temporary_name)
                temporary.write_text(normalized, encoding="utf-8")
                os.replace(temporary, snapshot_path)
                temporary = None
                created = True
                try:
                    connection.execute(
                        "INSERT INTO jd_snapshots VALUES(?,?,?,?,?)",
                        (snapshot_id, job_id, content_hash, self._stored_path(snapshot_path), now),
                    )
                    connection.execute("UPDATE jobs SET status='SNAPSHOTTED', updated_at=? WHERE job_id=?", (now, job_id))
                except sqlite3.IntegrityError:
                    existing = connection.execute("SELECT job_id, snapshot_path FROM jd_snapshots WHERE content_hash=?", (content_hash,)).fetchone()
                    if existing:
                        return {"status": "DUPLICATE", "job_id": existing["job_id"], "snapshot_hash": content_hash, "snapshot_path": existing["snapshot_path"]}
                    raise
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if created:
                snapshot_path.unlink(missing_ok=True)
            raise
        return {"status": "SNAPSHOTTED" if created else "DUPLICATE", "job_id": job_id, "snapshot_hash": content_hash, "snapshot_path": self._stored_path(snapshot_path)}
