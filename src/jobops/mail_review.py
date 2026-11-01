"""Source-bound human mail interpretation, separate from application execution."""
from .errors import JobOpsError
from .util import canonical_json, sha256_bytes


OUTCOME_STATUSES = frozenset({"APPLICATION_RECEIVED", "REJECTED", "INTERVIEW_INVITED", "UNKNOWN"})
MAX_QUOTE_CHARACTERS = 500


def verified_message_text(event, evidence):
    try:
        if (not isinstance(evidence, dict) or not isinstance(evidence.get("subject"), str)
                or not isinstance(evidence.get("body"), str)
                or len(evidence["subject"]) + len(evidence["body"]) > 50_000
                or sha256_bytes(canonical_json(evidence)) != event["evidence_sha256"]):
            raise ValueError()
        return evidence["subject"] + "\n" + evidence["body"]
    except (KeyError, TypeError, ValueError):
        raise JobOpsError("MAIL_EVIDENCE_MISMATCH", "The saved mail evidence no longer matches its review record.") from None


def build_user_outcome(event, evidence, reported_status, evidence_quote):
    if not isinstance(reported_status, str) or reported_status not in OUTCOME_STATUSES:
        raise JobOpsError("MAIL_OUTCOME_STATUS_INVALID", "Choose one supported message outcome; application execution states are unavailable.")
    text = verified_message_text(event, evidence)
    if not isinstance(evidence_quote, str) or not 1 <= len(evidence_quote.strip()) <= MAX_QUOTE_CHARACTERS:
        raise JobOpsError("MAIL_OUTCOME_EVIDENCE_REQUIRED", "Copy a short exact passage from this message to explain your review.")
    quote = evidence_quote.strip()
    start = text.find(quote)
    if start < 0:
        raise JobOpsError("MAIL_OUTCOME_EVIDENCE_REQUIRED", "The passage must occur in the retained message without rewriting.")
    if text.find(quote, start + 1) >= 0:
        raise JobOpsError("MAIL_OUTCOME_EVIDENCE_AMBIGUOUS", "This passage occurs more than once. Include enough surrounding text to identify one occurrence.")
    return {"version": 1, "status": reported_status, "origin": "USER_MESSAGE_REVIEW",
            "evidence_sha256": event["evidence_sha256"],
            "evidence_span": {"start": start, "end": start + len(quote)},
            "quote_sha256": sha256_bytes(quote.encode("utf-8"))}


def current_user_outcome(event, evidence):
    """A later legacy link/dismiss/clear entry retires the result without a cache.

    Deriving this from the final journal entry also avoids resurrecting a stale
    result after an older build appends a review it already understands.
    """
    history = event.get("review_history", [])
    if not isinstance(history, list):
        raise JobOpsError("MAIL_OUTCOME_RECORD_INVALID", "The saved message review needs inspection.")
    if not history:
        return None
    record = history[-1]
    if not isinstance(record, dict):
        raise JobOpsError("MAIL_OUTCOME_RECORD_INVALID", "The saved message review needs inspection.")
    if record.get("decision") != "RECORD_OUTCOME":
        return None
    text = verified_message_text(event, evidence)
    try:
        outcome = record["outcome"]
        if (not isinstance(outcome, dict)
                or set(outcome) != {"version", "status", "origin", "evidence_sha256", "evidence_span", "quote_sha256"}
                or type(outcome["version"]) is not int or outcome["version"] != 1
                or not isinstance(outcome["status"], str) or outcome["status"] not in OUTCOME_STATUSES
                or outcome["origin"] != "USER_MESSAGE_REVIEW"
                or outcome["evidence_sha256"] != event["evidence_sha256"]
                or not isinstance(record.get("application_id"), str) or not record["application_id"]
                or record["application_id"] != event.get("application_id")
                or event.get("review_status") != "USER_MANUALLY_ASSOCIATED"
                or not isinstance(record.get("reviewed_at"), str) or not record["reviewed_at"]):
            raise ValueError()
        span = outcome["evidence_span"]
        if (not isinstance(span, dict) or set(span) != {"start", "end"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"] <= len(text)
                or span["end"] - span["start"] > MAX_QUOTE_CHARACTERS):
            raise ValueError()
        quote = text[span["start"]:span["end"]]
        if not quote.strip() or sha256_bytes(quote.encode("utf-8")) != outcome["quote_sha256"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise JobOpsError("MAIL_OUTCOME_RECORD_INVALID", "The saved message result is inconsistent with its evidence or association.") from None
    return {**outcome, "quote": quote, "reviewed_at": record["reviewed_at"]}
