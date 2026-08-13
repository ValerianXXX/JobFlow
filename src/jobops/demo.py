from __future__ import annotations

import json
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from .ai_runtime import LocalSubprocessAIEngine
from .db import JobOpsDB
from .errors import JobOpsError
from .onboarding_center import OnboardingCenterService
from .onboarding_server import create_server
from .private_onboarding import PrivateOnboarding
from .secure_store import WindowsDPAPIStore
from .util import canonical_json


DEMO_SOURCE = (
    "At Synthetic Demo Studio, the Synthetic Workflow Contributor built a governed "
    "review workflow and improved synthetic review accuracy by 20%."
)
DEMO_SCHEMAS = ("candidate-profile", "onboarding-answer-bank", "onboarding-completion")


class SyntheticDemoAIEngine(LocalSubprocessAIEngine):
    def public_status(self) -> dict[str, Any]:
        return {
            **super().public_status(),
            "provider": "SYNTHETIC_DEMO",
            "display_name": "Synthetic demo analyzer",
            "model": "DETERMINISTIC_FIXTURE",
            "data_route": "TEMPORARY_LOCAL_SUBPROCESS",
        }


class SyntheticDemoService(OnboardingCenterService):
    """Isolated UI tour that cannot ingest files or connect to a real AI."""

    def bootstrap(self) -> dict[str, Any]:
        result = super().bootstrap()
        return {
            **result,
            "demo_mode": True,
            "demo_constraints": {
                "synthetic_only": True,
                "file_intake_enabled": False,
                "ai_connection_enabled": False,
                "temporary_runtime": True,
                "real_external_actions": 0,
            },
        }

    def connect_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise JobOpsError("DEMO_AI_CONNECTION_DISABLED", "Synthetic demo mode cannot connect to a real AI.")

    def preview_source(self, source_type: str, extension: str, data: bytes) -> dict[str, Any]:
        raise JobOpsError("DEMO_FILE_INTAKE_DISABLED", "Synthetic demo mode cannot ingest user files.")

    def import_source(self, source_type: str, extension: str, data: bytes) -> dict[str, Any]:
        raise JobOpsError("DEMO_FILE_INTAKE_DISABLED", "Synthetic demo mode cannot ingest user files.")

    def preview_large_chatgpt_export(
        self,
        path: Path,
        *,
        extension: str,
        source_hash: str,
        upload_size: int,
    ) -> dict[str, Any]:
        raise JobOpsError("DEMO_FILE_INTAKE_DISABLED", "Synthetic demo mode cannot ingest user files.")


def create_demo_service(
    source_project: Path,
    runtime_root: Path,
    *,
    secure_store: Any | None = None,
) -> SyntheticDemoService:
    source_project = source_project.resolve(strict=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    project = runtime_root / "project"
    schemas = project / "schemas"
    state = project / "state"
    schemas.mkdir(parents=True)
    state.mkdir()
    for name in DEMO_SCHEMAS:
        source = source_project / "schemas" / f"{name}.schema.json"
        if not source.is_file():
            raise JobOpsError("DEMO_SCHEMA_MISSING", "A required public demo Schema is missing.", schema=name)
        shutil.copy2(source, schemas / source.name)

    database = JobOpsDB(state / "jobops.db")
    database.initialize()
    if secure_store is None:
        script = source_project / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
        secure_store = WindowsDPAPIStore(script, local_app_data=runtime_root / "local")
    onboarding = PrivateOnboarding(database, secure_store)
    entity = {
        "entity_id": "ENT-SYNTHETIC-DEMO",
        "entity_fingerprint": "ENTKEY-SYNTHETIC-DEMO",
        "entity_key": "synthetic-demo-project",
        "entity_type": "project",
        "organization": "Synthetic Demo Studio",
        "role": "Synthetic Workflow Contributor",
        "start_date": "2025",
        "end_date": "2026",
        "line_start": 1,
        "line_end": 1,
    }
    onboarding.import_bytes(
        "claim_candidates",
        canonical_json(
            {
                "claims": [
                    {
                        "claim_id": "CLM-SYNTHETIC-DEMO-CONFLICT",
                        "category": "project",
                        "resume_statement": "The synthetic project delivered 10 projects after a governed review.",
                        "lifecycle_status": "CONFLICT_REQUIRES_REVIEW",
                        "confidence": "LOW",
                        "conflict": True,
                        "ai_validated": True,
                        "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
                        "claim_kind": "achievement",
                        "entity_id": entity["entity_id"],
                        "entity": entity,
                        "supporting_evidence": [
                            {
                                "source_id": "personal_redacted",
                                "heading": "Synthetic demo evidence",
                                "excerpt": "The synthetic project delivered 20 projects after a governed review.",
                            }
                        ],
                    }
                ]
            }
        ),
        synthetic=True,
    )
    engine = SyntheticDemoAIEngine([sys.executable, str(Path(__file__).with_name("_demo_ai_command.py"))])
    service = SyntheticDemoService(project, database, onboarding, ai_engine=engine)
    OnboardingCenterService.import_source(service, "project_case", ".txt", DEMO_SOURCE.encode("utf-8"))
    return service


def run_demo(source_project: Path, *, port: int = 0, open_browser: bool = True) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jobflow-synthetic-demo-") as temporary:
        service = create_demo_service(source_project, Path(temporary))
        server = create_server(service, port=port)
        safe = {
            "status": "SYNTHETIC_DEMO_READY",
            "url": server.url,
            "binding": "127.0.0.1",
            "synthetic_only": True,
            "temporary_runtime": True,
            "file_intake_enabled": False,
            "real_external_actions": 0,
            "next_safe_action": "explore synthetic data only; close with Ctrl+C",
        }
        print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)
        if open_browser:
            webbrowser.open(server.url, new=2)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {**safe, "status": "SYNTHETIC_DEMO_CLOSED"}
