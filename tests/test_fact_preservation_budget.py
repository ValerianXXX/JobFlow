from __future__ import annotations
import importlib.util
import unittest
from _support import PROJECT, project_temp
from jobops.errors import JobOpsError

spec = importlib.util.spec_from_file_location("fact_budget", PROJECT / "scripts/evaluate-fact-preservation.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class FactPreservationBudgetTests(unittest.TestCase):
    def test_two_attempts_are_reserved_once_and_cannot_restart(self):
        with project_temp() as root:
            path = root / "budget.jsonl"
            journal = experiment.TwoAttemptJournal(path)
            try:
                journal.reserve("en-chronological")
                with self.assertRaises(JobOpsError):
                    journal.reserve("en-chronological")
                journal.reserve("zh-chronological")
                with self.assertRaises(JobOpsError):
                    journal.reserve("another-case")
            finally:
                journal.close()
            with self.assertRaises(FileExistsError):
                experiment.TwoAttemptJournal(path)
            self.assertEqual(len(path.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
