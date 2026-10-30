"""Replay the two immutable real responses; no network/provider fallback exists.

Actual original synthetic PDF parsing and DPAPI persistence are exercised. This
does not create another live-model observation or accept the complete product.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("context_frozen_experiment", ROOT / "scripts/evaluate-source-context.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)
from jobops.ai_connections import WSLHermesCLIEngine
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import iso_utc

OBSERVATIONS = {
    "source-context-v1": ("source-context-live-v1.json", "f7cb0e3ae7be716b16c1f421da7ed2e192026ab14a213edc3c4641e60a3ad51a"),
    "fact-preservation-v1": ("fact-preservation-live-v1.json", "f2beb6531c1f46191befd233d865641e8233725497848244d8a1242d98965e3a"),
}


def load_observation(name="source-context-v1"):
    filename, expected_hash = OBSERVATIONS[name]
    path = ROOT / "evaluations/2026-10-30" / filename
    if experiment.digest(path) != expected_hash:
        raise ValueError("The pinned original observation changed")
    value = json.loads(path.read_text(encoding="utf8"))
    if not value.get("protocol_closed") or value["adapter_requests"] != 2 or len(value["responses"]) != 2:
        raise ValueError("Unexpected closed experiment population")
    return value


class ReplayEngine(WSLHermesCLIEngine):
    def __init__(self, content, text):
        super().__init__("unused", "unused", model="recorded-synthetic-response", provider_id="replay")
        self.content, self.text, self.used = content, text, False

    def _invoke(self, request):
        numbered = request.get("line_numbered_document") or []
        if (self.used or request.get("task") != "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2"
            or [str(line).split("\t", 1)[1] for line in numbered] != self.text.splitlines()
            or not numbered or not str(numbered[0]).startswith("1\t")):
            raise JobOpsError("CONTEXT_REPLAY_MISMATCH", "The recorded source/task was changed or replay exhausted; no fallback is available.")
        self.used = True
        return {"ok": True, "toolSummary": {"calls": 0, "tools": []}, "result": {"content": self.content}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observation", choices=tuple(OBSERVATIONS), default="source-context-v1")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Keep existing observations immutable; choose a new offline output")
    observation = load_observation(args.observation)
    observation_hash = OBSERVATIONS[args.observation][1]
    plan, old_arms = experiment.load_inputs(verify_product=False)
    old_by_case = {arm["case"]: arm for arm in old_arms}
    manifest = {row["id"]: row for row in experiment.complete.complete.load_manifest()["cases"]}
    reports = (ROOT / "reports").resolve()
    reports.mkdir(exist_ok=True)
    files = [*plan["product"], "src/jobops/ai_connections.py", "scripts/replay-source-context.py"]
    report = {"observed_at": iso_utc(), "mode": "SAME_RESPONSE_ORIGINAL_PDF_AND_DPAPI_REPLAY", "synthetic_only": True,
              "observation": args.observation, "original_observation_lf_sha256": observation_hash, "live_provider_calls": 0,
              "current_stage_model_cost_usd": 0, "original_billed_cost_usd": None,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_lf_sha256": {name: experiment.digest(ROOT / name) for name in files},
              "independent_human_acceptance": False, "whole_material_acceptance": False, "arms": []}
    for response in observation["responses"]:
        case_id = response["case"]
        old, case = old_by_case[case_id], manifest[case_id]
        data = (experiment.complete.complete.CORPUS / case["file"]).read_bytes()
        if experiment.hashlib.sha256(data).hexdigest() != case["sha256"]:
            raise ValueError("Original synthetic PDF bytes changed")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="source-context-replay-", dir=reports) as raw:
            temporary = Path(raw).resolve()
            temporary.relative_to(reports)
            project = temporary / "project"
            project.mkdir()
            shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
            shutil.copytree(ROOT / "schemas", project / "schemas")
            database = JobOpsDB(project / "state/jobops.db")
            database.initialize()
            store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1",
                                      local_app_data=temporary / "local")
            onboarding = PrivateOnboarding(database, store)
            engine = ReplayEngine(response["content"], old["analyzed_text"])
            service = OnboardingCenterService(project, database, onboarding, ai_engine=engine)
            try:
                with experiment.complete.layout.mode_environment("existing_text"):
                    preview = service.preview_source("resume", ".pdf", data)
                _, state = service.ensure_state()
                pending = state["pending_sources"][0]
                notes = pending["context_notes"]
                answers_before = service.bootstrap()["answers"]
                service.commit_source(preview["source_id"], [])
                reopened = OnboardingCenterService(project, database, onboarding, ai_engine=engine)
                try:
                    boot = reopened.bootstrap()
                    if boot["context_notes"] != notes or len(boot["claims"]) != 0:
                        raise AssertionError("Context persistence or no-Claim boundary failed")
                    # Existing explicit contact-hint import is measured separately;
                    # all six constraint answer fields must remain unchanged.
                    fields = ("remote_preference", "preferred_locations", "available_start_date",
                              "work_authorization", "visa_sponsorship", "minimum_salary")
                    if any(boot["answers"][key] != answers_before[key] for key in fields):
                        raise AssertionError("A context annotation changed a Profile constraint answer")
                    facts = [{"fact_id": fact["id"], "exact_quote_note_ids": [note["note_id"] for note in notes
                              if experiment.complete.normalized(fact["text"]) in experiment.complete.normalized(note["quote"])]}
                             for fact in case["facts"]]
                    row = {"case": case_id, "status": "SOURCE_SAVED_AND_REOPENED", "notes": notes,
                           "candidate_count": len(pending["candidates"]), "candidates": pending["candidates"],
                           "summary": pending["extraction_summary"], "exact_context_matches": facts,
                           "candidate_diagnostic": experiment.complete.assess(case, pending["candidates"], old["analyzed_text"]),
                           "constraint_answers_unchanged": True, "material_claims": 0,
                           "retained_original_matches": store.get_bytes(pending["metadata"]["secure_ref"]) == data}
                    with database.connect() as connection:
                        row["database_counts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                                                  for name in ("applications", "claims", "approvals", "external_action_attempts")}
                finally:
                    reopened.close()
            finally:
                service.close()
        row.update(temporary_state_removed=not Path(raw).exists(), elapsed_ms=round((time.monotonic() - started) * 1000))
        report["arms"].append(row)
        print(json.dumps({"case": case_id, "status": row["status"], "notes": len(notes), "candidate_count": row["candidate_count"]}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
