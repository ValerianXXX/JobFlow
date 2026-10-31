"""One-page, read-only connector contract; only a synthetic adapter is shipped.

Account/folder scope and provider cursors stay in encrypted private checkpoints.
The caller supplies the latest review journal and serializes access. This is not
OAuth, a mailbox client, a scheduler, or a cross-process synchronization service.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Protocol, TYPE_CHECKING
from .errors import JobOpsError
from .util import canonical_json, sha256_bytes

if TYPE_CHECKING:
    from .mail_tracking import StatusMessage


@dataclass(frozen=True)
class StatusPage:
    messages: list[StatusMessage] | tuple[StatusMessage, ...]
    next_cursor: str | None
    complete: bool


class ReadOnlyPagedStatusAdapter(Protocol):
    # Stable opaque identity for the authorized account AND selected folder/label
    # and filtering policy. It is not the author's email or a forged From header.
    source_id: str

    # Implementations must bound their own I/O deadline and enforce the approved
    # read-only scope. Provider authentication is distinct from sender proof.
    def read_page(self, *, cursor: str | None, limit: int) -> StatusPage: ...


class SyntheticPagedStatusAdapter:
    def __init__(self, messages, *, source_id="synthetic-status-adapter"):
        self.messages = list(messages)
        self.source_id = source_id

    def read_page(self, *, cursor=None, limit=50):
        try:
            if cursor is None:
                offset = 0
            elif not isinstance(cursor, str) or not cursor.startswith("synthetic-page-v1:"):
                raise ValueError()
            else:
                offset = int(cursor.removeprefix("synthetic-page-v1:"))
            if not 0 <= offset <= len(self.messages):
                raise ValueError()
        except (TypeError, ValueError):
            raise JobOpsError("MAIL_CURSOR_EXPIRED", "The synthetic cursor is unavailable.") from None
        selected = self.messages[offset:offset + limit]
        next_offset = offset + len(selected)
        return StatusPage(selected, f"synthetic-page-v1:{next_offset}", next_offset == len(self.messages))


def _valid_cursor(value):
    return value is None or (isinstance(value, str) and 1 <= len(value) <= 4096)


def collect_page(inbox, adapter: ReadOnlyPagedStatusAdapter, applications, *, checkpoint_ref=None, limit=50):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise JobOpsError("MAIL_STATUS_LIMIT", "Read at most 100 supplied messages per page.")
    source_id = getattr(adapter, "source_id", None)
    if not isinstance(source_id, str) or not 1 <= len(source_id) <= 300:
        raise JobOpsError("MAIL_SYNC_SCOPE_INVALID", "The connector must identify its authorized read scope.")
    scope_hash = sha256_bytes(source_id.encode("utf-8"))
    cursor = None
    if checkpoint_ref is not None:
        try:
            reader = getattr(inbox.private, "read_bytes", inbox.private.store.get_bytes)
            raw = reader(checkpoint_ref)
            if len(raw) > 16384:
                raise ValueError()
            checkpoint = json.loads(raw)
            if (not isinstance(checkpoint, dict) or type(checkpoint.get("schema_version")) is not int
                    or checkpoint["schema_version"] != 1 or not isinstance(checkpoint.get("source_scope_sha256"), str)
                    or "cursor" not in checkpoint or not _valid_cursor(checkpoint["cursor"])
                    or type(checkpoint.get("complete")) is not bool
                    or not isinstance(checkpoint.get("review_ref"), str)):
                raise ValueError()
        except Exception:
            raise JobOpsError("MAIL_SYNC_CHECKPOINT_INVALID", "The encrypted mail checkpoint is unavailable or invalid.") from None
        if checkpoint["source_scope_sha256"] != scope_hash:
            raise JobOpsError("MAIL_SYNC_SCOPE_MISMATCH", "The saved cursor belongs to another connector scope.")
        try:
            prior = json.loads(reader(checkpoint["review_ref"]))
            if not isinstance(prior, list) or len(prior) > 1000:
                raise ValueError()
            current = {event["event_id"]: event for event in inbox.events}
            stale = any(event["event_id"] not in current
                or event["evidence_sha256"] != current[event["event_id"]]["evidence_sha256"]
                or event["review_history"] != current[event["event_id"]]["review_history"][:len(event["review_history"])]
                for event in prior)
        except Exception:
            raise JobOpsError("MAIL_SYNC_CHECKPOINT_INVALID", "The saved cursor's evidence journal is unavailable or invalid.") from None
        if stale:
            raise JobOpsError("MAIL_SYNC_INBOX_STALE", "Reload the latest evidence journal before using this cursor.")
        cursor = checkpoint["cursor"]
    try:
        page = adapter.read_page(cursor=cursor, limit=limit)
    except Exception as error:
        if isinstance(error, JobOpsError) and error.code == "MAIL_CURSOR_EXPIRED":
            raise JobOpsError("MAIL_CURSOR_EXPIRED", "The provider cursor expired. Review the read scope before explicitly restarting.") from None
        raise JobOpsError("MAIL_PAGE_UNAVAILABLE", "The read-only adapter did not return a page. The cursor was not advanced.") from None
    if (not isinstance(page, StatusPage) or not isinstance(page.messages, (list, tuple))
            or len(page.messages) > limit or type(page.complete) is not bool
            or not _valid_cursor(page.next_cursor)):
        raise JobOpsError("MAIL_PAGE_INVALID", "The adapter returned an invalid or oversized page.")
    if not page.complete and (page.next_cursor is None or page.next_cursor == cursor):
        raise JobOpsError("MAIL_SYNC_NO_PROGRESS", "An incomplete page must provide a new continuation cursor.")
    result = inbox._collect_messages(page.messages, applications, source_scope=scope_hash)
    checkpoint = {"schema_version": 1, "source_scope_sha256": scope_hash,
        "cursor": page.next_cursor, "complete": page.complete, "review_ref": result["review_ref"]}
    try:
        saved = inbox.private.import_bytes("application_mail_sync_checkpoint", canonical_json(checkpoint))
        reference = saved["secure_ref"]
    except Exception:
        # The review journal committed first. Keep its evidence and user decisions;
        # retry the old cursor against the latest inbox to deduplicate safely.
        raise JobOpsError("MAIL_SYNC_CHECKPOINT_FAILED", "Messages were retained, but the cursor was not saved. Reload the latest inbox and retry the previous checkpoint.") from None
    return {**result, "checkpoint_ref": reference, "complete": page.complete}
