from __future__ import annotations

import tomllib
import unittest

from _support import PROJECT


class ReleasePackagingTests(unittest.TestCase):
    def test_package_metadata_is_public_release_ready(self) -> None:
        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(metadata["name"], "jobflow-local")
        self.assertEqual(metadata["license"], "MIT")
        self.assertEqual(metadata["license-files"], ["LICENSE"])
        self.assertEqual(metadata["requires-python"], ">=3.11")
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
        self.assertIn("python -m jobops.public_release", workflow)
        self.assertIn("python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("upload-release-asset", workflow)

    def test_ci_exercises_the_isolated_fixed_install_instead_of_the_source_tree(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        installer_step = workflow.split("- name: Exercise first-time Windows installer", 1)[1].split(
            "- name: Run regression suite", 1
        )[0]
        self.assertIn("jobflow-fixed-install-qa-", installer_step)
        self.assertIn('.Substring(0, 8)', installer_step)
        self.assertIn('$env:JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY = "1"', installer_step)
        self.assertIn("./scripts/install-jobflow.ps1 -NoLaunch", installer_step)
        self.assertIn('Join-Path $installedRoot "current.json"', installer_step)
        self.assertIn('Join-Path $installedRoot "bin\\check-installed-jobflow.ps1"', installer_step)
        self.assertIn('$health.version -ne $pointer.version', installer_step)
        self.assertIn('[IO.Directory]::Delete("\\\\?\\" + $acceptanceRoot, $true)', installer_step)
        self.assertNotIn("./scripts/check-jobflow.ps1 -Json", installer_step)


if __name__ == "__main__":
    unittest.main()
