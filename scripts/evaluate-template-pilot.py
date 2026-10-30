"""Two model-unmeasured template families, within the existing six-call campaign.

The parser has already seen these synthetic families. This is not an independent
blind holdout, a before/after quality comparison or whole-material acceptance.
Each case gets one adapter attempt; failure stops the run, and journals cannot
be resumed or overwritten. No arbitrary applicant file or output path is read.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("template_context", ROOT / "scripts/evaluate-source-context.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
complete = previous.complete
layout = complete.layout
from jobops.errors import JobOpsError
from jobops.util import has_reparse_component, iso_utc

PROTOCOL = "MODEL_TEMPLATE_PILOT_V1"
CASES = ("en-timeline_cards", "zh-asymmetric_sidebar")
PLAN = ROOT / "evaluations/protocols/model-template-pilot-v1.json"
OUTPUT = ROOT / "reports/model-template-pilot-v1.json"
PUBLISHED = ROOT / "evaluations/2026-10-30/model-template-pilot-v1.json"


def load_plan():
    plan = json.loads(PLAN.read_text(encoding="utf8"))
    if (plan["protocol"] != PROTOCOL or plan["cases"] != list(CASES)
        or plan["max_new_requests"] != 2 or plan["campaign_ceiling"] != 6
        or plan["prior_requests"] != 4 or plan["requests_per_case"] != 1):
        raise ValueError("Changed fixed population or campaign budget")
    for relative, expected in plan["inputs"].items():
        if previous.digest(ROOT / relative) != expected:
            raise ValueError("Changed frozen input or product code")
    actual = 0
    for paths in plan["closed_predecessors"]:
        observation = json.loads((ROOT / paths["observation"]).read_text(encoding="utf8"))
        rows = [json.loads(line) for line in (ROOT / paths["journal"]).read_text(encoding="utf8").splitlines()]
        reserved = sum(row.get("event") == "RESERVED" for row in rows)
        if observation["protocol_closed"] is not True or observation["adapter_requests"] != reserved or reserved != 2:
            raise ValueError("Preceding closed request ledger changed")
        actual += reserved
    if actual != plan["prior_requests"] or actual + plan["max_new_requests"] > plan["campaign_ceiling"]:
        raise ValueError("Existing campaign limit exceeded")
    cases = {row["id"]: row for row in complete.complete.load_manifest()["cases"]}
    selected = [cases[key] for key in CASES]
    if any(row["sha256"] != plan["sources"][row["id"]] or row["split"] != "held_out" for row in selected):
        raise ValueError("Changed frozen synthetic source")
    return plan, selected


def validate_dispatch(plan):
    """Current product pins apply only before fresh calls, not historical reads."""
    for relative, expected in plan["product"].items():
        if previous.digest(ROOT / relative) != expected:
            raise ValueError("Changed frozen input or product code")


def journal_path():
    common = Path(subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True,
    ).strip()).resolve()
    if not common.is_relative_to(ROOT.parent.resolve()) or has_reparse_component(common):
        raise ValueError("Budget directory is outside the workspace or traverses a reparse point")
    directory = common / "jobflow-evaluation-budgets"
    directory.mkdir(exist_ok=True)
    if has_reparse_component(directory):
        raise ValueError("Budget directory cannot be a reparse point")
    return directory / f"{PROTOCOL}.requests.jsonl"


class TwoCaseJournal(previous.AttemptJournal):
    def reserve(self, case):
        if case not in CASES or self.count >= 2 or case in self.cases:
            raise JobOpsError("TEMPLATE_PILOT_LIMIT", "One attempt per fixed case; no retry or expansion.")
        super().reserve(case)


class TemplateEngine(previous.ContextEngine):
    def analyze_document(self, text, *, source_id, source_type):
        self.analyzed_text = text
        return super().analyze_document(text, source_id=source_id, source_type=source_type)


def run_case(case, engine):
    started, before = time.monotonic(), engine.journal.count
    engine.case, engine.analyzed_text = case["id"], ""
    data = (complete.complete.CORPUS / case["file"]).read_bytes()
    receipt = {"case": case["id"], "source_sha256": case["sha256"], "mode": "pdf_regions"}
    with tempfile.TemporaryDirectory(prefix="jobflow-template-pilot-") as raw:
        temporary = Path(raw)
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = layout.JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = layout.WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1",
                                        local_app_data=temporary / "local")
        service = layout.OnboardingCenterService(project, database, layout.PrivateOnboarding(database, store), ai_engine=engine)
        try:
            with layout.mode_environment("pdf_regions"):
                try:
                    preview = service.preview_source("resume", ".pdf", data)
                    _, state = service.ensure_state()
                    pending = next(row for row in state["pending_sources"] if row["source_id"] == preview["source_id"])
                    candidates = pending["candidates"]
                    receipt.update(status=preview["status"], candidates=candidates, analyzed_text=engine.analyzed_text,
                                   assessment=complete.assess(case, candidates, engine.analyzed_text),
                                   extraction_summary=preview["extraction_summary"],
                                   context_notes=pending.get("context_notes", []),
                                   profile_hints=pending.get("profile_hints", {}),
                                   profile_hint_review=pending["metadata"].get("profile_hint_review", {}),
                                   retained_original_matches=store.get_bytes(pending["metadata"]["secure_ref"]) == data)
                    receipt["material_claims"] = len(state.get("material_claims", []))
                except JobOpsError as error:
                    receipt.update(status="BLOCKED", code=error.code)
                except Exception as error:
                    receipt.update(status="FAILED", code="EVALUATOR_ERROR", error_type=type(error).__name__)
            with database.connect() as connection:
                receipt["database_counts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                                              for name in ("applications", "claims", "approvals", "external_action_attempts")}
        finally:
            receipt["analyzed_text"] = engine.analyzed_text
            service.close()
    receipt.update(temporary_state_removed=not Path(raw).exists(), elapsed_ms=round((time.monotonic() - started) * 1000),
                   adapter_requests=engine.journal.count - before)
    return receipt


def main():
    if sys.argv[1:] != ["--live-hermes"]:
        raise SystemExit("Use --live-hermes only for the fixed two-case synthetic pilot.")
    if OUTPUT.exists() or PUBLISHED.exists():
        raise SystemExit("This protocol already produced an observation; do not overwrite or repeat it.")
    plan, cases = load_plan()
    validate_dispatch(plan)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Freeze and commit the protocol and code before dispatch.")
    budget = journal_path()
    if budget.exists():
        raise SystemExit("This protocol already started; no repeat or automatic resume is allowed.")
    route = layout.previous.public_hermes_route()
    if route != plan["route"]:
        raise SystemExit("The existing Hermes route changed before dispatch.")
    report = {"protocol": PROTOCOL, "observed_at": iso_utc(), "synthetic_only": True,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "plan_lf_sha256": previous.digest(PLAN), "runner_lf_sha256": previous.digest(Path(__file__)),
              "input_files": plan["inputs"], "product_files": plan["product"], "sources": plan["sources"],
              "route_before": route, "max_new_requests": 2, "prior_requests": 4, "campaign_ceiling": 6,
              "parser_previously_exposed": True, "model_output_previously_measured": False,
              "independent_blind_holdout": False, "whole_material_acceptance": False,
              "billed_cost_usd": None, "token_usage": None, "provider_internal_requests": None,
              "real_external_actions": 0, "automatic_approvals": 0, "arms": []}
    journal = TwoCaseJournal(budget)
    engine = TemplateEngine(route, journal)
    stopped = False
    try:
        for case in cases:
            if stopped:
                report["arms"].append({"case": case["id"], "status": "NOT_RUN_FAILURE_STOP"})
                continue
            row = run_case(case, engine)
            stopped = row["status"] != "SOURCE_PREVIEW_READY"
            report["arms"].append(row)
            journal.write({"event": "ARM", **row})
            print(json.dumps({"case": case["id"], "status": row["status"], "requests": journal.count,
                              "candidates": len(row.get("candidates", []))}), flush=True)
        report.update(adapter_requests=journal.count, campaign_actual_requests=4 + journal.count,
                      responses=engine.responses, protocol_closed=True)
        try:
            report["route_after"] = layout.previous.public_hermes_route()
        except Exception:
            report["route_after"] = {"status": "UNAVAILABLE"}
        report["route_unchanged"] = report["route_after"] == route
        OUTPUT.parent.mkdir(exist_ok=True)
        with OUTPUT.open("x", encoding="utf8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
    finally:
        journal.close()
    print(json.dumps({"protocol_closed": True, "requests": journal.count, "whole_material_acceptance": False}))


if __name__ == "__main__":
    main()
