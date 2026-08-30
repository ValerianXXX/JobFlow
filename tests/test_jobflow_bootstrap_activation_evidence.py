from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path

import test_jobflow_bootstrap_activation as activation_tests
from test_jobflow_bootstrap_trust import POWERSHELL, PROJECT


class JobFlowBootstrapActivationEvidenceTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = activation_tests.JobFlowBootstrapActivationTests(methodName="runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @staticmethod
    def _sha(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    def _verify_installed(self, *extra: str) -> subprocess.CompletedProcess[str]:
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
                str(self.fixture.script),
                "-VerifyInstalled",
                *extra,
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

    def _trust_directory(self, pointer: dict[str, object]) -> Path:
        return (
            self.fixture.install
            / "Data"
            / "state"
            / "activation-trust"
            / str(pointer["version_directory"])
        )

    def _activate(self) -> tuple[dict[str, object], dict[str, object], Path]:
        release = self.fixture._release("1.0.0")
        completed = self.fixture._run(release)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        pointer = self.fixture._pointer()
        return release, pointer, self._trust_directory(pointer)

    def test_activation_persists_exact_signed_inputs_and_canonical_bound_evidence(self) -> None:
        release, pointer, trust = self._activate()
        self.assertEqual(
            sorted(path.name for path in trust.iterdir()),
            [
                "activation-evidence.json",
                "release-manifest.json",
                "release-manifest.signature.json",
            ],
        )
        manifest_bytes = Path(release["manifest"]).read_bytes()
        signature_bytes = Path(release["signature"]).read_bytes()
        self.assertEqual((trust / "release-manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual(
            (trust / "release-manifest.signature.json").read_bytes(),
            signature_bytes,
        )

        evidence_bytes = (trust / "activation-evidence.json").read_bytes()
        evidence = json.loads(evidence_bytes)
        self.assertEqual(evidence_bytes, self.fixture._canonical(evidence))
        self.assertEqual(
            set(evidence),
            {
                "canonical_pointer_sha256",
                "kind",
                "manifest_sha256",
                "release_key_id",
                "runtime_closure_manifest_sha256",
                "runtime_tree_sha256",
                "schema_version",
                "signature_envelope_sha256",
                "source_payload_sha256",
                "transaction_id",
                "version",
                "version_directory",
            },
        )
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["kind"], "JOBFLOW_ACTIVATION_TRUST_EVIDENCE")
        self.assertEqual(evidence["manifest_sha256"], self._sha(manifest_bytes))
        self.assertEqual(evidence["signature_envelope_sha256"], self._sha(signature_bytes))
        self.assertEqual(
            evidence["canonical_pointer_sha256"],
            self._sha(self.fixture._canonical(pointer)),
        )
        self.assertEqual(
            evidence["runtime_closure_manifest_sha256"],
            pointer["runtime_closure_manifest_sha256"],
        )
        self.assertEqual(evidence["runtime_tree_sha256"], pointer["runtime_tree_sha256"])
        self.assertEqual(evidence["release_key_id"], pointer["release_key_id"])
        self.assertEqual(evidence["source_payload_sha256"], pointer["source_payload_sha256"])
        self.assertEqual(evidence["version"], pointer["version"])
        self.assertEqual(evidence["version_directory"], pointer["version_directory"])
        self.assertRegex(str(evidence["transaction_id"]), r"^[0-9a-f]{32}$")

    def test_verify_installed_has_strict_redacted_output_and_takes_no_paths(self) -> None:
        _release, pointer, _trust = self._activate()
        completed = self._verify_installed()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(
            set(result),
            {
                "activation_committed_during_recovery",
                "manifest_sha256",
                "paths_disclosed",
                "pointer_sha256",
                "real_external_actions",
                "recovery_performed",
                "release_key_id",
                "runtime_closure_manifest_sha256",
                "runtime_tree_sha256",
                "schema_version",
                "signature_envelope_sha256",
                "signed_activation_evidence_verified",
                "source_payload_sha256",
                "status",
                "version",
            },
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "JOBFLOW_INSTALLED_RUNTIME_VERIFIED")
        self.assertEqual(result["version"], pointer["version"])
        self.assertEqual(
            result["pointer_sha256"],
            self._sha(self.fixture._canonical(pointer)),
        )
        self.assertRegex(result["pointer_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(result["signed_activation_evidence_verified"])
        self.assertFalse(result["recovery_performed"])
        self.assertFalse(result["activation_committed_during_recovery"])
        self.assertFalse(result["paths_disclosed"])
        self.assertEqual(result["real_external_actions"], 0)
        self.assertNotIn(str(self.fixture.root), completed.stdout + completed.stderr)
        self.assertNotIn("transaction_id", result)

        manifest = Path(self.fixture.root / "never-read.json")
        for arguments in (
            ("-ManifestPath", str(manifest)),
            ("-SignaturePath", str(manifest)),
            ("-ArchivePath", str(manifest)),
            ("-RecoverOnly",),
        ):
            with self.subTest(arguments=arguments):
                rejected = self._verify_installed(*arguments)
                self.assertEqual(rejected.returncode, 1)
                self.assertEqual(rejected.stdout, "")
                self.assertEqual(rejected.stderr, "JOBFLOW_BOOTSTRAP_FAILED\n")

        current_path = self.fixture.install / "current.json"
        current_bytes = current_path.read_bytes()
        tampered_pointer = json.loads(current_bytes)
        tampered_pointer["source_commit"] = "f" * 40
        current_path.write_bytes(self.fixture._canonical(tampered_pointer))
        tampered = self._verify_installed()
        self.assertEqual(tampered.returncode, 1)
        self.assertEqual(tampered.stdout, "")
        self.assertEqual(tampered.stderr, "JOBFLOW_BOOTSTRAP_FAILED\n")
        current_path.write_bytes(current_bytes)
        restored = self._verify_installed()
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(
            json.loads(restored.stdout.lstrip("\ufeff"))["pointer_sha256"],
            result["pointer_sha256"],
        )

    def test_verify_installed_rejects_any_tampered_trust_artifact_without_mutation(self) -> None:
        _release, _pointer, trust = self._activate()
        pointer_before = (self.fixture.install / "current.json").read_bytes()
        target_before = self.fixture._tree_snapshot(
            self.fixture.install / "Application" / "versions"
        )
        artifacts = (
            "release-manifest.json",
            "release-manifest.signature.json",
            "activation-evidence.json",
        )
        for name in artifacts:
            path = trust / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original[:-1] + bytes((original[-1] ^ 1,)))
                rejected = self._verify_installed()
                self.assertEqual(rejected.returncode, 1)
                self.assertEqual(rejected.stdout, "")
                self.assertEqual(rejected.stderr, "JOBFLOW_BOOTSTRAP_FAILED\n")
                self.assertEqual(
                    (self.fixture.install / "current.json").read_bytes(),
                    pointer_before,
                )
                self.assertEqual(
                    self.fixture._tree_snapshot(
                        self.fixture.install / "Application" / "versions"
                    ),
                    target_before,
                )
                path.write_bytes(original)
                healthy = self._verify_installed()
                self.assertEqual(healthy.returncode, 0, healthy.stderr)

    def test_preexisting_activation_evidence_mismatch_fails_closed(self) -> None:
        release, _pointer, trust = self._activate()
        current_before = (self.fixture.install / "current.json").read_bytes()
        evidence_path = trust / "activation-evidence.json"
        evidence = json.loads(evidence_path.read_bytes())
        evidence["runtime_tree_sha256"] = "sha256:" + "0" * 64
        evidence_path.write_bytes(self.fixture._canonical(evidence))

        repeated = self.fixture._run(release)
        self.fixture._assert_failed(repeated)
        self.assertEqual((self.fixture.install / "current.json").read_bytes(), current_before)

    def test_verify_installed_recovers_pending_transaction_before_verification(self) -> None:
        first = self.fixture._release("1.0.0")
        self.assertEqual(self.fixture._run(first).returncode, 0)
        current_before = (self.fixture.install / "current.json").read_bytes()
        second = self.fixture._release("1.1.0", predecessor_minimum="1.0.0")
        boundary = "# JOBFLOW_ACTIVATION_PREPARED_BOUNDARY"
        crash = (
            boundary
            + "\n        [Diagnostics.Process]::GetCurrentProcess().Kill(); "
            + "[Threading.Thread]::Sleep(10000)"
        )
        crash_script = self.fixture._write_script(
            "bootstrap-crash-before-verify.ps1",
            mutation=(boundary, crash),
        )
        crashed = self.fixture._run(second, script=crash_script)
        self.assertNotEqual(crashed.returncode, 0)
        candidate = second["value"]["runtime_closure"]["source_payload_sha256"]
        candidate_directory = "v1.1.0-" + str(candidate)[7:19]
        candidate_trust = (
            self.fixture.install
            / "Data"
            / "state"
            / "activation-trust"
            / candidate_directory
        )
        self.assertTrue(candidate_trust.is_dir())

        verified = self._verify_installed()
        self.assertEqual(verified.returncode, 0, verified.stderr)
        result = json.loads(verified.stdout.lstrip("\ufeff"))
        self.assertTrue(result["recovery_performed"])
        self.assertFalse(result["activation_committed_during_recovery"])
        self.assertTrue(result["signed_activation_evidence_verified"])
        self.assertEqual((self.fixture.install / "current.json").read_bytes(), current_before)
        self.assertFalse(candidate_trust.exists())


if __name__ == "__main__":
    unittest.main()
