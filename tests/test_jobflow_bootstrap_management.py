from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import test_jobflow_bootstrap_activation as activation_tests
from tests import test_jobflow_bootstrap_activation_journal as journal_tests
from tests.test_jobflow_bootstrap_trust import (
    POWERSHELL,
    PROJECT,
    SCRIPT,
)


class JobFlowBootstrapManagementContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-management-")
        self.root = Path(self.temporary.name)
        self.local_app_data = self.root / "LocalAppData"
        self.local_app_data.mkdir()
        source = SCRIPT.read_text(encoding="utf-8")
        known_folder = "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
        self.assertEqual(source.count(known_folder), 1)
        source = source.replace(
            known_folder,
            "'" + str(self.local_app_data).replace("'", "''") + "'",
            1,
        )
        self.script = self.root / "jobflow-bootstrap.ps1"
        self.script.write_text(source, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *arguments: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.local_app_data)
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                *arguments,
            ],
            cwd=PROJECT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def _assert_contract_rejected(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "JOBFLOW_BOOTSTRAP_FAILED\n")
        self.assertNotIn(str(self.root), completed.stdout + completed.stderr)
        self.assertFalse((self.local_app_data / "JobOps").exists())

    def test_legacy_describe_is_byte_exact_alias_for_explicit_describe(self) -> None:
        fixture = activation_tests.JobFlowBootstrapActivationTests(methodName="runTest")
        fixture.setUp()
        try:
            release = fixture._release("1.0.0")
            base = (
                "-ManifestPath",
                str(release["manifest"]),
                "-SignaturePath",
                str(release["signature"]),
            )
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(fixture.local_app_data)

            def run(*extra: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-File", str(fixture.script), *base, *extra,
                    ],
                    cwd=PROJECT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )

            legacy = run()
            explicit = run("-DescribeManifest")
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertEqual(legacy.stdout, explicit.stdout)
            self.assertEqual(legacy.stderr, explicit.stderr)
            self.assertEqual(
                set(json.loads(legacy.stdout.lstrip("\ufeff"))),
                {
                    "schema_version",
                    "status",
                    "signature_verified",
                    "key_id",
                    "manifest_schema_version",
                    "publisher_attestation_bound",
                    "manifest_sha256",
                    "manifest_bytes",
                    "real_external_actions",
                },
            )
            self.assertFalse(fixture.install.exists())
        finally:
            fixture.tearDown()

    def test_all_missing_partial_ambiguous_and_mixed_modes_reject_before_layout_touch(self) -> None:
        missing = self.root / "must-not-be-opened"
        describe = ("-ManifestPath", str(missing), "-SignaturePath", str(missing))
        cases = (
            (),
            ("-DescribeManifest",),
            ("-ManifestPath", str(missing)),
            ("-SignaturePath", str(missing)),
            ("-ManifestPath", "", "-SignaturePath", str(missing)),
            ("-ManifestPath", str(missing), "-SignaturePath", ""),
            ("-ManifestPath", str(missing), "-SignaturePath", str(missing), "-ArchivePath", ""),
            (*describe, "-ArchivePath", str(missing)),
            ("-ExpandArchive", *describe),
            ("-Activate", *describe),
            ("-RecoverOnly", "-ManifestPath", str(missing)),
            ("-RecoverOnly", "-SignaturePath", str(missing)),
            ("-RecoverOnly", "-ArchivePath", str(missing)),
            ("-RecoverOnly", "-ManifestPath", ""),
            ("-RecoverOnly", "-SignaturePath", ""),
            ("-RecoverOnly", "-ArchivePath", ""),
            ("-VerifyInstalled", "-ManifestPath", str(missing)),
            ("-VerifyInstalled", "-SignaturePath", str(missing)),
            ("-VerifyInstalled", "-ArchivePath", str(missing)),
            ("-VerifyInstalled", "-ManifestPath", ""),
            ("-VerifyInstalled", "-SignaturePath", ""),
            ("-VerifyInstalled", "-ArchivePath", ""),
            (*describe, "-DescribeManifest", "-ExpandArchive", "-ArchivePath", str(missing)),
            (*describe, "-ExpandArchive", "-Activate", "-ArchivePath", str(missing)),
            ("-RecoverOnly", "-DescribeManifest"),
            ("-VerifyInstalled", "-RecoverOnly"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self._assert_contract_rejected(self._run(*arguments))

    def test_recover_only_without_installation_is_redacted_no_pending_and_creates_nothing(self) -> None:
        completed = self._run("-RecoverOnly")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        expected = {
            "schema_version": 1,
            "status": "JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING",
            "recovery_performed": False,
            "activation_committed": False,
            "retry_required": False,
            "real_external_actions": 0,
        }
        self.assertEqual(json.loads(completed.stdout.lstrip("\ufeff")), expected)
        self.assertEqual(completed.stderr, "")
        self.assertNotIn(str(self.root), completed.stdout)
        self.assertFalse((self.local_app_data / "JobOps").exists())

    def test_static_management_contract_and_lock_order(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        param = source.split(")\n\n$ErrorActionPreference", 1)[0]
        self.assertNotIn("Mandatory = $true", param)
        for switch in ("DescribeManifest", "RecoverOnly", "VerifyInstalled", "ExpandArchive", "Activate"):
            self.assertIn(f"[switch]${switch}", param)
        resolver = source.split("$selectedMode = $null", 1)[1].split("trap {", 1)[0]
        self.assertIn('$selectedMode = "DescribeManifest"', resolver)
        self.assertIn('$selectedMode = "RecoverOnly"', resolver)
        self.assertIn('$selectedMode = "VerifyInstalled"', resolver)
        recover = source.split("function Invoke-RecoverOnlyManagement", 1)[1].split(
            "function Invoke-VerifyInstalledManagement", 1
        )[0]
        self.assertLess(
            recover.index("Enter-ExistingBootstrapOperationLock"),
            recover.index("Enter-ExistingActivationMaintenanceLock"),
        )
        for forbidden in (
            "Expand-AndVerifySignedArchive",
            "Invoke-WebRequest",
            "Invoke-RestMethod",
            "Initialize-OrValidateDataRoot",
            "Initialize-ActivationLayout",
        ):
            self.assertNotIn(forbidden, recover)

        verify = source.split("function Invoke-VerifyInstalledManagement", 1)[1].split(
            "function Activate-VerifiedRuntime", 1
        )[0]
        self.assertLess(
            verify.index("Enter-ExistingBootstrapOperationLock"),
            verify.index("Enter-ExistingActivationMaintenanceLock"),
        )
        self.assertLess(
            verify.index("Recover-PendingActivation"),
            verify.index("Read-InstalledPointer"),
        )
        self.assertIn("Assert-ActivationTrustEvidenceForPointer", verify)
        for forbidden in (
            "Expand-AndVerifySignedArchive",
            "Invoke-WebRequest",
            "Invoke-RestMethod",
            "Initialize-OrValidateDataRoot",
            "Initialize-ActivationLayout",
        ):
            self.assertNotIn(forbidden, verify)


class JobFlowBootstrapRecoverOnlyIntegrationTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def _fixture() -> activation_tests.JobFlowBootstrapActivationTests:
        fixture = activation_tests.JobFlowBootstrapActivationTests(methodName="runTest")
        fixture.setUp()
        return fixture

    @staticmethod
    def _run_recover_only(
        fixture: activation_tests.JobFlowBootstrapActivationTests,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(fixture.local_app_data)
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(fixture.script),
                "-RecoverOnly",
            ],
            cwd=PROJECT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )

    def test_no_pending_preserves_complete_data_tree_byte_for_byte(self) -> None:
        fixture = self._fixture()
        try:
            release = fixture._release("1.0.0")
            self.assertEqual(fixture._run(release).returncode, 0)
            user_file = fixture.install / "Data" / "state" / "candidate.db"
            user_file.write_bytes(b"user-private-state-must-not-change")
            before = fixture._tree_snapshot(fixture.install / "Data")

            completed = self._run_recover_only(fixture)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout.lstrip("\ufeff"))["status"],
                "JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING",
            )
            self.assertEqual(fixture._tree_snapshot(fixture.install / "Data"), before)
            self.assertNotIn(str(fixture.root), completed.stdout + completed.stderr)
        finally:
            fixture.tearDown()

    def test_pending_activation_is_recovered_once_and_returns_retry_required_exit_6(self) -> None:
        fixture = self._fixture()
        journal = journal_tests.JobFlowBootstrapActivationJournalTests(methodName="runTest")
        try:
            release = fixture._release("1.0.0")
            crashed = fixture._run(
                release,
                script=journal._crash_script(
                    fixture,
                    "JOBFLOW_ACTIVATION_CANDIDATE_TARGET_READY_BOUNDARY",
                ),
            )
            self.assertNotEqual(crashed.returncode, 0)
            user_file = fixture.install / "Data" / "state" / "candidate.db"
            user_file.write_bytes(b"preserve-user-private-state")

            completed = self._run_recover_only(fixture)
            self.assertEqual(completed.returncode, 6, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(
                json.loads(completed.stdout.lstrip("\ufeff")),
                {
                    "schema_version": 1,
                    "status": "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED",
                    "recovery_performed": True,
                    "activation_committed": False,
                    "retry_required": True,
                    "real_external_actions": 0,
                },
            )
            self.assertEqual(user_file.read_bytes(), b"preserve-user-private-state")
            self.assertFalse(any(path.exists() for path in journal._journal_paths(fixture)))
            self.assertFalse((fixture.install / "current.json").exists())
            self.assertNotIn(str(fixture.root), completed.stdout)

            second = self._run_recover_only(fixture)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                json.loads(second.stdout.lstrip("\ufeff"))["status"],
                "JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING",
            )
        finally:
            fixture.tearDown()

    def test_corrupt_pending_state_fails_redacted_with_exit_3_and_preserves_evidence(self) -> None:
        fixture = self._fixture()
        journal = journal_tests.JobFlowBootstrapActivationJournalTests(methodName="runTest")
        try:
            release = fixture._release("1.0.0")
            crashed = fixture._run(
                release,
                script=journal._crash_script(fixture, "JOBFLOW_ACTIVATION_PREPARED_BOUNDARY"),
            )
            self.assertNotEqual(crashed.returncode, 0)
            main, backup = journal._journal_paths(fixture)
            backup.unlink()
            main_before = main.read_bytes()
            user_file = fixture.install / "Data" / "state" / "candidate.db"
            user_file.write_bytes(b"preserve-on-recovery-failure")

            completed = self._run_recover_only(fixture)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "JOBFLOW_ACTIVATION_RECOVERY_FAILED\n")
            self.assertEqual(main.read_bytes(), main_before)
            self.assertFalse(backup.exists())
            self.assertEqual(user_file.read_bytes(), b"preserve-on-recovery-failure")
            self.assertNotIn(str(fixture.root), completed.stdout + completed.stderr)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
