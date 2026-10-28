"""Optional stdio-only MCP entry point. No private store or application service."""

from __future__ import annotations

import argparse
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .application_workspace import next_action
from .errors import JobOpsError
from .runtime_paths import runtime_path


class ReadOnlyQueries:
    def __init__(self, project: Path):
        self.project = project.resolve(strict=True)

    @contextmanager
    def connection(self):
        path = runtime_path(self.project, "state", "jobops.db", operation="read")
        if not path.is_file():
            raise JobOpsError(
                "MCP_DATABASE_NOT_READY",
                "Open JobFlow locally before querying application state.",
            )
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _status(value: str) -> str:
        from .state_machine import TRANSITIONS

        known = set(TRANSITIONS) | {
            state for states in TRANSITIONS.values() for state in states
        }
        return value if value in known else "UNKNOWN"

    def readiness(self) -> dict:
        with self.connection() as c:
            total = c.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            pending = c.execute(
                "SELECT COUNT(*) FROM applications WHERE status='AWAITING_APPROVAL'"
            ).fetchone()[0]
            uncertain = c.execute(
                "SELECT COUNT(*) FROM applications WHERE status IN ('SUBMITTING','SUBMITTED','SUBMISSION_UNKNOWN')"
            ).fetchone()[0]
        return {
            "application_count": total,
            "pending_review_count": pending,
            "external_result_needs_verification_count": uncertain,
            "private_content_included": False,
            "final_submit": "USER_ONLY",
            "automatic_retry": False,
        }

    def pending(self, limit: int = 20) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JobOpsError(
                "MCP_ARGUMENT_INVALID", "Limit must be an integer from 1 to 100."
            )
        with self.connection() as c:
            rows = c.execute(
                "SELECT application_id,status FROM applications WHERE status IN ('AWAITING_APPROVAL','SUBMISSION_UNKNOWN','SUBMITTING','SUBMITTED') ORDER BY updated_at DESC,application_id LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "applications": [
                {
                    "application_id": row["application_id"],
                    "status": self._status(row["status"]),
                    "next_action": next_action(row["status"]),
                }
                for row in rows
                if re.fullmatch(r"APP-[A-F0-9]{12}", row["application_id"])
            ],
            "private_content_included": False,
            "review_in": "JobFlow local application records",
        }

    def summary(self, application_id: str) -> dict:
        if not isinstance(application_id, str) or not re.fullmatch(
            r"APP-[A-F0-9]{12}", application_id
        ):
            raise JobOpsError(
                "MCP_ARGUMENT_INVALID",
                "Use an application ID returned by list_pending_reviews.",
            )
        with self.connection() as c:
            row = c.execute(
                "SELECT status FROM applications WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if row is None:
                raise JobOpsError(
                    "APPLICATION_NOT_FOUND", "The application is not available."
                )
            stopped = c.execute(
                "SELECT COUNT(*) FROM application_fields WHERE application_id=? AND status='STOP_REQUIRED'",
                (application_id,),
            ).fetchone()[0]
            materials = c.execute(
                "SELECT COUNT(*) FROM materials WHERE application_id=?",
                (application_id,),
            ).fetchone()[0]
            packets = c.execute(
                "SELECT COUNT(*) FROM review_packets WHERE application_id=?",
                (application_id,),
            ).fetchone()[0]
        status = self._status(row["status"])
        return {
            "application_id": application_id,
            "status": status,
            "next_action": next_action(status, stopped),
            "required_field_count": stopped,
            "prepared_material_count": materials,
            "review_version_count": packets,
            "delivery_inferred_from_materials": False,
            "private_content_included": False,
            "review_in": "JobFlow local application records",
        }


def create_mcp_server(project: Path):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    queries = ReadOnlyQueries(project)
    server = MCPServer(
        "JobFlow",
        version=__version__,
        log_level="CRITICAL",
        instructions="Read-only, redacted local workflow metadata. Open JobFlow locally to review private evidence. These tools cannot approve, fill, submit, send mail, or change state.",
    )
    annotations = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    def safe(operation, *args) -> dict:
        try:
            return operation(*args)
        except JobOpsError as exc:
            return {
                "status": "BLOCKED",
                "code": exc.code,
                "private_content_included": False,
            }
        except Exception:
            return {
                "status": "BLOCKED",
                "code": "MCP_LOCAL_STATE_UNAVAILABLE",
                "private_content_included": False,
            }

    @server.tool(annotations=annotations, structured_output=True)
    def get_readiness() -> dict[str, Any]:
        """Count local applications and pending reviews without reading applicant facts."""
        return safe(queries.readiness)

    @server.tool(annotations=annotations, structured_output=True)
    def list_pending_reviews(limit: int = 20) -> dict[str, Any]:
        """List up to 100 opaque application IDs needing review; excludes names, jobs and emails."""
        return safe(queries.pending, limit)

    @server.tool(annotations=annotations, structured_output=True)
    def get_application_summary(application_id: str) -> dict[str, Any]:
        """Read one application's state and counts. Private evidence stays in the local UI."""
        return safe(queries.summary, application_id)

    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="JobFlow read-only MCP over stdio")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        create_mcp_server(args.project).run(transport="stdio")
    except ImportError:
        import sys

        print(
            "MCP_EXTRA_REQUIRED: install jobflow-local[mcp] in the selected Python environment.",
            file=sys.stderr,
        )
        return 2
    except Exception:
        import sys

        print(
            "MCP_STARTUP_FAILED: check the local JobFlow project and optional MCP runtime.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
