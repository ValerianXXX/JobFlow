from __future__ import annotations

from dataclasses import dataclass

from .errors import JobOpsError


@dataclass(frozen=True)
class QueueDecision:
    pending_count: int
    pending_limit: int
    slots_available: int
    continue_intake: bool
    decision: str

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def validate_pending_limit(limit: int, *, minimum: int = 1, maximum: int = 1000) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not minimum <= limit <= maximum:
        raise JobOpsError("PENDING_LIMIT_INVALID", "Pending approval limit is outside configured bounds.", minimum=minimum, maximum=maximum, value=limit)
    return limit


def queue_decision(pending_count: int, pending_limit: int, *, minimum: int = 1, maximum: int = 1000) -> QueueDecision:
    validate_pending_limit(pending_limit, minimum=minimum, maximum=maximum)
    if pending_count < 0:
        raise JobOpsError("PENDING_COUNT_INVALID", "Pending approval count cannot be negative.")
    slots = max(0, pending_limit - pending_count)
    return QueueDecision(
        pending_count=pending_count,
        pending_limit=pending_limit,
        slots_available=slots,
        continue_intake=slots > 0,
        decision="CONTINUE_OTHER_JOBS" if slots > 0 else "PAUSE_NEW_INTAKE_AT_LIMIT",
    )

