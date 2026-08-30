from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "prepare-windows-runtime-inputs.ps1"


class RuntimeInputWrapperTests(unittest.TestCase):
    def test_wrapper_requires_explicit_network_opt_in_and_uses_isolated_python(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('throw "JOBFLOW_RUNTIME_INPUT_NETWORK_OPT_IN_REQUIRED"', text)
        self.assertIn('$arguments = @("-I", "-m", "jobops.runtime_inputs"', text)
        self.assertIn('"acquire", "--destination", $destinationPath, "--allow-network"', text)
        self.assertIn('"verify", "--bundle", $destinationPath', text)
        for forbidden in ("Invoke-WebRequest", "Start-BitsTransfer", "DownloadString", "HttpClient"):
            self.assertNotIn(forbidden, text)

    def test_wrapper_authenticates_and_retains_the_bounded_python_launcher(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('$python -cne $expectedPython', text)
        self.assertIn('config\\release-toolchain.json', text)
        self.assertIn('Microsoft.PowerShell.Security\\Get-AuthenticodeSignature', text)
        self.assertIn('[IO.FileShare]::Read', text)
        self.assertIn('GetFileInformationByHandle', text)
        self.assertIn('Assert-RetainedFile $retainedPython -VerifyHash', text)
        self.assertIn('Assert-RetainedFile $retainedPolicy -VerifyHash', text)
        self.assertIn('JOBFLOW_RUNTIME_INPUT_PYTHON_TRUST_INVALID', text)

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

    @unittest.skipUnless(os.name == "nt", "Windows trust-chain integration")
    def test_default_launcher_is_authenticated_before_a_redacted_verify_failure(self) -> None:
        powershell = (
            Path(os.environ["SystemRoot"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-wrapper-") as temporary:
            missing_bundle = Path(temporary) / "missing-bundle"
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(SCRIPT),
                    "-Destination",
                    str(missing_bundle),
                    "-VerifyOnly",
                ],
                cwd=PROJECT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn('"reason":"RUNTIME_INPUT_BUNDLE_INVALID"', completed.stdout)
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_INPUT_OPERATION_FAILED")
        self.assertNotIn(str(PROJECT), completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
