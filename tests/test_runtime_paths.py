from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import PROJECT
from jobops.cli import _database, _project_input
from jobops.db import JobOpsDB
from jobops.errors import SecurityBoundaryError
from jobops.onboarding_center import OnboardingCenterService
from jobops.private_onboarding import PrivateOnboarding
from jobops.runtime_paths import (
    RUNTIME_DATA_ENV,
    RUNTIME_DATA_MARKER,
    RUNTIME_DATA_MARKER_VALUE,
    runtime_data_root,
    runtime_path,
    runtime_relative_path,
)
from jobops.secure_store import WindowsDPAPIStore


def _marked_data_root(parent: Path) -> Path:
    root = parent / "Data"
    root.mkdir()
    (root / RUNTIME_DATA_MARKER).write_text(
        json.dumps(RUNTIME_DATA_MARKER_VALUE),
        encoding="utf-8",
    )
    return root


class RuntimePathTests(unittest.TestCase):
    def test_source_checkout_keeps_project_local_runtime_layout(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RUNTIME_DATA_ENV, None)
            self.assertEqual(runtime_data_root(PROJECT), PROJECT.resolve())
            self.assertEqual(
                runtime_path(PROJECT, "state", "jobops.db", operation="write"),
                PROJECT / "state" / "jobops.db",
            )

    def test_marked_installed_data_root_is_separate_and_paths_are_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-") as raw:
            root = _marked_data_root(Path(raw))
            with mock.patch.dict(os.environ, {RUNTIME_DATA_ENV: str(root)}):
                state = runtime_path(PROJECT, "state", "jobops.db", operation="write")
                snapshot = runtime_path(
                    PROJECT,
                    "workspace",
                    "jobs",
                    "JOB-SYNTHETIC",
                    "raw",
                    "jd.txt",
                    operation="write",
                )
                self.assertEqual(state, root / "state" / "jobops.db")
                self.assertEqual(
                    runtime_relative_path(PROJECT, snapshot),
                    "workspace/jobs/JOB-SYNTHETIC/raw/jd.txt",
                )
                self.assertNotIn(str(root), runtime_relative_path(PROJECT, snapshot))

    def test_unmarked_relative_and_project_overlapping_roots_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-") as raw:
            unmarked = Path(raw) / "Data"
            unmarked.mkdir()
            cases = (
                ("relative-data", "RUNTIME_DATA_ROOT_INVALID"),
                (str(unmarked), "RUNTIME_DATA_MARKER_MISSING"),
                (str(PROJECT), "RUNTIME_DATA_ROOT_NOT_SEPARATE"),
            )
            for value, code in cases:
                with self.subTest(code=code), mock.patch.dict(os.environ, {RUNTIME_DATA_ENV: value}):
                    with self.assertRaises(SecurityBoundaryError) as raised:
                        runtime_data_root(PROJECT)
                    self.assertEqual(raised.exception.code, code)

    def test_reparse_root_and_runtime_parent_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-") as raw:
            root = _marked_data_root(Path(raw))
            with mock.patch.dict(os.environ, {RUNTIME_DATA_ENV: str(root)}):
                with mock.patch("jobops.runtime_paths.has_reparse_component", return_value=True):
                    with self.assertRaises(SecurityBoundaryError) as raised:
                        runtime_data_root(PROJECT)
                    self.assertEqual(raised.exception.code, "RUNTIME_DATA_ROOT_UNTRUSTED")

                def candidate_only(path: Path, stop_at: Path | None = None) -> bool:
                    return stop_at is not None and Path(path).name == "state"

                (root / "state").mkdir()
                with mock.patch(
                    "jobops.runtime_paths.has_reparse_component",
                    side_effect=candidate_only,
                ):
                    with self.assertRaises(SecurityBoundaryError) as raised:
                        runtime_path(PROJECT, "state", "jobops.db", operation="write")
                    self.assertEqual(raised.exception.code, "REPARSE_POINT_DISALLOWED")

    def test_database_and_onboarding_index_use_installed_data_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-") as raw:
            temporary = Path(raw)
            root = _marked_data_root(temporary)
            local_app_data = temporary / "LocalAppData"
            local_app_data.mkdir()
            script = (
                PROJECT
                / ".agents"
                / "skills"
                / "job-application-operator"
                / "scripts"
                / "secure-store.ps1"
            )
            with mock.patch.dict(os.environ, {RUNTIME_DATA_ENV: str(root)}):
                database = _database(PROJECT)
                self.assertEqual(database.path, root / "state" / "jobops.db")
                onboarding = PrivateOnboarding(
                    JobOpsDB(database.path),
                    WindowsDPAPIStore(script, local_app_data=local_app_data),
                )
                service = OnboardingCenterService(PROJECT, database, onboarding)
                try:
                    self.assertEqual(
                        service.index_path,
                        root / "state" / "onboarding-center-index.json",
                    )
                finally:
                    service.close()

    def test_cli_runtime_area_inputs_and_outputs_follow_installed_data_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-") as raw:
            root = _marked_data_root(Path(raw))
            (root / "workspace").mkdir()
            source = root / "workspace" / "selected-job.html"
            source.write_text("<html>synthetic</html>", encoding="utf-8")
            with mock.patch.dict(os.environ, {RUNTIME_DATA_ENV: str(root)}):
                self.assertEqual(
                    _project_input(PROJECT, Path("workspace/selected-job.html")),
                    source,
                )
                self.assertEqual(
                    _project_input(PROJECT, source),
                    source,
                )
                self.assertEqual(
                    _project_input(
                        PROJECT,
                        Path("reports/diagnostics.json"),
                        operation="write",
                    ),
                    root / "reports" / "diagnostics.json",
                )
                self.assertEqual(
                    _project_input(PROJECT, Path("config/policy.json")),
                    PROJECT / "config" / "policy.json",
                )


if __name__ == "__main__":
    unittest.main()
