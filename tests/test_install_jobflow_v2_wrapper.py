import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "Install JobFlow.cmd"
CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"


FIXTURE_INSTALLER = r'''[CmdletBinding()]
param([switch]$NoLaunch)
$ErrorActionPreference = "Stop"
[IO.File]::WriteAllText(
    $env:JOBFLOW_WRAPPER_MARKER,
    ("NoLaunch=" + [bool]$NoLaunch),
    [Text.UTF8Encoding]::new($false)
)
if (-not [string]::IsNullOrWhiteSpace($env:JOBFLOW_WRAPPER_ERROR)) {
    [Console]::Error.WriteLine($env:JOBFLOW_WRAPPER_ERROR)
}
exit [int]$env:JOBFLOW_WRAPPER_EXIT
'''


class ThinV2InstallerWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CMD.exists():
            raise unittest.SkipTest("Windows cmd.exe is required")
        cls.source = WRAPPER.read_text(encoding="utf-8")

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.root = Path(tempfile.gettempdir()) / f"JobFlow v2 包含空格 {suffix}"
        self.scripts = self.root / "scripts"
        self.local_app_data = self.root / "Local App Data"
        self.marker = self.root / "PowerShell argument marker.txt"
        self.scripts.mkdir(parents=True)
        self.local_app_data.mkdir(parents=True)
        shutil.copy2(WRAPPER, self.root / WRAPPER.name)
        (self.scripts / "install-jobflow-v2.ps1").write_text(
            FIXTURE_INSTALLER, encoding="utf-8-sig"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, *arguments: str, exit_code: int, error: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "LOCALAPPDATA": str(self.local_app_data),
                "JOBFLOW_WRAPPER_MARKER": str(self.marker),
                "JOBFLOW_WRAPPER_EXIT": str(exit_code),
                "JOBFLOW_WRAPPER_ERROR": error,
            }
        )
        driver = self.root / "invoke-wrapper.cmd"
        forwarded = " " + " ".join(arguments) if arguments else ""
        driver.write_text(
            '@echo off\ncall "%~dp0Install JobFlow.cmd"' + forwarded + "\nexit /b %ERRORLEVEL%\n",
            encoding="ascii",
        )
        return subprocess.run(
            [str(CMD), "/d", "/c", driver.name],
            cwd=self.root,
            env=env,
            input="\n",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_static_wrapper_is_thin_and_never_calls_legacy_installer(self) -> None:
        lower = self.source.casefold()
        self.assertIn(r'"%systemroot%\system32\windowspowershell\v1.0\powershell.exe"', lower)
        self.assertIn(r'-file "%~dp0scripts\install-jobflow-v2.ps1"', lower)
        self.assertNotIn(r'scripts\install-jobflow.ps1', lower)
        self.assertIn("-noninteractive", lower)
        self.assertIn('set "jobflow_install_exit=%errorlevel%"', lower)
        self.assertIn("exit /b %jobflow_install_exit%", lower)
        self.assertIn("pause", lower)
        self.assertIn("signed schema-v2 complete-runtime release", lower)
        self.assertIn(r'%localappdata%\jobops\bin\start-installed-jobflow.ps1', lower)

    def test_unicode_space_path_and_no_launch_argument_reach_powershell(self) -> None:
        result = self._run("-NoLaunch", exit_code=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "NoLaunch=True")
        self.assertIn("Automatic launch was skipped", result.stdout)

    def test_schema_v1_fail_closed_code_and_message_are_preserved(self) -> None:
        code = "JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED"
        result = self._run("-NoLaunch", exit_code=2, error=code)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(code, result.stderr)
        self.assertIn("signed schema-v2 complete-runtime release", result.stdout)
        self.assertIn("Nothing was activated", result.stdout)

    def test_unknown_argument_is_rejected_before_powershell(self) -> None:
        result = self._run("-Unexpected", exit_code=0)
        self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn("accepts only the optional -NoLaunch argument", result.stdout)


if __name__ == "__main__":
    unittest.main()
