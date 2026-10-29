"""Evaluate one frozen synthetic PDF through the actual optional preview path.

No input-file option is offered: this runner must never accept a real resume.
The default is baseline-only. --live-hermes uses the existing configured route,
with its production zero-tool transport and at most three adapter requests.
Provider-internal retries and billed usage are not exposed by that adapter.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_connections import WSLHermesCLIEngine, _WSL_HERMES_PYTHON_SELECTION
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import sha256_bytes

FIXTURE = ROOT / "evaluations/2026-10-27/visual-hermes-repaired-2026-10-27.synthetic.pdf"
FIXTURE_SHA256 = "bbdc34d94ec92aaf277e11bbc4f8acd4a74cc2998c4f5d9c48083bd21d738e25"
FACTS = [
    {"id": "stores", "value": "12", "context": "store", "organization": "North Star Lab", "years": ["2023", "2025"], "page": 1},
    {"id": "reporting_time", "value": "18", "context": "reporting", "organization": "North Star Lab", "years": ["2023", "2025"], "page": 1},
    {"id": "records", "value": "1200", "context": "record", "organization": "Cedar Studio", "years": ["2024"], "page": 2},
    {"id": "budget", "value": "12500", "context": "budget", "organization": "Bay Project", "years": ["2025"], "page": 2, "owner": "team"},
    {"id": "scenarios", "value": "24", "context": "scenario", "organization": "Harbor Project", "years": ["2026"], "page": 3, "owner": "team"},
    {"id": "reports", "value": "6", "context": "report", "organization": "Harbor Project", "years": ["2026"], "page": 3, "owner": "applicant"},
]


def _normalized(value: str) -> str:
    return " ".join(value.casefold().replace(",", "").split())


def _mentions(value: str, fact: dict) -> bool:
    value = _normalized(value)
    return bool(re.search(r"(?<!\d)" + fact["value"] + r"(?!\d)", value) and fact["context"] in value)


def assess_candidates(candidates: list[dict], transcript: str) -> dict:
    """Conservative fixed-fact matches; not a general semantic accuracy judge.

    A paraphrase not recognized by these fixed checks stays unmatched for manual
    adjudication. No zero-candidate or missing-evidence result can pass vacuously.
    """
    lines = transcript.splitlines()
    results = []
    for fact in FACTS:
        mentions = [item for item in candidates if _mentions(str(item.get("statement", "")), fact)]
        matched = []
        issues = set()
        for item in mentions:
            entity, provenance = item.get("entity") or {}, item.get("provenance") or {}
            statement = _normalized(str(item.get("statement", "")))
            problems = []
            if _normalized(str(entity.get("organization", ""))) != _normalized(fact["organization"]):
                problems.append("ENTITY_MISMATCH")
            if fact.get("owner") == "team" and "team" not in statement:
                problems.append("TEAM_OWNERSHIP_NOT_EXPLICIT")
            if fact.get("owner") == "applicant" and ("applicant" not in statement or "team prepared" in statement):
                problems.append("APPLICANT_OWNERSHIP_NOT_EXPLICIT")
            dates = str(entity.get("start_date", "")) + " " + str(entity.get("end_date", ""))
            observed_years = set(re.findall(r"\b20\d{2}\b", statement + " " + dates))
            if observed_years != set(fact["years"]):
                problems.append("DATE_MISSING_OR_CHANGED")
            first, last = provenance.get("line_start"), provenance.get("line_end")
            excerpt = ""
            if type(first) is int and type(last) is int and 1 <= first <= last <= len(lines):
                excerpt = "\n".join(lines[first - 1:last])
            if not _mentions(excerpt, fact):
                problems.append("LINE_EVIDENCE_MISSING")
            regions = provenance.get("visual_regions") or []
            if not any(region.get("page") == fact["page"] for region in regions if isinstance(region, dict)):
                problems.append("PAGE_EVIDENCE_MISSING")
            if provenance.get("visual_source_sha256") != "sha256:" + FIXTURE_SHA256:
                problems.append("SOURCE_HASH_MISMATCH")
            if item.get("selected") is not False or provenance.get("visual_verified") is not False:
                problems.append("USER_REVIEW_BOUNDARY_MISSING")
            issues.update(problems)
            if not problems:
                matched.append(item["candidate_id"])
        results.append({"fact_id": fact["id"], "matched_candidate_ids": matched, "observed_issues": sorted(issues),
                        "status": "MATCHED_WITH_EVIDENCE" if matched else "NEEDS_MANUAL_REVIEW_OR_MISSING"})
    return {"expected_facts": len(FACTS), "matched_facts": sum(bool(row["matched_candidate_ids"]) for row in results),
            "facts": results, "numeric_errors": None, "misattributions": None,
            "manual_adjudication_required": True,
            "scope": "Fixed numeric facts, entity/date ownership and line/page/hash evidence; unmatched paraphrases are not automatically errors."}


@contextmanager
def visual_mode(enabled: bool):
    updates = {"JOBFLOW_VISUAL_ENABLE": str(enabled).lower(), "JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER": str(enabled).lower(), "JOBFLOW_DOCUMENT_PARSER": ""}
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def public_hermes_route() -> dict:
    # Print only public selection/revision fields, never runtime credentials.
    code = """
