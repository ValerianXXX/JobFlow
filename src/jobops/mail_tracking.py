"""Read-only application-status proposals. No mailbox access, sends or protected-state writes."""

from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
import re
from typing import Protocol
from lxml.etree import ParserError
from .errors import JobOpsError
from .mail_interpretation import interpret_status
from .util import canonical_json, sha256_bytes

MAX_EML_BYTES = 2 * 1024 * 1024


class LocalEmlAdapter:
    """An explicitly selected file is evidence, not authenticated mailbox delivery."""

    def __init__(self, raw: bytes):
        if not raw or len(raw) > MAX_EML_BYTES:
            raise JobOpsError(
                "MAIL_FILE_LIMIT", "Select one EML file of at most 2 MiB."
            )
        try:
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            if (
                parsed.defects
                or len(parsed.get_all("From", [])) != 1
                or len(parsed.get_all("Date", [])) != 1
            ):
                raise ValueError()
            moment = parsedate_to_datetime(str(parsed["Date"]))
            if moment.tzinfo is None:
                raise ValueError()
            address = parseaddr(str(parsed["From"]))[1]
            domain = address.rsplit("@", 1)[1].casefold()
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
                raise ValueError()
            parts = list(parsed.walk())
            if len(parts) > 50:
                raise ValueError()
            part = parsed.get_body(preferencelist=("plain", "html"))
            if part is None or part.get_content_disposition() == "attachment":
                raise ValueError()
            body = part.get_content()
            if not isinstance(body, str) or len(body) > 50_000:
                raise ValueError()
            if part.get_content_type() == "text/html":
                from lxml import html

                root = html.fromstring(body)
                for element in root.xpath("//script|//style|//iframe|//object|//head"):
                    element.drop_tree()
                body = root.text_content()
            subject = str(parsed.get("Subject", ""))
            if len(subject) + len(body) > 50_000:
                raise ValueError()
            # Raw content identity is stable even without a Message-ID. Ignore all
            # Authentication-Results headers: an imported file can forge them.
            self.message = StatusMessage(
                sha256_bytes(raw), moment.isoformat(), domain, False, subject, body
            )
            self.raw = raw
        except (
            ValueError,
            TypeError,
            IndexError,
            UnicodeError,
            LookupError,
            ParserError,
        ):
            raise JobOpsError(
                "MAIL_FILE_INVALID",
                "The EML must contain a bounded message body, sender and timezone-aware date.",
            ) from None

    def read_messages(
        self, *, after: str | None = None, limit: int = 50
    ) -> list[StatusMessage]:
        if after and datetime.fromisoformat(
            self.message.received_at
        ) <= datetime.fromisoformat(after):
            return []
        return [self.message][:limit]


@dataclass(frozen=True)
class StatusMessage:
    message_id: str
    received_at: str
    sender_domain: str
    authenticated_sender: bool
    subject: str
    body: str


class ReadOnlyStatusAdapter(Protocol):
    """A future connector must enforce user-approved mailbox scope and attest sender checks."""

    def read_messages(
        self, *, after: str | None, limit: int
    ) -> list[StatusMessage]: ...


class SyntheticStatusAdapter:
    def __init__(self, messages: list[StatusMessage]):
        self.messages = messages

    def read_messages(
        self, *, after: str | None = None, limit: int = 50
    ) -> list[StatusMessage]:
        cutoff = datetime.fromisoformat(after) if after else None
        return [
            message
            for message in self.messages
            if not cutoff or datetime.fromisoformat(message.received_at) > cutoff
        ][:limit]


