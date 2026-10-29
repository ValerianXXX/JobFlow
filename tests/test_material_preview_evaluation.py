from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from _support import PROJECT

spec = importlib.util.spec_from_file_location("material_preview_evaluation", PROJECT / "scripts/evaluate-material-preview.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class MaterialPreviewEvaluationTests(unittest.TestCase):
    def fixture(self):
        statements = [
            "Built a dashboard covering 12 stores.",
            "Reduced reporting time by 18%.",
            "Processed 1,200 synthetic records.",
            "Team managed a $12,500 budget.",
            "Team tested 24 synthetic scenarios.",
            "Applicant prepared 6 review reports.",
        ]
        candidates = []
        for index, (statement, fact) in enumerate(zip(statements, evaluation.FACTS), 1):
            candidates.append({
                "candidate_id": f"C{index}", "statement":statement, "selected":False,
                "entity":{"organization":fact["organization"], "start_date":fact["years"][0], "end_date":fact["years"][-1]},
                "provenance":{"line_start":index, "line_end":index, "visual_regions":[{"page":fact["page"]}],
                              "visual_source_sha256":"sha256:"+evaluation.FIXTURE_SHA256, "visual_verified":False},
            })
        return candidates, "\n".join(statements)

    def test_frozen_numeric_facts_need_their_own_entity_and_evidence(self):
        candidates, transcript = self.fixture()
        result = evaluation.assess_candidates(candidates, transcript)
        self.assertEqual(result["matched_facts"], 6)
        self.assertTrue(result["manual_adjudication_required"])
        self.assertIsNone(result["numeric_errors"])
        self.assertIsNone(result["misattributions"])

    def test_empty_candidates_do_not_pass(self):
        self.assertEqual(evaluation.assess_candidates([], "")["matched_facts"], 0)

    def test_same_number_assigned_to_another_organization_is_not_credit(self):
        candidates, transcript = self.fixture()
        candidates[3]["entity"]["organization"] = "Cedar Studio"
        result = evaluation.assess_candidates(candidates, transcript)
        self.assertEqual(result["matched_facts"], 5)
        self.assertIn("ENTITY_MISMATCH", result["facts"][3]["observed_issues"])

    def test_team_result_cannot_be_reworded_as_a_personal_result(self):
        candidates, transcript = self.fixture()
        candidates[3]["statement"] = "Applicant managed a $12,500 budget."
        result = evaluation.assess_candidates(candidates, transcript)
        self.assertEqual(result["matched_facts"], 5)
        self.assertIn("TEAM_OWNERSHIP_NOT_EXPLICIT", result["facts"][3]["observed_issues"])

    def test_changed_number_or_year_cannot_count_as_grounded(self):
        candidates, transcript = self.fixture()
        candidates[0]["statement"] = "Built a dashboard covering 120 stores."
        candidates[1]["entity"]["end_date"] = "2026"
        result = evaluation.assess_candidates(candidates, transcript)
        self.assertEqual(result["matched_facts"], 4)
        self.assertIn("DATE_MISSING_OR_CHANGED", result["facts"][1]["observed_issues"])

    def test_wrong_source_page_line_or_automatic_selection_is_not_credit(self):
        candidates, transcript = self.fixture()
        mutations = [
            lambda row:row["provenance"].update(visual_source_sha256="sha256:"+"0"*64),
            lambda row:row["provenance"].update(visual_regions=[{"page":3}]),
            lambda row:row["provenance"].update(line_start=6, line_end=6),
            lambda row:row.update(selected=True),
        ]
        for mutate in mutations:
            changed = deepcopy(candidates)
            mutate(changed[0])
            with self.subTest(mutation=mutate):
                self.assertEqual(evaluation.assess_candidates(changed, transcript)["matched_facts"], 5)


if __name__ == "__main__":
    unittest.main()