import contextlib, io, json, re, subprocess
from pathlib import Path
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import hermes_cli
    from hermes_cli.config import load_config
    config = load_config()
    model = config.get('model', {})
    root = Path(hermes_cli.__file__).resolve().parents[1]
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
if not isinstance(model, dict): raise SystemExit(2)
output = {'model':model.get('default') or model.get('model'), 'provider':model.get('provider'), 'hermes_commit':revision if re.fullmatch('[a-f0-9]{40}', revision) else None}
print(json.dumps(output))
"""
    command = ["wsl.exe", "-d", "Ubuntu", "--exec", "sh", "-lc",
               "set -eu\n" + _WSL_HERMES_PYTHON_SELECTION + '\n"$jobops_python" -I -c ' + shlex.quote(code)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise JobOpsError("EVALUATION_HERMES_UNAVAILABLE", "The public Hermes selection could not be verified.")
    value = json.loads(result.stdout)
    if not value.get("model") or not value.get("provider"):
        raise JobOpsError("EVALUATION_HERMES_UNCONFIGURED", "An existing configured Hermes provider is required.")
    return value


class ObservedHermes(WSLHermesCLIEngine):
    def __init__(self, route: dict):
        super().__init__("wsl.exe", "Ubuntu", model=route["model"], provider_id=route["provider"])
        self.calls: list[dict] = []
        self.transcript = ""

    def _invoke(self, request: dict):
        if len(self.calls) >= 3:
            raise JobOpsError("EVALUATION_CALL_LIMIT", "The three-request synthetic evaluation budget is exhausted.")
        record = {"task": request.get("task"), "status": "FAILED", "elapsed_ms": 0, "tool_calls": None}
        self.calls.append(record)
        started = time.monotonic()
        try:
            result = super()._invoke(request)
            record.update(status="RETURNED", tool_calls=result["toolSummary"]["calls"])
            if request.get("task") == "JOBOPS_VISUAL_TRANSCRIPTION_V1":
                try:
                    value = json.loads(result["result"]["content"])
                    self.transcript = "\n".join(block["text"] for page in value["pages"] for block in page["blocks"])
                except (ValueError, TypeError, KeyError):
                    # Observation must not replace the production validator's
                    # error or discard a failed model attempt.
                    record["transcription_observation"] = "INVALID"
            return result
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)


def run_evaluation(*, live: bool) -> dict:
    data = FIXTURE.read_bytes()
    if hashlib.sha256(data).hexdigest() != FIXTURE_SHA256:
        raise JobOpsError("EVALUATION_FIXTURE_CHANGED", "The frozen synthetic input hash does not match the protocol.")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    receipt = {"schema_version": 1, "verified_at": datetime.now(timezone.utc).isoformat(),
               "evaluation_commit": commit,
               "evaluation_script_lf_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
               "fixture_sha256": FIXTURE_SHA256, "synthetic_only": True,
               "python": sys.version.split()[0], "baseline": {}, "candidate": {"status": "NOT_RUN"},
               "packages": {name:version(name) for name in ("pdfplumber", "Pillow", "pypdf")},
               "cost_usd": None, "usage_tokens": None, "usage_note": "The production document adapter does not expose usage or billing receipts; unknown is not zero.",
               "model_api_calls": None,
               "external_application_actions": None, "claim_approval": False}
    route = public_hermes_route() if live else None
    engine = ObservedHermes(route) if route else None
    with tempfile.TemporaryDirectory(prefix="jobflow-material-preview-eval-") as raw:
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
            started = time.monotonic()
            with visual_mode(False):
                try:
                    service.preview_source("resume", ".pdf", data)
                    receipt["baseline"] = {"status": "PREVIEW_RETURNED"}
                except JobOpsError as error:
                    receipt["baseline"] = {"status": "BLOCKED", "code": error.code}
            receipt["baseline"]["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            receipt["baseline"]["adapter_requests"] = len(engine.calls) if engine else 0
            if live:
                started = time.monotonic()
                with visual_mode(True):
                    try:
                        preview = service.preview_source("resume", ".pdf", data)
                        _, state = service.ensure_state()
                        pending = next(row for row in state["pending_sources"] if row["source_id"] == preview["source_id"])
                        candidates = pending["candidates"]
                        retained = store.get_bytes(pending["metadata"]["secure_ref"])
                        refusal = "NOT_ATTEMPTED_NO_CANDIDATES"
                        if candidates:
                            try:
                                service.commit_source(preview["source_id"], [{"candidate_id":candidates[0]["candidate_id"], "selected":True}])
                                refusal = "UNEXPECTEDLY_ACCEPTED"
                            except JobOpsError as error:
                                refusal = error.code
                        _, after = service.ensure_state()
                        receipt["candidate"] = {"status":preview["status"], "candidate_count":len(candidates), "candidates":candidates,
                            "transcript":engine.transcript, "extraction_summary":preview["extraction_summary"],
                            "assessment":assess_candidates(candidates, engine.transcript),
                            "retained_original_matches":sha256_bytes(retained) == sha256_bytes(data),
                            "unconfirmed_selection_result":refusal, "committed_material_claims":len(after["material_claims"])}
                    except JobOpsError as error:
                        receipt["candidate"] = {"status":"FAILED", "code":error.code, "details":error.details}
                    except Exception as error:
                        receipt["candidate"] = {"status":"FAILED", "code":"EVALUATION_UNEXPECTED_ERROR", "error_type":type(error).__name__}
                receipt["candidate"]["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                receipt["model_route_before"] = route
                try:
                    receipt["model_route_after"] = public_hermes_route()
                except (JobOpsError, OSError, ValueError, subprocess.SubprocessError):
                    receipt["model_route_after"] = {"status":"UNAVAILABLE"}
                receipt["model_route_unchanged"] = receipt["model_route_after"] == route
                receipt["adapter_requests"] = engine.calls
            with database.connect() as connection:
                receipt["database_counts"] = {
                    table:connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in ("applications", "claims", "approvals", "external_action_attempts")
                }
            receipt["external_application_actions"] = receipt["database_counts"]["external_action_attempts"]
        finally:
            service.close()
    receipt["temporary_state_removed"] = not Path(raw).exists()
    candidate = receipt["candidate"]
    receipt["fixed_fixture_checks"] = "NOT_RUN" if not live else ("PASS" if (
        candidate.get("status") == "SOURCE_PREVIEW_READY"
        and candidate.get("candidate_count", 0) > 0
        and candidate.get("assessment", {}).get("matched_facts") == len(FACTS)
        and candidate.get("retained_original_matches") is True
        and candidate.get("unconfirmed_selection_result") == "VISUAL_SOURCE_VERIFICATION_REQUIRED"
        and candidate.get("committed_material_claims") == 0
        and receipt.get("model_route_unchanged") is True
        and all(call.get("tool_calls") == 0 for call in engine.calls)
        and all(count == 0 for count in receipt["database_counts"].values())
        and receipt["temporary_state_removed"]
    ) else "FAIL")
    receipt["business_completion"] = "NOT_ATTEMPTED_USER_APPROVAL_REQUIRED"
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-hermes", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output; prior observations must be retained.")
    result = run_evaluation(live=args.live_hermes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"baseline":result["baseline"], "candidate_status":result["candidate"]["status"],
                      "fixed_fixture_checks":result["fixed_fixture_checks"],
                      "matched_facts":result["candidate"].get("assessment", {}).get("matched_facts"),
                      "adapter_requests":len(result.get("adapter_requests", []))}))
    return 0 if not args.live_hermes or result["fixed_fixture_checks"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
