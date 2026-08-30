from __future__ import annotations

import json
import tomllib
import unittest

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
        self.assertIn('"Git\\mingw64\\bin\\git.exe"', workflow)
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
        installer_step = workflow.split("- name: Exercise bootstrap-first Windows installer", 1)[1].split(
            "- name: Run regression suite", 1
        )[0]
        self.assertIn('test_install_jobflow_v2*.py', installer_step)
        self.assertIn("python -m unittest discover", installer_step)
        self.assertNotIn("install-jobflow.ps1", installer_step)
        self.assertNotIn("JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY", installer_step)
        self.assertNotIn("jobflow-fixed-install-qa-", installer_step)

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


if __name__ == "__main__":
    unittest.main()
