"""Disposable, synthetic-only local UI acceptance with the existing Hermes route.

No application, approval, completed profile, or AI result is seeded. Only the
frozen synthetic PDF used by evaluate-material-preview.py may be uploaded.
At most six adapter requests (including the real connection/capability test)
are allowed; no user Agent configuration changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import sha256_bytes
from jobops.ai_connections import WSLHermesCLIEngine

spec = importlib.util.spec_from_file_location("material_eval", ROOT / "scripts/evaluate-material-preview.py")
material_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(material_eval)


class AcceptanceHermes(WSLHermesCLIEngine):
    def __init__(self, route):
        super().__init__("wsl.exe", "Ubuntu", model=route["model"], provider_id=route["provider"])
        self.calls = []
        self.transcript = ""

    def _invoke(self, request):
        if len(self.calls) >= 6:
            raise JobOpsError("EVALUATION_CALL_LIMIT", "The six-request synthetic acceptance budget is exhausted.")
        record = {"task": request.get("task"), "status": "FAILED", "tool_calls": None}
        self.calls.append(record)
        started = time.monotonic()
        try:
            result = super()._invoke(request)
            record.update(status="RETURNED", tool_calls=result["toolSummary"]["calls"])
            if request.get("task") == "JOBOPS_VISUAL_TRANSCRIPTION_V1":
                try:
                    value = json.loads(result["result"]["content"])
                    self.transcript = "\n".join(block["text"] for page in value["pages"] for block in page["blocks"])
                except (ValueError, TypeError, KeyError):
                    record["transcription_observation"] = "INVALID"
            return result
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)


class SyntheticAcceptanceService(OnboardingCenterService):
    def preview_source(self, source_type, extension, data):
        if (source_type != "resume" or extension != ".pdf"
                or sha256_bytes(data) != "sha256:" + material_eval.FIXTURE_SHA256):
            raise JobOpsError("ACCEPTANCE_SYNTHETIC_INPUT_ONLY", "This acceptance workspace accepts only the frozen synthetic PDF.")
        return super().preview_source(source_type, extension, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live-hermes", action="store_true", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output; earlier observations are immutable.")
    route = material_eval.public_hermes_route()
    engine = AcceptanceHermes(route)
    engine.connection_test()
    receipt = {"verified_at": datetime.now(timezone.utc).isoformat(), "synthetic_only": True,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "harness_lf_sha256": sha256_bytes(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
        "model_route": route, "cost_usd": None, "usage_tokens": None,
        "initial_applications": 0, "initial_completed_profiles": 0,
        "business_completion": "NOT_ATTEMPTED"}
    with tempfile.TemporaryDirectory(prefix="jobflow-user-acceptance-") as raw:
        temporary = Path(raw)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1", local_app_data=temporary / "local")
        service = SyntheticAcceptanceService(project, database, PrivateOnboarding(database, store), ai_engine=engine)
        server = create_server(service, token="synthetic-user-acceptance")
        stop_file = args.output.with_suffix(".stop")
        def stop_when_requested():
            while not stop_file.exists():
                if finished.wait(1):
                    return
            server.shutdown()
        finished = threading.Event()
        threading.Thread(target=stop_when_requested, daemon=True).start()
        try:
            with material_eval.visual_mode(True):
                print(json.dumps({"url": server.url, "synthetic_only": True, "max_adapter_requests": 6, "capability": engine.public_status()["structured_capability_status"]}), flush=True)
                server.serve_forever(poll_interval=0.25)
        finally:
            finished.set()
            server.server_close()
            _, state = service.ensure_state()
            receipt["final_onboarding_status"] = state.get("status")
            receipt["sources"] = [{"source_id": row.get("source_id"), "sha256": row.get("sha256"), "extension": row.get("extension")} for row in state.get("sources", [])]
            receipt["pending_sources"] = len(state.get("pending_sources", []))
            receipt["material_claims"] = state.get("material_claims", [])
            receipt["adapter_requests"] = engine.calls
            receipt["transcript"] = engine.transcript
            with database.connect() as connection:
                receipt["database_counts"] = {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("applications", "claims", "approvals", "external_action_attempts")}
            service.close()
    receipt["temporary_state_removed"] = not Path(raw).exists()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "RECORDED", "requests": len(engine.calls), "temporary_state_removed": receipt["temporary_state_removed"]}), flush=True)


if __name__ == "__main__":
    main()
