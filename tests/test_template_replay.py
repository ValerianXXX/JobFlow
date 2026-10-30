import importlib.util
import unittest
from unittest import mock
from _support import PROJECT

spec = importlib.util.spec_from_file_location("template_replay_contract", PROJECT / "scripts/replay-template-pilot.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class TemplateReplayTests(unittest.TestCase):
    def test_pinned_response_requires_exact_text_and_single_read_without_provider(self):
        engine = replay.ReplayEngine({"case": replay.pilot.CASES[0], "content": "fixed response"}, "synthetic line")
        engine.case = replay.pilot.CASES[0]
        request = {"task": "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2", "line_numbered_document": ["1\tsynthetic line"]}
        with mock.patch.object(replay.pilot.previous.WSLHermesCLIEngine, "_invoke", side_effect=AssertionError("No live call")):
            result = engine._invoke(request)
            self.assertEqual(result["result"]["content"], "fixed response")
            with self.assertRaises(replay.pilot.JobOpsError): engine._invoke(request)

    def test_wrong_case_task_or_source_cannot_fall_back(self):
        for task, text, case in (("other", "1\tsynthetic line", replay.pilot.CASES[0]),
                                 ("JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2", "1\tchanged", replay.pilot.CASES[0]),
                                 ("JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2", "1\tsynthetic line", "other")):
            engine = replay.ReplayEngine({"case": replay.pilot.CASES[0], "content": "fixed"}, "synthetic line")
            engine.case = case
            with self.assertRaises(replay.pilot.JobOpsError):
                engine._invoke({"task": task, "line_numbered_document": [text]})
            self.assertEqual(engine.journal.count, 0)

    def test_observation_hash_and_closed_budget_are_fixed(self):
        self.assertEqual(replay.load_observation()["campaign_actual_requests"], 6)
        with mock.patch.object(replay.pilot.previous, "digest", return_value="0" * 64):
            with self.assertRaises(ValueError): replay.load_observation()


if __name__ == "__main__":
    unittest.main()
