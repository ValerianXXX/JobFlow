"""Own ephemeral form-preparation jobs without holding the service's work lock.

This is a local completion barrier, not a durable queue or a thread cancellation
mechanism. Inputs and progress stay in memory; application persistence and all
authorization decisions remain in the existing preparation service.
"""
from __future__ import annotations

from copy import deepcopy
import re
import secrets
import threading
import time
from typing import Any, Callable

from .errors import JobOpsError


class GuidedPreparationJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closing = False
        self._jobs: dict[str, dict[str, Any]] = {}
        self._workers: set[threading.Thread] = set()

    @staticmethod
    def _view(job: dict[str, Any]) -> dict[str, Any]:
        if isinstance(job.get("result"), dict):
            return deepcopy(job["result"])
        return {
            "status": "PREPARING_APPLICATION", "mode": "JOB_CAPTURE",
            "intake_id": job["intake_id"], "retry_after_ms": 3000,
            "automatic_retry": False, "real_external_actions": 0,
        }

    @staticmethod
    def _check_expiry(job: dict[str, Any]) -> None:
        if time.time() >= job["expires_epoch"]:
            raise JobOpsError(
                "GUIDED_INTAKE_EXPIRED", "This guided job-import session expired. Start it again.",
            )

    def _cached(self, token: str, fingerprint: str) -> dict[str, Any] | None:
        # Called only under _lock. Check closure even for an otherwise identical
        # replay: a POST cannot admit work once shutdown has started.
        if self._closing:
            raise JobOpsError(
                "GUIDED_PREPARATION_CLOSING", "Local preparation is closing. Wait for it to finish.",
                automatic_retry=False,
            )
        job = self._jobs.get(token)
        if job is None:
            return None
        self._check_expiry(job)
        result = job.get("result")
        if result is not None and result.get("status") not in {"REVIEW_PACKET_READY", "DEFERRED"}:
            return None  # A failed job can be retried only by a new explicit request.
        if job["fingerprint"] != fingerprint:
            raise JobOpsError(
                "GUIDED_PREPARATION_INPUT_CHANGED",
                "This import already has a different form. Check its preparation status before starting another import.",
                automatic_retry=False, next_safe_action="CHECK_LOCAL_PREPARATION_STATUS",
            )
        return self._view(job)

    def cached(self, token: str, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            return self._cached(token, fingerprint)

    def start(
        self, token: str, *, intake_id: str, expires_epoch: float, fingerprint: str,
        prepare: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            cached = self._cached(token, fingerprint)
            if cached is not None:
                return cached
            job = {
                "job_id": secrets.token_urlsafe(24), "intake_id": intake_id,
                "expires_epoch": expires_epoch, "started_epoch": time.time(),
                "fingerprint": fingerprint, "result": None,
            }
            self._check_expiry(job)
            worker = threading.Thread(
                target=self._run, args=(token, job["job_id"], prepare),
                name=f"jobflow-guided-form-{intake_id}", daemon=False,
            )
            # Keep live owners even when their public result has been discarded.
            # Do not remove the current worker from inside its own finally block:
            # close() must wait for actual thread exit, not just result publication.
            self._workers = {item for item in self._workers if item.is_alive()}
            self._jobs[token] = job
            self._workers.add(worker)
            try:
                worker.start()
            except BaseException as exc:
                started = worker.ident is not None
                if not started:
                    self._workers.discard(worker)
                    self._jobs.pop(token, None)
                if not isinstance(exc, Exception):
                    raise
                if started:
                    raise JobOpsError(
                        "GUIDED_PREPARATION_START_UNCONFIRMED",
                        "Preparation may already be running. Check its status before retrying.",
                        automatic_retry=False, next_safe_action="CHECK_LOCAL_PREPARATION_STATUS",
                    ) from None
                raise JobOpsError(
                    "GUIDED_PREPARATION_START_FAILED",
                    "Local preparation could not start. You may retry this import explicitly.",
                    automatic_retry=False,
                ) from None
            return self._view(job)

    def _run(self, token: str, job_id: str, prepare: Callable[[], dict[str, Any]]) -> None:
        try:
            result = deepcopy(prepare())
            if not isinstance(result, dict):
                raise TypeError("Invalid local preparation result")
        except Exception as exc:
            failure = exc.as_dict() if isinstance(exc, JobOpsError) else {
                "status": "BLOCKED", "code": "LOCAL_PREPARATION_FAILED",
                "message": "The local application review packet could not be prepared.", "details": {},
            }
            details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
            if failure.get("code") == "INELIGIBLE":
                failure["hard_gap_codes"] = [
                    str(value)[:120] for value in details.get("hard_gaps", [])[:20]
                    if re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", str(value))
                ]
                failure["unknown_condition_codes"] = [
                    str(value)[:120] for value in details.get("unknowns", [])[:20]
                    if re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", str(value))
                ]
            result = {**failure, "status": "FORM_CAPTURE_FAILED"}
        with self._lock:
            job = self._jobs.get(token)
            if job is not None and job["job_id"] == job_id:
                job["result"] = {
                    **result, "intake_id": job["intake_id"], "mode": "JOB_CAPTURE",
                    "automatic_retry": False, "real_external_actions": 0,
                }
                job["finished_epoch"] = time.time()

    def status(self, token: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(token)
            if job is None:
                raise JobOpsError(
                    "GUIDED_INTAKE_PREPARATION_NOT_FOUND",
                    "The local preparation job is no longer available. Start this job import again.",
                )
            self._check_expiry(job)
            return self._view(job)

    def discard(self, token: str) -> None:
        with self._lock:
            self._jobs.pop(token, None)

    def close(self) -> None:
        # Check before _close_lock: an external closer may already hold that
        # lock while joining this worker. Holding the service lock here would
        # likewise deadlock a preparation waiting to enter the service.
        with self._lock:
            if threading.current_thread() in self._workers:
                raise JobOpsError(
                    "GUIDED_PREPARATION_CLOSE_FROM_WORKER",
                    "A preparation worker cannot close its own local service.",
                )
            self._closing = True
        with self._close_lock:
            with self._lock:
                workers = tuple(self._workers)
            for worker in workers:
                worker.join()
            with self._lock:
                self._workers.clear()
