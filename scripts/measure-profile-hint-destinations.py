"""Measure only pinned public synthetic resume hints through encrypted source import.

No applicant path argument, provider access, Claim selection or Profile approval.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
OUT = args.output
if OUT.exists():
    parser.error("Use a fresh output; existing observations must not be overwritten")
OUT.parent.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("frozen_profile_replay", ROOT / "scripts/replay-complete-claims.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
layout = replay.pilot.layout
cases = {row["id"]: row for row in replay.pilot.complete.load_manifest()["cases"]}
initial = replay.load_observation()
visual = replay.load_observation("visual-followup")
observations = [row for row in initial["arms"] if row["mode"] != "visual"] + visual["arms"]
assert len(observations) == 6
report = {
    "observedAt": datetime.now(timezone.utc).isoformat(), "scope": "UNAPPROVED_PROFILE_HINT_PERSISTENCE_ONLY",
    "baseCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "syntheticOnly": True, "liveProviderCalls": 0, "newModelQualityMeasurement": False,
    "originalLiveModelCostUSD": None, "profileCompletionMeasured": False,
    "sourceLFHashes": {p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in ["src/jobops/onboarding_center.py", "src/jobops/candidate_profile.py", "src/jobops/resume_onboarding.py"]},
    "cases": [],
}
for observation in observations:
    case = cases[observation["case"]]
    expected_contact = next(f["text"] for f in case["facts"] if f["kind"] == "contact").split("：")[-1].split(": ")[-1].strip()
    engine = replay.ReplayEngine(observation["responses"])
    source = (replay.pilot.complete.CORPUS / case["file"]).read_bytes()
    row = {"caseId": case["id"], "mode": observation["mode"], "sourceSHA256": case["sha256"]}
    with tempfile.TemporaryDirectory(prefix="jobflow-profile-replay-") as raw:
        temporary = Path(raw).resolve()
        assert temporary.parent == Path(tempfile.gettempdir()).resolve()
        assert temporary.name.startswith("jobflow-profile-replay-")
        project = temporary / "project"
        project.mkdir()
        shutil.copy2(ROOT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(ROOT / "schemas", project / "schemas")
        database = layout.JobOpsDB(project / "state/jobops.db")
        database.initialize()
        store = layout.WindowsDPAPIStore(ROOT / ".agents/skills/job-application-operator/scripts/secure-store.ps1", local_app_data=temporary / "local")
        private = layout.PrivateOnboarding(database, store)
        service = layout.OnboardingCenterService(project, database, private, ai_engine=engine)
        try:
            with layout.mode_environment(observation["mode"]), patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "0"}):
                preview = service.preview_source("resume", Path(case["file"]).suffix, source)
                _, pending_state = service.ensure_state()
                pending = next(p for p in pending_state["pending_sources"] if p["source_id"] == preview["source_id"])
                row["pendingHintFields"] = sorted(pending["profile_hints"])
                row["sourceBytesPreserved"] = store.get_bytes(pending["metadata"]["secure_ref"]) == source
                imported = service.commit_source(preview["source_id"], [])
                row["importStatus"] = imported["status"]
                row["hintsApplied"] = imported["profile_hints_applied"]
                _, saved = service.ensure_state()
                answers = saved["answers"]
                row["emailHintMatchesSource"] = answers["email"]["value"] == expected_contact
                row["emailHintSource"] = answers["email"]["source"]
                row["profileReview"] = saved["profile_review"]
                row["namePartHintsPresent"] = all(answers[f].get("value") not in (None, "", "UNKNOWN") for f in ("first_name", "last_name"))
                row["remotePreferenceImported"] = answers["remote_preference"].get("value") == "remote"
                row["authorizationAnswerStatus"] = answers["work_authorization"]["status"]
                row["claimsSelected"] = sum(c.get("selected", False) for c in saved.get("material_claims", []))
                row["claimsConfirmed"] = sum(c.get("decision") == "CONFIRMED" for c in saved.get("material_claims", []))
            service.close()
            service = layout.OnboardingCenterService(project, database, private, ai_engine=engine)
            _, reopened = service.ensure_state()
            row["hintsSurviveServiceReopen"] = reopened["answers"] == answers
            row["onboardingStatus"] = reopened["status"]
            with database.connect() as connection:
                row["databaseCounts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in ("applications", "claims", "approvals", "external_action_attempts")}
        finally:
            service.close()
    row["temporaryStateRemoved"] = not Path(raw).exists()
    row["recordedResponsesConsumed"] = engine.cursor
    report["cases"].append(row)
    print(json.dumps(row), flush=True)
report["limitations"] = [
    "No completed or approved Profile is produced; this measures unapproved source-import hints and encrypted persistence only",
    "Numeric synthetic display names are structurally unsuitable for the current conservative first/last-name splitter; absence is not permission to infer legal names",
    "Original Claim candidate scores are not regraded by this measurement",
    "Non-Claim constraints and verification notes still require their own reviewed destinations",
]
OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"output": OUT.name, "cases": len(report["cases"]), "emailHintsMatched": sum(c["emailHintMatchesSource"] for c in report["cases"])}))
