from __future__ import annotations

import unittest

from _support import PROJECT


class GitHubCollaborationTests(unittest.TestCase):
    def test_issue_forms_require_synthetic_data_and_external_action_disclosure(self) -> None:
        bug = (PROJECT / ".github" / "ISSUE_TEMPLATE" / "bug-report.yml").read_text(encoding="utf-8")
        feature = (PROJECT / ".github" / "ISSUE_TEMPLATE" / "feature-request.yml").read_text(encoding="utf-8")
        self.assertIn("synthetic", bug.casefold())
        self.assertIn("personal data", bug.casefold())
        self.assertIn("required: true", bug)
        self.assertIn("External-action impact", feature)
        self.assertIn("AI impact", feature)
        self.assertIn("Private-data impact", feature)

    def test_pull_request_template_has_all_release_boundaries(self) -> None:
        template = (PROJECT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        for required in ("Knowledge", "Private staging", "Real external actions", "Chinese and English", "provenance-bound", "Rollback"):
            self.assertIn(required, template)

    def test_roadmap_does_not_claim_live_ats_support(self) -> None:
        roadmap = (PROJECT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        self.assertIn("not a claim of live compatibility", roadmap)
        self.assertIn("Synthetic Greenhouse", roadmap)
        self.assertIn("separately authorized", roadmap)


if __name__ == "__main__":
    unittest.main()
