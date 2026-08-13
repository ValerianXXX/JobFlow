from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .errors import JobOpsError
from .util import iso_utc, parse_iso


@dataclass
class FakeClock:
    now: datetime

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class OfflineScheduler:
    """In-memory deterministic scheduler; it never registers an OS or Codex task."""

    def __init__(self, clock: FakeClock, *, retry_delay: timedelta = timedelta(minutes=5), max_attempts: int = 3) -> None:
        self.clock = clock
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self.paused = False
        self._items: dict[str, dict[str, Any]] = {}

    def enqueue(self, key: str, payload: dict[str, Any], *, due_at: str, deadline: str | None = None, daily: bool = False) -> dict[str, Any]:
        if key in self._items:
            return {**self._items[key], "deduplicated": True}
        item = {
            "key": key, "payload": dict(payload), "due_at": due_at, "deadline": deadline,
            "daily": daily, "status": "SCHEDULED", "attempts": 0, "last_error_code": None,
        }
        self._items[key] = item
        return {**item, "deduplicated": False}

    def pause(self) -> None: self.paused = True
    def resume(self) -> None: self.paused = False

    def tick(self, handler: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
        if self.paused:
            return []
        results: list[dict[str, Any]] = []
        now = self.clock.now
        for key in sorted(self._items):
            item = self._items[key]
            if item["status"] not in {"SCHEDULED", "RETRY_WAIT", "DEFERRED_CAPACITY"} or parse_iso(item["due_at"]) > now:
                continue
            if item["deadline"] and now > parse_iso(item["deadline"]):
                item["status"] = "DEADLINE_PASSED"
                results.append({"key": key, "status": item["status"]})
                continue
            try:
                outcome = handler(dict(item["payload"]))
                if outcome == "DEFERRED_CAPACITY":
                    item["status"] = outcome
                    item["due_at"] = iso_utc(now + self.retry_delay)
                elif outcome != "SUCCESS":
                    raise JobOpsError("FAKE_HANDLER_FAILED", "Offline scheduler handler returned an unsupported outcome.")
                elif item["daily"]:
                    item["status"] = "SCHEDULED"
                    item["due_at"] = iso_utc(now + timedelta(days=1))
                    item["attempts"] = 0
                else:
                    item["status"] = "COMPLETED"
                item["last_error_code"] = None
            except Exception as exc:
                item["attempts"] += 1
                item["last_error_code"] = getattr(exc, "code", type(exc).__name__)
                if item["attempts"] >= self.max_attempts:
                    item["status"] = "FAILED"
                else:
                    item["status"] = "RETRY_WAIT"
                    item["due_at"] = iso_utc(now + self.retry_delay)
            results.append({"key": key, "status": item["status"], "attempts": item["attempts"]})
        return results

    def snapshot(self) -> dict[str, Any]:
        return {"paused": self.paused, "clock": iso_utc(self.clock.now), "items": [dict(self._items[key]) for key in sorted(self._items)], "system_tasks_registered": 0}
