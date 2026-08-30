from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT  # noqa: F401 - establishes the checked-in src path
from jobops.release import _release_truth
from jobops.release_verification import ReleaseVerificationError, run_release_verification


class ReleaseTruthSemanticsTests(unittest.TestCase):
    def test_local_success_never_claims_public_release_readiness(self) -> None:
        result = _release_truth(
            core_pass=True,
            independent_fresh=True,
            public_repository={
                "public_repository_ready": True,
                "public_release_blockers": [],
            },
        )

        self.assertTrue(result["public_repository_ready"])
        self.assertFalse(result["public_release_ready"])
        self.assertEqual(result["runtime_closure_status"], "UNATTESTED")
        self.assertEqual(
            result["public_release_blockers"],
            ["RELEASE_RUNTIME_CLOSURE_UNATTESTED"],
        )

    def test_local_blockers_remain_visible_without_duplicating_runtime_gate(self) -> None:
        result = _release_truth(
            core_pass=False,
            independent_fresh=False,
            public_repository={
                "public_repository_ready": False,
                "public_release_blockers": [
                    "GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED",
                    "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
                ],
            },
        )

        self.assertFalse(result["public_repository_ready"])
        self.assertFalse(result["public_release_ready"])
        self.assertEqual(
            result["public_release_blockers"],
            [
                "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
                "CORE_RELEASE_CHECK_FAILED",
                "GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED",
                "INDEPENDENT_QA_STALE_OR_MISSING",
            ],
        )

    def test_repository_readiness_requires_a_native_true_value(self) -> None:
        result = _release_truth(
            core_pass=True,
            independent_fresh=True,
            public_repository={
                "public_repository_ready": "true",
                "public_release_blockers": [],
            },
        )

        self.assertFalse(result["public_repository_ready"])
        self.assertFalse(result["public_release_ready"])

    def test_public_release_mode_fails_before_any_tool_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            with patch("jobops.release_verification._validated_tool") as validate_tool:
                with self.assertRaisesRegex(
                    ReleaseVerificationError,
                    "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
                ):
                    run_release_verification(
                        project,
                        node_path=project / "node.exe",
                        git_path=project / "git.exe",
                        require_public_release=True,
                    )
            validate_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
