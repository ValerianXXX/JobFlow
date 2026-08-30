from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

import test_jobflow_bootstrap_activation as activation_tests


class JobFlowBootstrapActivationJournalTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def _fixture() -> activation_tests.JobFlowBootstrapActivationTests:
        fixture = activation_tests.JobFlowBootstrapActivationTests(methodName="runTest")
        fixture.setUp()
        return fixture

    @staticmethod
    def _journal_paths(
        fixture: activation_tests.JobFlowBootstrapActivationTests,
    ) -> tuple[Path, Path]:
        state = fixture.install / "Data" / "state"
        return (
            state / ".jobflow-activation-transaction-v1.json",
            state / ".jobflow-activation-transaction-v1.backup.json",
        )

    @staticmethod
    def _receipt_path(fixture: activation_tests.JobFlowBootstrapActivationTests) -> Path:
        return fixture.install / ".jobflow-activation-completion-v1.json"

    def _crash_script(
        self,
        fixture: activation_tests.JobFlowBootstrapActivationTests,
        boundary: str,
        *,
        condition: str = "$true",
    ) -> Path:
        needle = "# " + boundary
        injection = (
            needle
            + "\n        if ("
            + condition
            + ") { [Diagnostics.Process]::GetCurrentProcess().Kill(); "
            + "[Threading.Thread]::Sleep(10000) }"
        )
        return fixture._write_script(
            "bootstrap-crash-" + boundary.lower().replace("_", "-") + ".ps1",
            mutation=(needle, injection),
        )

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _journal_value(
        self,
        source: dict[str, object],
        *,
        state: str | None = None,
        generation: int | None = None,
        transaction_id: str | None = None,
    ) -> dict[str, object]:
        value = json.loads(json.dumps(source))
        if state is not None:
            value["state"] = state
        if generation is not None:
            value["generation"] = generation
        if transaction_id is not None:
            value["transaction_id"] = transaction_id
        semantic = {key: value[key] for key in (
            "schema_version",
            "kind",
            "transaction_id",
            "state",
            "generation",
            "candidate_target_was_present",
            "original_current",
            "original_previous",
            "candidate",
        )}
        value["semantic_sha256"] = "sha256:" + hashlib.sha256(self._canonical(semantic)).hexdigest()
        return value

    def _create_prepared_pending(
        self,
        fixture: activation_tests.JobFlowBootstrapActivationTests,
        *,
        target_ready: bool = False,
    ) -> dict[str, object]:
        release = fixture._release("1.0.0")
        boundary = (
            "JOBFLOW_ACTIVATION_CANDIDATE_TARGET_READY_BOUNDARY"
            if target_ready
            else "JOBFLOW_ACTIVATION_PREPARED_BOUNDARY"
        )
        crashed = fixture._run(release, script=self._crash_script(fixture, boundary))
        self.assertNotEqual(crashed.returncode, 0)
        main, backup = self._journal_paths(fixture)
        self.assertTrue(main.is_file())
        self.assertTrue(backup.is_file())
        self.assertEqual(main.read_bytes(), backup.read_bytes())
        return release

    def test_success_writes_canonical_redacted_receipt_and_leaves_no_journal_or_staging(self) -> None:
        fixture = self._fixture()
        try:
            release = fixture._release("1.0.0")
            completed = fixture._run(release)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            main, backup = self._journal_paths(fixture)
            self.assertFalse(main.exists())
            self.assertFalse(backup.exists())
            receipt_path = self._receipt_path(fixture)
            receipt_bytes = receipt_path.read_bytes()
            receipt = json.loads(receipt_bytes)
            self.assertEqual(
                set(receipt),
                {"schema_version", "kind", "transaction_id", "status", "candidate", "semantic_sha256"},
            )
            self.assertEqual(receipt_bytes, self._canonical(receipt))
            semantic = {key: receipt[key] for key in (
                "schema_version", "kind", "transaction_id", "status", "candidate"
            )}
            self.assertEqual(
                receipt["semantic_sha256"],
                "sha256:" + hashlib.sha256(self._canonical(semantic)).hexdigest(),
            )
            self.assertNotIn(str(fixture.root), receipt_bytes.decode("ascii"))
            self.assertEqual(fixture._orphans(), ([], []))
            state = fixture.install / "Data" / "state"
            self.assertEqual(
                sorted(path.name for path in state.iterdir()),
                [".jobflow-runtime-maintenance.lock", "activation-trust"],
            )
        finally:
            fixture.tearDown()

    def test_prepared_with_final_live_pointers_rolls_back_and_stops_recovery_only(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            old_current = (fixture.install / "current.json").read_bytes()
            second = fixture._release("1.1.0", predecessor_minimum="1.0.0")
            crash_script = self._crash_script(
                fixture, "JOBFLOW_ACTIVATION_CURRENT_POINTER_PUBLISHED_BOUNDARY"
            )
            crashed = fixture._run(second, script=crash_script)
            self.assertNotEqual(crashed.returncode, 0)
            candidate = json.loads(self._journal_paths(fixture)[0].read_bytes())["candidate"]
            candidate_target = fixture._target(candidate)
            self.assertTrue(candidate_target.is_dir())

            recovered = fixture._run(second)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            result = json.loads(recovered.stdout.lstrip("\ufeff"))
            self.assertEqual(result["status"], "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED")
            self.assertFalse(result["activation_committed"])
            self.assertTrue(result["retry_required"])
            self.assertEqual((fixture.install / "current.json").read_bytes(), old_current)
            self.assertFalse((fixture.install / "previous.json").exists())
            self.assertFalse(candidate_target.exists())
            self.assertFalse(any(path.exists() for path in self._journal_paths(fixture)))
            self.assertEqual(len(list((fixture.install / "Application" / "versions").iterdir())), 1)
            self.assertEqual(fixture._orphans(), ([], []))
        finally:
            fixture.tearDown()

    def test_fresh_prepared_recovery_leaves_no_pointer_and_does_not_start_second_transaction(self) -> None:
        fixture = self._fixture()
        try:
            release = self._create_prepared_pending(fixture, target_ready=True)
            journal = json.loads(self._journal_paths(fixture)[0].read_bytes())
            target = fixture._target(journal["candidate"])
            self.assertTrue(target.is_dir())
            marker = fixture.install / "Data" / "state" / "user-preserved.bin"
            marker.write_bytes(b"preserve")

            recovered = fixture._run(release)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            result = json.loads(recovered.stdout.lstrip("\ufeff"))
            self.assertEqual(result["status"], "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED")
            self.assertFalse(result["activation_committed"])
            self.assertFalse((fixture.install / "current.json").exists())
            self.assertFalse((fixture.install / "previous.json").exists())
            self.assertFalse(target.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")
            self.assertFalse(any(path.exists() for path in self._journal_paths(fixture)))
            self.assertEqual(list((fixture.install / "Application" / "versions").iterdir()), [])
            self.assertEqual(fixture._orphans(), ([], []))
        finally:
            fixture.tearDown()

    def test_owned_candidate_is_validated_exactly_before_delete_and_corruption_is_preserved(self) -> None:
        fixture = self._fixture()
        try:
            release = self._create_prepared_pending(fixture, target_ready=True)
            journal = json.loads(self._journal_paths(fixture)[0].read_bytes())
            target = fixture._target(journal["candidate"])
            corrupt = target / "app" / "jobops" / "cli.py"
            corrupt.write_bytes(corrupt.read_bytes() + b"#corrupt")
            main_before, backup_before = (path.read_bytes() for path in self._journal_paths(fixture))

            recovered = fixture._run(release)
            self.assertEqual(recovered.returncode, 3)
            self.assertEqual(recovered.stdout, "")
            self.assertEqual(recovered.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
            self.assertTrue(target.is_dir())
            self.assertTrue(corrupt.read_bytes().endswith(b"#corrupt"))
            main, backup = self._journal_paths(fixture)
            self.assertEqual(main.read_bytes(), main_before)
            self.assertEqual(backup.read_bytes(), backup_before)
            self.assertFalse((fixture.install / "current.json").exists())
            self.assertFalse((fixture.install / "previous.json").exists())
        finally:
            fixture.tearDown()

    def test_backup_anchor_partial_and_malformed_copy_truth_table(self) -> None:
        cases = (
            ("backup_only", True),
            ("malformed_main", True),
            ("main_only", False),
            ("malformed_backup", False),
            ("unsafe_backup", False),
        )
        for case, should_recover in cases:
            with self.subTest(case=case):
                fixture = self._fixture()
                try:
                    release = self._create_prepared_pending(fixture)
                    main, backup = self._journal_paths(fixture)
                    if case == "backup_only":
                        main.unlink()
                    elif case == "malformed_main":
                        main.write_bytes(b"{")
                    elif case == "main_only":
                        backup.unlink()
                    elif case == "malformed_backup":
                        backup.write_bytes(b"{")
                    elif case == "unsafe_backup":
                        backup.unlink()
                        backup.mkdir()
                    main_before = main.read_bytes() if main.is_file() else None
                    backup_before = backup.read_bytes() if backup.is_file() else None

                    completed = fixture._run(release)
                    if should_recover:
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        result = json.loads(completed.stdout.lstrip("\ufeff"))
                        self.assertEqual(result["status"], "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED")
                        self.assertFalse(any(path.exists() for path in (main, backup)))
                    else:
                        self.assertEqual(completed.returncode, 3)
                        self.assertEqual(completed.stdout, "")
                        self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
                        self.assertEqual(main.read_bytes() if main.is_file() else None, main_before)
                        self.assertEqual(backup.read_bytes() if backup.is_file() else None, backup_before)
                finally:
                    fixture.tearDown()

    def test_divergent_immutable_backup_ahead_and_nonadjacent_generations_reject_without_mutation(self) -> None:
        cases = ("immutable_mismatch", "backup_ahead", "nonadjacent")
        for case in cases:
            with self.subTest(case=case):
                fixture = self._fixture()
                try:
                    release = self._create_prepared_pending(fixture)
                    main, backup = self._journal_paths(fixture)
                    prepared = json.loads(main.read_bytes())
                    if case == "immutable_mismatch":
                        value = self._journal_value(
                            prepared,
                            state="PRE_HEALTH_OK",
                            generation=2,
                            transaction_id="f" * 32,
                        )
                        main.write_bytes(self._canonical(value))
                    elif case == "backup_ahead":
                        value = self._journal_value(prepared, state="PRE_HEALTH_OK", generation=2)
                        backup.write_bytes(self._canonical(value))
                    else:
                        value = self._journal_value(prepared, state="POINTER_SWITCHED", generation=3)
                        main.write_bytes(self._canonical(value))
                    main_before = main.read_bytes()
                    backup_before = backup.read_bytes()

                    completed = fixture._run(release)
                    self.assertEqual(completed.returncode, 3)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
                    self.assertEqual(main.read_bytes(), main_before)
                    self.assertEqual(backup.read_bytes(), backup_before)
                    self.assertFalse((fixture.install / "current.json").exists())
                    self.assertFalse((fixture.install / "previous.json").exists())
                finally:
                    fixture.tearDown()

    def test_impossible_live_pointer_combination_rejects_without_mutation(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            second = fixture._release("1.1.0", predecessor_minimum="1.0.0")
            crash_script = self._crash_script(fixture, "JOBFLOW_ACTIVATION_PREPARED_BOUNDARY")
            crashed = fixture._run(second, script=crash_script)
            self.assertNotEqual(crashed.returncode, 0)
            main, backup = self._journal_paths(fixture)
            journal = json.loads(main.read_bytes())
            (fixture.install / "current.json").write_bytes(self._canonical(journal["candidate"]))
            main_before = main.read_bytes()
            backup_before = backup.read_bytes()
            current_before = (fixture.install / "current.json").read_bytes()

            completed = fixture._run(second)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
            self.assertEqual(main.read_bytes(), main_before)
            self.assertEqual(backup.read_bytes(), backup_before)
            self.assertEqual((fixture.install / "current.json").read_bytes(), current_before)
            self.assertFalse((fixture.install / "previous.json").exists())
        finally:
            fixture.tearDown()

    def test_preexisting_exact_candidate_target_is_never_deleted_by_prepared_recovery(self) -> None:
        fixture = self._fixture()
        try:
            first = fixture._release("1.0.0")
            self.assertEqual(fixture._run(first).returncode, 0)
            original_current = (fixture.install / "current.json").read_bytes()
            second = fixture._release("1.1.0", predecessor_minimum="1.0.0")
            self.assertEqual(fixture._run(second).returncode, 0)
            candidate = fixture._pointer()
            target = fixture._target(candidate)
            target_before = fixture._tree_snapshot(target)
            (fixture.install / "current.json").write_bytes(original_current)
            (fixture.install / "previous.json").unlink()

            crash_script = self._crash_script(fixture, "JOBFLOW_ACTIVATION_PREPARED_BOUNDARY")
            crashed = fixture._run(second, script=crash_script)
            self.assertNotEqual(crashed.returncode, 0)
            journal = json.loads(self._journal_paths(fixture)[0].read_bytes())
            self.assertTrue(journal["candidate_target_was_present"])

            recovered = fixture._run(second)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            result = json.loads(recovered.stdout.lstrip("\ufeff"))
            self.assertEqual(result["status"], "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED")
            self.assertFalse(result["activation_committed"])
            self.assertEqual((fixture.install / "current.json").read_bytes(), original_current)
            self.assertFalse((fixture.install / "previous.json").exists())
            self.assertTrue(target.is_dir())
            self.assertEqual(fixture._tree_snapshot(target), target_before)
            self.assertFalse(any(path.exists() for path in self._journal_paths(fixture)))
        finally:
            fixture.tearDown()

    def test_fixed_state_files_reject_hardlinks_ads_and_directories(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows fixed-file semantics")
        cases = (
            ("main", "hardlink"),
            ("backup", "hardlink"),
            ("receipt", "hardlink"),
            ("main", "ads"),
            ("backup", "ads"),
            ("receipt", "ads"),
            ("main", "directory"),
            ("backup", "directory"),
            ("receipt", "directory"),
        )
        for kind, tamper in cases:
            with self.subTest(kind=kind, tamper=tamper):
                fixture = self._fixture()
                try:
                    if kind == "receipt":
                        release = fixture._release("1.0.0")
                        self.assertEqual(fixture._run(release).returncode, 0)
                        path = self._receipt_path(fixture)
                    else:
                        release = self._create_prepared_pending(fixture)
                        main, backup = self._journal_paths(fixture)
                        path = main if kind == "main" else backup
                    if tamper == "hardlink":
                        alias = path.with_name(path.name + ".hardlink-test")
                        os.link(path, alias)
                    elif tamper == "ads":
                        with open(str(path) + ":jobflow-test", "wb") as stream:
                            stream.write(b"unexpected-stream")
                    else:
                        path.unlink()
                        path.mkdir()

                    completed = fixture._run(release)
                    self.assertEqual(completed.returncode, 3)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
                finally:
                    fixture.tearDown()

    def test_fixed_state_file_reparse_point_is_rejected(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows reparse semantics")
        fixture = self._fixture()
        try:
            release = self._create_prepared_pending(fixture)
            main, _ = self._journal_paths(fixture)
            target = main.with_name(main.name + ".symlink-target")
            target.write_bytes(main.read_bytes())
            main.unlink()
            try:
                os.symlink(target, main)
            except OSError as error:
                self.skipTest("File symlink creation unavailable: " + str(error.winerror))
            completed = fixture._run(release)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
        finally:
            fixture.tearDown()

    def test_every_activation_crash_boundary_recovers_deterministically(self) -> None:
        cases = (
            ("JOBFLOW_ACTIVATION_JOURNAL_INITIAL_BACKUP_PUBLISHED_BOUNDARY", "$true", True, False, True),
            ("JOBFLOW_ACTIVATION_JOURNAL_INITIAL_MAIN_PUBLISHED_BOUNDARY", "$true", True, False, True),
            ("JOBFLOW_ACTIVATION_PREPARED_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_CANDIDATE_TARGET_READY_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_PRE_HEALTH_COMPLETED_BOUNDARY", "$true", False, False, True),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_MAIN_ADVANCED_BOUNDARY",
                "$Value.state -ceq 'PRE_HEALTH_OK'",
                False,
                False,
                True,
            ),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_BACKUP_SYNCHRONIZED_BOUNDARY",
                "$Value.state -ceq 'PRE_HEALTH_OK'",
                False,
                False,
                True,
            ),
            ("JOBFLOW_ACTIVATION_PRE_HEALTH_OK_STATE_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_PREVIOUS_POINTER_PUBLISHED_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_CURRENT_POINTER_PUBLISHED_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_POINTER_PAIR_PUBLISHED_BOUNDARY", "$true", False, False, True),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_MAIN_ADVANCED_BOUNDARY",
                "$Value.state -ceq 'POINTER_SWITCHED'",
                False,
                False,
                True,
            ),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_BACKUP_SYNCHRONIZED_BOUNDARY",
                "$Value.state -ceq 'POINTER_SWITCHED'",
                False,
                False,
                True,
            ),
            ("JOBFLOW_ACTIVATION_POINTER_SWITCHED_STATE_BOUNDARY", "$true", False, False, True),
            ("JOBFLOW_ACTIVATION_POST_HEALTH_COMPLETED_BOUNDARY", "$true", False, False, True),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_MAIN_ADVANCED_BOUNDARY",
                "$Value.state -ceq 'POST_HEALTH_OK'",
                False,
                False,
                True,
            ),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_BACKUP_SYNCHRONIZED_BOUNDARY",
                "$Value.state -ceq 'POST_HEALTH_OK'",
                False,
                True,
                True,
            ),
            ("JOBFLOW_ACTIVATION_POST_HEALTH_OK_STATE_BOUNDARY", "$true", False, True, True),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_MAIN_ADVANCED_BOUNDARY",
                "$Value.state -ceq 'COMMITTED'",
                False,
                True,
                True,
            ),
            (
                "JOBFLOW_ACTIVATION_JOURNAL_BACKUP_SYNCHRONIZED_BOUNDARY",
                "$Value.state -ceq 'COMMITTED'",
                False,
                True,
                True,
            ),
            ("JOBFLOW_ACTIVATION_COMMITTED_STATE_BOUNDARY", "$true", False, True, True),
            ("JOBFLOW_ACTIVATION_COMPLETION_RECEIPT_BOUNDARY", "$true", False, True, True),
            ("JOBFLOW_ACTIVATION_JOURNAL_MAIN_REMOVED_BOUNDARY", "$true", False, True, True),
            ("JOBFLOW_ACTIVATION_JOURNAL_BACKUP_REMOVED_BOUNDARY", "$true", False, True, False),
        )
        for boundary, condition, fresh, recovery_committed, pending_expected in cases:
            with self.subTest(boundary=boundary, condition=condition):
                fixture = self._fixture()
                try:
                    if not fresh:
                        first = fixture._release("1.0.0")
                        self.assertEqual(fixture._run(first).returncode, 0)
                        old_current = (fixture.install / "current.json").read_bytes()
                        release = fixture._release("1.1.0", predecessor_minimum="1.0.0")
                    else:
                        old_current = None
                        release = fixture._release("1.0.0")
                    crash_script = self._crash_script(fixture, boundary, condition=condition)
                    crashed = fixture._run(release, script=crash_script)
                    self.assertNotEqual(crashed.returncode, 0)
                    marker = fixture.install / "Data" / "state" / "user-preserved.bin"
                    marker.write_bytes(b"preserve")

                    recovered = fixture._run(release)
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    result = json.loads(recovered.stdout.lstrip("\ufeff"))
                    if pending_expected:
                        self.assertEqual(result["status"], "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED")
                        self.assertEqual(result["activation_committed"], recovery_committed)
                        final = fixture._run(release)
                        self.assertEqual(final.returncode, 0, final.stderr)
                        final_result = json.loads(final.stdout.lstrip("\ufeff"))
                        self.assertEqual(final_result["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
                    else:
                        self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
                        self.assertFalse(result["activation_performed"])
                    pointer = fixture._pointer()
                    self.assertEqual(pointer["version"], str(release["value"]["release"]["version"]))
                    if old_current is not None:
                        self.assertEqual((fixture.install / "previous.json").read_bytes(), old_current)
                    self.assertEqual(marker.read_bytes(), b"preserve")
                    self.assertFalse(any(path.exists() for path in self._journal_paths(fixture)))
                    self.assertEqual(fixture._orphans(), ([], []))
                    self.assertNotIn(str(fixture.root), recovered.stdout + recovered.stderr)
                finally:
                    fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
