from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import PROJECT
from jobops.desktop_update import launch_installed_update, update_availability
from jobops.errors import JobOpsError


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        base = PROJECT / "tests" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="desktop-update-", dir=base))
        self.local_app_data = self.root / "LocalAppData"
        self.local_root = self.local_app_data / "JobOps"
        self.versions = self.local_root / "Application" / "versions"
        self.active = self.versions / "v0.4.1-synthetic"
        self.active.mkdir(parents=True)
        (self.active / ".jobops-root").write_text("jobflow\n", encoding="ascii")
        (self.local_root / "Update JobFlow.cmd").write_text("@echo off\r\n", encoding="ascii")
        (self.local_root / "current.json").write_text(json.dumps({
            "schema_version": 1,
            "version_directory": self.active.name,
            "version": "0.4.1",
            "source_sha256": "a" * 64,
        }), encoding="utf-8")
        self.environ = {"LOCALAPPDATA": str(self.local_app_data)}

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fixed_install_is_available_without_disclosing_paths(self) -> None:
        result = update_availability(self.active, environ=self.environ)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertTrue(result["available"])
        self.assertFalse(result["automatic_check"])
        self.assertFalse(result["automatic_install"])
        self.assertNotIn(str(self.root), json.dumps(result))

        source_result = update_availability(PROJECT, environ=self.environ)
        self.assertEqual(source_result["status"], "INSTALL_REQUIRED")
        self.assertFalse(source_result["available"])

    def test_launch_requires_explicit_confirmation_and_uses_only_fixed_launcher(self) -> None:
        with self.assertRaises(JobOpsError) as unconfirmed:
            launch_installed_update(self.active, user_confirmed=False, environ=self.environ)
        self.assertEqual(unconfirmed.exception.code, "EXPLICIT_CONFIRMATION_REQUIRED")

        launched: list[str] = []
        result = launch_installed_update(
            self.active,
            user_confirmed=True,
            environ=self.environ,
            shell_launcher=launched.append,
        )
        self.assertEqual(result["status"], "JOBFLOW_UPDATE_WINDOW_OPENED")
        self.assertEqual(launched, [str(self.local_root / "Update JobFlow.cmd")])
        self.assertFalse(result["automatic_update"])
        self.assertEqual(result["real_recruitment_actions"], 0)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_invalid_pointer_and_reparse_install_fail_closed(self) -> None:
        (self.local_root / "current.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(JobOpsError) as pointer:
            update_availability(self.active, environ=self.environ)
        self.assertEqual(pointer.exception.code, "JOBFLOW_UPDATE_POINTER_INVALID")

        with mock.patch("jobops.desktop_update.has_reparse_component", return_value=True):
            with self.assertRaises(JobOpsError) as reparse:
                update_availability(self.active, environ=self.environ)
        self.assertEqual(reparse.exception.code, "JOBFLOW_UPDATE_INSTALL_UNTRUSTED")


if __name__ == "__main__":
    unittest.main()
