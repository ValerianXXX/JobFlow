from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import test_jobflow_bootstrap_activation as activation_tests
from test_jobflow_bootstrap_trust import SCRIPT


OK = b"JOBFLOW_RUNTIME_HEALTH_OK_V1\n"
FAILED = b"JOBFLOW_RUNTIME_HEALTH_FAILED_V1\n"


class JobFlowBootstrapRuntimeHealthTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def _fixture() -> activation_tests.JobFlowBootstrapActivationTests:
        fixture = activation_tests.JobFlowBootstrapActivationTests(methodName="runTest")
        fixture.setUp()
        return fixture

    @staticmethod
    def _health_entries(
        fixture: activation_tests.JobFlowBootstrapActivationTests,
    ) -> tuple[Path, ...]:
        root = fixture.install / "RuntimeHealthV1"
        return tuple(root.iterdir()) if root.exists() else ()

    @staticmethod
    def _data_snapshot(
        fixture: activation_tests.JobFlowBootstrapActivationTests,
    ) -> tuple[tuple[str, str, bytes], ...]:
        return fixture._tree_snapshot(fixture.install / "Data")

    @staticmethod
    def _preserved_data_snapshot(
        fixture: activation_tests.JobFlowBootstrapActivationTests,
    ) -> tuple[tuple[str, str, bytes], ...]:
        return fixture._preserved_data_snapshot()

    @staticmethod
    def _source(body: str) -> bytes:
        return ("from __future__ import annotations\n" + body).encode("utf-8")

    def test_static_runner_is_native_bounded_and_uses_exact_command_environment_and_closure(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        runner = source.split("public static class JobFlowRuntimeHealthRunner", 1)[1].split(
            "public static class JobFlowBootstrapJson", 1
        )[0]
        self.assertIn('"app/jobops/runtime_health.py"', source)
        self.assertIn("CreateProcessW(", runner)
        self.assertIn("CreateSuspended", runner)
        self.assertIn("AssignProcessToJobObject(job, process.Process)", runner)
        self.assertLess(
            runner.index("AssignProcessToJobObject(job, process.Process)"),
            runner.index("ResumeThread(process.Thread)"),
        )
        self.assertIn("JobObjectLimitKillOnJobClose", runner)
        self.assertIn("ActiveProcessLimit = 1", runner)
        self.assertIn("20L * 10000000L", runner)
        self.assertIn("512UL * 1024UL * 1024UL", runner)
        self.assertIn("private const int OutputLimit = 8192", runner)
        self.assertIn("private const int WallClockMilliseconds = 30000", runner)
        self.assertIn('"\\\"" + executable + "\\\" -I -B -X utf8 -m jobops.runtime_health"', runner)
        self.assertIn('"LOCALAPPDATA=" + AssertValue(localApplicationData)', runner)
        self.assertIn('"SystemRoot=" + AssertValue(systemRoot)', runner)
        self.assertIn('"TEMP=" + AssertValue(temporaryRoot)', runner)
        self.assertIn('"TMP=" + AssertValue(temporaryRoot)', runner)
        self.assertIn('"WinDir=" + AssertValue(winDir)', runner)
        for forbidden in (
            '"PATH="', '"JOBFLOW_DATA_ROOT="', '"PYTHONPATH="', '"HTTP_PROXY="',
            "Start-Process", "ProcessStartInfo",
        ):
            self.assertNotIn(forbidden, runner)
        self.assertIn('"RuntimeHealthV1"', source)
        self.assertIn('$maximumRuntimeHealthTemporaryEntries = 64', source)
        self.assertIn('$maximumRuntimeHealthTemporaryBytes = 16 * 1024 * 1024', source)
        activation = source.split("function Activate-VerifiedRuntime", 1)[1].split(
            "$manifestBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile", 1
        )[0]
        ordered_steps = (
            "Write-ActivationJournalPair $layout $journal",
            'Invoke-CandidateRuntimeHealth $layout $candidate ([string]$journal.transaction_id) "pre"',
            '$journal = Set-ActivationJournalState $layout $journal "PRE_HEALTH_OK"',
            "Publish-PointerPair $jobOpsRoot $candidate $oldCurrent $oldPrevious",
            '$journal = Set-ActivationJournalState $layout $journal "POINTER_SWITCHED"',
            "$publishedCandidate = Read-InstalledPointer $currentPath $true",
            'Invoke-CandidateRuntimeHealth $layout $publishedCandidate ([string]$journal.transaction_id) "post"',
            '$journal = Set-ActivationJournalState $layout $journal "POST_HEALTH_OK"',
            '$journal = Set-ActivationJournalState $layout $journal "COMMITTED"',
            "Write-ActivationCompletionReceipt $layout $journal",
            "Remove-ActivationJournalPair $layout",
        )
        positions = [activation.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))
        recovery = source.split("function Recover-PendingActivation", 1)[1].split(
            "function Activate-VerifiedRuntime", 1
        )[0]
        self.assertIn(
            '$journal.state -in @("PREPARED", "PRE_HEALTH_OK", "POINTER_SWITCHED")',
            recovery,
        )
        self.assertIn('$journal.state -ceq "POST_HEALTH_OK"', recovery)
        self.assertIn('Set-ActivationJournalState $Layout $journal "COMMITTED"', recovery)

    def test_real_child_gets_only_fixed_environment_and_fresh_temp_then_data_is_unchanged(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            marker = fixture.install / "Data" / "state" / "user-private-canary.bin"
            marker.write_bytes(b"preserve-exactly")
            before = self._preserved_data_snapshot(fixture)
            health = self._source(
                "import os, sys\n"
                "from pathlib import Path\n"
                "expected={'LOCALAPPDATA','SYSTEMROOT','TEMP','TMP','WINDIR'}\n"
                "ok=(set(os.environ)==expected and os.environ['TEMP']==os.environ['TMP'])\n"
                "cwd=Path.cwd().resolve()\n"
                "temp=Path(os.environ['TEMP']).resolve()\n"
                "local=Path(os.environ['LOCALAPPDATA']).resolve()\n"
                "ok=ok and Path(sys.executable).resolve().parent==cwd/'runtime'\n"
                "ok=ok and temp.is_dir() and temp.parent==local/'JobOps'/'RuntimeHealthV1'\n"
                "ok=ok and temp.name.startswith('health-') and temp.name.endswith(('-pre','-post'))\n"
                "ok=ok and not str(temp).startswith(str(local/'JobOps'/'Data'))\n"
                "(temp/'bounded-test-marker').write_bytes(b'x')\n"
                "if ok:\n"
                "    sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
                "else:\n"
                "    sys.stderr.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_FAILED_V1\\n'); sys.stderr.buffer.flush(); raise SystemExit(1)\n"
            )
            second = fixture._release(
                "1.1.0", predecessor_minimum="1.0.0", payload_tag="minimal-env",
                health_source=health,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "JOBFLOW_PRIVATE_CANARY": "must-not-pass",
                    "HTTPS_PROXY": "http://secret.invalid",
                    "PYTHONPATH": "C:\\private-canary",
                },
            ):
                completed = fixture._run(second)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout.lstrip("\ufeff"))["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
            self.assertNotIn("must-not-pass", completed.stdout + completed.stderr)
            self.assertNotIn("secret.invalid", completed.stdout + completed.stderr)
            self.assertEqual(self._preserved_data_snapshot(fixture), before)
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_pre_health_protocol_failure_is_exit_4_redacted_and_rolls_back(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            current_before = (fixture.install / "current.json").read_bytes()
            marker = fixture.install / "Data" / "state" / "user-private-canary.bin"
            marker.write_bytes(b"preserve-exactly")
            data_before = self._data_snapshot(fixture)
            health = self._source(
                "import sys\n"
                "sys.stderr.buffer.write(b'private-output-canary\\n')\n"
                "sys.stderr.buffer.flush()\n"
                "raise SystemExit(7)\n"
            )
            release = fixture._release(
                "1.1.0", predecessor_minimum="1.0.0", payload_tag="pre-failure",
                health_source=health,
            )
            completed = fixture._run(release)
            self.assertEqual(completed.returncode, 4)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_HEALTH_PRE_FAILED")
            self.assertNotIn("private-output-canary", completed.stderr)
            self.assertEqual((fixture.install / "current.json").read_bytes(), current_before)
            self.assertFalse((fixture.install / "previous.json").exists())
            self.assertEqual(self._data_snapshot(fixture), data_before)
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_post_health_failure_is_exit_5_and_restores_original_pointers_and_data(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            current_before = (fixture.install / "current.json").read_bytes()
            marker = fixture.install / "Data" / "state" / "user-private-canary.bin"
            marker.write_bytes(b"preserve-exactly")
            data_before = self._data_snapshot(fixture)
            health = self._source(
                "import os, sys\n"
                "if os.path.basename(os.environ['TEMP']).endswith('-pre'):\n"
                "    sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
                "else:\n"
                "    sys.stderr.buffer.write(b'post-private-canary\\n'); sys.stderr.buffer.flush(); raise SystemExit(8)\n"
            )
            second = fixture._release(
                "1.1.0", predecessor_minimum="1.0.0", payload_tag="post-failure",
                health_source=health,
            )
            completed = fixture._run(second)
            self.assertEqual(completed.returncode, 5)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_HEALTH_POST_FAILED")
            self.assertNotIn("post-private-canary", completed.stderr)
            self.assertEqual((fixture.install / "current.json").read_bytes(), current_before)
            self.assertFalse((fixture.install / "previous.json").exists())
            self.assertEqual(self._data_snapshot(fixture), data_before)
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_wall_timeout_is_exit_4_kills_child_and_leaves_no_temp_or_pointer(self) -> None:
        fixture = self._fixture()
        try:
            health = self._source(
                "import sys, time\n"
                "time.sleep(5)\n"
                "sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n')\n"
            )
            release = fixture._release("1.0.0", health_source=health)
            script = fixture._write_script(
                "bootstrap-health-timeout.ps1",
                mutation=(
                    "private const int WallClockMilliseconds = 30000;",
                    "private const int WallClockMilliseconds = 250;",
                ),
            )
            completed = fixture._run(release, script=script)
            self.assertEqual(completed.returncode, 4)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_HEALTH_PRE_FAILED")
            self.assertFalse((fixture.install / "current.json").exists())
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_output_overflow_is_exit_4_and_never_echoes_child_output(self) -> None:
        fixture = self._fixture()
        try:
            health = self._source(
                "import sys\n"
                "sys.stdout.buffer.write(b'private-overflow-canary-' + b'x'*9000)\n"
                "sys.stdout.buffer.flush()\n"
            )
            completed = fixture._run(fixture._release("1.0.0", health_source=health))
            self.assertEqual(completed.returncode, 4)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_RUNTIME_HEALTH_PRE_FAILED")
            self.assertNotIn("private-overflow-canary", completed.stderr)
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_job_object_active_process_limit_rejects_child_process_creation(self) -> None:
        fixture = self._fixture()
        try:
            health = self._source(
                "import subprocess, sys\n"
                "blocked=False\n"
                "try:\n"
                "    subprocess.run([sys.executable, '-I', '-c', 'pass'], check=False, timeout=2)\n"
                "except (OSError, subprocess.TimeoutExpired):\n"
                "    blocked=True\n"
                "if blocked:\n"
                "    sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
                "else:\n"
                "    sys.stderr.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_FAILED_V1\\n'); sys.stderr.buffer.flush(); raise SystemExit(1)\n"
            )
            completed = fixture._run(fixture._release("1.0.0", health_source=health))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout.lstrip("\ufeff"))["status"],
                "JOBFLOW_BOOTSTRAP_ACTIVATED",
            )
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()

    def test_child_closure_mutation_prioritizes_recovery_exit_3_and_preserves_evidence(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            current_before = (fixture.install / "current.json").read_bytes()
            marker = fixture.install / "Data" / "state" / "user-private-canary.bin"
            marker.write_bytes(b"preserve-exactly")
            data_before = self._preserved_data_snapshot(fixture)
            health = self._source(
                "import sys\n"
                "from pathlib import Path\n"
                "with Path(__file__).open('ab') as stream: stream.write(b'#test-only-mutation')\n"
                "sys.stdout.buffer.write(b'JOBFLOW_RUNTIME_HEALTH_OK_V1\\n'); sys.stdout.buffer.flush()\n"
            )
            release = fixture._release(
                "1.1.0", predecessor_minimum="1.0.0", payload_tag="closure-mutation",
                health_source=health,
            )
            completed = fixture._run(release)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
            self.assertEqual((fixture.install / "current.json").read_bytes(), current_before)
            self.assertFalse((fixture.install / "previous.json").exists())
            digest = str(release["value"]["runtime_closure"]["source_payload_sha256"])[7:19]
            target = fixture.install / "Application" / "versions" / f"v1.1.0-{digest}"
            self.assertTrue(target.is_dir())
            self.assertTrue((target / "app" / "jobops" / "runtime_health.py").read_bytes().endswith(b"#test-only-mutation"))
            after = self._preserved_data_snapshot(fixture)
            activation_journal_names = {
                "state/.jobflow-activation-transaction-v1.json",
                "state/.jobflow-activation-transaction-v1.backup.json",
            }
            self.assertEqual(
                tuple(record for record in after if record[0] not in activation_journal_names),
                data_before,
            )
            journal_records = tuple(record for record in after if record[0] in activation_journal_names)
            self.assertEqual({record[0] for record in journal_records}, activation_journal_names)
            self.assertEqual(journal_records[0][2], journal_records[1][2])
            self.assertEqual(self._health_entries(fixture), ())
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
