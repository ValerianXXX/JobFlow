from __future__ import annotations
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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
        with self.assertRaisesRegex(ValueError, "A fact, candidate or note was dropped or invented"):
            checker.verify(value)

    def test_valid_but_unrelated_candidate_cannot_claim_source_coverage(self):
        value = self.load()
        value["cases"][0]["facts"][3]["candidate_ids"] = [value["cases"][0]["candidate_review"][4]["candidate_id"]]
        with self.assertRaisesRegex(ValueError, "A full-fact mapping does not cite that source fact"):
            checker.verify(value)

    def test_forged_note_cannot_inflate_retention(self):
        value = self.load()
        value["cases"][0]["facts"][0]["context_note_ids"] = ["CTX-INVENTED"]
        with self.assertRaisesRegex(ValueError, "Changed source-context reference"):
            checker.verify(value)

    def test_history_cannot_be_rewritten(self):
        value = self.load()
        value["cases"][0]["facts"][5]["previous_candidate_ids"] = ["EXT-INVENTED"]
        with self.assertRaisesRegex(ValueError, "Historical review was relabeled"):
            checker.verify(value)

    def test_observation_does_not_certify_complete_product(self):
        value = self.load()
        value["whole_material_acceptance"] = True
        with self.assertRaisesRegex(ValueError, "Changed observation or unsupported acceptance claim"):
            checker.verify(value)

    def observation(self):
        return json.loads(checker.OBSERVATION.read_text(encoding="utf8"))

    def test_current_product_changes_do_not_invalidate_historical_evidence(self):
        digest = checker.inputs.previous.digest
        current_product = PROJECT / "src/jobops/onboarding_center.py"
        def altered(path):
            return "0" * 64 if path == current_product else digest(path)
        with mock.patch.object(checker.inputs.previous, "digest", side_effect=altered), \
             mock.patch.object(checker.inputs, "budget_path", side_effect=AssertionError("No journal access")), \
             mock.patch.object(checker.inputs.previous, "ContextEngine", side_effect=AssertionError("No model")):
            self.assertEqual(checker.verify()["source_facts"], 94)

    def test_live_runner_still_rejects_current_product_changes(self):
        digest = checker.inputs.previous.digest
        current_product = PROJECT / "src/jobops/onboarding_center.py"
        with mock.patch.object(checker.inputs.previous, "digest", side_effect=lambda path:
                               "0" * 64 if path == current_product else digest(path)):
            with self.assertRaisesRegex(ValueError, "Frozen source, observation or code changed"):
                checker.inputs.load_plan()

    def test_changed_source_is_not_excused_by_historical_code(self):
        digest = checker.inputs.previous.digest
        source = PROJECT / "evaluations/2026-10-29/source-coverage-replay-text.json"
        with mock.patch.object(checker.inputs.previous, "digest", side_effect=lambda path:
                               "0" * 64 if path == source else digest(path)):
            with self.assertRaisesRegex(ValueError, "Frozen source or observation changed"):
                checker.verify()

    def test_changed_recorded_product_fails_closed(self):
        with mock.patch.object(checker, "recorded_product_digest", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "Recorded product differs from the frozen experiment"):
                checker.verify()

    def test_changed_plan_or_recorded_manifest_is_rejected(self):
        for field in ("protocol", "plan_lf_sha256", "runner_lf_sha256", "input_files", "product_files"):
            with self.subTest(field=field):
                observation = self.observation()
                observation[field] = {} if field.endswith("files") else "changed"
                with self.assertRaisesRegex(ValueError, "Changed frozen experiment plan or recorded product binding"):
                    checker.review_inputs(observation)

    def test_repointed_observation_is_rejected_before_reading_git(self):
        observation = self.observation()
        observation["source_commit"] = "0" * 40
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observation.json"
            path.write_text(json.dumps(observation), encoding="utf8")
            with mock.patch.object(checker, "OBSERVATION", path), \
                 mock.patch.object(checker, "recorded_product_digest", side_effect=AssertionError("Too early")):
                with self.assertRaisesRegex(ValueError, "Changed observation or unsupported acceptance claim"):
                    checker.verify()

    def test_missing_history_never_falls_back_to_current_files(self):
        observation = self.observation()
        for failure in (FileNotFoundError(), subprocess.CalledProcessError(128, ["git"]),
                        subprocess.TimeoutExpired(["git"], 15)):
            with self.subTest(error=type(failure).__name__), \
                 mock.patch.object(checker.subprocess, "run", side_effect=failure):
                with self.assertRaisesRegex(ValueError, "Recorded product history is unavailable"):
                    checker.recorded_product_digest(observation["source_commit"], "src/jobops/ai_runtime.py")

    def test_git_reference_is_a_full_commit_and_relative_posix_path(self):
        commit = self.observation()["source_commit"]
        values = [("HEAD", "src/jobops/ai_runtime.py"), (None, "src/jobops/ai_runtime.py")]
        values += [(commit, path) for path in (None, "../private", "/private", "C:/private", "a\\b",
                                              "src//file", "src/./file", "src/../file", "src/file\nextra")]
        with mock.patch.object(checker.subprocess, "run", side_effect=AssertionError("Invalid reference executed")):
            for revision, path in values:
                with self.subTest(revision=revision, path=path):
                    with self.assertRaisesRegex(ValueError, "Invalid recorded product reference"):
                        checker.recorded_product_digest(revision, path)


if __name__ == "__main__":
    unittest.main()
