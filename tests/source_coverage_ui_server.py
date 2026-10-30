"""Actual local HTTP/DPAPI review of one pinned synthetic model replay.

No live model, mailbox, user material or external application is available.
Send STOP on stdin to close the server and remove its owned synthetic state.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("source_coverage_replay", ROOT / "scripts/replay-complete-claims.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

from jobops.onboarding_server import create_server  # noqa: E402


def main():
    if sys.argv[1:] != ["--stdio-control"]:
        raise SystemExit("Use --stdio-control; this fixture accepts no applicant path.")
    reports = (ROOT / "reports").resolve()
    reports.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-coverage-ui-", dir=reports) as raw:
        temporary = Path(raw).resolve()
        temporary.relative_to(reports)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = replay.pilot.layout.JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = replay.pilot.layout.WindowsDPAPIStore(
            ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1",
            local_app_data=temporary / "local",
        )
        old = next(row for row in replay.load_observation()["arms"] if row["case"] == "en-chronological")
        case = next(row for row in replay.pilot.complete.load_manifest()["cases"] if row["id"] == old["case"])
        engine = replay.ReplayEngine(old["responses"])
        service = replay.pilot.layout.OnboardingCenterService(
            project, database, replay.pilot.layout.PrivateOnboarding(database, store), ai_engine=engine,
        )
        server = None
        try:
            with replay.pilot.layout.mode_environment("existing_text"):
                service.preview_source("resume", ".pdf", (replay.pilot.complete.CORPUS / case["file"]).read_bytes())
            server = create_server(service, token="synthetic-source-coverage-session")
            def stop():
                for line in sys.stdin:
                    if line.strip() == "STOP":
                        break
                server.shutdown()
            threading.Thread(target=stop, daemon=True).start()
            print(json.dumps({"url": server.url, "fixture": "SYNTHETIC_RECORDED_MODEL",
                              "live_provider_calls": 0}), flush=True)
            server.serve_forever(poll_interval=0.25)
        finally:
            if server is not None:
                server.server_close()
            service.close()
    print(json.dumps({"temporary_state_removed": not Path(raw).exists(), "live_provider_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
