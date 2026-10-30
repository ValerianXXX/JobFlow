"""Two fixed follow-up requests; the preceding closed protocol stays immutable.

The changed prompt tests preservation of entity facts and limitations. A budget
journal in this repository's common Git directory prevents another worktree
from repeating the protocol. No arbitrary input/output path or retry is accepted.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fact_context_predecessor", ROOT / "scripts/evaluate-source-context.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
from jobops.errors import JobOpsError
from jobops.source_context import bind_context_notes
from jobops.util import has_reparse_component, iso_utc

PROTOCOL = "SOURCE_FACT_PRESERVATION_V1"
PLAN = ROOT / "evaluations/protocols/fact-preservation-v1.json"
OUTPUT = ROOT / "reports/fact-preservation-live-v1.json"
PUBLISHED = ROOT / "evaluations/2026-10-30/fact-preservation-live-v1.json"


class TwoAttemptJournal(previous.AttemptJournal):
    def reserve(self, case):
        if self.count >= 2:
            raise JobOpsError("FACT_EVALUATION_LIMIT", "This follow-up permits only two requests, with no retry.")
        super().reserve(case)


def budget_path():
    common = Path(subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True,
    ).strip()).resolve()
    if not common.is_relative_to(ROOT.parent.resolve()) or has_reparse_component(common):
        raise ValueError("The common budget directory is outside this workspace or traverses a reparse point")
    directory = common / "jobflow-evaluation-budgets"
    directory.mkdir(exist_ok=True)
    if has_reparse_component(directory):
        raise ValueError("The budget directory cannot be a reparse point")
    return directory / f"{PROTOCOL}.requests.jsonl"


def load_plan():
    plan = json.loads(PLAN.read_text(encoding="utf8"))
    if (plan["protocol"] != PROTOCOL or plan["max_new_requests"] != 2
        or plan["cases"] != ["en-chronological", "zh-chronological"]):
        raise ValueError("Unexpected fixed follow-up population or budget")
    for path, digest in {**plan["inputs"], **plan["product"]}.items():
        if previous.digest(ROOT / path) != digest:
            raise ValueError("Frozen source, observation or code changed")
    original = json.loads((ROOT / plan["predecessor_observation"]).read_text())
    if original["adapter_requests"] != 2 or original["protocol_closed"] is not True:
        raise ValueError("The predecessor is not the expected closed two-request experiment")
    _, arms = previous.load_inputs(verify_product=False)
    return plan, [arm for case in plan["cases"] for arm in arms if arm["case"] == case]


def main():
    if sys.argv[1:] != ["--live-hermes"]:
        raise SystemExit("Use --live-hermes only for this fixed two-request synthetic follow-up.")
    if OUTPUT.exists() or PUBLISHED.exists():
        raise SystemExit("This follow-up already exists; do not remove or overwrite observations.")
    plan, arms = load_plan()
    journal_path = budget_path()
    if journal_path.exists():
        raise SystemExit("This protocol already started in a shared worktree; no repeat or automatic resume is allowed.")
    route = previous.complete.layout.previous.public_hermes_route()
    if route != plan["route"]:
        raise SystemExit("Existing Hermes selection changed before dispatch.")
    gold = {case["id"]: case for case in previous.complete.complete.load_manifest()["cases"]}
    journal = TwoAttemptJournal(journal_path)
    engine = previous.ContextEngine(route, journal)
    report = {"protocol": PROTOCOL, "observed_at": iso_utc(), "synthetic_only": True,
              "mode": "ANALYSIS_ONLY_SAME_TWO_EXPOSED_TRANSCRIPTS", "route_before": route,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "plan_lf_sha256": previous.digest(PLAN), "runner_lf_sha256": previous.digest(Path(__file__)),
              "input_files": plan["inputs"], "product_files": plan["product"],
              "predecessor_actual_requests": 2, "max_new_requests": 2, "shared_campaign_ceiling": 6,
              "independent_human_acceptance": False, "whole_material_acceptance": False,
              "billed_cost_usd": None, "token_usage": None, "provider_internal_requests": None,
              "real_external_actions": 0, "automatic_profile_updates": 0, "automatic_approvals": 0, "arms": []}
    failed = False
    try:
        for old in arms:
            case = old["case"]
            if failed:
                report["arms"].append({"case": case, "status": "NOT_RUN_FAILURE_STOP"})
                continue
            engine.case = case
            text = old["analyzed_text"]
            source_id = f"SRC-SYNTHETIC-CONTEXT-{case}"
            row = {"case": case, "source_sha256": old["source_sha256"],
                   "text_sha256": previous.hashlib.sha256(text.encode()).hexdigest()}
            started = time.monotonic()
            try:
                result = engine.analyze_document(text, source_id=source_id, source_type="resume")
                candidates, summary = result
                notes = bind_context_notes(result.context_notes, text=text, source_id=source_id,
                                           source_sha256=old["source_sha256"])
                row.update(status="ANALYSIS_RETURNED", notes=notes, candidates=candidates, summary=summary,
                           candidate_diagnostic=previous.complete.assess(gold[case], candidates, text),
                           exact_context_matches=[{"fact_id": fact["id"], "note_ids": [note["note_id"] for note in notes
                             if previous.complete.normalized(fact["text"]) in previous.complete.normalized(note["quote"])]}
                             for fact in gold[case]["facts"]])
            except JobOpsError as error:
                failed = True
                row.update(status="BLOCKED", code=error.code)
            except Exception as error:
                failed = True
                row.update(status="FAILED", error_type=type(error).__name__)
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            report["arms"].append(row)
            journal.write({"event": "ARM", **row})
            print(json.dumps({"case": case, "status": row["status"], "candidates": len(row.get("candidates", [])),
                              "notes": len(row.get("notes", [])), "requests_reserved": journal.count}), flush=True)
        try:
            report["route_after"] = previous.complete.layout.previous.public_hermes_route()
        except Exception:
            report["route_after"] = {"status": "UNAVAILABLE"}
        report.update(adapter_requests=journal.count, campaign_actual_requests=2 + journal.count,
                      route_unchanged=report["route_after"] == route, responses=engine.responses, protocol_closed=True)
        OUTPUT.parent.mkdir(exist_ok=True)
        with OUTPUT.open("x", encoding="utf8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
    finally:
        journal.close()
    print(json.dumps({"protocol_closed": True, "new_requests": journal.count,
                      "campaign_requests": 2 + journal.count, "whole_material_acceptance": False}))


if __name__ == "__main__":
    main()
