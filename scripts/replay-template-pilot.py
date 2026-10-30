"""Replay two pinned public synthetic responses through actual PDF/DPAPI preview.

No model, network fallback, approval, source confirmation or applicant path is
available. This measures validation of saved output, not a fresh model result.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("template_replay_inputs", ROOT / "scripts/evaluate-template-pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
PIN = "e8bf0935eb3c41a670d83ce08b9885070683b32ae1140085703c20f4bfcb64a9"
OUTPUT = ROOT / "reports/model-template-replay-v1.json"


def load_observation():
    if pilot.previous.digest(pilot.PUBLISHED) != PIN:
        raise ValueError("The fixed synthetic observation changed")
    observation = json.loads(pilot.PUBLISHED.read_text(encoding="utf8"))
    if (observation["protocol_closed"] is not True or observation["adapter_requests"] != 2
        or observation["campaign_actual_requests"] != 6
        or [row["case"] for row in observation["responses"]] != list(pilot.CASES)):
        raise ValueError("Changed closed protocol population")
    return observation


class ReplayEngine(pilot.TemplateEngine):
    def __init__(self, response, text):
        super().__init__({"model": "recorded-synthetic-response", "provider": "replay"}, SimpleNamespace(count=0))
        self.response, self.text = response, text

    def _invoke(self, request):
        numbered = request.get("line_numbered_document") or []
        if (self.journal.count or self.case != self.response["case"]
            or request.get("task") != "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2"
            or [line.split("\t", 1)[1] for line in numbered] != self.text.splitlines()
            or not numbered or not numbered[0].startswith("1\t")):
            raise pilot.JobOpsError("TEMPLATE_REPLAY_MISMATCH", "Fixed source/task changed or replay consumed; no fallback.")
        self.journal.count += 1
        return {"ok": True, "toolSummary": {"calls": 0, "tools": []}, "result": {"content": self.response["content"]}}


def main():
    if OUTPUT.exists():
        raise SystemExit("Keep prior replay immutable; this fixed replay already exists.")
    observation = load_observation()
    _, cases = pilot.load_plan()
    original = {arm["case"]: arm for arm in observation["arms"]}
    report = {"observed_at": pilot.iso_utc(), "synthetic_only": True, "mode": "RECORDED_RESPONSE_PDF_DPAPI_REPLAY",
              "original_observation_lf_sha256": PIN, "live_provider_calls": 0, "original_billed_cost_usd": None,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "product_files": {path: pilot.previous.digest(ROOT / path) for path in
                                ("src/jobops/ai_runtime.py", "src/jobops/claim_attribution.py")},
              "independent_human_acceptance": False, "whole_material_acceptance": False, "arms": []}
    for case, response in zip(cases, observation["responses"], strict=True):
        engine = ReplayEngine(response, original[case["id"]]["analyzed_text"])
        row = pilot.run_case(case, engine)
        row["recorded_response_reads"] = row.pop("adapter_requests")
        row["live_provider_calls"] = 0
        report["arms"].append(row)
        print(json.dumps({"case": case["id"], "status": row["status"],
                          "candidates": len(row.get("candidates", [])), "live_provider_calls": 0}), flush=True)
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
