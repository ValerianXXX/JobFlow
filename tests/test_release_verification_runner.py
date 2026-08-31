from __future__ import annotations

import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT
from jobops.release_verification import (
    _PYTHON_CHECK_BOOTSTRAP,
    ReleaseVerificationError,
    _isolated_python_arguments,
    _publish_report_and_checkpoint,
    _resolved_git_executable,
    authenticate_signing_evidence,
    main,
    run_release_verification,
    run_signing_transaction_verification,
    safe_atomic_write_bytes,
    validate_release_test_report,
)
from jobops.release_toolchain import LockedToolIdentity, ReleaseToolchainError


def _report(commit: str, *, baseline_attempts: int = 0, baseline_real: int = 0) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": 1,
        "status": "PASS",
        "source_commit": commit,
        "passed": 1,
        "failed": 0,
        "schema_count": 1,
        "javascript_e2e_count": 1,
        "command_count": 1,
        "network_actions": 0,
        "recruiting_sites_visited": 0,
        "knowledge_write_operations": 0,
        "observation_scope": {
            "counter_semantics": "JOBFLOW_COMPATIBILITY_COUNTERS_ONLY",
            "network_actions": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
            "recruiting_sites_visited": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
            "knowledge_write_operations": "JOBFLOW_KNOWLEDGE_GATEWAY_SNAPSHOTS_ONLY",
            "process_network_isolation": "UNATTESTED",
            "write_restore_detection": "UNATTESTED",
            "public_release_authority": "NONE",
        },
        "categories": {"unittest_discovery": 1},
        "command": "scripts/run-release-verification.ps1",
        "command_summary": [],
        "tool_identities": {
            "node": {
                "status": "PASS",
                "tool": "node",
                "sha256": digest,
                "signer_subject": "CN=OpenJS Foundation, O=OpenJS Foundation, L=San Francisco, S=California, C=US",
                "signer_thumbprint": "8EA1D142EA3F46023BACA38C23A7E7AE6AFCE30C",
            },
            "git": {
                "status": "PASS",
                "tool": "git",
                "sha256": digest,
                "signer_subject": "CN=Johannes Schindelin, O=Johannes Schindelin, S=Nordrhein-Westfalen, C=DE",
                "signer_thumbprint": "3EB14A3AEF84B7153E139397F0A49E2FAC662B0E",
            },
            "python": {
                "status": "PASS",
                "tool": "python",
                "sha256": digest,
                "signer_subject": "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US",
                "signer_thumbprint": "36168EE17C1A240517388540C903BB6717DD2563",
            },
        },
        "output_sha256": digest,
        "external_actions": {
            "status": "PASS",
            "baseline_attempt_count": baseline_attempts,
            "baseline_real_external_actions": baseline_real,
            "final_attempt_count": baseline_attempts,
            "final_real_external_actions": baseline_real,
            "attempt_delta": 0,
            "real_external_action_delta": 0,
        },
    }


def _complete_report(commit: str, *, baseline_attempts: int = 0, baseline_real: int = 0) -> dict[str, object]:
    value = _report(commit, baseline_attempts=baseline_attempts, baseline_real=baseline_real)
    digest = "sha256:" + "a" * 64
    ids = [
        "git-head-start",
        "git-clean-start",
        "external-actions-baseline",
        "git-tool-identity",
        "node-tool-identity",
        "python-tool-identity",
        "python-unittest-discovery",
        "javascript-package-lock",
        "javascript-dependency-tree",
        "javascript-runtime-version",
        "javascript-runner",
        "javascript-e2e-browser-companion",
        "python-compileall",
        "runtime-schema-json",
        "browser-companion-store-package",
        "browser-companion-store-package-validation",
        "public-repository-boundary",
        "git-head-before-report",
        "git-clean-before-report",
        "external-actions-final",
        "git-tool-identity-final",
        "node-tool-identity-final",
        "python-tool-identity-final",
    ]
    value["command_summary"] = [
        {"id": item, "exit_code": 0, "line_count": 1, "output_sha256": digest}
        for item in ids
    ]
    value["command_count"] = len(ids)
    return value


