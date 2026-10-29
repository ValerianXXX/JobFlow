from __future__ import annotations
from copy import deepcopy
import importlib.util
import unittest
from unittest.mock import patch
import _support
from _support import PROJECT
from jobops.errors import JobOpsError

spec = importlib.util.spec_from_file_location("layout_claim_eval", PROJECT / "scripts/evaluate-layout-claims.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class LayoutClaimEvaluationTests(unittest.TestCase):
    def fixture(self):
        case = next(c for c in evaluation.corpus.load_cases() if c["id"] == "en-columns")
        lines, candidates = [], []
        for entity in case["entities"]:
            start = len(lines) + 1
            lines.extend(entity["lines"])
            for fact in entity["facts"]:
                candidates.append({"candidate_id": f"CASE-{len(candidates)}", "statement": fact["statement"], "selected": False,
                    "entity": {"organization": entity["organization"], "start_date": entity["years"][0], "end_date": entity["years"][1]},
                    "provenance": {"line_start": start, "line_end": len(lines)}})
        return case, candidates, "\n".join(lines)

    def test_evidence_entity_dates_and_ownership_are_separate_requirements(self):
        case, candidates, text = self.fixture()
        self.assertEqual(evaluation.assess(case, candidates, text)["matched_facts"], 4)
        mutations = [
            lambda c: c[0]["entity"].update(organization="Another Synthetic Entity"),
            lambda c: c[0]["entity"].update(start_date="2020"),
            lambda c: c[0].update(statement=c[0]["statement"].replace("Team", "Applicant")),
            lambda c: c[0]["provenance"].update(line_start=5, line_end=8),
            lambda c: c[0].update(selected=True),
        ]
        for mutate in mutations:
            changed = deepcopy(candidates)
            mutate(changed)
            self.assertEqual(evaluation.assess(case, changed, text)["matched_facts"], 3)

    def test_empty_result_cannot_pass_and_unknown_adjudication_is_not_zero(self):
        case, _, text = self.fixture()
        result = evaluation.assess(case, [], text)
        self.assertEqual(result["matched_facts"], 0)
        self.assertIsNone(result["numeric_errors"])
        self.assertIsNone(result["misattributions"])
        self.assertTrue(result["manual_adjudication_required"])

    def test_budget_refuses_next_request_before_invoking_provider(self):
        engine = evaluation.ObservedHermes({"model": "synthetic-model", "provider": "synthetic-provider"})
        engine.calls = [{}] * evaluation.CALL_LIMIT
        with patch("jobops.ai_connections.WSLHermesCLIEngine._invoke") as invoke:
            with self.assertRaises(JobOpsError) as caught:
                engine._invoke({"task": "synthetic"})
            self.assertEqual(caught.exception.code, "EVALUATION_CALL_LIMIT")
            invoke.assert_not_called()

    def test_failed_requests_consume_budget_and_do_not_record_private_details(self):
        engine = evaluation.ObservedHermes({"model": "synthetic-model", "provider": "synthetic-provider"})
        with patch("jobops.ai_connections.WSLHermesCLIEngine._invoke", side_effect=JobOpsError("AI_ENGINE_FAILED", "synthetic-private-error")):
            with self.assertRaises(JobOpsError):
                engine._invoke({"task": "synthetic"})
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["status"], "FAILED")
        self.assertNotIn("synthetic-private-error", str(engine.calls))


if __name__ == "__main__":
    unittest.main()
