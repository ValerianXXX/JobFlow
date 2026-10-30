"""One closed six-request experiment on pinned public synthetic transcripts.

The existing Hermes route is required. No image re-transcription, applicant
input, tool use, Profile update, approval, import or business action is performed.
The fixed journal is exclusive and permanently consumes attempts before dispatch.
There is no resume, alternate output path or automatic retry entry point.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("context_complete_inputs", ROOT / "scripts/evaluate-complete-claims.py")
complete = importlib.util.module_from_spec(spec)
spec.loader.exec_module(complete)

from jobops.ai_connections import WSLHermesCLIEngine
from jobops.errors import JobOpsError
from jobops.source_context import bind_context_notes
from jobops.util import iso_utc
import hashlib


PLAN = ROOT / "evaluations/protocols/source-context-v1.json"
JOURNAL = ROOT / "reports/source-context-live-v1.requests.jsonl"
OUTPUT = ROOT / "reports/source-context-live-v1.json"


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_inputs(*, verify_product=True):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan["protocol"] != "SOURCE_CONTEXT_V1" or plan["max_requests"] != 6 or plan["requests_per_case"] != 1:
        raise ValueError("Unexpected fixed protocol")
    for relative, expected in {**plan["inputs"], **(plan["product"] if verify_product else {})}.items():
        if digest(ROOT / relative) != expected:
            raise ValueError("Frozen input or product code changed")
    arms = {}
    for relative in plan["observations"]:
        observation = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        if observation.get("synthetic_only") is not True:
            raise ValueError("Only public synthetic observations are allowed")
        for arm in observation["arms"]:
            if arm.get("status") == "SOURCE_PREVIEW_READY" and arm.get("analyzed_text"):
                arms[arm["case"]] = arm
    if set(arms) != set(plan["cases"]):
        raise ValueError("The fixed population changed")
    return plan, [arms[case] for case in plan["cases"]]


class AttemptJournal:
    def __init__(self, path):
        self.stream = path.open("x", encoding="utf-8")
        self.cases = set()
        self.count = 0

    def write(self, row):
        self.stream.write(json.dumps(row, ensure_ascii=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def reserve(self, case):
        if self.count >= 6 or case in self.cases:
            raise JobOpsError("CONTEXT_EVALUATION_LIMIT", "The frozen request budget is exhausted; no retry is allowed.")
        self.count += 1
        self.cases.add(case)
        self.write({"event": "RESERVED", "request": self.count, "case": case, "at": iso_utc()})

    def close(self):
        self.stream.close()


class ContextEngine(WSLHermesCLIEngine):
    def __init__(self, route, journal):
        super().__init__("wsl.exe", "Ubuntu", model=route["model"], provider_id=route["provider"])
        self.journal, self.case = journal, None
        self.responses = []

    def _invoke(self, request):
        if request.get("task") != "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2" or not self.case:
            raise JobOpsError("CONTEXT_EVALUATION_TASK", "Only the fixed document-understanding task is allowed.")
        self.journal.reserve(self.case)
        started = time.monotonic()
        record = {"case": self.case, "status": "FAILED", "tool_calls": None}
        try:
            result = super()._invoke(request)
            record["tool_calls"] = result["toolSummary"]["calls"]
            if record["tool_calls"] != 0:
                raise JobOpsError("CONTEXT_EVALUATION_TOOL_USE", "Unexpected tool use; stop this observation.")
            record["status"] = "RETURNED"
            self.responses.append({"case": self.case, "content": result["result"]["content"]})
            return result
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            self.journal.write({"event": "FINISHED", **record})


def main():
    if sys.argv[1:] != ["--live-hermes"]:
        raise SystemExit("Use --live-hermes for the fixed one-shot synthetic protocol only.")
    plan, arms = load_inputs()
    if OUTPUT.exists() or JOURNAL.exists():
        raise SystemExit("This protocol has already started or completed. Do not remove its receipts or rerun it.")
    route = complete.layout.previous.public_hermes_route()
    if route != plan["route"]:
        raise SystemExit("The existing Hermes route changed; the frozen experiment was not dispatched.")
    gold = {case["id"]: case for case in complete.complete.load_manifest()["cases"]}
    omissions = {case["caseId"]: [fact["factId"] for fact in case["facts"]
                 if fact["decision"] == "OMITTED" and fact["omissionReason"] != "PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM"]
                 for case in json.loads((ROOT / plan["review"]).read_text(encoding="utf-8"))["cases"]}
    JOURNAL.parent.mkdir(exist_ok=True)
    journal = AttemptJournal(JOURNAL)
    engine = ContextEngine(route, journal)
    report = {"protocol": plan["protocol"], "observed_at": iso_utc(), "mode": "ANALYSIS_ONLY_FIXED_TRANSCRIPTS",
              "synthetic_only": True, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "plan_lf_sha256": digest(PLAN), "runner_lf_sha256": digest(Path(__file__)),
              "route_before": route, "max_requests": 6, "input_files": plan["inputs"], "product_files": plan["product"],
              "before_reviewed_claim_representation": {"covered": 241, "facts": 282, "candidates": 197},
              "new_semantic_recall": None, "billed_cost_usd": None, "token_usage": None,
              "provider_internal_requests": None, "human_review_seconds": None,
              "independent_human_acceptance": False, "whole_material_acceptance": False,
              "automatic_profile_updates": 0, "automatic_approvals": 0, "real_external_actions": 0, "arms": []}
    failures = 0
    try:
        for old in arms:
            case = old["case"]
            if failures >= 2:
                report["arms"].append({"case": case, "status": "NOT_RUN_FAILURE_STOP"})
                continue
            engine.case = case
            started = time.monotonic()
            text = old["analyzed_text"]
            source_id = f"SRC-SYNTHETIC-CONTEXT-{case}"
            row = {"case": case, "source_sha256": old["source_sha256"],
                   "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "target_fact_ids": omissions[case]}
            try:
                result = engine.analyze_document(text, source_id=source_id, source_type="resume")
                candidates, summary = result
                notes = bind_context_notes(result.context_notes, text=text, source_id=source_id,
                                           source_sha256=old["source_sha256"])
                matches = []
                for fact in gold[case]["facts"]:
                    if fact["id"] in omissions[case]:
                        matches.append({"fact_id": fact["id"], "exact_quote_note_ids": [n["note_id"] for n in notes
                                        if complete.normalized(fact["text"]) in complete.normalized(n["quote"])],
                                        "semantic_status": "REQUIRES_SOURCE_ADJUDICATION"})
                row.update(status="ANALYSIS_RETURNED", notes=notes, candidates=candidates, summary=summary,
                           context_exact_matches=matches, candidate_diagnostic=complete.assess(gold[case], candidates, text))
            except JobOpsError as error:
                failures += 1
                row.update(status="BLOCKED", code=error.code)
            except Exception as error:
                failures += 1
                row.update(status="FAILED", error_type=type(error).__name__)
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            report["arms"].append(row)
            journal.write({"event": "ARM", **row})
            print(json.dumps({"case": case, "status": row["status"], "notes": len(row.get("notes", [])),
                              "requests_reserved": journal.count}), flush=True)
        try:
            report["route_after"] = complete.layout.previous.public_hermes_route()
        except Exception:
            report["route_after"] = {"status": "UNAVAILABLE"}
        report.update(adapter_requests=journal.count, responses=engine.responses,
                      route_unchanged=report["route_after"] == route, protocol_closed=True)
        with OUTPUT.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
    finally:
        journal.close()
    print(json.dumps({"protocol_closed": True, "adapter_requests": journal.count, "whole_material_acceptance": False}))


if __name__ == "__main__":
    main()