def propose_status(message: StatusMessage, applications: list[dict]) -> dict:
    if (
        not message.message_id
        or len(message.message_id) > 300
        or len(message.subject) + len(message.body) > 50_000
    ):
        raise JobOpsError(
            "MAIL_STATUS_INPUT_INVALID",
            "The supplied status message exceeds the bounded adapter contract.",
        )
    try:
        moment = datetime.fromisoformat(message.received_at)
        if moment.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise JobOpsError(
            "MAIL_STATUS_DATE_INVALID",
            "A timezone-aware receipt timestamp is required.",
        ) from exc
    interpretation = interpret_status(message.subject, message.body)
    text, status = interpretation["active_text"], interpretation["status"]
    exact_candidates, role_candidates = [], []
    domain = message.sender_domain.strip().casefold()
    if message.authenticated_sender is True:
        for application in applications:
            domains = {
                str(x).casefold() for x in application.get("allowed_sender_domains", [])
            }
            if domain not in domains:
                continue
            reference = str(application.get("external_reference", "")).strip()
            role = str(application.get("role", "")).strip()
            exact_ref = reference and re.search(
                r"(?<![\w-])" + re.escape(reference) + r"(?![\w-])", text, re.I
            )
            role_match = len(role) >= 5 and role.casefold() in text.casefold()
            if exact_ref:
                exact_candidates.append(str(application["application_id"]))
            elif role_match:
                role_candidates.append(str(application["application_id"]))
    # A known reference identifies the role more precisely than a shared title.
    # Multiple references still require a decision; history does not add candidates.
    candidates = list(dict.fromkeys(exact_candidates or role_candidates))
    reasons = [("EXACT_REFERENCE_AND_SENDER" if exact_candidates else "ROLE_AND_SENDER_REVIEW")] * len(candidates)
    linked = (
        len(candidates) == 1
        and reasons == ["EXACT_REFERENCE_AND_SENDER"]
        and status != "UNKNOWN"
    )
    digest = sha256_bytes(canonical_json(asdict(message)))
    return {
        "event_id": digest,
        "received_at": message.received_at,
        "evidence_sha256": digest,
        "status_proposal": status,
        "application_id": candidates[0] if linked else None,
        "candidate_application_ids": candidates,
        "match_reasons": reasons,
        "review_status": "MATCHED_AWAITING_REVIEW" if linked else "UNCERTAIN",
        "status_evidence_spans": interpretation["spans"],
        "status_review_reasons": interpretation["review_reasons"],
        "application_state_changed": False,
        "real_external_actions": 0,
    }


