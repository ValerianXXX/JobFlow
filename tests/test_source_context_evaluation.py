from __future__ import annotations

import importlib.util
import json
import unittest
from _support import PROJECT, project_temp
from jobops.errors import JobOpsError

spec = importlib.util.spec_from_file_location("context_experiment", PROJECT / "scripts/evaluate-source-context.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class SourceContextEvaluationTests(unittest.TestCase):
    def test_attempts_are_consumed_before_dispatch_and_cannot_repeat_or_restart(self):
        with project_temp() as root:
            path = root / "attempts.jsonl"
            journal = experiment.AttemptJournal(path)
            try:
                journal.reserve("case-1")
                with self.assertRaises(JobOpsError):
                    journal.reserve("case-1")
                for index in range(2, 7):
                    journal.reserve(f"case-{index}")
                with self.assertRaises(JobOpsError):
                    journal.reserve("case-7")
            finally:
                journal.close()
            self.assertEqual(len(path.read_text(encoding="utf8").splitlines()), 6)
            with self.assertRaises(FileExistsError):
                experiment.AttemptJournal(path)
            self.assertTrue(all(json.loads(line)["event"] == "RESERVED" for line in path.read_text().splitlines()))

    def test_six_pinned_transcripts_retain_original_population_and_no_gold_in_model_text(self):
        # Historical input integrity remains testable after later product edits;
        # actual dispatch always checks the frozen product hashes as well.
        plan, arms = experiment.load_inputs(verify_product=False)
        self.assertEqual(len(arms), 6)
        self.assertEqual([arm["case"] for arm in arms], plan["cases"])
        self.assertEqual(sum(len(arm["candidates"]) for arm in arms), 197)
        for arm in arms:
            self.assertTrue(arm["analyzed_text"])
            self.assertNotIn("PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM", arm["analyzed_text"])


if __name__ == "__main__":
    unittest.main()
