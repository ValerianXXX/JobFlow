from __future__ import annotations

import re
import unittest
from pathlib import Path

from _support import PROJECT


MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


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

    def test_quick_start_is_bilingual_and_covers_the_safe_first_run(self) -> None:
        guide = (PROJECT / "docs" / "quickstart.md").read_text(encoding="utf-8")
        for required in (
            "Install JobFlow.cmd",
            "Start JobFlow Demo.cmd",
            "Start JobFlow.cmd",
            "Check JobFlow.cmd",
            "Failed to fetch",
            "Windows 与 WSL",
            "A local approval is not a submission",
            "批准本机审阅包不等于已经投递",
        ):
            self.assertIn(required, guide)
        self.assertNotIn("C:\\Users\\", guide)
        self.assertNotIn("$env:USERPROFILE\\OneDrive", guide)

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
