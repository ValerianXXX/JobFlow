"""Run the frozen synthetic source-status set locally with zero model calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_runtime import _statement_grounding_report


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh output; never replace earlier observations")
    fixture_path = ROOT / "tests/fixtures/assertion_status/cases.json"
    if digest(fixture_path) != "93d3076899fe29a0aea20908fe009793c0a638c4d5318480ee82597672861450":
        raise ValueError("The frozen synthetic fixture changed")
    fixture = json.loads(fixture_path.read_text(encoding="utf8"))
    if fixture["synthetic_only"] is not True or len(fixture["cases"]) != 60:
        raise ValueError("Unexpected synthetic fixture population")
    started = time.perf_counter()
    rows = []
    for case in fixture["cases"]:
        report = _statement_grounding_report(case["statement"], case["source"])
        rows.append({"id": case["id"], "language": case["language"], "group": case["group"],
                     "expected_valid": case["expected_valid"], "actual_valid": report["valid"],
                     "matched_expectation": report["valid"] == case["expected_valid"], "diagnostics": report})
    summary = {}
    for language in ("all", "en", "zh", "mixed"):
        subset = [row for row in rows if language == "all" or row["language"] == language]
        summary[language] = {
            "contradictions": sum(not row["expected_valid"] for row in subset),
            "incorrectly_accepted_contradictions": sum(not row["expected_valid"] and row["actual_valid"] for row in subset),
            "supported_controls": sum(row["expected_valid"] for row in subset),
            "incorrectly_filtered_controls": sum(row["expected_valid"] and not row["actual_valid"] for row in subset),
        }
    files = ["src/jobops/ai_runtime.py", "src/jobops/claim_assertion_status.py", "scripts/evaluate-assertion-status.py"]
    value = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "protocol": fixture["protocol"],
        "synthetic_only": True, "measurement": "HOST_VALIDATOR_FROZEN_COUNTEREXAMPLES",
        "whole_material_acceptance": False, "independent_human_acceptance": False,
        "new_model_quality_measurement": False, "live_provider_calls": 0, "model_cost_usd": 0,
        "fixture_lf_sha256": digest(fixture_path), "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_lf_sha256": {file: digest(ROOT / file) for file in files if (ROOT / file).is_file()},
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "summary": summary, "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
