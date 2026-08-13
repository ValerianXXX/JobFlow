from __future__ import annotations


class JobOpsError(RuntimeError):
    """Base error carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "BLOCKED",
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class SecurityBoundaryError(JobOpsError):
    pass


class LocationError(JobOpsError):
    pass


class ApprovalError(JobOpsError):
    pass

