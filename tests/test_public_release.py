from __future__ import annotations

import unittest

from _support import PROJECT
from jobops.public_release import validate_public_paths


class PublicReleaseBoundaryTests(unittest.TestCase):
    def test_runtime_and_private_files_cannot_be_tracked(self) -> None:
        findings = validate_public_paths(
            [
                "state/jobops.db",
                "state/onboarding-center-index.json",
                "reports/checkpoint-final.json",
                "workspace/jobs/JOB-1/raw/page.html",
                "private.dpapi",
                "export.zip",
                "src/jobops/__pycache__/cli.pyc",
            ]
        )
        kinds = {item["kind"] for item in findings}
        self.assertIn("runtime_state_tracked", kinds)
        self.assertIn("private_or_generated_file_tracked", kinds)
        self.assertIn("generated_path_tracked", kinds)

    def test_only_empty_runtime_sentinels_are_public(self) -> None:
        sentinels = [
            "state/.gitkeep",
            "reports/.gitkeep",
            "workspace/inbox/.gitkeep",
            "workspace/jobs/.gitkeep",
            "workspace/review-packets/.gitkeep",
        ]
        self.assertEqual(validate_public_paths(sentinels), [])

    def test_project_has_no_checked_in_git_metadata_before_initialization_fixture(self) -> None:
        self.assertTrue((PROJECT / ".jobops-root").is_file())
