from __future__ import annotations

from .errors import JobOpsError


PRIMARY_STATES = (
    "DISCOVERED", "SNAPSHOTTED", "PARSED", "ELIGIBILITY_CHECKED", "SCORED",
    "SHORTLISTED", "RESEARCHED", "MATERIALS_DRAFTED", "MATERIALS_VALIDATED",
    "FORM_PREFILLED", "FORM_VALIDATED", "AWAITING_APPROVAL", "APPROVED",
    "SUBMITTING", "SUBMITTED", "CONFIRMED",
)
OUTCOME_STATES = ("FOLLOW_UP", "INTERVIEW", "REJECTED", "OFFER", "CLOSED")
BLOCKING_STATES = (
    "NEEDS_USER_INPUT", "INELIGIBLE", "BLOCKED_LOGIN", "BLOCKED_CAPTCHA",
    "SITE_CHANGED", "APPROVAL_EXPIRED", "SUBMISSION_UNKNOWN", "NEEDS_ACCOUNT_APPROVAL",
    "MATERIALS_NEEDS_CORRECTION",
)
ALL_STATES = set(PRIMARY_STATES + OUTCOME_STATES + BLOCKING_STATES)

TRANSITIONS: dict[str, set[str]] = {
    state: {PRIMARY_STATES[index + 1]} for index, state in enumerate(PRIMARY_STATES[:-1])
}
TRANSITIONS["CONFIRMED"] = set(OUTCOME_STATES)
TRANSITIONS["FOLLOW_UP"] = {"INTERVIEW", "REJECTED", "OFFER", "CLOSED"}
TRANSITIONS["INTERVIEW"] = {"FOLLOW_UP", "REJECTED", "OFFER", "CLOSED"}
TRANSITIONS["OFFER"] = {"CLOSED"}
TRANSITIONS["REJECTED"] = {"CLOSED"}
TRANSITIONS["CLOSED"] = set()

for state in PRIMARY_STATES:
    TRANSITIONS.setdefault(state, set()).update({"NEEDS_USER_INPUT", "INELIGIBLE"})
for state in ("FORM_PREFILLED", "FORM_VALIDATED", "AWAITING_APPROVAL", "APPROVED", "SUBMITTING"):
    TRANSITIONS[state].update({"BLOCKED_LOGIN", "BLOCKED_CAPTCHA", "SITE_CHANGED", "NEEDS_ACCOUNT_APPROVAL"})
for state in ("MATERIALS_DRAFTED", "MATERIALS_VALIDATED"):
    TRANSITIONS[state].add("MATERIALS_NEEDS_CORRECTION")
for state in ("APPROVED", "SUBMITTING"):
    TRANSITIONS[state].add("APPROVAL_EXPIRED")
for state in ("SUBMITTING", "SUBMITTED"):
    TRANSITIONS[state].add("SUBMISSION_UNKNOWN")
for state in BLOCKING_STATES:
    TRANSITIONS.setdefault(state, set())


def assert_transition(current: str, target: str) -> None:
    if current not in ALL_STATES or target not in ALL_STATES:
        raise JobOpsError("UNKNOWN_STATE", "The requested application state is not recognized.", current=current, target=target)
    if current == "SUBMISSION_UNKNOWN":
        raise JobOpsError("SUBMISSION_UNKNOWN_NO_RETRY", "Unknown submissions require manual verification and cannot be retried automatically.")
    if target not in TRANSITIONS.get(current, set()):
        raise JobOpsError("INVALID_STATE_TRANSITION", "The state transition is not allowed.", current=current, target=target)


def is_external_action_state(state: str) -> bool:
    return state in {"SUBMITTING", "SUBMITTED", "CONFIRMED"}
