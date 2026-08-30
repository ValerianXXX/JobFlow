from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "run-release-verification.ps1"
TRUST_CONFIG = PROJECT / "config" / "release-toolchain.json"
SIGNED_PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
PYVENV_CONFIG = PROJECT / ".venv" / "pyvenv.cfg"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


class ReleaseVerificationPowerShellTests(unittest.TestCase):
    maxDiff = None

    def _run(self, script: Path, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *arguments,
            ],
            cwd=script.parents[1],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    def _run_after_systemroot_override(
        self,
        script: Path,
        fake_systemroot: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        quoted_script = str(script).replace("'", "''")
        quoted_root = str(fake_systemroot).replace("'", "''")
        quoted_arguments = " ".join(
            value if value.startswith("-") else "'" + value.replace("'", "''") + "'"
            for value in arguments
        )
        command = f"$env:SystemRoot='{quoted_root}'; & '{quoted_script}' {quoted_arguments}"
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=script.parents[1],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    def _harness(self, root: Path) -> tuple[Path, Path, Path, Path]:
        (root / "scripts").mkdir(parents=True)
        (root / "config").mkdir()
        (root / "src" / "jobops").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / ".venv" / "Scripts").mkdir(parents=True)
        site_packages = root / ".venv" / "Lib" / "site-packages"
        site_packages.mkdir(parents=True)
        (root / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
        shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
        shutil.copy2(TRUST_CONFIG, root / "config" / TRUST_CONFIG.name)
        shutil.copy2(SIGNED_PYTHON, root / ".venv" / "Scripts" / "python.exe")
        shutil.copy2(PYVENV_CONFIG, root / ".venv" / "pyvenv.cfg")
        (root / "src" / "jobops" / "__init__.py").write_text("", encoding="utf-8")
        node = root / "node.exe"
        git = root / "git.exe"
        node.write_bytes(b"test-only node placeholder")
        git.write_bytes(b"test-only git placeholder")
        marker = root / "sitecustomize-executed.txt"
        (site_packages / "sitecustomize.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
            encoding="utf-8",
        )
        module = textwrap.dedent(
            """
            import json
            import os
            import sys

            if sys.flags.isolated != 1 or sys.flags.no_site != 1 or sys.flags.safe_path != 1:
                raise SystemExit("INTERPRETER_NOT_ISOLATED")
            if any(name.upper().startswith("PYTHON") for name in os.environ):
                raise SystemExit("PYTHON_ENVIRONMENT_LEAKED")
            if "JOBFLOW_ATTACK_MARKER" in os.environ or "NODE_OPTIONS" in os.environ:
                raise SystemExit("CALLER_ENVIRONMENT_LEAKED")
            print(json.dumps({
                "status": "RELEASE_VERIFICATION_RECORDED",
                "source_commit": "a" * 40,
                "real_external_actions": 0,
            }, sort_keys=True))
            """
        ).strip()
        (root / "src" / "jobops" / "release_verification.py").write_text(module + "\n", encoding="utf-8")
        return root / "scripts" / SCRIPT.name, node, git, marker

    def test_python_selection_is_bounded_and_certificate_pinned(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        policy = json.loads(TRUST_CONFIG.read_text(encoding="utf-8"))["tools"]["python"]
        self.assertNotIn("[string]$PythonPath", script)
        self.assertNotIn('Get-Command "python"', script)
        self.assertIn("$trustedSystemDirectory = [Environment]::SystemDirectory", script)
        self.assertIn("$env:SystemRoot = $trustedWindowsRoot", script)
        self.assertIn("$env:SystemDrive = $trustedSystemDrive", script)
        self.assertIn('$result["SystemDrive"] = $trustedSystemDrive', script)
        self.assertNotIn("Join-Path $env:SystemRoot", script)
        self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", script)
        self.assertIn("JOBFLOW_RELEASE_PYTHON_SIGNER_UNPINNED", script)
        self.assertIn("JOBFLOW_RELEASE_PYTHON_SIGNATURE_INVALID", script)
        self.assertNotIn("allowed_unsigned_sha256", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("JOBFLOW_RELEASE_PYTHON_CHANGED_DURING_RUN", script)
        self.assertIn("[IO.FileShare]::Read", script)
        self.assertIn("Lock = $lock", script)
        self.assertIn("function Set-ProcessEnvironment", script)
        self.assertIn("function Get-MinimalChildEnvironment", script)
        self.assertNotIn("Microsoft.PowerShell.Core\\Get-Command", script)
        self.assertEqual(policy["allowed_unsigned_sha256"], [
            "sha256:d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa"
        ])
        self.assertEqual(policy["allowed_signers"], [{
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US",
            "thumbprint": "36168EE17C1A240517388540C903BB6717DD2563",
        }])

    def test_caller_cannot_override_python_path(self) -> None:
        result = self._run(SCRIPT, "-PythonPath", r"C:\Windows\System32\cmd.exe")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("PythonPath", result.stdout)
        self.assertIn("parameter", result.stdout.lower())

    def test_unsigned_python_at_the_bounded_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "config").mkdir()
            (root / ".venv" / "Scripts").mkdir(parents=True)
            (root / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
            shutil.copy2(TRUST_CONFIG, root / "config" / TRUST_CONFIG.name)
            (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"unsigned interpreter")
            result = self._run(root / "scripts" / SCRIPT.name)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("JOBFLOW_RELEASE_PYTHON_SIGNATURE_INVALID", result.stdout)

    def test_path_shadow_python_is_never_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "config").mkdir()
            (root / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
            shutil.copy2(TRUST_CONFIG, root / "config" / TRUST_CONFIG.name)
            shadow = root / "shadow"
            shadow.mkdir()
            (shadow / "python.exe").write_bytes(b"malicious path shadow")
            env = os.environ.copy()
            env["PATH"] = str(shadow)
            result = self._run(root / "scripts" / SCRIPT.name, env=env)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("JOBFLOW_RELEASE_PYTHON_MISSING", result.stdout)
            self.assertNotIn("malicious path shadow", result.stdout)

    def test_python_environment_and_sitecustomize_are_ignored(self) -> None:
        if not SIGNED_PYTHON.is_file():
            self.skipTest("The installer-created signed project venv is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script, node, git, marker = self._harness(root)
            attack = root / "attack"
            attack.mkdir()
            (attack / "sitecustomize.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('attack', encoding='utf-8')\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update({
                "PYTHONPATH": str(attack),
                "PYTHONHOME": str(attack),
                "PYTHONUSERBASE": str(attack),
                "PYTHONSTARTUP": str(attack / "sitecustomize.py"),
                "JOBFLOW_ATTACK_MARKER": "must-not-reach-child",
                "NODE_OPTIONS": "--require=caller-controlled.js",
            })
            result = self._run(script, "-NodePath", str(node), "-GitPath", str(git), env=env)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("release verification passed", result.stdout)
            self.assertIn("SHA-256", result.stdout)
            self.assertFalse(marker.exists(), marker.read_text(encoding="utf-8") if marker.exists() else "")

    def test_fake_systemroot_cannot_change_python_identity_resolution(self) -> None:
        if not SIGNED_PYTHON.is_file():
            self.skipTest("The installer-created signed project venv is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script, node, git, marker = self._harness(root)
            fake_windows = root / "fake-windows"
            fake_windows.mkdir()
            result = self._run_after_systemroot_override(
                script,
                fake_windows,
                "-NodePath",
                str(node),
                "-GitPath",
                str(git),
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("release verification passed", result.stdout)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