class ApplicationStatusInbox:
    """Keep original evidence and review history in the existing private encrypted store."""

    def __init__(self, private_onboarding, *, prior_review_ref: str | None = None):
        import json

        self.private = private_onboarding
        reader = getattr(
            private_onboarding, "read_bytes", private_onboarding.store.get_bytes
        )
        self.events = json.loads(reader(prior_review_ref)) if prior_review_ref else []
        if not isinstance(self.events, list) or len(self.events) > 1000:
            raise JobOpsError(
                "MAIL_INBOX_INVALID", "The encrypted status inbox needs review."
            )

    def collect(
        self,
        adapter: ReadOnlyStatusAdapter,
        applications: list[dict],
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> dict:
        from copy import deepcopy

        if type(limit) is not int or not 1 <= limit <= 100:
            raise JobOpsError(
                "MAIL_STATUS_LIMIT", "Read at most 100 supplied messages per batch."
            )
        messages = adapter.read_messages(after=after, limit=limit)
        if len(messages) > limit:
            raise JobOpsError(
                "MAIL_STATUS_LIMIT", "The adapter exceeded the requested message limit."
            )
        known = {event["event_id"] for event in self.events}
        proposals = [
            (message, propose_status(message, applications)) for message in messages
        ]
        if len(known | {event["event_id"] for _, event in proposals}) > 1000:
            raise JobOpsError(
                "MAIL_INBOX_LIMIT",
                "The local status inbox has reached its 1000-message limit.",
            )
        updated, created, added = deepcopy(self.events), [], 0

        def retain(kind, raw):
            record = self.private.import_bytes(kind, raw)
            # Deduplicated evidence can belong to an earlier import: never
            # remove it when compensating this new, incomplete transaction.
            if not record.get("deduplicated", False):
                created.append(record["secure_ref"])
            return record["secure_ref"]

        try:
            for message, event in proposals:
                if event["event_id"] in known:
                    continue
                event["evidence_ref"] = retain(
                    "application_mail_evidence", canonical_json(asdict(message))
                )
                if isinstance(adapter, LocalEmlAdapter):
                    event["original_ref"] = retain(
                        "application_mail_source", adapter.raw
                    )
                    event["source_kind"] = "LOCAL_EML_UNAUTHENTICATED"
                    event["timestamp_kind"] = "UNVERIFIED_DATE_HEADER"
                event["review_history"] = []
                updated.append(event)
                known.add(event["event_id"])
                added += 1
            updated.sort(key=lambda event: datetime.fromisoformat(event["received_at"]))
            stored = self.private.import_bytes(
                "application_mail_review", canonical_json(updated)
            )
        except Exception:
            failures = 0
            for reference in reversed(created):
                try:
                    self.private.delete(reference, user_confirmed=True)
                except Exception:
                    failures += 1
            if failures:
                raise JobOpsError(
                    "MAIL_IMPORT_ROLLBACK_FAILED",
                    "The incomplete mail import needs private-storage repair.",
                ) from None
            raise JobOpsError(
                "MAIL_IMPORT_FAILED",
                "The mail import did not commit; newly created evidence was removed.",
            ) from None
        self.events = updated
        return {
            "status": "REVIEW_REQUIRED",
            "review_ref": stored["secure_ref"],
            "added": added,
            "uncertain": sum(
                event["review_status"] == "UNCERTAIN" for event in self.events
            ),
            "application_state_changed": False,
            "real_external_actions": 0,
        }

    def review(
        self,
        event_id: str,
        *,
        application_id: str | None,
        decision: str,
        confirmed: bool = False,
    ) -> dict:
        if confirmed is not True or decision not in {"ACCEPT_LINK", "REJECT_LINK"}:
            raise JobOpsError(
                "MAIL_STATUS_REVIEW_REQUIRED",
                "Explicitly confirm or reject the evidence link.",
            )
        event = next(
            (event for event in self.events if event["event_id"] == event_id), None
        )
        if event is None or (
            decision == "ACCEPT_LINK"
            and application_id not in event["candidate_application_ids"]
        ):
            raise JobOpsError(
                "MAIL_STATUS_LINK_INVALID",
                "Choose an application supported by the evidence or reject the link.",
            )
        return self._save_review(event_id, application_id, decision,
            linked=decision == "ACCEPT_LINK",
            review_status="USER_LINK_CONFIRMED" if decision == "ACCEPT_LINK" else "USER_LINK_REJECTED")

    def manual_review(
        self,
        event_id: str,
        *,
        application_id: str | None,
        decision: str,
        allowed_application_ids: set[str],
        confirmed: bool = False,
    ) -> dict:
        """Keep the user's association separate from a connector-authenticated match."""
        if confirmed is not True or decision not in {"MANUAL_LINK", "CLEAR_REVIEW"}:
            raise JobOpsError(
                "MAIL_STATUS_REVIEW_REQUIRED",
                "Confirm this manual evidence association.",
            )
        event = next(
            (item for item in self.events if item["event_id"] == event_id), None
        )
        if event is None or (
            decision == "MANUAL_LINK" and application_id not in allowed_application_ids
        ):
            raise JobOpsError(
                "MAIL_STATUS_LINK_INVALID", "Choose an existing application."
            )
        # This operation does not authenticate the sender or write to applications.
        return self._save_review(event_id, application_id, decision,
            linked=decision == "MANUAL_LINK",
            review_status="USER_MANUALLY_ASSOCIATED" if decision == "MANUAL_LINK" else "UNCERTAIN")

    def _save_review(self, event_id, application_id, decision, *, linked, review_status):
        from copy import deepcopy
        from .util import iso_utc

        updated = deepcopy(self.events)
        event = next(item for item in updated if item["event_id"] == event_id)
        event["review_history"].append({"decision": decision, "application_id": application_id, "reviewed_at": iso_utc()})
        event["application_id"] = application_id if linked else None
        event["review_status"] = review_status
        try:
            stored = self.private.import_bytes("application_mail_review", canonical_json(updated))
            reference = stored["secure_ref"]
        except Exception:
            raise JobOpsError("MAIL_REVIEW_SAVE_FAILED", "The evidence decision was not saved. Reload and try again.") from None
        # Publish in-memory state only after the encrypted journal commits.
        self.events = updated
        return {
            "review_ref": reference,
            "application_state_changed": False,
            "real_external_actions": 0,
        }
