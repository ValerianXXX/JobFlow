from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

from _support import PROJECT


class ReleasePackagingTests(unittest.TestCase):
    def test_package_metadata_is_public_release_ready(self) -> None:
        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(metadata["name"], "jobflow-local")
        self.assertEqual(metadata["license"], "MIT")
        self.assertEqual(metadata["license-files"], ["LICENSE"])
        self.assertEqual(metadata["requires-python"], ">=3.11,<3.14")
        self.assertIn("Programming Language :: Python :: 3.13", metadata["classifiers"])
        self.assertNotIn("License :: OSI Approved :: MIT License", metadata["classifiers"])
        self.assertEqual(metadata["scripts"], {"jobflow": "jobops.cli:main", "jobops": "jobops.cli:main"})

    def test_source_manifest_excludes_runtime_and_private_artifacts(self) -> None:
        manifest = (PROJECT / "MANIFEST.in").read_text(encoding="utf-8")
        for required in ("LICENSE", "README.md", "CHANGELOG.md", ".agents/skills/job-application-operator", "config", "docs", "schemas", "scripts"):
            self.assertIn(required, manifest)
        for private_root in ("reports", "state", "workspace"):
            self.assertIn(f"prune {private_root}", manifest)
        for forbidden in ("*.db", "*.sqlite", "*.dpapi", "*.zip"):
            self.assertIn(forbidden, manifest)

    def test_ci_fetches_full_history_before_public_scan_and_builds_without_publish(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('".cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\native\\git"', workflow)
        self.assertIn('$gitPath = Join-Path $gitRoot "mingw64\\bin\\git.exe"', workflow)
        self.assertIn("MinGit-2.53.0.3-64-bit.zip", workflow)
        self.assertIn("JOBFLOW_CI_GIT_ARCHIVE_HASH_MISMATCH", workflow)
        self.assertIn("JOBFLOW_CI_GIT_EXECUTABLE_HASH_MISMATCH", workflow)
        self.assertIn("JOBFLOW_CI_GIT_UNTRUSTED", workflow)
        self.assertIn("JOBFLOW_RELEASE_GIT_PATH=$gitPath", workflow)
        self.assertIn(
            "python -m jobops.public_release --git-path $env:JOBFLOW_RELEASE_GIT_PATH",
            workflow,
        )
        self.assertIn(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
            workflow,
        )
        self.assertIn(
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
            workflow,
        )
        self.assertIn(
            "python -m jobops.release_candidate --git-path $env:JOBFLOW_RELEASE_GIT_PATH",
            workflow,
        )
        self.assertNotIn("run: python -m jobops.release_candidate\n", workflow)
        self.assertIn("python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("upload-release-asset", workflow)

    def test_ci_exercises_the_bootstrap_first_installer_contract(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        discovery = "run: python -m unittest discover -s tests -v"
        self.assertEqual(workflow.count(discovery), 1)
        self.assertNotIn("JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY", workflow)
        self.assertNotIn("JOBFLOW_INSTALL_ACCEPTANCE_ENCRYPT_DIAGNOSTIC", workflow)
        for name in ("test_install_jobflow_v2.py", "test_install_jobflow_v2_local_archive.py"):
            self.assertTrue((PROJECT / "tests" / name).is_file())
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', workflow)

    def test_ci_prepares_signed_launchers_and_javascript_before_python_tests(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        regression = workflow.index("- name: Run regression suite")
        self.assertLess(workflow.index("run: npm ci"), regression)
        self.assertLess(workflow.index("-m venv .venv"), regression)
        self.assertIn("Get-AuthenticodeSignature -LiteralPath $env:LEGACY_TEST_PYTHON", workflow)
        self.assertIn("JOBFLOW_CI_LEGACY_PYTHON_UNTRUSTED", workflow)
        self.assertIn("Programs\\Python", workflow)
        self.assertIn("Path(tempfile.gettempdir()).resolve(strict=True)", workflow)
        self.assertIn('"TEMP=$canonicalTemp"', workflow)
        self.assertIn('"TMP=$canonicalTemp"', workflow)

    def test_ci_stages_exact_signed_node_at_the_existing_release_location(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        regression = workflow.index("- name: Run regression suite")
        self.assertLess(workflow.index("- name: Provision the pinned signed Node test prerequisite"), regression)
        self.assertIn('node-version: "24.19.0"', workflow)
        self.assertIn('".cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node"', workflow)
        self.assertIn('if (Test-Path -LiteralPath $nodeRoot)', workflow)
        self.assertIn("JOBFLOW_CI_NODE_DESTINATION_EXISTS", workflow)
        self.assertIn("3602F2BB1A10F2CBAB4C36886218A33C1AB3DB87290E73B033C46C77147D0237", workflow)
        self.assertIn("Get-FileHash -LiteralPath $nodeSource -Algorithm SHA256", workflow)
        self.assertIn("Get-AuthenticodeSignature -LiteralPath $nodeSource", workflow)
        self.assertIn("$policy.tools.node.allowed_signers", workflow)
        self.assertIn("JOBFLOW_CI_NODE_UNTRUSTED", workflow)
        self.assertIn('Copy-Item -LiteralPath $nodeSource -Destination $nodePath', workflow)
        self.assertIn("Get-FileHash -LiteralPath $nodePath -Algorithm SHA256", workflow)
        self.assertIn("JOBFLOW_CI_NODE_STAGED_HASH_MISMATCH", workflow)
        builder = (PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_text(encoding="utf-8")
        self.assertIn('Assert-NoReparsePath $absolute "JOBFLOW_RELEASE_NODE_NOT_TRUSTED"', builder)
        self.assertIn('Enter-AuthenticatedToolLock "node" $nodeApplication', builder)

    def test_ci_runs_every_checked_in_javascript_e2e_suite(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn(
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
            workflow,
        )
        self.assertIn("npm ci", workflow)
        self.assertIn("npx playwright install chromium", workflow)
        self.assertIn("npm run test:e2e", workflow)
        package = json.loads((PROJECT / "package.json").read_text(encoding="utf-8"))
        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(package["version"], metadata["version"])
        self.assertEqual(package["devDependencies"]["playwright"], "1.62.1")
        lock = json.loads((PROJECT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(lock["packages"][""]["devDependencies"], package["devDependencies"])
        runner = (PROJECT / "scripts" / "run-javascript-e2e.cjs").read_text(encoding="utf-8")
        self.assertIn('name.endsWith("e2e.cjs")', runner)
        self.assertIn("spawnSync(process.execPath", runner)
        self.assertIn("JOBFLOW_JAVASCRIPT_E2E_PASS", runner)


@unittest.skipUnless(os.name == "nt", "Windows CI prerequisite staging")
class WindowsCiNodeStagingTests(unittest.TestCase):
    def _run_staging(self, root: Path, source: Path) -> subprocess.CompletedProcess[str]:
        from jobops.release_toolchain import windows_system_directory

        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        section = workflow.split("- name: Provision the pinned signed Node test prerequisite", 1)[1]
        section = section.split("- name: Install pinned Browser Companion test runtime", 1)[0]
        block = textwrap.dedent(section.split("run: |\n", 1)[1]).rstrip()
        # Override only the test input and destination, not hashes, signature
        # policy, failure checks, copy operation, or staged-byte verification.
        lines = block.splitlines()
        for variable, replacement in (("$nodeRoot", "$args[0]"), ("$nodeSource", "$args[1]")):
            matches = [index for index, line in enumerate(lines) if line.startswith(variable + " =")]
            self.assertEqual(len(matches), 1)
            lines[matches[0]] = f"{variable} = {replacement}"
        harness = root / "ci-node-stage.ps1"
        harness.write_text("$ErrorActionPreference = 'Stop'\n" + "\n".join(lines) + "\n", encoding="utf-8")
        powershell = windows_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), str(root / "staged-node"), str(source)],
            cwd=PROJECT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def test_ci_node_staging_checks_exact_bytes_and_refuses_existing_destination(self) -> None:
        candidates = [
            Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe",
            Path(shutil.which("node") or "__missing_ci_node__"),
        ]
        source = next((path for path in candidates if path.is_file()), None)
        self.assertIsNotNone(source, "Install the checksum-pinned signed Node test prerequisite")
        with tempfile.TemporaryDirectory(prefix="jobflow-ci-node-") as temporary:
            root = Path(temporary).resolve()
            result = self._run_staging(root, source)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged = root / "staged-node" / "bin" / "node.exe"
            expected = "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), expected)
            repeated = self._run_staging(root, source)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("JOBFLOW_CI_NODE_DESTINATION_EXISTS", repeated.stderr)
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), expected)

    def test_ci_node_staging_rejects_modified_input_before_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-ci-node-") as temporary:
            root = Path(temporary).resolve()
            result = self._run_staging(root, PROJECT / "package.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JOBFLOW_CI_NODE_EXECUTABLE_HASH_MISMATCH", result.stderr)
            self.assertFalse((root / "staged-node").exists())


if __name__ == "__main__":
    unittest.main()
