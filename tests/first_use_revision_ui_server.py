"""Empty synthetic first-use journey with actual Windows DPAPI and PDF tools.

The only model inputs are the pinned PDF replay and two bounded existing fake
adapter tasks. No Profile, Claim decision, master, authorization or application
is seeded. All state changes must use the ordinary local UI/API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

from pdf_regions_ui_server import ROOT, FixedInputService, RecordedSyntheticEngine
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
import jobops.orchestrator as orchestrator_module

FIXTURES = ROOT / "tests/fixtures/first-use-revision-v1"
ALLOWED_TASKS = {"JOBFLOW_APPLICATION_MATERIAL_DECISION_V1", "JOBFLOW_FORM_SEMANTIC_REVIEW_V1"}


class JourneyEngine(RecordedSyntheticEngine):
    def __init__(self):
        self.task_counts: dict[str, int] = {}

    def execute_structured_task(self, request):
        task = request.get("task")
        if task not in ALLOWED_TASKS:
            raise JobOpsError("SYNTHETIC_FIXTURE_MISMATCH", "This journey accepts only its two synthetic adapter tasks.")
        self.task_counts[task] = self.task_counts.get(task, 0) + 1
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/fixtures/fake_jobops_ai.py")],
            input=json.dumps(request, ensure_ascii=False), text=True, encoding="utf-8",
            capture_output=True, check=True, timeout=30, cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return json.loads(result.stdout)


class JourneyService(FixedInputService):
    def prepare_offline_application_bundle(self, *, metadata, files):
        expected = {"jd": "role.txt", "official": "official.html", "form": "form.html"}
        if set(files) != set(expected) or any(
            value != (FIXTURES / expected[key]).read_bytes()
            for key, (_, value) in files.items()
        ):
            raise JobOpsError("SYNTHETIC_FIXTURE_ONLY", "Only this journey's pinned local snapshots are accepted.")
        if metadata.get("official_url") != "https://example.com/careers/analyst-first-use" or metadata.get("application_url") != "https://example.com/careers/analyst-first-use/apply":
            raise JobOpsError("SYNTHETIC_FIXTURE_ONLY", "Only the reserved example origin is accepted.")
        return super().prepare_offline_application_bundle(metadata=metadata, files=files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio-control", action="store_true")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    artifact_dir = args.artifacts.resolve()
    if not artifact_dir.is_relative_to((ROOT / "reports/first-use-revision").resolve()):
        raise ValueError("Synthetic artifacts must remain inside this checkout's report directory.")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JOBFLOW_DOCUMENT_PARSER"] = "pdf_regions"
    os.environ["JOBFLOW_VISUAL_ENABLE"] = "false"
    engine = JourneyEngine()
    with tempfile.TemporaryDirectory(prefix="jobflow-empty-revision-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        (project / "config").mkdir()
        shutil.copy2(ROOT / "config/policy.json", project / "config/policy.json")
        script_root = ROOT / ".agents/skills/job-application-operator/scripts"
        export_script = project / ".agents/skills/job-application-operator/scripts/export-docx-pdf.ps1"
        export_script.parent.mkdir(parents=True)
        shutil.copy2(script_root / "export-docx-pdf.ps1", export_script)
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(script_root / "secure-store.ps1", local_app_data=root / "local")
        service = JourneyService(project, database, PrivateOnboarding(database, store), ai_engine=engine)
        server = create_server(service, token="synthetic-first-use-revision")
        native_qa = orchestrator_module.structural_qa
        qa_count = 0

        def capture_native_qa(docx, pdf, pages, **kwargs):
            nonlocal qa_count
            # Observation only: retain actual synthetic inputs before the real
            # failure cleanup. Never substitute a result or alter a QA gate.
            result = native_qa(docx, pdf, pages, **kwargs)
            qa_count += 1
            destination = artifact_dir / f"native-qa-{qa_count}"
            destination.mkdir()
            shutil.copy2(docx, destination / docx.name)
            shutil.copy2(pdf, destination / pdf.name)
            for number, page in enumerate(pages, 1):
                shutil.copy2(page, destination / f"page-{number}.png")
            (destination / "qa.json").write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
            return result

        def stop_on_input():
            sys.stdin.readline()
            server.shutdown()

        if args.stdio_control:
            threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/session/synthetic-first-use-revision/", "synthetic_only": True}), flush=True)
        try:
            with patch.object(orchestrator_module, "structural_qa", capture_native_qa):
                server.serve_forever()
        finally:
            server.server_close()
            _, state = service.ensure_state()
            with database.connect() as connection:
                counts = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                          for name in ("applications", "claims", "approvals", "external_action_attempts")}
                rows = connection.execute("SELECT secure_ref, kind FROM private_refs WHERE status='ACTIVE' AND kind IN ('generated_resume_docx','generated_resume_pdf','generated_cover_letter_docx','generated_cover_letter_pdf') ORDER BY kind, created_at").fetchall()
            artifacts = []
            for index, row in enumerate(rows):
                extension = ".pdf" if row["kind"].endswith("_pdf") else ".docx"
                filename = f"{index + 1:02d}-{row['kind']}{extension}"
                content = service.onboarding.read_bytes(row["secure_ref"])
                (artifact_dir / filename).write_bytes(content)
                artifacts.append({"file": filename, "sha256": hashlib.sha256(content).hexdigest()})
            receipt = {
                "synthetic_only": True, "seeded_approval": False, "real_model_calls": 0,
                "recorded_response_replays": engine.replays, "synthetic_adapter_tasks": engine.task_counts,
                "revision_number": state.get("revision_number"), "status": state.get("status"),
                "source_count": len(state.get("sources", [])),
                "material_claim_count": len(state.get("material_claims", [])),
                "claim_decisions": [c.get("decision") for c in state.get("material_claims", [])],
                "database_counts": counts, "generated_artifacts": artifacts,
                "actual_native_qa_observations": qa_count, "qa_results_overridden": False,
            }
            service.close()
    receipt["temporary_state_removed"] = not root.exists()
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
