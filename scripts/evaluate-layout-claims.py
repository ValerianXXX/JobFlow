"""Six frozen synthetic inputs, existing Hermes, at most 12 adapter requests.

No input-file option, credential discovery, new provider, automatic approval or
external business action. A checkpoint is appended after every attempted arm.
Unmatched paraphrases require adjudication and are not silently counted wrong.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_connections import WSLHermesCLIEngine
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import iso_utc, sha256_bytes


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = load_script("layout_corpus", "evaluate-material-corpus.py")
previous = load_script("previous_material_evaluation", "evaluate-material-preview.py")
CASE_IDS = ("en-columns", "zh-columns", "mixed-staggered_columns", "zh-scan", "en-scanned_columns", "mixed-portfolio")
CALL_LIMIT = 12


class ObservedHermes(WSLHermesCLIEngine):
    def __init__(self, route, *, request_limit=CALL_LIMIT):
        if type(request_limit) is not int or not 1 <= request_limit <= 18:
            raise ValueError("Synthetic evaluation request limit must be between 1 and 18")
        super().__init__("wsl.exe", "Ubuntu", model=route["model"], provider_id=route["provider"])
        self.request_limit = request_limit
        self.calls = []
        self.analyzed_text = ""

    def _invoke(self, request):
        if len(self.calls) >= self.request_limit:
            raise JobOpsError("EVALUATION_CALL_LIMIT", "The fixed synthetic adapter request budget is exhausted.")
        record = {"task": request.get("task"), "status": "FAILED", "tool_calls": None}
        self.calls.append(record)
        started = time.monotonic()
        try:
            result = super()._invoke(request)
            record.update(status="RETURNED", tool_calls=result["toolSummary"]["calls"])
            return result
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)

    def analyze_document(self, text, *, source_id, source_type):
        self.analyzed_text = text
        return super().analyze_document(text, source_id=source_id, source_type=source_type)


@contextmanager
def mode_environment(mode):
    updates = {"JOBFLOW_DOCUMENT_PARSER": "pdf_regions" if mode == "pdf_regions" else "",
               "JOBFLOW_VISUAL_ENABLE": "true" if mode == "visual" else "false",
               "JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER": "true" if mode == "visual" else "false"}
    old = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def assess(case, candidates, text):
    lines = text.splitlines()
    facts = []
    context_words = (("store", "门店"), ("report", "报告"), ("budget", "预算"), ("scenario", "场景"))
    for entity_index, entity in enumerate(case["entities"]):
        for fact_index, fact in enumerate(entity["facts"]):
            context = context_words[entity_index * 2 + fact_index]
            matches = []
            issues = set()
            for candidate in candidates:
                statement = str(candidate.get("statement", "")).casefold().replace(",", "")
                if not re.search(r"(?<!\d)" + re.escape(fact["number"]) + r"(?!\d)", statement) or not any(w in statement for w in context):
                    continue
                problems = []
                observed = candidate.get("entity") or {}
                if corpus.normalized(str(observed.get("organization", ""))) != corpus.normalized(entity["organization"]):
                    problems.append("ENTITY_MISMATCH")
                owners = ("team", "团队") if fact["owner"] == "team" else ("applicant", "candidate", "申请人", "本人")
                if not any(word in statement for word in owners):
                    problems.append("OWNERSHIP_REQUIRES_ADJUDICATION")
                dates = str(observed.get("start_date", "")) + " " + str(observed.get("end_date", ""))
                if set(re.findall(r"20\d{2}", dates)) != set(entity["years"]):
                    problems.append("ENTITY_DATES_MISSING_OR_CHANGED")
                provenance = candidate.get("provenance") or {}
                start, end = provenance.get("line_start"), provenance.get("line_end")
                excerpt = "\n".join(lines[start-1:end]) if type(start) is int and type(end) is int and 1 <= start <= end <= len(lines) else ""
                if corpus.normalized(fact["statement"]) not in corpus.normalized(excerpt):
                    problems.append("VERBATIM_EVIDENCE_MISSING")
                if candidate.get("selected") is not False:
                    problems.append("CANDIDATE_PRESELECTED")
                if not problems:
                    matches.append(candidate["candidate_id"])
                issues.update(problems)
            facts.append({"organization": entity["organization"], "number": fact["number"], "owner": fact["owner"],
                          "matched_candidate_ids": matches, "issues": sorted(issues)})
    return {"expected_facts": len(facts), "matched_facts": sum(bool(f["matched_candidate_ids"]) for f in facts),
            "facts": facts, "numeric_errors": None, "misattributions": None,
            "manual_adjudication_required": True}


def run_arm(case, mode, engine, *, corpus_dir=corpus.CORPUS, assessor=assess):
    started, calls_before = time.monotonic(), len(engine.calls)
    engine.analyzed_text = ""
    data = (corpus_dir / case["file"]).read_bytes()
    receipt = {"case": case["id"], "mode": mode, "source_sha256": case["sha256"], "status": "NOT_RUN"}
    with tempfile.TemporaryDirectory(prefix="jobflow-layout-claims-") as raw:
        temporary = Path(raw)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1", local_app_data=temporary / "local")
        service = OnboardingCenterService(project, database, PrivateOnboarding(database, store), ai_engine=engine)
        try:
            with mode_environment(mode):
                try:
                    preview = service.preview_source("resume", Path(case["file"]).suffix, data)
                    _, state = service.ensure_state()
                    pending = next(row for row in state["pending_sources"] if row["source_id"] == preview["source_id"])
                    candidates = pending["candidates"]
                    receipt.update(status=preview["status"], candidates=candidates, analyzed_text=engine.analyzed_text,
                                   assessment=assessor(case, candidates, engine.analyzed_text), extraction_summary=preview["extraction_summary"],
                                   retained_original_matches=store.get_bytes(pending["metadata"]["secure_ref"]) == data)
                    if mode != "existing_text" and candidates:
                        try:
                            service.commit_source(preview["source_id"], [{"candidate_id": candidates[0]["candidate_id"], "selected": True}])
                            receipt["unconfirmed_selection"] = "UNEXPECTEDLY_ACCEPTED"
                        except JobOpsError as error:
                            receipt["unconfirmed_selection"] = error.code
                    _, after = service.ensure_state()
                    receipt["material_claims"] = len(after.get("material_claims", []))
                except JobOpsError as error:
                    receipt.update(status="BLOCKED", code=error.code)
                except Exception as error:
                    receipt.update(status="FAILED", code="EVALUATOR_ERROR", error_type=type(error).__name__)
            with database.connect() as connection:
                receipt["database_counts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                                              for name in ("applications", "claims", "approvals", "external_action_attempts")}
        finally:
            service.close()
    receipt.update(temporary_state_removed=not Path(raw).exists(), elapsed_ms=round((time.monotonic()-started)*1000),
                   adapter_requests=len(engine.calls)-calls_before)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-hermes", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoint = args.output.with_suffix(".jsonl")
    if args.output.exists() or checkpoint.exists():
        parser.error("Choose a new output; prior observations are immutable")
    cases = {case["id"]: case for case in corpus.load_cases()}
    route = previous.public_hermes_route()
    engine = ObservedHermes(route)
    report = {"observed_at": iso_utc(), "synthetic_only": True, "model_route_before": route, "adapter_request_limit": CALL_LIMIT,
              "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_observation": True, "manifest_lf_sha256": corpus.MANIFEST_LF_SHA256,
              "collector_lf_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
              "cost_usd": None, "usage_tokens": None, "provider_api_calls": None,
              "billing_note": "Adapter requests exclude unknown provider-internal retries; no billing receipt is available.",
              "business_completion": "NOT_ATTEMPTED", "arms": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open("x", encoding="utf-8") as stream:
        for case_id in CASE_IDS:
            case = cases[case_id]
            for mode in ("existing_text", "pdf_regions" if case["text_only_expected"] == "READABLE" else "visual"):
                row = run_arm(case, mode, engine)
                report["arms"].append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(json.dumps({"case": case_id, "mode": mode, "status": row["status"], "requests": row["adapter_requests"]}), flush=True)
    try:
        report["model_route_after"] = previous.public_hermes_route()
    except Exception:
        report["model_route_after"] = {"status": "UNAVAILABLE"}
    report["model_route_unchanged"] = report["model_route_after"] == route
    report["adapter_requests"] = engine.calls
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed_arms": len(report["arms"]), "requests": len(engine.calls), "cost_usd": None}))


if __name__ == "__main__":
    main()
