from __future__ import annotations

import unittest

from _support import PROJECT


class ReleaseReadinessContractTests(unittest.TestCase):
    def test_version_metadata_and_changelog_are_consistent(self) -> None:
        import tomllib
        from jobops import __version__

        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], __version__)
        changelog = (PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{__version__}]", changelog)
        self.assertIn("release candidate", changelog)

    def test_release_checklist_requires_identity_qa_tag_and_upload_authorization(self) -> None:
        checklist = (PROJECT / "docs" / "release-checklist.md").read_text(encoding="utf-8")
        for requirement in ("author identity", "independent QA", "annotated or signed", "explicit user authorization"):
            self.assertIn(requirement, checklist)
        self.assertIn("Real external actions must remain 0", checklist)


if __name__ == "__main__":
    unittest.main()
