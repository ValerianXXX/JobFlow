from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "build-windows-runtime-from-inputs.ps1"


class RuntimeFromInputsWrapperTests(unittest.TestCase):
    def test_offline_verification_precedes_protected_builder_and_paths_are_direct(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        verify = text.index("-Destination $bundle -VerifyOnly")
        build = text.index("& $builder `")
        self.assertLess(verify, build)
        self.assertIn('$artifact = Join-Path $bundle "python\\python-3.13.15-embed-amd64.zip"', text)
        self.assertIn('$wheelhouse = Join-Path $bundle "wheelhouse"', text)
        self.assertIn("-WheelhousePath $wheelhouse", text)
        for forbidden in ("Invoke-WebRequest", "Start-BitsTransfer", "DownloadString", "HttpClient"):
            self.assertNotIn(forbidden, text)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell parser")
    def test_wrapper_parses_in_stock_windows_powershell(self) -> None:
        powershell = (
            Path(os.environ["SystemRoot"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        escaped = str(SCRIPT).replace("'", "''")
        command = (
            "$e=$null;$t=$null;"
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped}',[ref]$t,[ref]$e)|Out-Null;"
            "if($e.Count){$e|ForEach-Object{$_.Message};exit 1};'PS51_PARSE_OK'"
        )
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=PROJECT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("PS51_PARSE_OK", completed.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows default-path integration")
    def test_stock_powershell_resolves_script_defaults_and_redacts_failures(self) -> None:
        powershell = (
            Path(os.environ["SystemRoot"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-build-wrapper-") as temporary:
            root = Path(temporary)
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(SCRIPT),
                    "-InputBundle",
                    str(root / "missing-bundle"),
                    "-GitPath",
                    str(root / "missing-git.exe"),
                    "-SourceCommit",
                    "0" * 40,
                    "-OutputDirectory",
                    str(root / "output"),
                ],
                cwd=PROJECT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_BUILD_INPUT_MISSING")
        self.assertNotIn(str(PROJECT), completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
