from __future__ import annotations

import re
import unittest
from pathlib import Path

from _support import PROJECT


MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
CJK_TEXT = re.compile(r"[\u3400-\u9fff]")


class PublicDocumentationTests(unittest.TestCase):
    def test_readme_relative_links_resolve_inside_the_repository(self) -> None:
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(readme):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            resolved = (PROJECT / relative).resolve()
            self.assertTrue(resolved.is_relative_to(PROJECT.resolve()))
            self.assertTrue(resolved.is_file(), target)

    def test_quick_start_is_english_and_covers_the_safe_first_run(self) -> None:
        guide = (PROJECT / "docs" / "quickstart.md").read_text(encoding="utf-8")
        for required in (
            "Install JobFlow.cmd",
            "Start JobFlow Demo.cmd",
            "Start JobFlow.cmd",
            "Check JobFlow.cmd",
            "Failed to fetch",
            "Windows and WSL",
            "A local approval is not a submission",
            "Final Submit is always user-only",
        ):
            self.assertIn(required, guide)
        self.assertIsNone(CJK_TEXT.search(guide))
        self.assertNotIn("C:\\Users\\", guide)
        self.assertNotIn("$env:USERPROFILE\\OneDrive", guide)

    def test_public_github_text_is_english_only(self) -> None:
        paths = [
            PROJECT / "README.md",
            PROJECT / "CHANGELOG.md",
            PROJECT / "CONTRIBUTING.md",
            PROJECT / "SECURITY.md",
            *sorted((PROJECT / "docs").rglob("*.md")),
            *sorted((PROJECT / ".github").rglob("*.md")),
            *sorted((PROJECT / ".github").rglob("*.yml")),
            *sorted((PROJECT / ".github").rglob("*.yaml")),
        ]
        for path in dict.fromkeys(paths):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(CJK_TEXT.search(text), str(path.relative_to(PROJECT)))

    def test_quick_start_relative_links_resolve(self) -> None:
        path = PROJECT / "docs" / "quickstart.md"
        guide = path.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(guide):
            if target.startswith(("http://", "https://", "#")):
                continue
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            self.assertTrue(resolved.is_relative_to(PROJECT.resolve()))
            self.assertTrue(resolved.is_file(), target)


if __name__ == "__main__":
    unittest.main()
