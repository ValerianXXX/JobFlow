from __future__ import annotations
from copy import deepcopy
import importlib.util
import json
import unittest
from unittest.mock import patch
import _support
from _support import PROJECT
from jobops.ai_runtime import LocalSubprocessAIEngine
from jobops.errors import JobOpsError

spec = importlib.util.spec_from_file_location("cjk_evaluation", PROJECT / "scripts/evaluate-cjk-validator.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class CjkEvaluationTests(unittest.TestCase):
    def test_frozen_response_retains_grounded_facts_without_mutation_or_selection(self):
        fixture = json.loads((PROJECT / "tests/fixtures/cjk-column-response-v1.json").read_text(encoding="utf-8"))
        case = next(c for c in evaluation.layout.corpus.load_cases() if c["id"] == fixture["case"])
        self.assertEqual(fixture["source_sha256"], case["sha256"])
        response = fixture["model_response"]
        original = deepcopy(response)
        result = evaluation.replay(LocalSubprocessAIEngine, response)
        self.assertEqual(response, original)
        self.assertEqual(len(result["candidates"]), 5)
        self.assertTrue(all(c["selected"] is False for c in result["candidates"]))
        assessment = evaluation.layout.assess(case, result["candidates"], "\n".join(response["source_lines"]))
        self.assertEqual(assessment["matched_facts"], 4)
        self.assertEqual(result["diagnostics"]["filtered_candidate_reasons"], {"STATEMENT_FRAGMENT": 1})

    def test_followup_cannot_inherit_larger_first_round_budget(self):
        engine = evaluation.SyntheticObservedHermes({"model": "synthetic", "provider": "synthetic"})
        engine.calls = [{}] * evaluation.CALL_LIMIT
        with patch.object(evaluation.layout.ObservedHermes, "_invoke") as invoke:
            with self.assertRaises(JobOpsError) as error:
                engine._invoke({"task": "synthetic"})
            self.assertEqual(error.exception.code, "EVALUATION_CALL_LIMIT")
            invoke.assert_not_called()

    def test_failed_request_counts_but_does_not_capture_error_or_fake_response(self):
        engine = evaluation.SyntheticObservedHermes({"model": "synthetic", "provider": "synthetic"})
        with patch("jobops.ai_connections.WSLHermesCLIEngine._invoke", side_effect=JobOpsError("AI_ENGINE_FAILED", "private-error-sentinel")):
            with self.assertRaises(JobOpsError):
                engine._invoke({"task": "synthetic"})
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.responses, [])
        self.assertNotIn("private-error-sentinel", str(engine.calls))


if __name__ == "__main__":
    unittest.main()
