"""Synthetic Claim review through real loopback HTTP and Windows DPAPI. No model calls."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobops.ai_runtime import LocalSubprocessAIEngine
from jobops.db import JobOpsDB
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import canonical_json


def main():
    if sys.argv[1:] != ["--stdio-control"]:
        raise SystemExit("Use --stdio-control; this fixture accepts no applicant path.")
    with tempfile.TemporaryDirectory(prefix="jobflow-wording-ui-") as raw:
        temporary = Path(raw).resolve()
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
        onboarding = PrivateOnboarding(database, store)
        entity = {"entity_id": "ENT-SYNTHETIC", "entity_fingerprint": "ENTKEY-SYNTHETIC",
                  "entity_type": "project", "organization": "Synthetic Organization", "role": "Contributor"}
        onboarding.import_bytes("claim_candidates", canonical_json({"claims": [
            {"claim_id": f"CLM-SYNTHETIC-{index}", "category": "project", "resume_statement": statement,
             "lifecycle_status": "AI_FILTERED_REQUIRES_CONFIRMATION", "confidence": "LOW", "conflict": False,
             "ai_validated": True, "analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "claim_kind": "achievement",
             "entity_id": entity["entity_id"], "entity": entity, "supporting_evidence": []}
            for index, statement in enumerate([
                "The applicant prepared a synthetic project with 12 review records.",
                "The applicant documented the synthetic team's contribution to 3 reports.",
                "The applicant retained synthetic evidence for an independent review.",
            ], start=1)
        ]}), synthetic=True)
        # Explicit fixture engine prevents discovery of a configured provider. It is never invoked.
        service = OnboardingCenterService(project, database, onboarding,
            ai_engine=LocalSubprocessAIEngine([sys.executable, str(ROOT / "tests/fixtures/fake_jobops_ai.py")]))
        server = None
        try:
            service.save_review({"profile_review": "CONFIRMED", "claim_decisions": {
                item["claim_id"]: "CONFIRMED" for item in service.bootstrap()["claims"]}})
            server = create_server(service, token="synthetic-claim-wording-session")
            def stop():
                for line in sys.stdin:
                    if line.strip() == "STOP":
                        break
                server.shutdown()
            threading.Thread(target=stop, daemon=True).start()
            print(json.dumps({"url": server.url, "fixture": "SYNTHETIC_CLAIM_WORDING", "live_provider_calls": 0}), flush=True)
            server.serve_forever(poll_interval=0.25)
        finally:
            if server is not None:
                server.server_close()
            service.close()
    print(json.dumps({"temporary_state_removed": not Path(raw).exists(), "live_provider_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
