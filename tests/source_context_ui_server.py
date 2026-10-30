"""Fixed synthetic context replay through actual loopback HTTP and Windows DPAPI.

No model calls, applicant paths, mailbox or external actions. Send STOP on stdin.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine
from jobops.db import JobOpsDB
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore


class SyntheticContextEngine(LocalSubprocessAIEngine):
    def execute_structured_task(self, task, *, timeout_seconds=None):
        return {"schema_version": AI_PROTOCOL_VERSION, "entities": [], "candidates": [], "review_notes": [
            {"kind": "profile_constraint", "field_id": "remote_preference", "line_start": 1, "line_end": 1},
            {"kind": "profile_constraint", "field_id": "work_authorization", "line_start": 2, "line_end": 2},
            {"kind": "evidence_gap", "field_id": "", "line_start": 3, "line_end": 3},
        ]}


def main():
    if sys.argv[1:] != ["--stdio-control"]:
        raise SystemExit("Use --stdio-control; this fixture accepts no applicant path.")
    reports = (ROOT / "reports").resolve()
    reports.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-context-ui-", dir=reports) as raw:
        temporary = Path(raw).resolve()
        temporary.relative_to(reports)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        (project / "config").mkdir()
        shutil.copy2(ROOT / "config/policy.json", project / "config/policy.json")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1",
                                  local_app_data=temporary / "local")
        service = OnboardingCenterService(project, database, PrivateOnboarding(database, store),
                                          ai_engine=SyntheticContextEngine([sys.executable]))
        server = None
        try:
            service.preview_source("project_case", ".txt", (
                "Synthetic preference: remote work only.\n"
                "Synthetic work authorization: unknown; applicant must confirm.\n"
                "Synthetic reference letters have not been verified."
            ).encode("utf-8"))
            server = create_server(service, token="synthetic-source-context-session")
            def stop():
                for line in sys.stdin:
                    if line.strip() == "STOP":
                        break
                server.shutdown()
            threading.Thread(target=stop, daemon=True).start()
            print(json.dumps({"url": server.url, "fixture": "SYNTHETIC_CONTEXT_CONTRACT",
                              "live_provider_calls": 0}), flush=True)
            server.serve_forever(poll_interval=0.25)
        finally:
            if server is not None:
                server.server_close()
            service.close()
    print(json.dumps({"temporary_state_removed": not Path(raw).exists(), "live_provider_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
