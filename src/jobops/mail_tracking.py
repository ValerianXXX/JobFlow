"""Read-only application-status proposals. No mailbox access, sends or protected-state writes."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Protocol
from .errors import JobOpsError
from .util import canonical_json, sha256_bytes


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
    def read_messages(self, *, after: str | None, limit: int) -> list[StatusMessage]: ...


class SyntheticStatusAdapter:
    def __init__(self, messages: list[StatusMessage]):
        self.messages = messages

    def read_messages(self, *, after: str | None = None, limit: int = 50) -> list[StatusMessage]:
        cutoff = datetime.fromisoformat(after) if after else None
        return [message for message in self.messages if not cutoff or datetime.fromisoformat(message.received_at) > cutoff][:limit]


def propose_status(message: StatusMessage, applications: list[dict]) -> dict:
    if not message.message_id or len(message.message_id) > 300 or len(message.subject) + len(message.body) > 50_000:
        raise JobOpsError("MAIL_STATUS_INPUT_INVALID", "The supplied status message exceeds the bounded adapter contract.")
    try:
        moment = datetime.fromisoformat(message.received_at)
        if moment.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise JobOpsError("MAIL_STATUS_DATE_INVALID", "A timezone-aware receipt timestamp is required.") from exc
    text = message.subject + "\n" + message.body
    patterns = {
        "APPLICATION_RECEIVED": r"application (?:has been |was )?received|received your application|申请已收到|收到您的申请",
        "REJECTED": r"not (?:be )?moving forward|not selected|unsuccessful|未通过|未能进入|不予录用",
        "INTERVIEW_INVITED": r"invite you (?:to|for) (?:an? )?interview|interview invitation|邀请.{0,8}面试|面试邀请",
    }
    matches = [(status, match) for status, pattern in patterns.items() if (match := re.search(pattern, text, re.I))]
    # Conflicting receipt/outcome phrases stay uncertain; no last-write-wins guesses.
    status = matches[0][0] if len(matches) == 1 else "UNKNOWN"
    candidates, reasons = [], []
    domain = message.sender_domain.strip().casefold()
    if message.authenticated_sender is True:
        for application in applications:
            domains = {str(x).casefold() for x in application.get("allowed_sender_domains", [])}
            if domain not in domains:
                continue
            reference = str(application.get("external_reference", "")).strip()
            role = str(application.get("role", "")).strip()
            exact_ref = reference and re.search(r"(?<![\w-])" + re.escape(reference) + r"(?![\w-])", text, re.I)
            role_match = len(role) >= 5 and role.casefold() in text.casefold()
            if exact_ref or role_match:
                candidates.append(str(application["application_id"]))
                reasons.append("EXACT_REFERENCE_AND_SENDER" if exact_ref else "ROLE_AND_SENDER_REVIEW")
    linked = len(candidates) == 1 and reasons == ["EXACT_REFERENCE_AND_SENDER"] and status != "UNKNOWN"
    digest = sha256_bytes(canonical_json(asdict(message)))
    return {"event_id": digest, "received_at": message.received_at, "evidence_sha256": digest,
            "status_proposal": status, "application_id": candidates[0] if linked else None,
            "candidate_application_ids": candidates, "match_reasons": reasons,
            "review_status": "MATCHED_AWAITING_REVIEW" if linked else "UNCERTAIN",
            "status_evidence_spans": [{"start": match.start(), "end": match.end()} for _, match in matches],
            "application_state_changed": False, "real_external_actions": 0}


class ApplicationStatusInbox:
    """Keep original evidence and review history in the existing private encrypted store."""
    def __init__(self, private_onboarding, *, prior_review_ref: str | None = None):
        import json
        self.private = private_onboarding
        self.events = json.loads(private_onboarding.store.get_bytes(prior_review_ref)) if prior_review_ref else []

    def collect(self, adapter: ReadOnlyStatusAdapter, applications: list[dict], *, after: str | None = None, limit: int = 50) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JobOpsError("MAIL_STATUS_LIMIT", "Read at most 100 supplied messages per batch.")
        messages = adapter.read_messages(after=after, limit=limit)
        if len(messages) > limit:
            raise JobOpsError("MAIL_STATUS_LIMIT", "The adapter exceeded the requested message limit.")
        known = {event["event_id"] for event in self.events}
        added = 0
        for message in messages:
            event = propose_status(message, applications)
            if event["event_id"] in known:
                continue
            stored = self.private.import_bytes("application_mail_evidence", canonical_json(asdict(message)))
            event["evidence_ref"] = stored["secure_ref"]
            event["review_history"] = []
            self.events.append(event); known.add(event["event_id"]); added += 1
        self.events.sort(key=lambda event: datetime.fromisoformat(event["received_at"]))
        stored = self.private.import_bytes("application_mail_review", canonical_json(self.events))
        return {"status": "REVIEW_REQUIRED", "review_ref": stored["secure_ref"], "added": added,
                "uncertain": sum(event["review_status"] == "UNCERTAIN" for event in self.events),
                "application_state_changed": False, "real_external_actions": 0}

    def review(self, event_id: str, *, application_id: str | None, decision: str, confirmed: bool = False) -> dict:
        if confirmed is not True or decision not in {"ACCEPT_LINK", "REJECT_LINK"}:
            raise JobOpsError("MAIL_STATUS_REVIEW_REQUIRED", "Explicitly confirm or reject the evidence link.")
        event = next((event for event in self.events if event["event_id"] == event_id), None)
        if event is None or (decision == "ACCEPT_LINK" and application_id not in event["candidate_application_ids"]):
            raise JobOpsError("MAIL_STATUS_LINK_INVALID", "Choose an application supported by the evidence or reject the link.")
        event["review_history"].append({"decision": decision, "application_id": application_id})
        event["application_id"] = application_id if decision == "ACCEPT_LINK" else None
        event["review_status"] = "USER_LINK_CONFIRMED" if decision == "ACCEPT_LINK" else "USER_LINK_REJECTED"
        stored = self.private.import_bytes("application_mail_review", canonical_json(self.events))
        return {"review_ref": stored["secure_ref"], "application_state_changed": False, "real_external_actions": 0}
