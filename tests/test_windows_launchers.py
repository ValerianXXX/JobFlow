from __future__ import annotations

import json
import subprocess
import sys
import unittest

from _support import PROJECT
from jobops import __version__


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
        check = (PROJECT / "scripts" / "check-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(" / ", install)
        self.assertIn(" / ", start)
        self.assertIn(" / ", check)
        combined = (install + start + check).casefold()
        for forbidden in ("invoke-webrequest", "start-bitstransfer", "git clone", "git push"):
            self.assertNotIn(forbidden, combined)

    def test_one_click_health_check_is_redacted_local_only_and_passing(self) -> None:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "check-jobflow.ps1"), "-Json",
            ],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["status"], "JOBFLOW_READY")
        self.assertEqual(result["version"], __version__)
        self.assertEqual(result["checks_passed"], result["checks_total"])
        self.assertEqual(result["private_values_read"], 0)
        self.assertEqual(result["private_values_emitted"], 0)
        self.assertEqual(result["network_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)
        serialized = json.dumps(result)
        self.assertNotIn(str(PROJECT), serialized)
        self.assertNotIn("secure-ref:", serialized)

    def test_public_cli_reports_a_safe_version(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "jobops.cli", "--version"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), f"JobFlow {__version__}")
        self.assertNotIn(str(PROJECT), completed.stdout)


if __name__ == "__main__":
    unittest.main()
