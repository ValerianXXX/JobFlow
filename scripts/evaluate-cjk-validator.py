"""Fixed synthetic CJK follow-up: <=4 Hermes requests, then paired local replay.

The first 12-request observation is immutable. This separate experiment follows
a reproduced uncased-script defect. It retains only frozen synthetic source
lines and structured model output, never credentials, arbitrary files or email.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_connections import _protocol_payload
from jobops.ai_runtime import LocalSubprocessAIEngine
from jobops.errors import JobOpsError
from jobops.util import iso_utc

spec = importlib.util.spec_from_file_location("layout_claim_followup", ROOT / "scripts/evaluate-layout-claims.py")
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)
BASE_COMMIT = "756b0eed99cf281a1f624788ce0cb494af0d30c9"
CALL_LIMIT = 4
CASES = (("zh-columns", "pdf_regions"), ("zh-scan", "visual"))


class SyntheticObservedHermes(layout.ObservedHermes):
    def __init__(self, route):
        super().__init__(route)
        self.responses = []

    def _invoke(self, request):
        if len(self.calls) >= CALL_LIMIT:
            raise JobOpsError("EVALUATION_CALL_LIMIT", "The CJK follow-up adapter request budget is exhausted.")
        result = super()._invoke(request)
        numbered = request.get("line_numbered_document")
        if numbered:
            self.responses.append({
                "task": request.get("task"),
                "adapter_request_index": len(self.calls),
                "line_number_start": int(numbered[0].split("\t", 1)[0]),
                "source_lines": [line.split("\t", 1)[1] for line in numbered],
                "payload": _protocol_payload(result),
            })
        return result


def original_validator():
    # Read a pinned, trusted repository module. No downloaded code is executed.
    source = subprocess.check_output(["git", "show", BASE_COMMIT + ":src/jobops/ai_runtime.py"], cwd=ROOT)
    module = types.ModuleType("jobops._evaluation_before_cjk")
    module.__package__ = "jobops"
    exec(compile(source, "pinned-ai-runtime.py", "exec"), module.__dict__)
    return module.LocalSubprocessAIEngine, hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()


def replay(engine, response):
    diagnostics = {}
    try:
        candidates = engine._validated_candidates(
            deepcopy(response["payload"]), source_id="SRC-SYNTHETIC-CJK-REPLAY",
            source_lines=response["source_lines"], line_number_start=response["line_number_start"],
            quality_diagnostics=diagnostics,
        )
        return {"status": "VALIDATED", "candidates": candidates, "diagnostics": diagnostics}
    except JobOpsError as error:
        return {"status": "REJECTED", "code": error.code, "diagnostics": diagnostics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-hermes", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoint = args.output.with_suffix(".jsonl")
    if args.output.exists() or checkpoint.exists():
        parser.error("Choose a new output; prior observations are immutable")
    cases = {case["id"]: case for case in layout.corpus.load_cases()}
    before, before_hash = original_validator()
    route = layout.previous.public_hermes_route()
    engine = SyntheticObservedHermes(route)
    report = {
        "observed_at": iso_utc(), "synthetic_only": True, "adapter_request_limit": CALL_LIMIT,
        "before_commit": BASE_COMMIT, "working_tree_observation": True,
        "manifest_lf_sha256": layout.corpus.MANIFEST_LF_SHA256,
        "before_validator_lf_sha256": before_hash,
        "source_lf_sha256": {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                              for name in ("src/jobops/ai_runtime.py", "src/jobops/local_document_parser.py",
                                           "src/jobops/_pdf_region_worker.py", "src/jobops/onboarding_center.py",
                                           "scripts/evaluate-layout-claims.py", "scripts/evaluate-cjk-validator.py")},
        "model_route_before": route, "arms": [], "replays": [],
        "cost_usd": None, "usage_tokens": None, "provider_api_calls": None,
        "business_completion": "NOT_ATTEMPTED",
        "scope": "Two fixed short synthetic probes; one new model sample each, same-output validator comparison; not a full-material acceptance study.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open("x", encoding="utf-8") as stream:
        for case_id, mode in CASES:
            first_response = len(engine.responses)
            row = layout.run_arm(cases[case_id], mode, engine)
            report["arms"].append(row)
            for response in engine.responses[first_response:]:
                paired = {"case": case_id, "response": response,
                          "before": replay(before, response), "after": replay(LocalSubprocessAIEngine, response)}
                report["replays"].append(paired)
            stream.write(json.dumps({"arm": row, "replays": report["replays"][first_response:]}, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({"case": case_id, "status": row["status"], "requests": row["adapter_requests"],
                              "candidates": len(row.get("candidates", []))}), flush=True)
    try:
        report["model_route_after"] = layout.previous.public_hermes_route()
    except Exception:
        report["model_route_after"] = {"status": "UNAVAILABLE"}
    report["model_route_unchanged"] = report["model_route_after"] == route
    report["adapter_requests"] = engine.calls
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"requests": len(engine.calls), "responses_replayed": len(report["replays"]), "cost_usd": None}))


if __name__ == "__main__":
    main()
