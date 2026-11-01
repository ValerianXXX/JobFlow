"""Disposable source-retention UI check using fixed synthetic responses only.

This adapter is a test fixture, not AI capability or live-model acceptance. It
cannot invoke a provider, accepts only its generated synthetic DOCX bytes, and
starts with no sources, Claims, Profile answers or approvals.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docx import Document
from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService, _docx_text
from jobops.onboarding_server import create_server
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import sha256_bytes


def fixtures(output: Path) -> tuple[dict[str, dict], dict[str, bytes]]:
    responses, documents = {}, {}
    cases = json.loads((ROOT / "tests/fixtures/assertion_status/cases.json").read_text(encoding="utf8"))["cases"]
    by_id = {row["id"]: row for row in cases}
    for name, ids, keep_valid in (
        ("english", ("en-negative", "en-proposed"), True),
        ("chinese", ("zh-negative", "zh-planned"), True),
        ("all-filtered", ("en-never", "en-planned"), False),
    ):
        header = "Synthetic Assertion Review " + name
        lines = [header, "Analyst 2023-2024", *(by_id[item]["source"] for item in ids)]
        document = Document()
        for line in lines:
            document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        data = buffer.getvalue()
        (output / (name + ".docx")).write_bytes(data)
        documents[name] = data
        text = _docx_text(data)
        assert text.splitlines() == lines, "Fixture lines changed"
        candidates = []
        for index, case_id in enumerate(ids, start=3):
            case = by_id[case_id]
            for statement in ([case["statement"], case["source"]] if keep_valid else [case["statement"]]):
                candidates.append({"entity_key": "synthetic", "category": "project", "claim_kind": "responsibility",
                                   "confidence": "HIGH", "statement": statement, "line_start": index, "line_end": index})
        responses[text] = {"schema_version": AI_PROTOCOL_VERSION,
            "entities": [{"entity_key": "synthetic", "entity_type": "project", "organization": header,
                          "role": "Analyst", "start_date": "2023", "end_date": "2024", "line_start": 1, "line_end": 2}],
            "candidates": candidates}
    return responses, documents


class FixtureEngine(LocalSubprocessAIEngine):
    def __init__(self, responses: dict[str, dict]) -> None:
        super().__init__([sys.executable])
        self.responses, self.calls = responses, []

    def public_status(self) -> dict:
        return {**super().public_status(), "provider": "SYNTHETIC_FIXTURE_ADAPTER", "test_fixture_only": True}

    def execute_structured_task(self, payload: dict) -> dict:
        text = "\n".join(line.split("\t", 1)[1] for line in payload.get("line_numbered_document", []))
        if text not in self.responses or len(self.calls) >= 6:
            raise JobOpsError("SYNTHETIC_FIXTURE_ONLY", "Only the generated synthetic fixture can be analyzed.")
        self.calls.append({"task": payload.get("task"), "input_sha256": sha256_bytes(text.encode("utf8"))})
        return deepcopy(self.responses[text])


class FixtureService(OnboardingCenterService):
    allowed_documents: set[str]

    def preview_source(self, source_type, extension, data):
        if source_type != "resume" or extension != ".docx" or sha256_bytes(data) not in self.allowed_documents:
            raise JobOpsError("SYNTHETIC_FIXTURE_ONLY", "Only the generated synthetic DOCX resumes are allowed.")
        return super().preview_source(source_type, extension, data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    responses, documents = fixtures(output)
    engine = FixtureEngine(responses)
    record = {"synthetic_only": True, "mode": "FIXED_ADAPTER_ACTUAL_HTTP_UI_AND_DPAPI",
              "live_provider_calls": 0, "model_quality_measured": False, "seeded_approvals": False,
              "started_at": datetime.now(timezone.utc).isoformat(), "fixture_calls": engine.calls,
              "fixture_sha256": {name: sha256_bytes(data) for name, data in documents.items()}}
    with tempfile.TemporaryDirectory(prefix="jobflow-assertion-status-") as raw:
        temporary = Path(raw)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1",
                                  local_app_data=temporary / "local")
        service = FixtureService(project, database, PrivateOnboarding(database, store), ai_engine=engine)
        service.allowed_documents = {sha256_bytes(data) for data in documents.values()}
        server = create_server(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"url": server.url, "fixtures": str(output), "adapter": "SYNTHETIC_ONLY"}), flush=True)
        (output / "local-endpoint.json").write_text(json.dumps({"url": server.url}), encoding="utf8")
        try:
            deadline = time.monotonic() + 1200
            while not (output / "stop").exists() and time.monotonic() < deadline:
                time.sleep(0.25)
            boot = service.bootstrap()
            _, state = service.ensure_state()
            record.update(source_count=len(state["sources"]), pending_count=len(state["pending_sources"]),
                          claim_count=len(boot["claims"]),
                          master_resume_present=boot["application_readiness"]["master_resume"]["present"],
                          master_resume_editable=boot["application_readiness"]["master_resume"]["editable_docx"],
                          sources=[{"source_id": row["source_id"], "extraction_summary": row["extraction_summary"],
                                    "original_bytes_match": sha256_bytes(store.get_bytes(row["secure_ref"]))
                                    in service.allowed_documents} for row in state["sources"]])
            with database.connect() as connection:
                record["database_counts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                                             for name in ("applications", "claims", "approvals", "external_action_attempts")}
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            service.close()
    record["temporary_state_removed"] = not temporary.exists()
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output / "receipt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"receipt": str(output / "receipt.json"), "temporary_state_removed": record["temporary_state_removed"]}), flush=True)


if __name__ == "__main__":
    main()
