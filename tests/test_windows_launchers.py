from __future__ import annotations

import unittest

from _support import PROJECT


class WindowsLauncherTests(unittest.TestCase):
    def test_installer_discovers_python_without_hardcoding_one_minor(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('@{ Name = "python"; Prefix = @() }', script)
        self.assertIn('@{ Name = "py"; Prefix = @("-3") }', script)
        self.assertNotIn('Prefix = @("-3.11")', script)
        self.assertIn("-ge 11", script)

    def test_installer_uses_relative_editable_target_and_checks_dependencies(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Push-Location $projectRoot', script)
        self.assertIn('--editable ".[build]"', script)
        self.assertNotIn('--editable $projectRoot', script)
        self.assertGreaterEqual(script.count("--quiet"), 2)
        self.assertIn("-m pip check", script)
        self.assertIn("setuptools>=77,<81", script)
        self.assertIn("wheel>=0.43,<1", script)

    def test_launcher_messages_are_bilingual_and_external_actions_are_absent(self) -> None:
        install = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        start = (PROJECT / "scripts" / "start-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(" / ", install)
        self.assertIn(" / ", start)
        combined = (install + start).casefold()
        for forbidden in ("invoke-webrequest", "start-bitstransfer", "git clone", "git push"):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
