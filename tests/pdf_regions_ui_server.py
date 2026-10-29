"""Empty synthetic workspace: real PDF parser/HTTP/DPAPI, recorded model output.

Only the exact frozen synthetic PDF can be uploaded. No model, account, source,
Claim approval or application is seeded. The replay is not live AI acceptance.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_connections import _analyze_all_chunks
from jobops.ai_runtime import AIAnalysisEngine, AI_QUALITY_CONTRACT
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore

FIXTURE = json.loads((ROOT / "tests/fixtures/cjk-column-response-v1.json").read_text(encoding="utf-8"))


class RecordedSyntheticEngine(AIAnalysisEngine):
    ready = True
    replays = 0

    def public_status(self):
        return {"status": "READY", "mode": "AI_CORE_STRUCTURED_ANALYSIS", "provider": "SYNTHETIC_RESPONSE_REPLAY",
                "private_transport": "MEMORY_ONLY", "automatic_claim_selection": False, "claim_output_allowed": True,
                "quality_contract": AI_QUALITY_CONTRACT, "operator_mode": "UNAVAILABLE", "jobflow_tool_authority": "NONE"}

    def _invoke(self, request):
        response = FIXTURE["model_response"]
        expected = [f"{i}\t{line}" for i, line in enumerate(response["source_lines"], response["line_number_start"])]
        if request.get("line_numbered_document") != expected:
            raise JobOpsError("SYNTHETIC_FIXTURE_MISMATCH", "Only the frozen synthetic source can be replayed.")
        self.replays += 1
        return deepcopy(response["payload"])

    def analyze_document(self, text, *, source_id, source_type):
        candidates, details = _analyze_all_chunks(self._invoke, text, source_id=source_id, source_type=source_type)
        return candidates, {"analysis_mode": "AI_CORE_ENTITY_ANALYSIS", **details,
                            "automatic_claim_selection": False, "quality_contract": AI_QUALITY_CONTRACT}


class FixedInputService(OnboardingCenterService):
    def preview_source(self, source_type, extension, data, **kwargs):
        if source_type != "resume" or extension != ".pdf" or hashlib.sha256(data).hexdigest() != FIXTURE["source_sha256"]:
            raise JobOpsError("SYNTHETIC_FIXTURE_ONLY", "Upload only the pinned synthetic Chinese columns PDF.")
        return super().preview_source(source_type, extension, data, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio-control", action="store_true")
    args = parser.parse_args()
    os.environ["JOBFLOW_DOCUMENT_PARSER"] = "pdf_regions"
    os.environ["JOBFLOW_VISUAL_ENABLE"] = "false"
    engine = RecordedSyntheticEngine()
    with tempfile.TemporaryDirectory(prefix="jobflow-cjk-ui-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        (project / "config").mkdir()
        shutil.copy2(ROOT / "config/policy.json", project / "config/policy.json")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1", local_app_data=root / "local")
        service = FixedInputService(project, database, PrivateOnboarding(database, store), ai_engine=engine)
        server = create_server(service, token="synthetic-pdf-regions")

        def stop_on_input():
            sys.stdin.readline()
            server.shutdown()

        if args.stdio_control:
            threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/session/synthetic-pdf-regions/", "synthetic_only": True}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            _, state = service.ensure_state()
            with database.connect() as connection:
                counts = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                          for name in ("applications", "claims", "approvals", "external_action_attempts")}
            receipt = {"synthetic_only": True, "real_model_calls": 0, "recorded_response_replays": engine.replays,
                       "source_count": len(state.get("sources", [])), "material_claim_count": len(state.get("material_claims", [])),
                       "claim_decisions": [c.get("decision") for c in state.get("material_claims", [])], "database_counts": counts}
            service.close()
    receipt["temporary_state_removed"] = not root.exists()
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
