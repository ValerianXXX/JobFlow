"""Validate the frozen review's references, not its semantic judgments."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fact_review_inputs", ROOT / "scripts/evaluate-fact-preservation.py")
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)
DIRECTORY = ROOT / "evaluations/2026-10-30"
REVIEW = DIRECTORY / "fact-preservation-review-v1.json"
REPLAY = DIRECTORY / "fact-preservation-replay-v1.json"
OBSERVATION = DIRECTORY / "fact-preservation-live-v1.json"
PREVIOUS = DIRECTORY / "source-context-review-v1.json"


def unique_rows(rows, key):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate review reference")
    return result


def verify(review=None):
    plan, original = inputs.load_plan()
    text = {row["case"]: row["analyzed_text"] for row in original}
    observation = json.loads(OBSERVATION.read_text(encoding="utf8"))
    replay = json.loads(REPLAY.read_text(encoding="utf8"))
    previous = unique_rows(json.loads(PREVIOUS.read_text(encoding="utf8"))["cases"], "case")
    review = deepcopy(review) if review is not None else json.loads(REVIEW.read_text(encoding="utf8"))
    digest = inputs.previous.digest
    if (review["observation_lf_sha256"] != digest(OBSERVATION)
        or review["replay_lf_sha256"] != digest(REPLAY)
        or review["predecessor_review_lf_sha256"] != digest(PREVIOUS)
        or any(review[key] is not False for key in ("independent_review", "semantic_acceptance", "whole_material_acceptance"))):
        raise ValueError("Changed observation or unsupported acceptance claim")
    if (observation["protocol_closed"] is not True or observation["adapter_requests"] != 2
        or observation["campaign_actual_requests"] != 4 or replay["live_provider_calls"] != 0
        or replay["original_observation_lf_sha256"] != digest(OBSERVATION)):
        raise ValueError("Changed closed experiment or offline replay boundary")
    gold = {row["id"]: row for row in inputs.previous.complete.complete.load_manifest()["cases"]}
    actual = unique_rows(replay["arms"], "case")
    reviewed = unique_rows(review["cases"], "case")
    if set(actual) != set(plan["cases"]) or set(reviewed) != set(actual):
        raise ValueError("Changed fixed population")
    normalize = inputs.previous.complete.normalized
    totals = {"source_facts": 0, "candidates_reviewed": 0, "notes_reviewed": 0,
              "primary_targets_retained": 0, "prior_non_contact_omissions_retained": 0}
    counts = []
    for case, row in reviewed.items():
        observed = actual[case]
        facts = unique_rows(row["facts"], "fact_id")
        gold_facts = unique_rows(gold[case]["facts"], "id")
        old_facts = unique_rows(previous[case]["facts"], "fact_id")
        candidates = unique_rows(observed["candidates"], "candidate_id")
        notes = unique_rows(observed["notes"], "note_id")
        candidate_reviews = unique_rows(row["candidate_review"], "candidate_id")
        note_reviews = unique_rows(row["note_review"], "note_id")
        lines = text[case].splitlines()
        if set(facts) != set(gold_facts) or set(candidates) != set(candidate_reviews) or set(notes) != set(note_reviews):
            raise ValueError("A fact, candidate or note was dropped or invented")
        if (row["source_facts"] != len(facts) or row["candidates_reviewed"] != len(candidates)
            or row["notes_reviewed"] != len(notes)):
            raise ValueError("Changed review denominator")
        for note in notes.values():
            start, end = note["line_start"], note["line_end"]
            if not (1 <= start <= end <= len(lines)) or note["quote"] != "\n".join(lines[start - 1:end]):
                raise ValueError("Note is not an exact source excerpt")
            if note["approved_for_external"] is not False or note["status"] != "UNCONFIRMED_SOURCE_CONTEXT":
                raise ValueError("Source context was promoted into an approval")
        for identifier, fact in facts.items():
            old = old_facts[identifier]
            if (fact["previous_candidate_ids"] != old["candidate_ids"]
                or fact["previous_context_note_ids"] != old["context_note_ids"]):
                raise ValueError("Historical review was relabeled")
            references = fact["candidate_ids"] + fact["partial_candidate_ids"]
            if len(references) != len(set(references)) or not set(references) <= set(candidates):
                raise ValueError("Unknown or duplicate candidate reference")
            exact_notes = [n["note_id"] for n in observed["notes"]
                           if normalize(gold_facts[identifier]["text"]) in normalize(n["quote"])]
            if fact["context_note_ids"] != exact_notes:
                raise ValueError("Changed source-context reference")
            for candidate_id in fact["candidate_ids"]:
                ranges = candidate_reviews[candidate_id]["reviewed_source_ranges"]
                quoted = "\n".join("\n".join(lines[start - 1:end]) for start, end in ranges)
                if normalize(gold_facts[identifier]["text"]) not in normalize(quoted):
                    raise ValueError("A full-fact mapping does not cite that source fact")
            expected = ("FULL_FACT_IN_CANDIDATE" if fact["candidate_ids"] else
                        "EXACT_UNCONFIRMED_CONTEXT_ONLY" if exact_notes else
                        "PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM" if old["prior_omission_reason"] == "PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM" else "OMITTED")
            if fact["decision"] != expected:
                raise ValueError("Decision disagrees with reviewed references")
        for identifier, candidate in candidates.items():
            entry = candidate_reviews[identifier]
            provenance, entity = candidate["provenance"], candidate.get("entity")
            expected = [[provenance["line_start"], provenance["line_end"]]]
            if entity and [entity["line_start"], entity["line_end"]] not in expected:
                expected.append([entity["line_start"], entity["line_end"]])
            if (entry["reviewed_source_ranges"] != expected
                or any(not 1 <= start <= end <= len(lines) for start, end in expected)
                or entry["full_fact_ids"] != [f["fact_id"] for f in row["facts"] if identifier in f["candidate_ids"]]):
                raise ValueError("Candidate citation or reverse mapping changed")
        targets = set(previous[case]["new_missing_or_partial_fact_ids"])
        omissions = set(previous[case]["target_omission_fact_ids"])
        retained_targets = {f for f in targets if facts[f]["candidate_ids"]}
        retained_omissions = {f for f in omissions if facts[f]["candidate_ids"] or facts[f]["context_note_ids"]}
        regressions = [f for f, value in facts.items() if (value["previous_candidate_ids"] or value["previous_context_note_ids"])
                       and not (value["candidate_ids"] or value["context_note_ids"])]
        if (set(row["primary_target_fact_ids"]) != targets or set(row["primary_targets_fully_in_candidates"]) != retained_targets
            or set(row["prior_non_contact_omissions"]) != omissions or set(row["prior_non_contact_omissions_retained"]) != retained_omissions
            or row["new_missing_or_partial_fact_ids"] != regressions):
            raise ValueError("Target denominator or regressions changed")
        for key, value in (("source_facts", len(facts)), ("candidates_reviewed", len(candidates)), ("notes_reviewed", len(notes)),
                           ("primary_targets_retained", len(retained_targets)), ("prior_non_contact_omissions_retained", len(retained_omissions))):
            totals[key] += value
        counts.append({"case": case, "source_facts": len(facts),
                       "full_facts_in_candidates": sum(bool(f["candidate_ids"]) for f in facts.values()),
                       "mixed_candidate_or_context_count": sum(bool(f["candidate_ids"] or f["context_note_ids"]) for f in facts.values()),
                       "new_missing_or_partial_fact_ids": regressions})
    return {"status": "REFERENCES_AND_DENOMINATORS_VALID", **totals, "cases": counts,
            "independent_review": False, "semantic_judgments_verified_by_script": False, "whole_material_acceptance": False}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
