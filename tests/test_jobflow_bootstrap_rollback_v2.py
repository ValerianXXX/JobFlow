from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests import test_jobflow_bootstrap_activation as activation_tests
from tests.test_jobflow_bootstrap_trust import POWERSHELL, PROJECT, SCRIPT


class JobFlowBootstrapRollbackV2Tests(unittest.TestCase):
    """Contract tests for the pathless, v2-only bootstrap rollback.

    Rollback is a local control-plane operation.  The fixture creates two
    independently signed and activated v2 runtimes, then invokes only the
    stable bootstrap with ``-Rollback``.  No caller-supplied manifest, archive,
    path, website, account, or private value is accepted by this contract.
    """

    maxDiff = None

    def setUp(self) -> None:
        self.fixture = activation_tests.JobFlowBootstrapActivationTests(
            methodName="runTest"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @property
    def state_root(self) -> Path:
        return self.fixture.install / "Data" / "state"

    @property
    def journal(self) -> Path:
        return self.state_root / ".jobflow-rollback-transaction-v1.json"

    @property
    def journal_backup(self) -> Path:
        return self.state_root / ".jobflow-rollback-transaction-v1.backup.json"

    @property
    def completion(self) -> Path:
        return self.state_root / ".jobflow-rollback-completion-v1.json"

    @staticmethod
    def _health_source(body: str) -> bytes:
        return ("from __future__ import annotations\n" + body).encode("utf-8")

    def _run_rollback(
        self,
        *extra: str,
        script: Path | None = None,
        timeout: int = 90,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.fixture.local_app_data)
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script or self.fixture.script),
                "-Rollback",
                *extra,
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

    def _activate_pair(
        self,
        *,
        previous_health: bytes | None = None,
        current_health: bytes | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        first = self.fixture._release("1.0.0", health_source=previous_health)
        activated_first = self.fixture._run(first)
        self.assertEqual(activated_first.returncode, 0, activated_first.stderr)
        previous = self.fixture._pointer()

        second = self.fixture._release(
            "1.1.0",
            predecessor_minimum="1.0.0",
            health_source=current_health,
        )
        activated_second = self.fixture._run(second)
        self.assertEqual(activated_second.returncode, 0, activated_second.stderr)
        current = self.fixture._pointer()
        self.assertEqual(self.fixture._pointer("previous.json"), previous)
        return current, previous

    def _pointer_bytes(self) -> tuple[bytes, bytes]:
        return (
            (self.fixture.install / "current.json").read_bytes(),
            (self.fixture.install / "previous.json").read_bytes(),
        )

    def _verify_installed(self) -> dict[str, object]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.fixture.local_app_data)
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.fixture.script),
                "-VerifyInstalled",
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
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout.lstrip("\ufeff"))

    def _run_mode(
        self,
        *arguments: str,
        script: Path | None = None,
        timeout: int = 90,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.fixture.local_app_data)
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script or self.fixture.script),
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

    def _trust_directory(self, pointer: dict[str, object]) -> Path:
        return (
            self.state_root
            / "activation-trust"
            / str(pointer["version_directory"])
        )

    def _write_crash_script(self, boundary: str) -> Path:
        crash = (
            boundary
            + "\n        [Diagnostics.Process]::GetCurrentProcess().Kill(); "
            + "[Threading.Thread]::Sleep(10000)"
        )
        return self.fixture._write_script(
            "bootstrap-rollback-crash.ps1", mutation=(boundary, crash)
        )

    def test_static_contract_is_pathless_v2_only_and_orders_locks_and_states(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        parameter_block = source.split("param(", 1)[1].split("\n)", 1)[0]
        self.assertIn("[switch]$Rollback", parameter_block)
        self.assertIn("[switch]$StartNewRollback", parameter_block)

        management = source.split("function Invoke-RollbackManagement", 1)[1].split(
            "function ", 1
        )[0]
        ordered = (
            "Enter-ExistingBootstrapOperationLock",
            "Enter-ExistingActivationMaintenanceLock",
            "Enter-RollbackDiscoveryLock",
            "Recover-PendingRollback",
        )
        positions = [management.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Recover-PendingLegacyMigration", management)
        self.assertIn("Recover-PendingActivation", management)

        rollback = source.split("function Recover-PendingRollback", 1)[1].split(
            "function ", 1
        )[0]
        state_order = (
            '"PREPARED"',
            '"PRE_HEALTH_OK"',
            '"POINTER_SWITCHED"',
            '"POST_HEALTH_OK"',
            '"COMMITTED"',
        )
        positions = [rollback.index(state) for state in state_order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Assert-RollbackPointerPairTrusted", rollback)
        self.assertIn("Assert-ActivationTrustEvidenceForPointer", rollback)
        self.assertIn("Invoke-CandidateRuntimeHealth", rollback)
        self.assertNotIn("ManifestPath", management)
        self.assertNotIn("ArchivePath", management)

    def test_success_swaps_only_two_signed_v2_pointers_and_is_redacted(self) -> None:
        current, previous = self._activate_pair()
        private_marker = self.state_root / "user-private-canary.bin"
        private_marker.write_bytes(b"preserve-exactly")

        completed = self._run_rollback()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_ROLLED_BACK")
        self.assertEqual(result["version"], previous["version"])
        self.assertTrue(result["signed_activation_evidence_verified"])
        self.assertFalse(result["paths_disclosed"])
        self.assertEqual(result["real_external_actions"], 0)
        self.assertNotIn("transaction_id", result)
        self.assertNotIn(str(self.fixture.root), completed.stdout + completed.stderr)

        self.assertEqual(self.fixture._pointer(), previous)
        self.assertEqual(self.fixture._pointer("previous.json"), current)
        self.assertEqual(private_marker.read_bytes(), b"preserve-exactly")
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.journal_backup.exists())
        receipt = json.loads(self.completion.read_bytes())
        self.assertEqual(receipt["kind"], "JOBFLOW_ROLLBACK_COMPLETION")
        self.assertEqual(receipt["status"], "COMMITTED")
        self.assertEqual(receipt["restored_current"], previous)
        self.assertEqual(self._verify_installed()["version"], previous["version"])

    def test_paths_missing_previous_v1_and_tampered_evidence_fail_before_health(self) -> None:
        health_marker = self.fixture.local_app_data / "rollback-health-called"
        instrumented = self._health_source(
            "import os, sys\n"
            "from pathlib import Path\n"
            "(Path(os.environ['LOCALAPPDATA'])/'rollback-health-called').write_bytes(b'called')\n"
            "sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
        )
        current, previous = self._activate_pair(
            previous_health=instrumented, current_health=instrumented
        )
        health_marker.unlink(missing_ok=True)
        pointer_before = self._pointer_bytes()

        never_read = self.fixture.root / "never-read.json"
        for arguments in (
            ("-ManifestPath", str(never_read)),
            ("-SignaturePath", str(never_read)),
            ("-ArchivePath", str(never_read)),
        ):
            with self.subTest(arguments=arguments):
                rejected = self._run_rollback(*arguments)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(rejected.stdout, "")
                self.assertEqual(rejected.stderr, "JOBFLOW_BOOTSTRAP_FAILED\n")
                self.assertEqual(self._pointer_bytes(), pointer_before)
                self.assertFalse(health_marker.exists())

        previous_path = self.fixture.install / "previous.json"
        previous_bytes = previous_path.read_bytes()
        previous_path.unlink()
        missing = self._run_rollback()
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(health_marker.exists())
        previous_path.write_bytes(previous_bytes)

        legacy = dict(previous)
        legacy["schema_version"] = 1
        previous_path.write_bytes(self.fixture._canonical(legacy))
        v1 = self._run_rollback()
        self.assertNotEqual(v1.returncode, 0)
        self.assertFalse(health_marker.exists())
        previous_path.write_bytes(previous_bytes)

        evidence = self._trust_directory(previous) / "activation-evidence.json"
        evidence_bytes = evidence.read_bytes()
        evidence.write_bytes(evidence_bytes[:-1] + bytes((evidence_bytes[-1] ^ 1,)))
        tampered = self._run_rollback()
        self.assertNotEqual(tampered.returncode, 0)
        self.assertFalse(health_marker.exists())
        self.assertEqual(self._pointer_bytes(), pointer_before)
        evidence.write_bytes(evidence_bytes)
        self.assertEqual(self._verify_installed()["version"], current["version"])

    def test_post_health_failure_restores_original_only_after_reverification(self) -> None:
        failure_marker = self.fixture.local_app_data / "rollback-fail-post"
        target_health = self._health_source(
            "import os, sys\n"
            "from pathlib import Path\n"
            "marker=Path(os.environ['LOCALAPPDATA'])/'rollback-fail-post'\n"
            "if marker.exists() and Path(os.environ['TEMP']).name.endswith('-post'):\n"
            "    raise SystemExit(7)\n"
            "sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
        )
        current, _previous = self._activate_pair(previous_health=target_health)
        original = self._pointer_bytes()
        failure_marker.write_bytes(b"fail-post")

        failed = self._run_rollback()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self._pointer_bytes(), original)
        self.assertEqual(self._verify_installed()["version"], current["version"])
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.journal_backup.exists())

    def test_each_crash_state_forward_completes_once_then_requires_new_intent(self) -> None:
        boundaries = (
            "# JOBFLOW_ROLLBACK_PREPARED_BOUNDARY",
            "# JOBFLOW_ROLLBACK_PRE_HEALTH_OK_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_POINTER_SWITCHED_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_POST_HEALTH_OK_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_COMPLETION_RECEIPT_BOUNDARY",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                fixture = activation_tests.JobFlowBootstrapActivationTests(
                    methodName="runTest"
                )
                fixture.setUp()
                outer = self.fixture
                self.fixture = fixture
                try:
                    current, previous = self._activate_pair()
                    crashed = self._run_rollback(
                        script=self._write_crash_script(boundary)
                    )
                    self.assertNotEqual(crashed.returncode, 0)

                    recovered = self._run_rollback()
                    self.assertIn(recovered.returncode, (0, 6), recovered.stderr)
                    self.assertEqual(self.fixture._pointer(), previous)
                    self.assertEqual(self.fixture._pointer("previous.json"), current)
                    self.assertFalse(self.journal.exists())
                    self.assertFalse(self.journal_backup.exists())
                    self.assertTrue(self.completion.is_file())

                    no_reverse = self._run_rollback()
                    self.assertEqual(no_reverse.returncode, 0, no_reverse.stderr)
                    self.assertEqual(self.fixture._pointer(), previous)
                    self.assertEqual(self.fixture._pointer("previous.json"), current)

                    reversed_again = self._run_rollback("-StartNewRollback")
                    self.assertEqual(reversed_again.returncode, 0, reversed_again.stderr)
                    self.assertEqual(self.fixture._pointer(), current)
                    self.assertEqual(self.fixture._pointer("previous.json"), previous)
                finally:
                    self.fixture = outer
                    fixture.tearDown()

    def test_crash_recovery_is_a_barrier_for_launch_check_update_and_manifest_modes(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        recover_only = source.split(
            "function Invoke-RecoverOnlyManagement", 1
        )[1].split("function ", 1)[0]
        verify = source.split(
            "function Invoke-VerifyInstalledManagement", 1
        )[1].split("function ", 1)[0]
        for body in (recover_only, verify):
            ordered = (
                "Enter-ExistingBootstrapOperationLock",
                "Enter-ExistingActivationMaintenanceLock",
                "Enter-RollbackDiscoveryLock",
                "Recover-PendingRollback",
            )
            positions = [body.index(token) for token in ordered]
            self.assertEqual(positions, sorted(positions))

        launcher_expectations = {
            "start-installed-jobflow.ps1": "-VerifyInstalled",
            "check-installed-jobflow.ps1": "-VerifyInstalled",
            "run-authorized-discovery-task.ps1": "-VerifyInstalled",
            "update-installed-jobflow.ps1": '"RecoverOnly"',
        }
        runtime_scripts = PROJECT / "scripts" / "windows-runtime"
        for name, token in launcher_expectations.items():
            with self.subTest(launcher=name):
                self.assertIn(
                    token,
                    (runtime_scripts / name).read_text(encoding="utf-8-sig"),
                )

        current, previous = self._activate_pair()
        crash_script = self._write_crash_script(
            "# JOBFLOW_ROLLBACK_POINTER_SWITCHED_STATE_BOUNDARY"
        )

        crashed = self._run_rollback(script=crash_script)
        self.assertNotEqual(crashed.returncode, 0)
        verified = self._verify_installed()
        self.assertTrue(verified["recovery_performed"])
        self.assertEqual(verified["version"], previous["version"])
        self.assertEqual(self.fixture._pointer(), previous)
        self.assertFalse(self.journal.exists())

        crashed = self._run_rollback("-StartNewRollback", script=crash_script)
        self.assertNotEqual(crashed.returncode, 0)
        recovered = self._run_mode("-RecoverOnly")
        self.assertEqual(recovered.returncode, 6, recovered.stderr)
        recovery_result = json.loads(recovered.stdout.lstrip("\ufeff"))
        self.assertEqual(
            recovery_result["status"],
            "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED",
        )
        self.assertTrue(recovery_result["recovery_performed"])
        self.assertEqual(self.fixture._pointer(), current)
        self.assertFalse(self.journal.exists())

        never_read = self.fixture.root / "rollback-barrier-never-read"
        crashed = self._run_rollback("-StartNewRollback", script=crash_script)
        self.assertNotEqual(crashed.returncode, 0)
        described = self._run_mode(
            "-DescribeManifest",
            "-ManifestPath",
            str(never_read),
            "-SignaturePath",
            str(never_read),
        )
        self.assertEqual(described.returncode, 6, described.stderr)
        self.assertEqual(
            json.loads(described.stdout.lstrip("\ufeff"))["status"],
            "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED",
        )
        self.assertEqual(self.fixture._pointer(), previous)
        self.assertFalse(self.journal.exists())

        crashed = self._run_rollback("-StartNewRollback", script=crash_script)
        self.assertNotEqual(crashed.returncode, 0)
        activated = self._run_mode(
            "-Activate",
            "-ManifestPath",
            str(never_read),
            "-SignaturePath",
            str(never_read),
            "-ArchivePath",
            str(never_read),
        )
        self.assertEqual(activated.returncode, 6, activated.stderr)
        self.assertEqual(
            json.loads(activated.stdout.lstrip("\ufeff"))["status"],
            "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED",
        )
        self.assertEqual(self.fixture._pointer(), current)
        self.assertFalse(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
