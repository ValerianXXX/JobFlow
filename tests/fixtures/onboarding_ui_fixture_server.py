from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from jobops.db import JobOpsDB  # noqa: E402
from jobops.ai_runtime import LocalSubprocessAIEngine  # noqa: E402
from jobops.onboarding_catalog import FIELD_BY_ID, FIELD_IDS  # noqa: E402
from jobops.onboarding_center import OnboardingCenterService  # noqa: E402
from jobops.onboarding_server import create_server  # noqa: E402
from jobops.private_onboarding import PrivateOnboarding  # noqa: E402
from jobops.secure_store import WindowsDPAPIStore  # noqa: E402
from jobops.util import canonical_json  # noqa: E402


class SlowFixtureService(OnboardingCenterService):
    def bootstrap(self):  # type: ignore[no-untyped-def]
        time.sleep(1.2)
        return super().bootstrap()

    def save_review(self, payload):  # type: ignore[no-untyped-def]
        time.sleep(1.2)
        return super().save_review(payload)

    def import_source(self, source_type, extension, data):  # type: ignore[no-untyped-def]
        time.sleep(1.2)
        return super().import_source(source_type, extension, data)

    def preview_source(self, source_type, extension, data):  # type: ignore[no-untyped-def]
        time.sleep(1.2)
        return super().preview_source(source_type, extension, data)

    def reprocess_source(self, source_id):  # type: ignore[no-untyped-def]
        time.sleep(2.2)
        return super().reprocess_source(source_id)


def complete_synthetic_onboarding(service: OnboardingCenterService) -> None:
    answers = {}
    for field_id in FIELD_IDS:
        field = FIELD_BY_ID[field_id]
        if field["input_type"] == "tags":
            value = ["synthetic"]
        elif field["options"]:
            value = field["options"][0]["value"]
        else:
            value = "synthetic"
        if field_id == "minimum_salary":
            value = "100000"
        elif field_id == "available_start_date":
            value = "2026-09-01"
        answers[field_id] = {"value": value, "status": "CONFIRMED", "use_policy": field["default_policy"]}
    service.save_answers({"locale": "zh", "answers": answers})
    bootstrap = service.bootstrap()
    service.save_review({
        "profile_review": "CONFIRMED",
        "claim_decisions": {item["claim_id"]: "CONFIRMED" for item in bootstrap["claims"]},
        "conflict_resolutions": {
            item["conflict_id"]: {"resolution": "USE_RESUME", "manual_value": None}
            for item in bootstrap["conflicts"]
        },
    })
    service.complete(user_confirmed=True)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="jobops-ui-fixture-"))
    try:
        project = root / "project"
        (project / "schemas").mkdir(parents=True)
        (project / "state").mkdir()
        for name in (
            "candidate-profile", "onboarding-answer-bank", "onboarding-completion",
            "external-claim-set", "application-readiness",
        ):
            shutil.copy2(PROJECT / "schemas" / f"{name}.schema.json", project / "schemas")
        database = JobOpsDB(project / "state" / "jobops.db")
        database.initialize()
        script = PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
        store = WindowsDPAPIStore(script, local_app_data=root / "local")
        onboarding = PrivateOnboarding(database, store)
        entity = {
            "entity_id": "ENT-SYNTHETIC", "entity_fingerprint": "ENTKEY-SYNTHETIC",
            "entity_key": "synthetic-project", "entity_type": "project",
            "organization": "Synthetic Organization", "role": "Project Contributor",
            "start_date": "2025", "end_date": "2026", "line_start": 1, "line_end": 1,
        }
        onboarding.import_bytes("claim_candidates", canonical_json({"claims": [{
            "claim_id": "CLM-SYNTHETIC-CONFLICT", "category": "project",
            "resume_statement": "The synthetic project delivered 10 projects after a governed review.",
            "lifecycle_status": "CONFLICT_REQUIRES_REVIEW", "confidence": "LOW", "conflict": True,
            "ai_validated": True, "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
            "claim_kind": "achievement", "entity_id": entity["entity_id"], "entity": entity,
            "supporting_evidence": [{
                "source_id": "personal_redacted", "heading": "Synthetic evidence",
                "excerpt": "The synthetic project delivered 20 projects after a governed review.",
            }],
        }]}), synthetic=True)
        engine = LocalSubprocessAIEngine([sys.executable, str(PROJECT / "tests" / "fixtures" / "fake_jobops_ai.py")])
        service = SlowFixtureService(project, database, onboarding, ai_engine=engine)
        service.import_source(
            "project_case", ".txt",
            b"Built a synthetic governed workflow and improved review accuracy by 20%.",
        )
        if "--completed" in sys.argv[1:]:
            complete_synthetic_onboarding(service)
        server = create_server(service, token="synthetic-visual-session")
        print(server.url, flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
