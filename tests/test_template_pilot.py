from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from _support import PROJECT

spec = importlib.util.spec_from_file_location("template_pilot_contract", PROJECT / "scripts/evaluate-template-pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class TemplatePilotTests(unittest.TestCase):
    def test_frozen_sources_and_predecessor_budget_are_exact(self):
        plan, cases = pilot.load_plan()
        self.assertEqual([case["id"] for case in cases], list(pilot.CASES))
        self.assertEqual(sum(len(case["facts"]) for case in cases), 94)
        self.assertEqual(plan["prior_requests"] + plan["max_new_requests"], 6)
        self.assertTrue(all(case["split"] == "held_out" for case in cases))

    def test_same_case_retry_third_call_and_unknown_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = pilot.TwoCaseJournal(Path(folder) / "requests.jsonl")
            try:
                journal.reserve(pilot.CASES[0])
                for case in (pilot.CASES[0], "unknown"):
                    with self.assertRaises(pilot.JobOpsError): journal.reserve(case)
                journal.reserve(pilot.CASES[1])
                with self.assertRaises(pilot.JobOpsError): journal.reserve(pilot.CASES[1])
                self.assertEqual(journal.count, 2)
            finally:
                journal.close()
            records = [json.loads(line) for line in (Path(folder) / "requests.jsonl").read_text().splitlines()]
            self.assertEqual([row["case"] for row in records], list(pilot.CASES))

    def test_existing_journal_cannot_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.jsonl"
            journal = pilot.TwoCaseJournal(path)
            journal.close()
            with self.assertRaises(FileExistsError): pilot.TwoCaseJournal(path)

    def test_failed_provider_call_consumes_attempt_and_cannot_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = pilot.TwoCaseJournal(Path(folder) / "requests.jsonl")
            engine = pilot.TemplateEngine({"model": "synthetic", "provider": "synthetic"}, journal)
            engine.case = pilot.CASES[0]
            try:
                with mock.patch.object(pilot.previous.WSLHermesCLIEngine, "_invoke",
                                       side_effect=pilot.JobOpsError("TEST_FAILURE", "Synthetic failure")) as invoke:
                    for attempt in range(2):
                        with self.assertRaises(pilot.JobOpsError):
                            engine._invoke({"task": "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2"})
                    self.assertEqual(invoke.call_count, 1)
                    self.assertEqual(journal.count, 1)
            finally:
                journal.close()

    def test_tool_use_and_wrong_task_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = pilot.TwoCaseJournal(Path(folder) / "requests.jsonl")
            engine = pilot.TemplateEngine({"model": "synthetic", "provider": "synthetic"}, journal)
            engine.case = pilot.CASES[0]
            try:
                with mock.patch.object(pilot.previous.WSLHermesCLIEngine, "_invoke",
                                       return_value={"toolSummary": {"calls": 1}, "result": {"content": "synthetic"}}) as invoke:
                    with self.assertRaises(pilot.JobOpsError): engine._invoke({"task": "NOT_ALLOWED"})
                    self.assertEqual(invoke.call_count, 0)
                    with self.assertRaises(pilot.JobOpsError):
                        engine._invoke({"task": "JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2"})
                    self.assertEqual(journal.count, 1)
                    self.assertEqual(engine.responses, [])
            finally:
                journal.close()

    def test_changed_product_or_prior_observation_prevents_dispatch(self):
        plan, _ = pilot.load_plan()
        for relative in (next(iter(plan["product"])), next(iter(plan["inputs"]))):
            digest = pilot.previous.digest
            with self.subTest(path=relative), mock.patch.object(pilot.previous, "digest", side_effect=lambda path:
                                                               "0" * 64 if path == PROJECT / relative else digest(path)):
                with self.assertRaisesRegex(ValueError, "Changed frozen input or product code"):
                    if relative in plan["product"]:
                        pilot.validate_dispatch(plan)
                    else:
                        pilot.load_plan()


if __name__ == "__main__":
    unittest.main()
