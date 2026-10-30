"""Check review references and denominators, never certify semantic judgments."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("context_review_inputs", ROOT / "scripts/evaluate-source-context.py")
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)
REPLAY = ROOT / "evaluations/2026-10-30/source-context-replay-v1.json"
REVIEW = ROOT / "evaluations/2026-10-30/source-context-review-v1.json"


def verify(review=None):
    inputs.load_inputs(verify_product=False)
    replay = json.loads(REPLAY.read_text(encoding="utf8"))
    review = deepcopy(review) if review is not None else json.loads(REVIEW.read_text(encoding="utf8"))
    if (review["replay_lf_sha256"] != inputs.digest(REPLAY) or review["independent_review"] is not False
        or review["semantic_acceptance"] is not False):
        raise ValueError("Changed observation or unsupported acceptance claim")
    gold = {case["id"]: case for case in inputs.complete.complete.load_manifest()["cases"]}
    old = {case["caseId"]: case for case in json.loads((ROOT / "evaluations/2026-10-29/complete-claims-source-review.json").read_text())["cases"]}
    expected = {row["case"]: row for row in replay["arms"]}
    if len(review["cases"]) != 2 or {row["case"] for row in review["cases"]} != set(expected):
        raise ValueError("Changed reviewed population")
    facts_total = targets_total = targets_retained = candidate_total = note_total = 0
    new_missing = []
    for row in review["cases"]:
        case, actual = row["case"], expected[row["case"]]
        fact_ids = {fact["id"] for fact in gold[case]["facts"]}
        candidates = {candidate["candidate_id"] for candidate in actual["candidates"]}
        notes = {note["note_id"] for note in actual["notes"]}
        targets = {fact["factId"] for fact in old[case]["facts"] if fact["decision"] == "OMITTED"
                   and fact.get("omissionReason") != "PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM"}
        exact_notes = {fact["fact_id"]: fact["exact_quote_note_ids"] for fact in actual["exact_context_matches"]}
        previous = {fact["factId"]: fact for fact in old[case]["facts"]}
        if len(row["facts"]) != len(fact_ids) or {f["fact_id"] for f in row["facts"]} != fact_ids:
            raise ValueError("A source fact was dropped, duplicated or invented")
        for fact in row["facts"]:
            before = previous[fact["fact_id"]]
            if (fact["prior_candidate_decision"] != before["decision"]
                or fact["prior_omission_reason"] != before.get("omissionReason")):
                raise ValueError("Historical review was relabeled")
            if not set(fact["candidate_ids"] + fact["partial_candidate_ids"]) <= candidates:
                raise ValueError("Unknown candidate reference")
            if fact["context_note_ids"] != exact_notes[fact["fact_id"]] or not set(fact["context_note_ids"]) <= notes:
                raise ValueError("Changed source-context reference")
        if {r["candidate_id"] for r in row["candidate_review"]} != candidates or len(row["candidate_review"]) != len(candidates):
            raise ValueError("Candidate review population changed")
        if {r["note_id"] for r in row["note_review"]} != notes or len(row["note_review"]) != len(notes):
            raise ValueError("Note review population changed")
        retained = {f["fact_id"] for f in row["facts"] if f["fact_id"] in targets and f["context_note_ids"]}
        missing = [f["fact_id"] for f in row["facts"] if f["prior_candidate_decision"] == "COVERED"
                   and not f["candidate_ids"] and not f["context_note_ids"]]
        if targets != set(row["target_omission_fact_ids"]) or retained != set(row["target_facts_retained_in_notes"]):
            raise ValueError("Target denominator or retained references changed")
        if missing != row["new_missing_or_partial_fact_ids"]:
            raise ValueError("New omissions were hidden")
        facts_total += len(fact_ids)
        targets_total += len(targets)
        targets_retained += len(retained)
        candidate_total += len(candidates)
        note_total += len(notes)
        new_missing.extend(missing)
    return {"status": "REFERENCES_AND_DENOMINATORS_VALID", "source_facts": facts_total,
            "candidates_reviewed": candidate_total, "notes_reviewed": note_total,
            "prior_target_facts": targets_total, "prior_target_facts_retained_in_notes": targets_retained,
            "new_missing_or_partial_fact_ids": new_missing, "independent_review": False,
            "semantic_judgments_verified_by_script": False, "whole_material_acceptance": False}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
