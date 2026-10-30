from __future__ import annotations
import importlib.util
import json
import unittest
from _support import PROJECT

spec = importlib.util.spec_from_file_location("fact_review_check", PROJECT / "scripts/verify-fact-preservation-review.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class FactPreservationReviewTests(unittest.TestCase):
    def load(self):
        return json.loads(checker.REVIEW.read_text(encoding="utf8"))

    def test_references_preserve_all_facts_without_certifying_semantics(self):
        result = checker.verify()
        self.assertEqual(result["source_facts"], 94)
        self.assertEqual(result["candidates_reviewed"], 68)
        self.assertEqual(result["primary_targets_retained"], 4)
        self.assertEqual(result["prior_non_contact_omissions_retained"], 9)
        self.assertFalse(result["semantic_judgments_verified_by_script"])

    def test_dropped_fact_cannot_shrink_denominator(self):
        value = self.load()
        value["cases"][0]["facts"].pop()
        with self.assertRaises(ValueError): checker.verify(value)

    def test_valid_but_unrelated_candidate_cannot_claim_source_coverage(self):
        value = self.load()
        value["cases"][0]["facts"][3]["candidate_ids"] = [value["cases"][0]["candidate_review"][4]["candidate_id"]]
        with self.assertRaises(ValueError): checker.verify(value)

    def test_forged_note_cannot_inflate_retention(self):
        value = self.load()
        value["cases"][0]["facts"][0]["context_note_ids"] = ["CTX-INVENTED"]
        with self.assertRaises(ValueError): checker.verify(value)

    def test_history_cannot_be_rewritten(self):
        value = self.load()
        value["cases"][0]["facts"][5]["previous_candidate_ids"] = ["EXT-INVENTED"]
        with self.assertRaises(ValueError): checker.verify(value)

    def test_observation_does_not_certify_complete_product(self):
        value = self.load()
        value["whole_material_acceptance"] = True
        with self.assertRaises(ValueError): checker.verify(value)


if __name__ == "__main__":
    unittest.main()