def _write_javascript_contract(project: Path, suite_name: str = "browser_companion_e2e.cjs") -> None:
    (project / ".git").mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "src").mkdir(exist_ok=True)
    (project / ".venv" / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    (project / "scripts").mkdir(exist_ok=True)
    (project / "tests" / suite_name).write_text("// fixture\n", encoding="utf-8")
    (project / "scripts" / "run-javascript-e2e.cjs").write_text("// fixed runner\n", encoding="utf-8")
    package = {
        "name": "jobflow-verification",
        "version": "0.6.0",
        "private": True,
        "scripts": {"test:e2e": "node scripts/run-javascript-e2e.cjs"},
        "devDependencies": {"playwright": "1.62.1"},
    }
    lock = {
        "name": "jobflow-verification",
        "version": "0.6.0",
        "lockfileVersion": 3,
        "packages": {
            "": {
                "name": "jobflow-verification",
                "version": "0.6.0",
                "devDependencies": {"playwright": "1.62.1"},
            },
            "node_modules/playwright": {"version": "1.62.1"},
            "node_modules/playwright-core": {"version": "1.62.1"},
        },
    }
    (project / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (project / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    _write_toolchain_policy(project)


def _write_toolchain_policy(project: Path) -> None:
    target = project / "config"
    target.mkdir(exist_ok=True)
    for name in (
        "python-support-policy.json",
        "release-toolchain.json",
        "windows-cp313-build.lock",
        "windows-cp313-runtime.lock",
        "windows-runtime-source.json",
    ):
        (target / name).write_bytes((PROJECT / "config" / name).read_bytes())


@contextmanager
def _locked_tool_fixture(_project: Path, path: Path, tool: str):
    subjects = {
        "node": (
            "CN=OpenJS Foundation, O=OpenJS Foundation, L=San Francisco, S=California, C=US",
            "8EA1D142EA3F46023BACA38C23A7E7AE6AFCE30C",
        ),
        "git": (
            "CN=Johannes Schindelin, O=Johannes Schindelin, S=Nordrhein-Westfalen, C=DE",
            "3EB14A3AEF84B7153E139397F0A49E2FAC662B0E",
        ),
        "python": (
            "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US",
            "36168EE17C1A240517388540C903BB6717DD2563",
        ),
    }
    subject, thumbprint = subjects[tool]
    yield LockedToolIdentity(
        status="PASS",
        tool=tool,
        sha256="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        signer_subject=subject,
        signer_thumbprint=thumbprint,
        volume_serial=1,
        file_index=2,
        file_size=path.stat().st_size,
    )


@contextmanager
def _locked_javascript_fixture(_project: Path):
    yield {
        "status": "PASS",
        "packages": ["playwright", "playwright-core"],
        "file_count": 2,
        "total_bytes": 2,
        "tree_sha256": "sha256:" + "d" * 64,
    }


def _checkpoint(commit: str, report: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "PASS",
        "source_commit": commit,
        "verification_scope": "LOCAL_DEVELOPMENT",
        "public_repository_ready": True,
        "runtime_closure_status": "UNATTESTED",
        "public_release_ready": False,
        "public_release_blockers": ["RELEASE_RUNTIME_CLOSURE_UNATTESTED"],
        "real_external_actions": 0,
        "checks": {
            "tests": True,
            "skill": True,
            "knowledge": True,
            "security": True,
            "external_actions": True,
            "database": True,
            "synthetic_private_purged": True,
            "private_store_consistent": True,
            "public_repository": True,
            "independent_qa": True,
        },
        "tests": report,
        "independent_qa": {
            "status": "PASS",
            "fresh_for_current_release": True,
        },
        "external_action_audit": {
            "status": "PASS",
            "attempt_count": 0,
            "real_external_actions": 0,
        },
        "p0_open": 0,
        "p1_open": 0,
        "must_fix_open": 0,
    }


class ReleaseVerificationRunnerTests(unittest.TestCase):
    def test_windows_release_checks_hide_nested_console_processes(self) -> None:
        self.assertIn('if os.name == "nt":', _PYTHON_CHECK_BOOTSTRAP)
        self.assertIn('getattr(subprocess, "CREATE_NO_WINDOW", 0)', _PYTHON_CHECK_BOOTSTRAP)
        self.assertIn('kwargs["creationflags"]', _PYTHON_CHECK_BOOTSTRAP)
        self.assertIn('| _jobflow_no_window', _PYTHON_CHECK_BOOTSTRAP)
        completed = subprocess.run(
            [
                sys.executable,
                *_isolated_python_arguments(
                    PROJECT,
                    ["-c", "import subprocess; print(subprocess.Popen.__init__.__name__)"],
                ),
            ],
            cwd=PROJECT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        expected = "_jobflow_hidden_popen_init" if os.name == "nt" else "__init__"
        self.assertEqual(completed.stdout.strip(), expected)

    def test_test_modules_use_the_isolated_tests_root_import_contract(self) -> None:
        offenders: list[str] = []
        for path in sorted((PROJECT / "tests").glob("test_*.py")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.startswith("from tests") or line.startswith("import tests"):
                    offenders.append(f"{path.name}:{line_number}")
        self.assertEqual(offenders, [])

    def test_powershell_runner_has_the_complete_offline_contract(self) -> None:
        script = (PROJECT / "scripts" / "run-release-verification.ps1").read_text(encoding="utf-8")
        self.assertIn('.venv\\Scripts\\python.exe', script)
        self.assertIn('dependencies\\node\\bin\\node.exe', script)
        self.assertIn('$result["NODE_PATH"] = $NodeModules', script)
        self.assertIn('Set-ProcessEnvironment (Get-MinimalChildEnvironment $childNodeModules)', script)
        self.assertIn('[IO.FileShare]::Read', script)
        self.assertIn('dependencies\\native\\git\\cmd\\git.exe', script)
        self.assertGreater(len(list((PROJECT / "tests").glob("*e2e.cjs"))), 0)
        self.assertIn('-m jobops.release_verification run --node $node --git $git', script)
        self.assertIn('No caller-authored', script)
        self.assertNotIn('publish-and-checkpoint', script)
        self.assertNotIn('$reportJson', script)
        self.assertNotIn('JOBFLOW_RELEASE_BASELINE_ATTEMPTS', script)
        self.assertNotIn("git push", script)
        self.assertNotIn("git tag", script)
        self.assertNotIn("Invoke-WebRequest", script)
        self.assertNotIn("Start-BitsTransfer", script)

    def test_same_process_runner_builds_report_only_from_executed_checks(self) -> None:
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            (project / "reports").mkdir()
            _write_javascript_contract(project)
            node = project / "node.exe"
            git = project / "git.exe"
            node.write_bytes(b"fixture")
            git.write_bytes(b"fixture")

            def completed(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                arguments = command[1:]
                if "rev-parse" in arguments and "HEAD" in arguments:
                    output = commit + "\n"
                elif "status" in arguments and "--porcelain=v1" in arguments:
                    output = ""
                elif "unittest" in arguments:
                    output = "test_ok ... ok\n\nRan 7 tests in 0.001s\n\nOK\n"
                elif arguments == ["--version"]:
                    output = "v24.19.0\n"
                elif arguments and arguments[-1].endswith("run-javascript-e2e.cjs"):
                    output = (
                        '[RUN] browser_companion_e2e.cjs\n'
                        '{"status":"PASS","real_external_actions":0}'
                        'JOBFLOW_JAVASCRIPT_E2E_PASS=1\n'
                    )
                elif "compileall" in arguments:
                    output = ""
                elif "jobops.browser_companion_store" in arguments:
                    output = json.dumps({
                        "schema_version": 1,
                        "status": "BUILT",
                        "version": "0.9.2",
                        "path": "JobFlow-Browser-Companion-v0.9.2-store.zip",
                        "sha256": "sha256:" + "b" * 64,
                        "source_sha256": "sha256:" + "c" * 64,
                        "file_count": 11,
                        "private_binding_files": 0,
                    })
                elif arguments[-2:-1] == ["-c"]:
                    output = "SCHEMAS_VALID=3\n"
                elif arguments[-2:] == ["jobops.public_release"] or "jobops.public_release" in arguments:
                    output = '{"status":"PASS"}\n'
                else:
                    raise AssertionError(f"Unexpected command: {command!r}")
                return subprocess.CompletedProcess(command, 0, stdout=output)

            published = {
                "status": "RELEASE_VERIFICATION_RECORDED",
                "source_commit": commit,
                "real_external_actions": 0,
            }
            snapshot = {
                "status": "AUDIT_SNAPSHOT",
                "attempt_count": 0,
                "real_external_actions": 0,
            }

            with (
                patch("jobops.release_verification.subprocess.run", side_effect=completed) as run,
                patch(
                    "jobops.release_verification.locked_authenticated_tool",
                    side_effect=_locked_tool_fixture,
                ),
                patch(
                    "jobops.release_verification.locked_javascript_dependency_tree",
                    side_effect=_locked_javascript_fixture,
                ),
                patch("jobops.release_verification.action_snapshot", return_value=snapshot),
                patch(
                    "jobops.release_verification.knowledge_snapshot",
                    return_value={"schema_version": 1, "collections": {}},
                ),
                patch(
                    "jobops.release_verification._publish_report_and_checkpoint",
                    return_value=published,
                ) as publish,
                patch(
                    "jobops.release_verification.verify_store_package",
                    return_value={
                        "status": "PASS",
                        "version": "0.9.2",
                        "sha256": "sha256:" + "b" * 64,
                        "file_count": 11,
                        "private_binding_files": 0,
                        "findings": [],
                    },
                ),
            ):
                result = run_release_verification(project, node_path=node, git_path=git)
            self.assertEqual(result, published)
            self.assertGreaterEqual(run.call_count, 8)
            report = publish.call_args.args[1]
            ids = [item["id"] for item in report["command_summary"]]
            self.assertIn("python-unittest-discovery", ids)
            self.assertIn("javascript-e2e-browser_companion_e2e", ids)
            self.assertIn("javascript-package-lock", ids)
            self.assertIn("javascript-dependency-tree", ids)
            self.assertIn("javascript-runtime-version", ids)
            self.assertIn("javascript-runner", ids)
            self.assertIn("browser-companion-store-package", ids)
            self.assertIn("browser-companion-store-package-validation", ids)
            self.assertIn("external-actions-baseline", ids)
            self.assertIn("external-actions-final", ids)
            self.assertIn("knowledge-baseline", ids)
            self.assertEqual(
                publish.call_args.kwargs["knowledge_baseline"],
                {"schema_version": 1, "collections": {}},
            )
            self.assertEqual(
                report["tool_identities"]["node"]["signer_subject"],
                "CN=OpenJS Foundation, O=OpenJS Foundation, L=San Francisco, S=California, C=US",
            )
            self.assertEqual(
                report["tool_identities"]["git"]["signer_subject"],
                "CN=Johannes Schindelin, O=Johannes Schindelin, S=Nordrhein-Westfalen, C=DE",
            )
            self.assertEqual(report["tool_identities"]["python"]["tool"], "python")
            for call in run.call_args_list:
                environment = call.kwargs.get("env")
                self.assertIsInstance(environment, dict)
                self.assertNotIn("NODE_OPTIONS", environment)
                self.assertNotIn("NODE_PATH", environment)
                self.assertNotIn("PYTHONPATH", environment)
                self.assertNotIn("GIT_DIR", environment)
            unittest_call = next(
                call for call in run.call_args_list if "unittest" in call.args[0]
            )
            self.assertEqual(
                unittest_call.kwargs["env"]["JOBFLOW_RELEASE_GIT_PATH"],
                str(git.resolve()),
            )
            self.assertEqual(report["passed"], 7)
            self.assertEqual(report["schema_count"], 3)
            self.assertEqual(report["command_count"], len(ids))
            self.assertEqual(report["external_actions"]["real_external_action_delta"], 0)
            self.assertEqual(
                report["observation_scope"],
                {
                    "counter_semantics": "JOBFLOW_COMPATIBILITY_COUNTERS_ONLY",
                    "network_actions": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
                    "recruiting_sites_visited": "JOBFLOW_EXTERNAL_ACTION_DATABASE_SNAPSHOTS_ONLY",
                    "knowledge_write_operations": "JOBFLOW_KNOWLEDGE_GATEWAY_SNAPSHOTS_ONLY",
                    "process_network_isolation": "UNATTESTED",
                    "write_restore_detection": "UNATTESTED",
                    "public_release_authority": "NONE",
                },
            )

    def test_perfect_exit_zero_fake_node_is_rejected_before_evidence_write(self) -> None:
        commit = "9" * 40
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            _write_toolchain_policy(project)
            reports = project / "reports"
            reports.mkdir()
            _write_javascript_contract(project)
            report_path = reports / "release-test-results.json"
            checkpoint_path = reports / "checkpoint-final.json"
            old_report = b'{"trusted":"report"}\n'
            old_checkpoint = b'{"trusted":"checkpoint"}\n'
            report_path.write_bytes(old_report)
            checkpoint_path.write_bytes(old_checkpoint)
            node = project / "node.exe"
            git = project / "git.exe"
            node.write_bytes(b"exit-zero fake")
            git.write_bytes(b"fixture")

            def completed(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                arguments = command[1:]
                if arguments == ["rev-parse", "HEAD"]:
                    output = commit + "\n"
                elif arguments[:1] == ["status"]:
                    output = ""
                elif "unittest" in arguments:
                    output = "Ran 1 test in 0.001s\n\nOK\n"
                elif arguments == ["--version"]:
                    output = "v24.19.0\n"
                elif arguments and arguments[-1].endswith("run-javascript-e2e.cjs"):
                    output = (
                        '[RUN] browser_companion_e2e.cjs\n'
                        '{"status":"PASS","real_external_actions":0}'
                        'JOBFLOW_JAVASCRIPT_E2E_PASS=1\n'
                    )
                else:
                    raise AssertionError(f"Unexpected command: {command!r}")
                return subprocess.CompletedProcess(command, 0, stdout=output)

            snapshot = {"status": "AUDIT_SNAPSHOT", "attempt_count": 0, "real_external_actions": 0}
            with (
                patch("jobops.release_verification.subprocess.run", side_effect=completed) as run,
                patch(
                    "jobops.release_verification.locked_authenticated_tool",
                    side_effect=ReleaseToolchainError("RELEASE_NODE_UNTRUSTED"),
                ),
                patch("jobops.release_verification.action_snapshot", return_value=snapshot),
                patch("jobops.release_verification._publish_report_and_checkpoint") as publish,
            ):
                with self.assertRaisesRegex(
                    ReleaseVerificationError,
                    "RELEASE_NODE_UNTRUSTED",
                ):
                    run_release_verification(project, node_path=node, git_path=git)
            run.assert_not_called()
            publish.assert_not_called()
            self.assertEqual(report_path.read_bytes(), old_report)
            self.assertEqual(checkpoint_path.read_bytes(), old_checkpoint)

    def test_fabricated_stdin_cannot_create_or_replace_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            reports = project / "reports"
            reports.mkdir()
            report_path = reports / "release-test-results.json"
            checkpoint_path = reports / "checkpoint-final.json"
            old_report = b'{"trusted":"report"}\n'
            old_checkpoint = b'{"trusted":"checkpoint"}\n'
            report_path.write_bytes(old_report)
            checkpoint_path.write_bytes(old_checkpoint)
            forged = json.dumps(_complete_report("f" * 40))
            output = io.StringIO()
            with (
                patch("jobops.release_verification.project_root", return_value=project),
                patch.object(sys, "argv", ["release_verification", "publish-and-checkpoint"]),
                patch.object(sys, "stdin", io.StringIO(forged)),
                redirect_stdout(output),
            ):
                status = main()
            self.assertEqual(status, 2)
            self.assertIn("RELEASE_VERIFICATION_COMMAND_INVALID", output.getvalue())
            self.assertEqual(report_path.read_bytes(), old_report)
            self.assertEqual(checkpoint_path.read_bytes(), old_checkpoint)

    def test_report_accepts_only_native_counts_and_zero_action_delta(self) -> None:
        commit = "b" * 40
        validated = validate_release_test_report(
            _complete_report(commit),
            project=PROJECT,
            expected_commit=commit,
            baseline_attempts=0,
            baseline_real=0,
            current_attempts=0,
            current_real=0,
        )
        self.assertEqual(validated["status"], "PASS")
        for field, bad in (("passed", True), ("failed", "0"), ("schema_count", 1.0)):
            with self.subTest(field=field):
                value = _complete_report(commit)
                value[field] = bad
                with self.assertRaises(ReleaseVerificationError):
                    validate_release_test_report(
                        value,
                        project=PROJECT,
                        expected_commit=commit,
                        baseline_attempts=0,
                        baseline_real=0,
                        current_attempts=0,
                        current_real=0,
                    )

    def test_report_observation_scope_cannot_claim_os_isolation_or_public_authority(self) -> None:
        commit = "b" * 40
        mutations = {
            "missing_scope": None,
            "os_network_claim": {
                **_report(commit)["observation_scope"],
                "process_network_isolation": "ATTESTED",
            },
            "write_restore_claim": {
                **_report(commit)["observation_scope"],
                "write_restore_detection": "PASS",
            },
            "public_authority_claim": {
                **_report(commit)["observation_scope"],
                "public_release_authority": "GRANTED",
            },
            "unexpected_scope": {
                **_report(commit)["observation_scope"],
                "operating_system_firewall": "PASS",
            },
        }
        for name, scope in mutations.items():
            with self.subTest(name=name):
                value = _complete_report(commit)
                if scope is None:
                    value.pop("observation_scope")
                else:
                    value["observation_scope"] = scope
                with self.assertRaises(ReleaseVerificationError):
                    validate_release_test_report(
                        value,
                        project=PROJECT,
                        expected_commit=commit,
                        baseline_attempts=0,
                        baseline_real=0,
                        current_attempts=0,
                        current_real=0,
                    )
        value = _complete_report(commit)
        value["external_actions"]["final_real_external_actions"] = 1  # type: ignore[index]
        value["external_actions"]["real_external_action_delta"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(ReleaseVerificationError, "RELEASE_EXTERNAL_ACTION_DELTA_NONZERO"):
            validate_release_test_report(
                value,
                project=PROJECT,
                expected_commit=commit,
                baseline_attempts=0,
                baseline_real=0,
                current_attempts=0,
                current_real=1,
            )
        value = _complete_report(commit)
        value["external_actions"]["final_attempt_count"] = 1  # type: ignore[index]
        value["external_actions"]["attempt_delta"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(ReleaseVerificationError, "RELEASE_EXTERNAL_ACTION_DELTA_NONZERO"):
            validate_release_test_report(
                value,
                project=PROJECT,
                expected_commit=commit,
                baseline_attempts=0,
                baseline_real=0,
                current_attempts=1,
                current_real=0,
            )

    def test_report_rejects_private_output_and_mismatched_head(self) -> None:
        commit = "b" * 40
        value = _complete_report(commit)
        value["private"] = "C:\\Users\\private-user\\resume.docx"
        with self.assertRaisesRegex(ReleaseVerificationError, "RELEASE_TEST_REPORT_INVALID"):
            validate_release_test_report(
                value,
                project=PROJECT,
                expected_commit=commit,
                baseline_attempts=0,
                baseline_real=0,
                current_attempts=0,
                current_real=0,
            )
        value = _complete_report(commit)
        value["command_summary"][0]["id"] = "private-user" + chr(64) + "example.com"  # type: ignore[index]
        with self.assertRaisesRegex(ReleaseVerificationError, "RELEASE_TEST_REPORT_INVALID"):
            validate_release_test_report(
                value,
                project=PROJECT,
                expected_commit=commit,
                baseline_attempts=0,
                baseline_real=0,
                current_attempts=0,
                current_real=0,
            )
        with self.assertRaises(ReleaseVerificationError):
            validate_release_test_report(
                _complete_report(commit),
                project=PROJECT,
                expected_commit="c" * 40,
                baseline_attempts=0,
                baseline_real=0,
                current_attempts=0,
                current_real=0,
            )

    def test_atomic_report_write_uses_exclusive_temp_and_rejects_hardlinked_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            reports = project / "reports"
            reports.mkdir()
            target = reports / "release-test-results.json"
            with patch("jobops.release_verification.os.replace", wraps=os.replace) as replace:
                digest = safe_atomic_write_bytes(target, b'{"status":"PASS"}\n', project)
            self.assertEqual(digest[:7], "sha256:")
            self.assertEqual(target.read_bytes(), b'{"status":"PASS"}\n')
            replace.assert_called_once()
            linked = reports / "linked.json"
            try:
                os.link(target, linked)
            except OSError:
                self.skipTest("Hardlinks are unavailable in this test environment")
            with self.assertRaisesRegex(ReleaseVerificationError, "RELEASE_REPORT_TARGET_UNSAFE"):
                safe_atomic_write_bytes(target, b"replacement", project)

    def test_publish_transaction_restores_both_reports_after_late_action_delta(self) -> None:
        commit = "d" * 40
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            _write_toolchain_policy(project)
            reports = project / "reports"
            reports.mkdir()
            (project / "state").mkdir()
            report_path = reports / "release-test-results.json"
            checkpoint_path = reports / "checkpoint-final.json"
            old_report = b'{"old":"report"}\n'
            old_checkpoint = b'{"old":"checkpoint"}\n'
            report_path.write_bytes(old_report)
            checkpoint_path.write_bytes(old_checkpoint)
            git_path = project / "git.exe"
            git_path.write_bytes(b"placeholder")
            audit_values = [
                {"attempt_count": 0, "real_external_actions": 0},
                {"attempt_count": 0, "real_external_actions": 0},
                {"attempt_count": 1, "real_external_actions": 0},
            ]
            verification = {
                "status": "PASS",
                "source_commit": commit,
                "verification_scope": "LOCAL_DEVELOPMENT",
                "public_repository_ready": True,
                "runtime_closure_status": "UNATTESTED",
                "public_release_ready": False,
                "public_release_blockers": [
                    "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
                    "INDEPENDENT_QA_STALE_OR_MISSING",
                ],
                "independent_qa": {"status": "PENDING", "fresh_for_current_release": False},
                "tests": _complete_report(commit),
                "real_external_actions": 0,
            }
            with (
                patch("jobops.release_verification._assert_frozen_git"),
                patch("jobops.release_verification._database", return_value=object()),
                patch("jobops.release_verification.audit_real_external_actions", side_effect=audit_values),
                patch("jobops.release_verification.verify_release", return_value=verification),
            ):
                with self.assertRaisesRegex(
                    ReleaseVerificationError,
                    "RELEASE_EXTERNAL_ACTION_DELTA_NONZERO",
                ):
                    _publish_report_and_checkpoint(
                        project,
                        _complete_report(commit),
                        expected_commit=commit,
                        baseline_attempts=0,
                        baseline_real=0,
                        git_path=git_path,
                    )
            self.assertEqual(report_path.read_bytes(), old_report)
            self.assertEqual(checkpoint_path.read_bytes(), old_checkpoint)

    def test_fabricated_persisted_reports_can_never_authorize_signing(self) -> None:
        commit = "e" * 40
        report = _complete_report(commit)
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            reports = project / "reports"
            reports.mkdir()
            (project / "state").mkdir()
            (reports / "release-test-results.json").write_text(json.dumps(report), encoding="utf-8")
            checkpoint_path = reports / "checkpoint-final.json"
            git_path = project / "git.exe"
            git_path.write_bytes(b"placeholder")
            for scope in ("LOCAL_DEVELOPMENT", "PUBLIC_RELEASE"):
                checkpoint = _checkpoint(commit, report)
                checkpoint["verification_scope"] = scope
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                with self.subTest(scope=scope):
                    with self.assertRaisesRegex(
                        ReleaseVerificationError,
                        "RELEASE_SIGNING_PERSISTED_EVIDENCE_FORBIDDEN",
                    ):
                        authenticate_signing_evidence(
                            project,
                            expected_commit=commit,
                            git_path=git_path,
                        )
            output = io.StringIO()
            with (
                patch("jobops.release_verification.project_root", return_value=project),
                patch.object(sys, "argv", ["release_verification", "authenticate-signing-evidence"]),
                redirect_stdout(output),
            ):
                status = main()
            self.assertEqual(status, 2)
            self.assertIn("RELEASE_SIGNING_PERSISTED_EVIDENCE_FORBIDDEN", output.getvalue())

    def test_signing_transaction_returns_nonce_bound_local_only_evidence(self) -> None:
        commit = "f" * 40
        nonce = "1" * 64
        nonce_sha256 = "sha256:" + hashlib.sha256(nonce.encode("ascii")).hexdigest()
        local_result = {
            "status": "RELEASE_VERIFICATION_RECORDED",
            "source_commit": commit,
            "verification_scope": "LOCAL_DEVELOPMENT",
            "verification_run_id": "2" * 32,
            "transaction_nonce_sha256": nonce_sha256,
            "public_release_ready": True,
            "public_release_blockers": [],
            "independent_qa_fresh": True,
            "report_sha256": "sha256:" + "3" * 64,
            "checkpoint_sha256": "sha256:" + "4" * 64,
            "real_external_actions": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            node = project / "node.exe"
            git_path = project / "git.exe"
            node.write_bytes(b"node")
            git_path.write_bytes(b"git")
            with (
                patch(
                    "jobops.release_verification.run_release_verification",
                    return_value=local_result,
                ) as run,
                patch(
                    "jobops.release_verification.locked_authenticated_tool",
                    side_effect=_locked_tool_fixture,
                ),
                patch("jobops.release_verification._assert_frozen_git"),
            ):
                result = run_signing_transaction_verification(
                    project,
                    node_path=node,
                    git_path=git_path,
                    expected_commit=commit,
                    transaction_nonce=nonce,
                )
            self.assertFalse(run.call_args.kwargs["require_public_release"])
            self.assertEqual(
                run.call_args.kwargs["transaction_nonce_sha256"],
                nonce_sha256,
            )
            self.assertEqual(
                set(result),
                {
                    "schema_version", "status", "source_commit", "verification_scope",
                    "verification_run_id", "transaction_nonce_sha256", "report_sha256",
                    "checkpoint_sha256", "public_release_ready",
                    "public_release_blockers", "real_external_actions",
                },
            )
            self.assertEqual(result["status"], "LOCAL_BEST_EFFORT_VERIFICATION_PASS")
            self.assertEqual(result["verification_scope"], "LOCAL_RELEASE_QA")
            self.assertFalse(result["public_release_ready"])
            self.assertEqual(
                result["public_release_blockers"],
                ["RELEASE_RUNTIME_CLOSURE_UNATTESTED"],
            )
            self.assertEqual(result["real_external_actions"], 0)

    def test_local_signing_qa_is_nonce_bound_and_old_result_cannot_replay(self) -> None:
        commit = "a" * 40
        nonce_one = "5" * 64
        nonce_two = "6" * 64

        def direct_result(*_: object, **kwargs: object) -> dict[str, object]:
            direct_result.calls += 1
            return {
                "status": "RELEASE_VERIFICATION_RECORDED",
                "source_commit": commit,
                "verification_scope": "LOCAL_DEVELOPMENT",
                "verification_run_id": f"{direct_result.calls:032x}",
                "public_release_ready": False,
                "public_release_blockers": ["RELEASE_RUNTIME_CLOSURE_UNATTESTED"],
                "independent_qa_fresh": True,
                "report_sha256": "sha256:" + "7" * 64,
                "checkpoint_sha256": "sha256:" + "8" * 64,
                "transaction_nonce_sha256": kwargs["transaction_nonce_sha256"],
                "real_external_actions": 0,
            }

        direct_result.calls = 0
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".jobops-root").write_text("jobflow\n", encoding="utf-8")
            node = project / "node.exe"
            git_path = project / "git.exe"
            node.write_bytes(b"node")
            git_path.write_bytes(b"git")
            stale_result = direct_result(
                transaction_nonce_sha256=(
                    "sha256:" + hashlib.sha256(nonce_one.encode("ascii")).hexdigest()
                )
            )
            with patch(
                "jobops.release_verification.run_release_verification",
                return_value=stale_result,
            ):
                with self.assertRaisesRegex(
                    ReleaseVerificationError,
                    "RELEASE_LOCAL_QA_VERIFICATION_INVALID",
                ):
                    run_signing_transaction_verification(
                        project,
                        node_path=node,
                        git_path=git_path,
                        expected_commit=commit,
                        transaction_nonce=nonce_two,
                    )
            with (
                patch(
                    "jobops.release_verification.run_release_verification",
                    side_effect=direct_result,
                ),
                patch(
                    "jobops.release_verification.locked_authenticated_tool",
                    side_effect=_locked_tool_fixture,
                ),
                patch("jobops.release_verification._assert_frozen_git"),
            ):
                first = run_signing_transaction_verification(
                    project,
                    node_path=node,
                    git_path=git_path,
                    expected_commit=commit,
                    transaction_nonce=nonce_one,
                )
                second = run_signing_transaction_verification(
                    project,
                    node_path=node,
                    git_path=git_path,
                    expected_commit=commit,
                    transaction_nonce=nonce_two,
                )
            self.assertEqual(first["status"], "LOCAL_BEST_EFFORT_VERIFICATION_PASS")
            self.assertEqual(first["verification_scope"], "LOCAL_RELEASE_QA")
            self.assertFalse(first["public_release_ready"])
            self.assertNotEqual(first["verification_run_id"], second["verification_run_id"])
            self.assertNotEqual(first["transaction_nonce_sha256"], second["transaction_nonce_sha256"])
            replay_target = "sha256:" + hashlib.sha256(nonce_two.encode("ascii")).hexdigest()
            self.assertNotEqual(first["transaction_nonce_sha256"], replay_target)

    def test_git_cmd_shim_resolves_to_authenticated_mingw_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Git"
            wrapper = root / "cmd" / "git.exe"
            executed = root / "mingw64" / "bin" / "git.exe"
            wrapper.parent.mkdir(parents=True)
            executed.parent.mkdir(parents=True)
            wrapper.write_bytes(b"wrapper")
            executed.write_bytes(b"executed")
            self.assertEqual(_resolved_git_executable(wrapper), executed.resolve())


if __name__ == "__main__":
    unittest.main()
