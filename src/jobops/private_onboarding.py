from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Iterator

from .db import JobOpsDB
from .errors import JobOpsError
from .secure_store import WindowsDPAPIStore
from .util import has_reparse_component, is_relative_to, iso_utc, sha256_bytes


PRIVATE_KINDS = {
    "candidate_profile", "answer_bank", "master_resume_docx", "master_resume_pdf", "claim_approvals",
    "generated_resume_docx", "generated_resume_pdf", "review_packet", "visual_evidence",
    "resume_analysis", "claim_candidates", "onboarding_review_packet",
    "onboarding_center_state", "onboarding_source_document", "onboarding_ai_derived",
    "onboarding_completion_packet",
}
MAX_PRIVATE_IMPORT_FILE_BYTES = 64 * 1024 * 1024


class PrivateOnboarding:
    def __init__(self, database: JobOpsDB, store: WindowsDPAPIStore) -> None:
        self.database = database
        self.store = store

    def assert_outside_project(self, project: Path) -> None:
        project_root = project.resolve(strict=True)
        private_root = self.store.private_root.absolute()
        if is_relative_to(private_root, project_root) or is_relative_to(project_root, private_root):
            raise JobOpsError(
                "PRIVATE_STORE_PROJECT_OVERLAP",
                "The DPAPI private root and project directory must be completely separate.",
            )
        if has_reparse_component(private_root):
            raise JobOpsError(
                "PRIVATE_STORE_REPARSE_FORBIDDEN",
                "The DPAPI private root cannot traverse a link or Windows reparse point.",
            )

    def _staging_root(self) -> Path:
        self.store.private_root.mkdir(parents=True, exist_ok=True)
        staging = self.store.private_root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        if has_reparse_component(staging, self.store.private_root):
            raise JobOpsError(
                "PRIVATE_STAGING_UNSAFE",
                "Private staging cannot traverse a link or Windows reparse point.",
            )
        return staging

    def clear_staging_residue(self) -> dict[str, object]:
        """Remove crash residue only after the caller holds the local instance lock."""
        staging = self._staging_root()
        entries: list[Path] = []
        directories = [staging]
        while directories:
            directory = directories.pop()
            for path in directory.iterdir():
                entries.append(path)
                if has_reparse_component(path, staging):
                    raise JobOpsError(
                        "PRIVATE_STAGING_REPARSE_FORBIDDEN",
                        "Private staging residue contains a link or Windows reparse point and was not removed.",
                    )
                if path.is_dir():
                    directories.append(path)
        top_level = list(staging.iterdir())
        deleted = len(entries)
        for path in top_level:
            if not is_relative_to(path.absolute(), staging.absolute()):
                raise JobOpsError("PRIVATE_STAGING_BOUNDARY_INVALID", "Private staging cleanup left its controlled boundary.")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        return {
            "status": "PRIVATE_STAGING_CLEAN",
            "staging_items_deleted": deleted,
            "private_values_emitted": 0,
            "real_external_actions": 0,
        }

    def import_bytes(self, kind: str, value: bytes, *, synthetic: bool = False) -> dict[str, object]:
        if kind not in PRIVATE_KINDS:
            raise JobOpsError("PRIVATE_KIND_INVALID", "Unsupported private onboarding kind.", kind=kind)
        now = iso_utc()
        display = kind.replace("_", "-")
        content_hash = sha256_bytes(value)
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM private_refs WHERE kind=? AND content_sha256=? AND status='ACTIVE' AND synthetic=? ORDER BY version DESC LIMIT 1",
                (kind, content_hash, int(synthetic)),
            ).fetchone()
        if existing is not None:
            return {
                "secure_ref": existing["secure_ref"], "kind": kind, "display_name": existing["display_name"],
                "content_sha256": existing["content_sha256"], "ciphertext_sha256": existing["ciphertext_sha256"],
                "version": int(existing["version"]), "status": "ACTIVE", "deduplicated": True,
            }
        stored = self.store.put_bytes(value)
        secure_ref = str(stored["secure_ref"])
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO private_refs(
                    secure_ref,kind,display_name,ciphertext_sha256,content_sha256,version,status,synthetic,created_at,updated_at)
                    VALUES(?,?,?,?,?,1,'ACTIVE',?,?,?)""",
                    (secure_ref, kind, display, stored["ciphertext_sha256"], content_hash, int(synthetic), now, now),
                )
        except Exception as exc:
            try:
                self.store.delete(secure_ref)
            except Exception as cleanup_error:
                raise JobOpsError(
                    "PRIVATE_IMPORT_ROLLBACK_FAILED",
                    "Private storage could not be rolled back after its local metadata transaction failed.",
                ) from cleanup_error
            raise JobOpsError(
                "PRIVATE_IMPORT_DATABASE_FAILED",
                "Private storage was removed after its local metadata transaction failed.",
            ) from exc
        return {"secure_ref": secure_ref, "kind": kind, "display_name": display, "content_sha256": content_hash, "ciphertext_sha256": stored["ciphertext_sha256"], "version": 1, "status": "ACTIVE", "deduplicated": False}

    def import_file(self, kind: str, selected_path: Path, *, synthetic: bool = False) -> dict[str, object]:
        selected_path = Path(selected_path).absolute()
        if has_reparse_component(selected_path):
            raise JobOpsError(
                "PRIVATE_IMPORT_REPARSE_FORBIDDEN",
                "The explicitly selected private import file cannot traverse a link or Windows reparse point.",
            )
        try:
            path_before = selected_path.stat()
            if not stat.S_ISREG(path_before.st_mode):
                raise JobOpsError("PRIVATE_IMPORT_FILE_MISSING", "The explicitly selected private import file does not exist.")
            if path_before.st_size <= 0:
                raise JobOpsError("PRIVATE_IMPORT_FILE_EMPTY", "The explicitly selected private import file is empty.")
            if path_before.st_size > MAX_PRIVATE_IMPORT_FILE_BYTES:
                raise JobOpsError(
                    "PRIVATE_IMPORT_FILE_TOO_LARGE",
                    "The explicitly selected private import file exceeds the bounded local import limit.",
                    maximum_bytes=MAX_PRIVATE_IMPORT_FILE_BYTES,
                )
            with selected_path.open("rb") as handle:
                opened_before = os.fstat(handle.fileno())
                value = handle.read(MAX_PRIVATE_IMPORT_FILE_BYTES + 1)
                opened_after = os.fstat(handle.fileno())
            path_after = selected_path.stat()
        except JobOpsError:
            raise
        except OSError as exc:
            raise JobOpsError(
                "PRIVATE_IMPORT_FILE_UNAVAILABLE",
                "The explicitly selected private import file could not be read safely.",
            ) from exc
        identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        snapshots = (path_before, opened_before, opened_after, path_after)
        if any(getattr(snapshot, field) != getattr(path_before, field) for snapshot in snapshots[1:] for field in identity_fields):
            raise JobOpsError(
                "PRIVATE_IMPORT_FILE_CHANGED",
                "The explicitly selected private import file changed while it was being read; select it again.",
            )
        if len(value) > MAX_PRIVATE_IMPORT_FILE_BYTES:
            raise JobOpsError(
                "PRIVATE_IMPORT_FILE_TOO_LARGE",
                "The explicitly selected private import file exceeds the bounded local import limit.",
                maximum_bytes=MAX_PRIVATE_IMPORT_FILE_BYTES,
            )
        return self.import_bytes(kind, value, synthetic=synthetic)

    def _record(self, reference: str):
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM private_refs WHERE secure_ref=?", (reference,)).fetchone()
        if row is None:
            raise JobOpsError("SECURE_REFERENCE_MISSING", "Secure reference is not registered.")
        return row

    def read_bytes(self, reference: str) -> bytes:
        row = self._record(reference)
        if row["status"] != "ACTIVE":
            raise JobOpsError("SECURE_REFERENCE_REVOKED", "Secure reference is not active.", status=row["status"])
        value = self.store.get_bytes(reference)
        if sha256_bytes(value) != row["content_sha256"]:
            raise JobOpsError("SECURE_CONTENT_HASH_MISMATCH", "Decrypted private content failed integrity verification.")
        return value

    def rotate(self, reference: str, value: bytes) -> dict[str, object]:
        row = self._record(reference)
        if row["status"] != "ACTIVE":
            raise JobOpsError("SECURE_REFERENCE_REVOKED", "Only an active secure reference can rotate.")
        previous = self.read_bytes(reference)
        try:
            stored = self.store.put_bytes(value, reference=reference)
        except Exception as exc:
            try:
                self.store.put_bytes(previous, reference=reference)
            except Exception as rollback_error:
                try:
                    with self.database.connect() as connection:
                        connection.execute(
                            "UPDATE private_refs SET status='CORRUPT',updated_at=? WHERE secure_ref=?",
                            (iso_utc(), reference),
                        )
                except Exception:
                    pass
                raise JobOpsError(
                    "PRIVATE_ROTATION_ROLLBACK_FAILED",
                    "Private storage could not restore its prior content after an interrupted ciphertext update.",
                ) from rollback_error
            raise JobOpsError(
                "PRIVATE_ROTATION_WRITE_FAILED",
                "Private storage restored its prior content after an interrupted ciphertext update.",
            ) from exc
        version = int(row["version"]) + 1
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE private_refs SET ciphertext_sha256=?,content_sha256=?,version=?,updated_at=? WHERE secure_ref=?",
                    (stored["ciphertext_sha256"], sha256_bytes(value), version, iso_utc(), reference),
                )
        except Exception as exc:
            try:
                self.store.put_bytes(previous, reference=reference)
            except Exception as rollback_error:
                try:
                    with self.database.connect() as connection:
                        connection.execute(
                            "UPDATE private_refs SET status='CORRUPT',updated_at=? WHERE secure_ref=?",
                            (iso_utc(), reference),
                        )
                except Exception:
                    pass
                raise JobOpsError(
                    "PRIVATE_ROTATION_ROLLBACK_FAILED",
                    "Private storage could not restore its prior content after a local metadata failure.",
                ) from rollback_error
            raise JobOpsError(
                "PRIVATE_ROTATION_DATABASE_FAILED",
                "Private storage restored its prior content after a local metadata failure.",
            ) from exc
        return {"secure_ref": reference, "version": version, "status": "ACTIVE", "content_sha256": sha256_bytes(value)}

    def revoke(self, reference: str) -> dict[str, object]:
        self._record(reference)
        with self.database.connect() as connection:
            connection.execute("UPDATE private_refs SET status='REVOKED',updated_at=? WHERE secure_ref=?", (iso_utc(), reference))
        return {"secure_ref": reference, "status": "REVOKED"}

    def delete(self, reference: str, *, user_confirmed: bool) -> dict[str, object]:
        if not user_confirmed:
            raise JobOpsError("PRIVATE_DELETE_CONFIRMATION_REQUIRED", "Private deletion requires explicit user confirmation.")
        row = self._record(reference)
        previous_status = str(row["status"])
        try:
            ciphertext_present = self.store.test(reference)
        except Exception as exc:
            raise JobOpsError(
                "PRIVATE_DELETE_STORAGE_PROBE_FAILED",
                "Private deletion could not verify the local ciphertext state; no metadata was changed.",
            ) from exc
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE private_refs SET status='DELETED',updated_at=? WHERE secure_ref=?",
                    (iso_utc(), reference),
                )
        except Exception as exc:
            raise JobOpsError(
                "PRIVATE_DELETE_DATABASE_FAILED",
                "Private deletion could not reserve the reference for deletion; the ciphertext was retained.",
            ) from exc
        if ciphertext_present:
            try:
                self.store.delete(reference)
            except Exception as exc:
                # A helper can fail after performing the deletion. Probe before
                # rolling metadata back so an already-complete deletion remains
                # internally consistent and idempotent.
                try:
                    ciphertext_present = self.store.test(reference)
                except Exception as probe_error:
                    try:
                        with self.database.connect() as connection:
                            connection.execute(
                                "UPDATE private_refs SET status='CORRUPT',updated_at=? WHERE secure_ref=?",
                                (iso_utc(), reference),
                            )
                    except Exception:
                        pass
                    raise JobOpsError(
                        "PRIVATE_DELETE_STATE_UNKNOWN",
                        "Private deletion could not determine the final ciphertext state; the reference was quarantined.",
                    ) from probe_error
                if ciphertext_present:
                    try:
                        with self.database.connect() as connection:
                            connection.execute(
                                "UPDATE private_refs SET status=?,updated_at=? WHERE secure_ref=?",
                                (previous_status, iso_utc(), reference),
                            )
                    except Exception as rollback_error:
                        raise JobOpsError(
                            "PRIVATE_DELETE_ROLLBACK_FAILED",
                            "Private deletion could not restore its prior metadata after a local storage failure.",
                        ) from rollback_error
                    raise JobOpsError(
                        "PRIVATE_DELETE_STORAGE_FAILED",
                        "Private deletion failed and restored its prior reference status.",
                    ) from exc
        return {"secure_ref": reference, "status": "DELETED", "deleted": ["ciphertext", "reference", "staging_cache"], "secure_erase_claimed": False}

    def purge_synthetic(self) -> dict[str, object]:
        with self.database.connect() as connection:
            refs = [row[0] for row in connection.execute("SELECT secure_ref FROM private_refs WHERE synthetic=1 AND status!='DELETED'")]
        for reference in refs:
            self.delete(reference, user_confirmed=True)
        return {"status": "PURGED", "synthetic_refs_deleted": len(refs), "secure_erase_claimed": False}

    def _remove_staging_directory(self, directory: Path) -> None:
        staging = self._staging_root()
        if not is_relative_to(directory.absolute(), staging.absolute()) or directory == staging:
            raise JobOpsError(
                "PRIVATE_STAGING_BOUNDARY_INVALID",
                "Private staging cleanup refused a directory outside its controlled session boundary.",
            )
        if directory.exists() and has_reparse_component(directory, staging):
            raise JobOpsError(
                "PRIVATE_STAGING_REPARSE_FORBIDDEN",
                "Private staging cleanup found a link or Windows reparse point and stopped.",
            )
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise JobOpsError(
                "PRIVATE_STAGING_CLEANUP_FAILED",
                "A private staging session could not be completely removed; restart JobFlow to retry locked cleanup.",
            ) from exc
        if directory.exists():
            raise JobOpsError(
                "PRIVATE_STAGING_CLEANUP_FAILED",
                "A private staging session still exists after cleanup and must not be treated as cleared.",
            )

    @contextlib.contextmanager
    def staged_file(self, reference: str, suffix: str) -> Iterator[Path]:
        if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix) is None:
            raise JobOpsError("PRIVATE_STAGING_SUFFIX_INVALID", "Private staging accepts only a simple file extension.")
        staging = self._staging_root()
        directory = Path(tempfile.mkdtemp(prefix="jobops-stage-", dir=staging))
        target = directory / ("material" + suffix)
        try:
            target.write_bytes(self.read_bytes(reference))
            yield target
        finally:
            self._remove_staging_directory(directory)

    @contextlib.contextmanager
    def staging_directory(self) -> Iterator[Path]:
        """Create a private, OneDrive-external working directory and always clean it."""
        staging = self._staging_root()
        directory = Path(tempfile.mkdtemp(prefix="jobops-stage-", dir=staging))
        try:
            yield directory
        finally:
            self._remove_staging_directory(directory)
