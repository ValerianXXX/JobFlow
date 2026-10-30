from __future__ import annotations
import importlib.util
import json
import unittest
from _support import PROJECT

spec = importlib.util.spec_from_file_location("context_review_check", PROJECT / "scripts/verify-source-context-review.py")
review_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_check)


class SourceContextReviewTests(unittest.TestCase):
    def test_receipt_keeps_new_omissions_visible_and_does_not_certify_semantics(self):
        result = review_check.verify()
        self.assertEqual(result["source_facts"], 94)
        self.assertEqual(result["prior_target_facts_retained_in_notes"], 9)
        self.assertEqual(len(result["new_missing_or_partial_fact_ids"]), 4)
        self.assertFalse(result["semantic_judgments_verified_by_script"])

    def test_dropping_a_fact_is_rejected(self):
        value = json.loads(review_check.REVIEW.read_text())
        value["cases"][0]["facts"].pop()
        with self.assertRaises(ValueError):
            review_check.verify(value)

    def test_unknown_context_reference_cannot_inflate_retention(self):
        value = json.loads(review_check.REVIEW.read_text())
        value["cases"][0]["facts"][0]["context_note_ids"] = ["CTX-INVENTED"]
        with self.assertRaises(ValueError):
            review_check.verify(value)

    def test_prior_status_cannot_be_rewritten_to_hide_a_new_omission(self):
        value = json.loads(review_check.REVIEW.read_text())
        value["cases"][0]["facts"][5]["prior_candidate_decision"] = "OMITTED"
        value["cases"][0]["new_missing_or_partial_fact_ids"] = []
        with self.assertRaises(ValueError):
            review_check.verify(value)


if __name__ == "__main__":
    unittest.main()
