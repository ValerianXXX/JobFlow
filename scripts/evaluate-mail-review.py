"""Offline fixed mail comparison. Does not open a mailbox or call a model."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]
from jobops.mail_tracking import LocalEmlAdapter, StatusMessage, propose_status
from mail_review_corpus import APPLICATIONS, mail_contract_cases
from workbench_fixtures import mail_cases


def evaluate():
    rows = []
    for case in mail_contract_cases():
        start = time.perf_counter()
        actual = propose_status(StatusMessage(**case["message"]), APPLICATIONS)
        elapsed = (time.perf_counter() - start) * 1000
        expected_candidates = set(case["expected_candidates"])
        actual_candidates = set(actual["candidate_application_ids"])
        rows.append({
            "id": case["id"], "family": case["family"], "language": case["language"],
            "expected_status": case["expected_status"], "actual_status": actual["status_proposal"],
            "status_error": actual["status_proposal"] != case["expected_status"],
            "incorrect_confident_association": actual["application_id"] is not None and actual["application_id"] != case["expected_confident"],
            "missed_confident_proposal": case["expected_confident"] is not None and actual["application_id"] != case["expected_confident"],
            "missing_candidate_count": len(expected_candidates - actual_candidates),
            "extra_candidate_count": len(actual_candidates - expected_candidates),
            "unique_matchable": case["uniquely_matchable"],
            "unique_candidate_found": case["uniquely_matchable"] and expected_candidates <= actual_candidates,
            "uncertain": actual["review_status"] == "UNCERTAIN",
            "human_review_required": True,
            "application_state_changed": actual["application_state_changed"],
            "real_external_actions": actual["real_external_actions"],
            "elapsed_ms": round(elapsed, 4),
        })
    legacy = []
    for case in mail_cases():
        result = propose_status(LocalEmlAdapter(case["raw"]).read_messages()[0], APPLICATIONS)
        legacy.append({"id": case["id"], "status_error": result["status_proposal"] != case["expected_status"],
                       "incorrect_confident_association": result["application_id"] is not None})
    def summary(items):
        counts = {key: sum(row[key] for row in items) for key in (
            "status_error", "incorrect_confident_association", "missed_confident_proposal",
            "missing_candidate_count", "extra_candidate_count", "unique_matchable", "unique_candidate_found",
            "uncertain", "human_review_required", "application_state_changed", "real_external_actions")}
        return {"cases": len(items), **counts,
            "unique_candidate_recall": counts["unique_candidate_found"] / counts["unique_matchable"] if counts["unique_matchable"] else None,
            "median_ms": round(statistics.median(row["elapsed_ms"] for row in items), 4)}
    raw = json.dumps(mail_contract_cases(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1, "data_kind": "ORIGINAL_SYNTHETIC_DEVELOPMENT_SET_NOT_BLIND_HOLDOUT",
        "corpus_sha256": hashlib.sha256(raw).hexdigest(), "python": platform.python_version(),
        "platform": platform.system(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip(),
        "summary": summary(rows),
        "families": {family: summary([row for row in rows if row["family"] == family]) for family in sorted({row["family"] for row in rows})},
        "languages": dict(Counter(row["language"] for row in rows)), "rows": rows,
        "legacy": {"cases": len(legacy), "distinct_templates": 10,
                   "status_errors": sum(row["status_error"] for row in legacy),
                   "incorrect_confident_associations": sum(row["incorrect_confident_association"] for row in legacy)},
        "model_requests": 0, "model_cost_usd": 0, "mailbox_access": False,
        "human_review_time": None, "real_business_completion": None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an observation.")
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"summary": report["summary"], "legacy": report["legacy"], "corpus_sha256": report["corpus_sha256"]}))
