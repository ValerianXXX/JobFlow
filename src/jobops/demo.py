from __future__ import annotations

import json
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from .ai_runtime import LocalSubprocessAIEngine
from .application_execution import build_application_execution_plan
from .approvals import ApprovalContext, UploadBinding
from .db import JobOpsDB
from .errors import JobOpsError
from .onboarding_center import OnboardingCenterService
from .onboarding_server import create_server
from .private_onboarding import PrivateOnboarding
from .queue_manager import QueueManager
from .secure_store import WindowsDPAPIStore
from .util import canonical_json, iso_utc, sha256_bytes


DEMO_SOURCE = (
    "At Synthetic Demo Studio, the Synthetic Workflow Contributor built a governed "
    "review workflow and improved synthetic review accuracy by 20%."
)
DEMO_SCHEMAS = (
    "candidate-profile",
    "onboarding-answer-bank",
    "onboarding-completion",
    "review-packet",
    "external-claim-set",
    "application-readiness",
    "resume-tailoring-manifest",
)
DEMO_APPLICATION_ID = "APP-DEFACED00001"


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

    def discover_official_jobs(
        self,
        snapshot: bytes,
        *,
        official_entry_url: str,
        company_domain: str,
        source_format: str,
    ) -> dict[str, Any]:
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


def _seed_demo_review_queue(database: JobOpsDB, onboarding: PrivateOnboarding, *, profile_ref: str) -> None:
    queue = QueueManager(database)
    job_id = "JOB-DEFACED00001"
    packet_id = "RPK-DEFACED00001"
    official_url = "https://careers.example.test/jobs/synthetic-demo"
    application_url = "https://boards.example.test/jobs/synthetic-demo"
    jd_hash = sha256_bytes(b"synthetic-demo-jd")
    freshness_hash = sha256_bytes(b"synthetic-demo-freshness")
    route_hash = sha256_bytes(b"synthetic-demo-route")
    claim_hash = sha256_bytes(b"synthetic-demo-claims")
    form_hash = sha256_bytes(b"synthetic-demo-form")
    answers_hash = sha256_bytes(b"synthetic-demo-answers")
    resume_hash = sha256_bytes(b"synthetic-demo-resume")
    upload = UploadBinding("synthetic-resume.pdf", "resume", resume_hash)
    demo_form_questions = [
        {
            "id": "field-synthetic-location",
            "label": "Preferred work location",
            "classification": "ordinary",
            "action": "PREFILL_PROPOSAL",
        }
    ]
    demo_sensitive_fields = [
        {
            "id": "field-final-submit",
            "label": "Final submission",
            "classification": "final_submit_stop",
            "action": "STOP",
        }
    ]
    material_plan = {
        "schema_version": 1,
        "status": "READY_FOR_REVIEW",
        "resume": {
            "derivation": "TAILORED_COPY_OF_SINGLE_APPROVED_MASTER",
            "generated_before_application": True,
        },
        "cover_letter": {"request_status": "NOT_REQUESTED", "generation_status": "NOT_GENERATED"},
        "public_links": [],
        "portfolio_file": {"request_status": "NOT_REQUESTED", "binding_status": "NOT_REQUESTED"},
        "all_uploads_and_submission_blocked": True,
        "real_external_actions": 0,
    }
    execution_plan = build_application_execution_plan(
        application_id=DEMO_APPLICATION_ID,
        source_route={
            "provider": "greenhouse", "route_hash": route_hash,
            "guest_mode": "GUEST_SELECTED", "account_action": "NONE",
        },
        form_snapshot_hash=form_hash,
        browser_plan_hash=form_hash,
        form_fields=[*demo_form_questions, *demo_sensitive_fields],
        material_plan=material_plan,
        pending_limit=int(queue.status()["pending_limit"]),
    )
    packet: dict[str, Any] = {
        "schema_version": 1,
        "status": "AWAITING_APPROVAL",
        "packet_id": packet_id,
        "application_id": DEMO_APPLICATION_ID,
        "job": {
            "job_id": job_id,
            "company": "Synthetic Demo Studio",
            "title": "Workflow Quality Analyst",
            "official_url": official_url,
        },
        "jd_captured_at": iso_utc(),
        "fit": {
            "overall_score": 88,
            "recommendation": "STRONG_MATCH",
            "explanation": ["Synthetic skills align with the fictional role; no live freshness claim is made."],
        },
        "hard_gaps": [],
        "resume_bullets": [
            {
                "text": "Improved a synthetic governed-review workflow by 20%.",
                "claim_id": "CLM-SYNTHETIC-DEMO",
                "evidence": ["synthetic-demo-source"],
            }
        ],
        "master_resume_diff": {"changed_sections": ["project"]},
        "form_questions": demo_form_questions,
        "sensitive_fields": demo_sensitive_fields,
        "uploads": [upload.as_dict()],
        "material_plan": material_plan,
        "execution_plan": execution_plan,
        "external_actions": ["upload_material", "submit_application"],
        "source_route": {
            "route_kind": "OFFICIAL_TO_APPROVED_ATS",
            "provider": "greenhouse",
            "guest_mode": "GUEST_SELECTED",
            "account_action": "NONE",
            "official_entry_url": official_url,
            "current_url": application_url,
        },
        "queue": queue.status(),
    }
    packet["content_hash"] = sha256_bytes(canonical_json(packet))
    packet_ref = onboarding.import_bytes("review_packet", canonical_json(packet), synthetic=True)
    context = ApprovalContext(
        application_id=DEMO_APPLICATION_ID,
        job_id=job_id,
        jd_snapshot_hash=jd_hash,
        jd_freshness_hash=freshness_hash,
        source_route_hash=route_hash,
        canonical_url=application_url,
        ats_tenant="synthetic-demo",
        ats_board="careers",
        ats_job_identity="synthetic-demo",
        profile_version="SYNTHETIC-DEMO-1",
        claim_set_hash=claim_hash,
        form_snapshot_hash=form_hash,
        answers_hash=answers_hash,
        review_packet_hash=str(packet["content_hash"]),
        uploads=(upload,),
        external_actions=("upload_material", "submit_application"),
        site_policy_version="SYNTHETIC-DEMO-1",
    )
    admission = queue.enqueue(jd_hash, source_type="synthetic_demo", source_locator="synthetic-demo")
    queue.admit_awaiting(
        admission.reservation_id,
        context,
        snapshot_relative_path="synthetic-demo/jd.txt",
        job_details={
            "source_type": "synthetic_demo",
            "source_locator": "synthetic-demo",
            "official_url": official_url,
            "company": "Synthetic Demo Studio",
            "title": "Workflow Quality Analyst",
            "location": "Remote · Synthetic",
        },
        secure_profile_ref=profile_ref,
        review_packet={
            "packet_id": packet_id,
            "content_hash": packet["content_hash"],
            "secure_ref": packet_ref["secure_ref"],
            "status": "AWAITING_APPROVAL",
        },
    )


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
    source = OnboardingCenterService.import_source(service, "project_case", ".txt", DEMO_SOURCE.encode("utf-8"))
    _, private_state = service.ensure_state()
    imported = next(
        item for item in private_state["sources"]
        if item["source_id"] == source["source_id"]
    )
    _seed_demo_review_queue(database, onboarding, profile_ref=str(imported["secure_ref"]))
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
