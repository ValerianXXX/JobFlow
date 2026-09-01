from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import base64
import hashlib
import re
import struct
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "windows-runtime" / "jobflow-bootstrap.ps1"
PUBLISHED_UPDATE_FIXTURE = PROJECT / "tests" / "fixtures" / "published-update-v0.6.0"
PRODUCTION_MANIFEST = PUBLISHED_UPDATE_FIXTURE / "JobFlow-update-manifest.json"
PRODUCTION_SIGNATURE = PUBLISHED_UPDATE_FIXTURE / "JobFlow-update-manifest.sig.json"
PRODUCTION_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
PRODUCTION_MODULUS = (
    "4_GvTbc3dTuLSvzARhbG2Msy6mTvLnN5nINaBcSjAiEI986j44U1YxtmkAQ7ZQooPaA5s_xzJvFn5ZlYuExeaZy5L2om2LMfMljz7IOfFeEcz5wOcO8Rokd-zVK8fKFh4xAi4DkGoYxle1vpCiNdr09QeYH4o123GNCAKOfYjNW1WlHKh-9aRnlvrvt2JrsJni--JPLVmoThCeKUdH1ic1rojRR761L6U5AXRfYC46rp952HMr8xt7U_w_M0XukoJLuUtHa1UbGYZZIaU0lRstcpQiwIWtgub0K8Pnnf_l52kc02S2TlrFhGQko32pSOQPifMHiNy6Fg5n8I4F9IGl0MiHFh1fdiKCDzM_m5_bqhFUIIgMULF3BJTPYT41gqXZ_BRELH1g08Q41DAAIzpdDO2iOXvVVizPjvlqThNabz9enDt_uVoEPaTW1VfDV3rswbzfLaO0dTsbtlHxhLLe66u1XhOmnb0ELha6f9iOyijlgSNPwptc7YIpzN8G-d"
)
PRODUCTION_EXPONENT = "AQAB"
POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def _published_manifest_bytes() -> bytes:
    """Recover the exact bytes covered by the published v0.6.0 signature."""

    payload = PRODUCTION_MANIFEST.read_bytes()
    if payload.endswith(b"\r\n"):
        return payload[:-2]
    if payload.endswith(b"\n"):
        return payload[:-1]
    return payload


class JobFlowBootstrapTrustTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._local_data_context = tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-localdata-")
        self.local_app_data = Path(self._local_data_context.name)

    def tearDown(self) -> None:
        self._local_data_context.cleanup()

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        manifest = root / "manifest.json"
        signature = root / "manifest.sig.json"
        manifest.write_bytes(_published_manifest_bytes())
        shutil.copy2(PRODUCTION_SIGNATURE, signature)
        return manifest, signature

    def _run(
        self,
        manifest: Path,
        signature: Path,
        *,
        script: Path = SCRIPT,
        archive: Path | None = None,
        expand: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.local_app_data)
        if extra_env:
            environment.update(extra_env)
        source = script.read_text(encoding="utf-8")
        expression = "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
        if expression in source:
            self.assertEqual(source.count(expression), 1)
            literal = "'" + str(self.local_app_data).replace("'", "''") + "'"
            source = source.replace(expression, literal, 1)
        isolated_script = self.local_app_data / ("bootstrap-" + hashlib.sha256(os.urandom(32)).hexdigest() + ".ps1")
        isolated_script.write_text(source, encoding="utf-8")
        command = [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(isolated_script),
                "-ManifestPath",
                str(manifest),
                "-SignaturePath",
                str(signature),
            ]
        if archive is not None:
            command.extend(("-ArchivePath", str(archive)))
        if expand:
            command.append("-ExpandArchive")
        return subprocess.run(
            command,
            cwd=PROJECT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def _run_preflight_only(
        self,
        archive: Path,
        prefix: str,
        expected_bytes: int,
        expected_files: int,
    ) -> subprocess.CompletedProcess[str]:
        source = SCRIPT.read_text(encoding="utf-8")
        match = re.search(
            r"Add-Type -TypeDefinition @'\r?\n(?P<csharp>.*?)\r?\n'@",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        helper = archive.parent / "preflight-helper.ps1"
        helper.write_text(
            "param([string]$Archive,[string]$Prefix,[long]$Bytes,[int]$Files)\n"
            "$ErrorActionPreference='Stop'\n"
            "Add-Type -TypeDefinition @'\n"
            + match.group("csharp")  # type: ignore[union-attr]
            + "\n'@\n"
            "$locked=[JobFlowBootstrapFiles]::OpenLockedRegularFile($Archive,1610612736)\n"
            "try{[JobFlowBootstrapZip]::Preflight($locked.Stream,$Prefix,$Bytes,$Files,16777216)|Out-Null}\n"
            "finally{$locked.Dispose()}\n'PREFLIGHT_OK'\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-Archive",
                str(archive),
                "-Prefix",
                prefix,
                "-Bytes",
                str(expected_bytes),
                "-Files",
                str(expected_files),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    @staticmethod
    def _base64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_base64url(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))

    def _signed_v2_fixture(
        self,
        root: Path,
        *,
        attested_key_id: str | None = None,
    ) -> tuple[Path, Path, Path, str]:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        modulus = self._base64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
        exponent = self._base64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
        descriptor = {
            "algorithm": "RSA-PKCS1-v1_5-SHA256",
            "e": exponent,
            "n": modulus,
        }
        descriptor_bytes = json.dumps(
            descriptor, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        key_id = "sha256:" + hashlib.sha256(descriptor_bytes).hexdigest()
        manifest_value = {
            "publisher_attestation": {
                "release_key_id": attested_key_id if attested_key_id is not None else key_id,
            },
            "schema_version": 2,
        }
        manifest_bytes = json.dumps(
            manifest_value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        signature_bytes = private_key.sign(manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
        signature_value = {
            "algorithm": "RSA-PKCS1-v1_5-SHA256",
            "key_id": key_id,
            "schema_version": 1,
            "signature_b64url": self._base64url(signature_bytes),
        }
        manifest = root / "manifest-v2.json"
        signature = root / "manifest-v2.sig.json"
        bootstrap = root / "jobflow-bootstrap-fixture.ps1"
        manifest.write_bytes(manifest_bytes)
        signature.write_bytes(
            json.dumps(
                signature_value, ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
        )
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count(PRODUCTION_KEY_ID), 1)
        self.assertEqual(source.count(PRODUCTION_MODULUS), 1)
        bootstrap.write_text(
            source.replace(PRODUCTION_KEY_ID, key_id).replace(PRODUCTION_MODULUS, modulus),
            encoding="utf-8",
        )
        return bootstrap, manifest, signature, key_id

    def _sign_manifest_bytes(
        self, root: Path, manifest_bytes: bytes
    ) -> tuple[Path, Path, Path, str]:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        modulus = self._base64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
        exponent = self._base64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
        descriptor = {"algorithm": "RSA-PKCS1-v1_5-SHA256", "e": exponent, "n": modulus}
        key_id = "sha256:" + hashlib.sha256(
            json.dumps(descriptor, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        manifest_bytes = manifest_bytes.replace(b"KEY_ID_PLACEHOLDER", key_id.encode("ascii"))
        signature_bytes = private_key.sign(manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
        envelope = {
            "algorithm": "RSA-PKCS1-v1_5-SHA256",
            "key_id": key_id,
            "schema_version": 1,
            "signature_b64url": self._base64url(signature_bytes),
        }
        manifest = root / "manifest-v2.json"
        signature = root / "manifest-v2.sig.json"
        bootstrap = root / "jobflow-bootstrap-fixture.ps1"
        manifest.write_bytes(manifest_bytes)
        signature.write_bytes(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
        source = SCRIPT.read_text(encoding="utf-8")
        bootstrap.write_text(
            source.replace(PRODUCTION_KEY_ID, key_id).replace(PRODUCTION_MODULUS, modulus),
            encoding="utf-8",
        )
        return bootstrap, manifest, signature, key_id

    @staticmethod
    def _sha(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _complete_release_fixture(
        self,
        root: Path,
        *,
        extra_entries: list[tuple[str, bytes, int | None]] | None = None,
        closure_override: dict[str, object] | None = None,
        raw_closure: bytes | None = None,
        mutate_archive: Callable[[Path], None] | None = None,
        omit_files: set[str] | None = None,
    ) -> tuple[Path, Path, Path, Path, dict[str, object]]:
        version = "1.2.3"
        prefix = f"JobFlow-v{version}-windows-x64/"
        commit = "a" * 40
        runtime_package = {
            "name": "example-runtime",
            "version": "1.0.0",
            "filename": "example_runtime-1.0.0-py3-none-any.whl",
            "size": 1234,
            "sha256": "sha256:" + "d" * 64,
        }
        runtime_lock = self._canonical(
            {
                "schema_version": 1,
                "lock_type": "runtime-wheelhouse",
                "python_tag": "cp313",
                "abi": "cp313-or-abi3",
                "platform": "win_amd64",
                "only_binary": True,
                "packages": [runtime_package],
            }
        ) + b"\n"
        files = {
            ".jobops-root": b'{"schema_version":1,"kind":"JOBFLOW_APPLICATION_ROOT"}',
            "app/jobflow-1.2.3.dist-info/METADATA": b"Metadata-Version: 2.3\nName: jobflow\n",
            "app/jobflow-1.2.3.dist-info/entry_points.txt": b"[console_scripts]\njobflow = jobops.cli:main\n",
            "app/jobops/__init__.py": b"__version__ = '1.2.3'\n",
            "app/jobops/cli.py": b"def main():\n    return 0\n",
            "app/jobops/py.typed": b"",
            "app/jobops/runtime_health.py": b"def main():\n    return 0\n",
            "config/windows-cp313-build.lock": b'{"schema_version":1}\n',
            "config/windows-cp313-runtime.lock": runtime_lock,
            "runtime/python.exe": b"MZ" + b"x" * 62,
            "runtime/python313.dll": b"MZ" + b"d" * 62,
            "runtime/python313._pth": b"python313.zip\n.\n../app\n",
            "runtime/python313.zip": b"synthetic-embedded-python",
        }
        build_lock_body = files["config/windows-cp313-build.lock"]
        for omitted in omit_files or set():
            files.pop(omitted)
        records = [
            {"path": path, "sha256": self._sha(body), "size": len(body)}
            for path, body in sorted(files.items(), key=lambda item: item[0].upper())
        ]
        build_inputs = {
            "application_wheel_sha256": "sha256:" + "4" * 64,
            "application_wheel_provenance": {
                "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
                "source_commit": commit,
                "source_git_tree_oid": "b" * 40,
                "source_build_tree_sha256": "sha256:" + "7" * 64,
                "source_archive_sha256": "sha256:" + "8" * 64,
                "build_lock_sha256": self._sha(build_lock_body),
                "build_recipe_sha256": "sha256:" + "9" * 64,
                "pass_a_wheel_sha256": "sha256:" + "4" * 64,
                "pass_b_wheel_sha256": "sha256:" + "4" * 64,
                "reproducible": True,
            },
            "builder_toolchain_sha256": "sha256:" + "5" * 64,
            "wheel_lock_sha256": self._sha(runtime_lock),
            "wheelhouse_tree_sha256": "sha256:" + "3" * 64,
            "wheels": [
                {
                    "name": runtime_package["name"],
                    "version": runtime_package["version"],
                    "tag": "py3-none-any",
                    "size": runtime_package["size"],
                    "sha256": runtime_package["sha256"],
                }
            ],
        }
        closure: dict[str, object] = {
            "application_version": version,
            "artifact_type": "complete-runtime",
            "build_inputs": build_inputs,
            "file_count": len(records),
            "files": records,
            "layout": {
                "application_root": "app",
                "module": "jobops.cli",
                "python": "runtime/python.exe",
                "python_pth": "runtime/python313._pth",
            },
            "offline_smoke_tests": {"external_actions": 0, "import_passed": True, "schema_passed": True},
            "platform": "windows-x64",
            "protected_builder": {
                "deterministic_rebuild_match": True,
                "evidence_sha256": "sha256:" + "6" * 64,
                "outer_signature_ready": False,
            },
            "python": {
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_sha256": "sha256:" + "1" * 64,
                "sigstore_identity": "release" + chr(64) + "example.invalid",
                "sigstore_verified": False,
                "version": "3.13.15",
            },
            "schema_version": 1,
            "source_commit": commit,
            "status": "BUILT_UNATTESTED",
            "total_bytes": sum(len(body) for body in files.values()),
            "tree_sha256": self._sha(self._canonical(records)),
        }
        if closure_override:
            closure.update(closure_override)
        closure_bytes = raw_closure if raw_closure is not None else self._canonical(closure)
        archive = root / f"JobFlow-v{version}-windows-x64-complete.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for path, body in files.items():
                package.writestr(prefix + path, body)
            package.writestr(prefix + "runtime-closure.json", closure_bytes)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                for name, body, external in extra_entries or []:
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_STORED if name.endswith("/") else zipfile.ZIP_DEFLATED
                    if external is not None:
                        info.external_attr = external
                    package.writestr(info, body)
        if mutate_archive:
            mutate_archive(archive)
        archive_bytes = archive.read_bytes()
        runtime_summary = {
            "build_inputs": {
                "application_wheel_sha256": build_inputs["application_wheel_sha256"],
                "application_wheel_provenance": build_inputs["application_wheel_provenance"],
                "builder_toolchain_sha256": build_inputs["builder_toolchain_sha256"],
                "python_artifact_sha256": closure["python"]["artifact_sha256"],  # type: ignore[index]
                "wheel_count": len(build_inputs["wheels"]),
                "wheel_lock_sha256": build_inputs["wheel_lock_sha256"],
                "wheelhouse_tree_sha256": build_inputs["wheelhouse_tree_sha256"],
            },
            "file_count": closure["file_count"],
            "manifest_sha256": self._sha(closure_bytes),
            "platform": "windows-x64",
            "python_version": "3.13.15",
            "source_commit": commit,
            "source_payload_sha256": self._sha(archive_bytes),
            "structural_status": "BUILT_UNATTESTED",
            "total_bytes": closure["total_bytes"],
            "tree_sha256": closure["tree_sha256"],
        }
        policy = {
            "automatic_retry_submission_unknown": False,
            "external_actions_during_update": 0,
            "final_submit_user_only": True,
            "minimum_bootstrap_version": "0.6.0",
            "minimum_updater_version": "0.6.0",
            "publisher_attestation_required": True,
            "required_structural_status": "BUILT_UNATTESTED",
        }
        issuance_clock = datetime.now(timezone.utc)
        issued = issuance_clock.isoformat(timespec="microseconds").replace("+00:00", "Z")
        evidence_expires = (issuance_clock + timedelta(hours=1)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        manifest_value: dict[str, object] = {
            "asset": {
                "archive_prefix": prefix,
                "bytes": len(archive_bytes),
                "name": archive.name,
                "sha256": self._sha(archive_bytes),
            },
            "channel": "stable",
            "issued_at_utc": issued,
            "policy": policy,
            "predecessor": {
                "disallow_downgrade": True,
                "maximum_version_exclusive": version,
                "minimum_version": "1.0.0",
                "require_current_runtime_closure": True,
            },
            "product": "JobFlow",
            "publisher_attestation": {
                "build_inputs_sha256": self._sha(self._canonical(runtime_summary["build_inputs"])),
                "file_count": runtime_summary["file_count"],
                "format": "JOBFLOW_PUBLISHER_ATTESTATION_V2",
                "evidence_format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
                "runtime_build_evidence_sha256": self._sha(b"runtime-build-evidence"),
                "publisher_evidence_sha256": self._sha(b"publisher-evidence"),
                "evidence_expires_at_utc": evidence_expires,
                "signer_readiness_challenge_sha256": self._sha(b"signer-readiness"),
                "issued_at_utc": issued,
                "policy_sha256": self._sha(self._canonical(policy)),
                "release_key_id": "KEY_ID_PLACEHOLDER",
                "runtime_closure_manifest_sha256": runtime_summary["manifest_sha256"],
                "runtime_tree_sha256": runtime_summary["tree_sha256"],
                "source_commit": commit,
                "source_payload_sha256": runtime_summary["source_payload_sha256"],
                "status": "ATTESTED",
                "total_bytes": runtime_summary["total_bytes"],
            },
            "release": {"platform": "windows-x64", "source_commit": commit, "version": version},
            "runtime_closure": runtime_summary,
            "schema_version": 2,
        }
        # Construct one stable signing key, then bind its id into the signed body.
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        modulus = self._base64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
        exponent = self._base64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
        descriptor = {"algorithm": "RSA-PKCS1-v1_5-SHA256", "e": exponent, "n": modulus}
        key_id = self._sha(self._canonical(descriptor))
        manifest_value["publisher_attestation"]["release_key_id"] = key_id  # type: ignore[index]
        manifest_bytes = self._canonical(manifest_value)
        signature_bytes = private_key.sign(manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
        manifest = root / "manifest-v2.json"
        signature = root / "manifest-v2.sig.json"
        script = root / "jobflow-bootstrap-fixture.ps1"
        manifest.write_bytes(manifest_bytes)
        signature.write_bytes(self._canonical({
            "algorithm": "RSA-PKCS1-v1_5-SHA256", "key_id": key_id, "schema_version": 1,
            "signature_b64url": self._base64url(signature_bytes),
        }))
        script.write_text(
            SCRIPT.read_text(encoding="utf-8").replace(PRODUCTION_KEY_ID, key_id).replace(PRODUCTION_MODULUS, modulus),
            encoding="utf-8",
        )
        return script, manifest, signature, archive, manifest_value

    def _assert_redacted_failure(
        self, completed: subprocess.CompletedProcess[str], *private_values: str
    ) -> None:
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_BOOTSTRAP_FAILED")
        combined = completed.stdout + completed.stderr
        for value in private_values:
            self.assertNotIn(value, combined)

    def _assert_current_user_only_acl(self, paths: tuple[Path, ...]) -> None:
        quoted = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
        command = (
            "$ErrorActionPreference='Stop';$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
            f"$paths=@({quoted});"
            "$result=foreach($path in $paths){$acl=if([IO.Directory]::Exists($path)){[IO.Directory]::GetAccessControl($path)}else{[IO.File]::GetAccessControl($path)};"
            "$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));"
            "$full=@($rules|Where-Object{$_.IdentityReference.Value -eq $sid -and $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl});"
            "[pscustomobject]@{protected=$acl.AreAccessRulesProtected;owner_is_current=($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $sid);"
            "count=$rules.Count;only_current=@($rules|Where-Object{$_.IdentityReference.Value -ne $sid}).Count -eq 0;"
            "only_allow=@($rules|Where-Object{$_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow}).Count -eq 0;full_control=($full.Count -ge 1)}};"
            "$result|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        records = json.loads(completed.stdout.lstrip("\ufeff"))
        if isinstance(records, dict):
            records = [records]
        self.assertEqual(len(records), len(paths))
        for record in records:
            self.assertTrue(record["protected"])
            self.assertTrue(record["owner_is_current"])
            self.assertEqual(record["count"], 1)
            self.assertTrue(record["only_current"])
            self.assertTrue(record["only_allow"])
            self.assertTrue(record["full_control"])

    def _staging_snapshot(self) -> set[Path]:
        root = self.local_app_data / "JobOps" / "BootstrapStagingV2"
        return set(root.glob("stage-*")) if root.is_dir() else set()

    def _token_snapshot(self) -> set[Path]:
        root = self.local_app_data / "JobOps" / "BootstrapStagingTokensV2"
        return set(root.glob("*.json")) if root.is_dir() else set()

    def _resign_manifest_value(
        self, root: Path, value: dict[str, object]
    ) -> tuple[Path, Path, Path, str]:
        value["publisher_attestation"]["release_key_id"] = "KEY_ID_PLACEHOLDER"  # type: ignore[index]
        return self._sign_manifest_bytes(root, self._canonical(value))

    def test_valid_v2_signature_is_verified_offline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, _, value = self._complete_release_fixture(Path(raw))
            key_id = str(value["publisher_attestation"]["release_key_id"])  # type: ignore[index]
            secret = "JOBFLOW-BOOTSTRAP-CALLER-SECRET-DO-NOT-PRINT"
            completed = self._run(
                manifest,
                signature,
                script=script,
                extra_env={
                    "JOBFLOW_ATTACK_MARKER": secret,
                    "PYTHONPATH": secret,
                    "NODE_OPTIONS": secret,
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout.lstrip("\ufeff"))
            self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_MANIFEST_VERIFIED")
            self.assertTrue(result["signature_verified"])
            self.assertTrue(result["publisher_attestation_bound"])
            self.assertEqual(result["key_id"], key_id)
            self.assertEqual(result["real_external_actions"], 0)
            self.assertEqual(result["manifest_schema_version"], 2)
            self.assertRegex(result["manifest_sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertNotIn(secret, completed.stdout + completed.stderr)
            self.assertNotIn(str(manifest), completed.stdout + completed.stderr)

    def test_inventory_uses_exact_path_map_for_windows_order_and_accepts_empty_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, value = self._complete_release_fixture(Path(raw))
            prefix = str(value["asset"]["archive_prefix"])  # type: ignore[index]
            with zipfile.ZipFile(archive) as package:
                closure = json.loads(package.read(prefix + "runtime-closure.json"))
            records = closure["files"]
            paths = [str(record["path"]) for record in records]  # type: ignore[union-attr]
            entry_points = "app/jobflow-1.2.3.dist-info/entry_points.txt"
            metadata = "app/jobflow-1.2.3.dist-info/METADATA"
            self.assertLess(paths.index(entry_points), paths.index(metadata))
            self.assertEqual(
                next(record["size"] for record in records if record["path"] == "app/jobops/py.typed"),  # type: ignore[union-attr]
                0,
            )
            completed = self._run(
                manifest, signature, script=script, archive=archive, expand=True
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout.lstrip("\ufeff"))["status"],
                "JOBFLOW_BOOTSTRAP_RELEASE_VERIFIED",
            )
            self.assertEqual(self._staging_snapshot(), set())

    def test_manifest_only_rejects_incomplete_shape_and_future_minimum(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, _ = self._signed_v2_fixture(root)
            self._assert_redacted_failure(self._run(manifest, signature, script=script))

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            seed = Path(raw) / "seed"
            seed.mkdir()
            _, _, _, _, value = self._complete_release_fixture(seed)
            value["policy"]["minimum_bootstrap_version"] = "0.7.1"  # type: ignore[index]
            value["publisher_attestation"]["policy_sha256"] = self._sha(  # type: ignore[index]
                self._canonical(value["policy"])
            )
            signed = Path(raw) / "signed"
            signed.mkdir()
            script, manifest, signature, _ = self._resign_manifest_value(signed, value)
            completed = self._run(manifest, signature, script=script)
            self._assert_redacted_failure(completed)
            self.assertFalse((self.local_app_data / "JobOps").exists())

    def test_signed_manifest_rejects_duplicate_top_level_and_nested_properties(self) -> None:
        cases = {
            "top": (
                b'{"publisher_attestation":{"release_key_id":"KEY_ID_PLACEHOLDER"},'
                b'"schema_version":2,"schema_version":2}'
            ),
            "nested": (
                b'{"publisher_attestation":{"release_key_id":"KEY_ID_PLACEHOLDER",'
                b'"release_key_id":"KEY_ID_PLACEHOLDER"},"schema_version":2}'
            ),
            "escaped_alias": (
                b'{"publisher_attestation":{"release_key_id":"KEY_ID_PLACEHOLDER"},'
                b'"schema_version":2,"schema_\\u0076ersion":2}'
            ),
        }
        for label, body in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
                script, manifest, signature, _ = self._sign_manifest_bytes(Path(raw), body)
                self._assert_redacted_failure(self._run(manifest, signature, script=script))

    def test_valid_signed_complete_runtime_is_verified_without_persistent_staging(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, _ = self._complete_release_fixture(Path(raw))
            stages_before = self._staging_snapshot()
            tokens_before = self._token_snapshot()
            completed = self._run(manifest, signature, script=script, archive=archive, expand=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout.lstrip("\ufeff"))
            self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_RELEASE_VERIFIED")
            self.assertFalse(result["staging_path_disclosed"])
            self.assertFalse(result["activation_performed"])
            self.assertEqual(result["python_entry"], "runtime/python.exe")
            self.assertEqual(result["real_external_actions"], 0)
            self.assertNotIn("staging_token", result)
            self.assertNotIn(str(self.local_app_data), completed.stdout + completed.stderr)
            self.assertEqual(self._staging_snapshot(), stages_before)
            self.assertEqual(self._token_snapshot(), tokens_before)

    def test_signed_manifest_rejects_invalid_application_wheel_provenance(self) -> None:
        cases = ("source_commit", "pass_b_wheel_sha256", "reproducible", "extra_property")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-provenance-"
            ) as raw:
                root = Path(raw)
                seed = root / "seed"
                seed.mkdir()
                _, _, _, _, value = self._complete_release_fixture(seed)
                provenance = value["runtime_closure"]["build_inputs"][  # type: ignore[index]
                    "application_wheel_provenance"
                ]
                if case == "source_commit":
                    provenance["source_commit"] = "b" * 40  # type: ignore[index]
                elif case == "pass_b_wheel_sha256":
                    provenance["pass_b_wheel_sha256"] = "sha256:" + "e" * 64  # type: ignore[index]
                elif case == "reproducible":
                    provenance["reproducible"] = False  # type: ignore[index]
                else:
                    provenance["unexpected"] = True  # type: ignore[index]
                signed = root / "signed"
                signed.mkdir()
                script, manifest, signature, _ = self._resign_manifest_value(signed, value)

                completed = self._run(manifest, signature, script=script)

                self._assert_redacted_failure(completed)
                self.assertFalse((self.local_app_data / "JobOps").exists())

    def test_archive_mode_requires_both_archive_and_explicit_expand_switch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, _ = self._complete_release_fixture(Path(raw))
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive), str(archive)
            )
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, expand=True), str(manifest)
            )

    def test_signed_archive_preflight_rejects_unsafe_names_aliases_and_special_entries(self) -> None:
        prefix = "JobFlow-v1.2.3-windows-x64/"
        cases = {
            "traversal": [(prefix + "../escape.txt", b"x", None)],
            "backslash": [(prefix + "runtime\\evil.txt", b"x", None)],
            "absolute": [("/absolute.txt", b"x", None)],
            "empty_segment": [(prefix + "runtime//evil.txt", b"x", None)],
            "trailing_dot": [(prefix + "runtime/evil.", b"x", None)],
            "trailing_space": [(prefix + "runtime/evil ", b"x", None)],
            "reserved": [(prefix + "CON.txt", b"x", None)],
            "illegal_character": [(prefix + "runtime/evil<file", b"x", None)],
            "exact_duplicate": [(prefix + "runtime/python.exe", b"x", None)],
            "case_alias": [(prefix + "RUNTIME/PYTHON.EXE", b"x", None)],
            "file_directory_collision": [(prefix + "runtime", b"x", None)],
            "directory_entry": [(prefix + "empty/", b"", (0o40775 << 16) | 0x10)],
            "symlink": [(prefix + "runtime/link", b"target", 0o120777 << 16)],
            "compression_bomb": [(prefix + "runtime/bomb.bin", b"0" * (3 * 1024 * 1024), None)],
        }
        for label, entries in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
                root = Path(raw)
                before = self._staging_snapshot()
                script, manifest, signature, archive, _ = self._complete_release_fixture(root, extra_entries=entries)
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True),
                    str(archive),
                )
                self.assertEqual(self._staging_snapshot(), before)

    def test_zip_preflight_checks_are_causal_before_closure_validation(self) -> None:
        prefix = "JobFlow-v1.2.3-windows-x64/"
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            _, _, _, control, value = self._complete_release_fixture(Path(raw))
            passed = self._run_preflight_only(
                control,
                prefix,
                int(value["runtime_closure"]["total_bytes"]),  # type: ignore[index]
                int(value["runtime_closure"]["file_count"]),  # type: ignore[index]
            )
            self.assertEqual(passed.returncode, 0, passed.stdout)
            self.assertIn("PREFLIGHT_OK", passed.stdout)
        cases = {
            "traversal": [(prefix + "../escape.txt", b"x", None)],
            "file_directory_collision": [(prefix + "runtime", b"x", None)],
            "directory_entry": [(prefix + "empty/", b"", (0o40775 << 16) | 0x10)],
            "reserved_conin": [(prefix + "runtime/CONIN$.txt", b"x", None)],
            "oversized_segment": [(prefix + "runtime/" + "a" * 256, b"x", None)],
            "symlink": [(prefix + "runtime/link", b"target", 0o120777 << 16)],
            "compression_bomb": [(prefix + "runtime/bomb.bin", b"0" * (3 * 1024 * 1024), None)],
        }
        for label, entries in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                root = Path(raw)
                _, _, _, archive, _ = self._complete_release_fixture(root, extra_entries=entries)
                with zipfile.ZipFile(archive) as package:
                    payload = [
                        item
                        for item in package.infolist()
                        if not item.is_dir() and item.filename != prefix + "runtime-closure.json"
                    ]
                completed = self._run_preflight_only(
                    archive,
                    prefix,
                    sum(item.file_size for item in payload),
                    len(payload),
                )
                self.assertNotEqual(completed.returncode, 0, completed.stdout)

    def test_zip_preflight_rejects_structural_metadata_mutations(self) -> None:
        prefix = "JobFlow-v1.2.3-windows-x64/"

        def mutate(path: Path, label: str) -> None:
            value = bytearray(path.read_bytes())
            centrals = [match.start() for match in re.finditer(b"PK\x01\x02", value)]
            self.assertTrue(centrals)
            central = centrals[0]
            local = struct.unpack_from("<I", value, central + 42)[0]
            eocd = value.rfind(b"PK\x05\x06")
            self.assertGreaterEqual(eocd, 0)
            if label == "data_descriptor":
                struct.pack_into("<H", value, central + 8, struct.unpack_from("<H", value, central + 8)[0] | 8)
                struct.pack_into("<H", value, local + 6, struct.unpack_from("<H", value, local + 6)[0] | 8)
            elif label == "zip64":
                struct.pack_into("<I", value, central + 20, 0xFFFFFFFF)
            elif label == "multidisk":
                struct.pack_into("<H", value, eocd + 4, 1)
            elif label == "central_extra":
                struct.pack_into("<H", value, central + 30, 1)
            elif label == "central_comment":
                struct.pack_into("<H", value, central + 32, 1)
            elif label == "local_extra":
                struct.pack_into("<H", value, local + 28, 1)
            elif label == "unsupported_method":
                struct.pack_into("<H", value, central + 10, 99)
                struct.pack_into("<H", value, local + 8, 99)
            elif label == "local_central_mismatch":
                struct.pack_into("<H", value, local + 8, 0)
            elif label == "stored_size_mismatch":
                selected = next(
                    offset
                    for offset in centrals
                    if struct.unpack_from("<I", value, offset + 20)[0]
                    != struct.unpack_from("<I", value, offset + 24)[0]
                )
                selected_local = struct.unpack_from("<I", value, selected + 42)[0]
                struct.pack_into("<H", value, selected + 10, 0)
                struct.pack_into("<H", value, selected_local + 8, 0)
            elif label == "directory_crc":
                selected = next(
                    offset
                    for offset in centrals
                    if value[
                        offset + 46 : offset + 46 + struct.unpack_from("<H", value, offset + 28)[0]
                    ].endswith(b"/")
                )
                selected_local = struct.unpack_from("<I", value, selected + 42)[0]
                struct.pack_into("<I", value, selected + 16, 1)
                struct.pack_into("<I", value, selected_local + 14, 1)
            else:
                raise AssertionError(label)
            path.write_bytes(value)

        for label in (
            "data_descriptor",
            "zip64",
            "multidisk",
            "central_extra",
            "central_comment",
            "local_extra",
            "unsupported_method",
            "local_central_mismatch",
            "stored_size_mismatch",
            "directory_crc",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                extras = (
                    [(prefix + "empty/", b"", (0o40775 << 16) | 0x10)]
                    if label == "directory_crc"
                    else None
                )
                _, _, _, archive, value = self._complete_release_fixture(
                    Path(raw), extra_entries=extras
                )
                mutate(archive, label)
                completed = self._run_preflight_only(
                    archive,
                    prefix,
                    int(value["runtime_closure"]["total_bytes"]),  # type: ignore[index]
                    int(value["runtime_closure"]["file_count"]),  # type: ignore[index]
                )
                self.assertNotEqual(completed.returncode, 0, completed.stdout)

    def test_signed_archive_rejects_overlapping_local_file_ranges(self) -> None:
        def overlap(path: Path) -> None:
            value = bytearray(path.read_bytes())
            centrals = [match.start() for match in re.finditer(b"PK\x01\x02", value)]
            self.assertGreaterEqual(len(centrals), 2)
            first_offset = struct.unpack_from("<I", value, centrals[0] + 42)[0]
            original_compressed = struct.unpack_from("<I", value, centrals[0] + 20)[0]
            struct.pack_into("<I", value, centrals[0] + 20, original_compressed + 8)
            struct.pack_into("<I", value, first_offset + 18, original_compressed + 8)
            path.write_bytes(value)

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, _ = self._complete_release_fixture(
                Path(raw), mutate_archive=overlap
            )
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive, expand=True)
            )

    def test_signed_archive_rejects_unindexed_prefix_bytes(self) -> None:
        def prepend_unindexed_bytes(path: Path) -> None:
            value = bytearray(path.read_bytes())
            prefix = b"UNINDEXED"
            for central in [match.start() for match in re.finditer(b"PK\x01\x02", value)]:
                local_offset = struct.unpack_from("<I", value, central + 42)[0]
                struct.pack_into("<I", value, central + 42, local_offset + len(prefix))
            eocd = value.rfind(b"PK\x05\x06")
            self.assertGreaterEqual(eocd, 0)
            central_offset = struct.unpack_from("<I", value, eocd + 16)[0]
            struct.pack_into("<I", value, eocd + 16, central_offset + len(prefix))
            path.write_bytes(prefix + value)

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, _ = self._complete_release_fixture(
                Path(raw), mutate_archive=prepend_unindexed_bytes
            )
            before = self._staging_snapshot()
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive, expand=True)
            )
            self.assertEqual(self._staging_snapshot(), before)

    def test_signed_archive_preflight_rejects_encryption_flag(self) -> None:
        def mark_encrypted(path: Path) -> None:
            value = bytearray(path.read_bytes())
            local = value.index(b"PK\x03\x04")
            central = value.index(b"PK\x01\x02")
            struct.pack_into("<H", value, local + 6, struct.unpack_from("<H", value, local + 6)[0] | 1)
            struct.pack_into("<H", value, central + 8, struct.unpack_from("<H", value, central + 8)[0] | 1)
            path.write_bytes(value)

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            script, manifest, signature, archive, _ = self._complete_release_fixture(
                Path(raw), mutate_archive=mark_encrypted
            )
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive, expand=True)
            )

    def test_signed_runtime_closure_rejects_duplicate_properties(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            _, _, _, archive, _ = self._complete_release_fixture(seed)
            with zipfile.ZipFile(archive) as package:
                closure = package.read("JobFlow-v1.2.3-windows-x64/runtime-closure.json")
            cases = {
                "top": closure.replace(
                    b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
                ),
                "nested": closure.replace(
                    b'"application_root":"app"',
                    b'"application_root":"app","application_root":"app"',
                    1,
                ),
                "escaped_alias": closure.replace(
                    b'"application_root":"app"',
                    b'"application_root":"app","application_\\u0072oot":"app"',
                    1,
                ),
            }
            for label, duplicate in cases.items():
                with self.subTest(label=label):
                    case_root = root / label
                    case_root.mkdir()
                    script, manifest, signature, archive, _ = self._complete_release_fixture(
                        case_root, raw_closure=duplicate
                    )
                    before = self._staging_snapshot()
                    self._assert_redacted_failure(
                        self._run(manifest, signature, script=script, archive=archive, expand=True)
                    )
                    self.assertEqual(self._staging_snapshot(), before)

    def test_signed_asset_metadata_is_bound_to_exact_archive(self) -> None:
        for field in ("bytes", "sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
                root = Path(raw)
                _, _, _, archive, value = self._complete_release_fixture(root)
                if field == "bytes":
                    value["asset"]["bytes"] = int(value["asset"]["bytes"]) + 1  # type: ignore[index]
                else:
                    fake = "sha256:" + "9" * 64
                    value["asset"]["sha256"] = fake  # type: ignore[index]
                    value["runtime_closure"]["source_payload_sha256"] = fake  # type: ignore[index]
                    value["publisher_attestation"]["source_payload_sha256"] = fake  # type: ignore[index]
                script, manifest, signature, _ = self._resign_manifest_value(root, value)
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root)
            renamed = root / "renamed-identical.zip"
            shutil.copy2(archive, renamed)
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=renamed, expand=True),
                str(renamed),
            )

    def test_signed_runtime_closure_outer_bindings_and_inventory_are_enforced(self) -> None:
        # Both outer mutations remain correctly signed and internally attested,
        # but no longer match the extracted closure bytes/tree.
        for field in ("manifest_sha256", "tree_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
                root = Path(raw)
                _, _, _, archive, value = self._complete_release_fixture(root)
                fake = "sha256:" + ("8" if field == "manifest_sha256" else "7") * 64
                value["runtime_closure"][field] = fake  # type: ignore[index]
                publisher_field = (
                    "runtime_closure_manifest_sha256" if field == "manifest_sha256" else "runtime_tree_sha256"
                )
                value["publisher_attestation"][publisher_field] = fake  # type: ignore[index]
                script, manifest, signature, _ = self._resign_manifest_value(root, value)
                before = self._staging_snapshot()
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )
                self.assertEqual(self._staging_snapshot(), before)

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            _, _, _, seed_archive, _ = self._complete_release_fixture(seed)
            with zipfile.ZipFile(seed_archive) as package:
                closure = package.read("JobFlow-v1.2.3-windows-x64/runtime-closure.json")
            closure = closure.replace(b"runtime/python.exe", b"runtime/pyth0n.exe", 1)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root, raw_closure=closure)
            before = self._staging_snapshot()
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive, expand=True)
            )
            self.assertEqual(self._staging_snapshot(), before)

    def test_signed_runtime_closure_rejects_non_array_inventory_and_wheels(self) -> None:
        for label, mutate in (
            ("files_object", lambda value: value.__setitem__("files", value["files"][0])),
            ("wheels_null", lambda value: value["build_inputs"].__setitem__("wheels", None)),
            ("wheels_object", lambda value: value["build_inputs"].__setitem__("wheels", {})),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                root = Path(raw)
                seed = root / "seed"
                seed.mkdir()
                _, _, _, seed_archive, _ = self._complete_release_fixture(seed)
                with zipfile.ZipFile(seed_archive) as package:
                    closure_value = json.loads(
                        package.read("JobFlow-v1.2.3-windows-x64/runtime-closure.json")
                    )
                mutate(closure_value)
                script, manifest, signature, archive, _ = self._complete_release_fixture(
                    root, raw_closure=self._canonical(closure_value)
                )
                before = self._staging_snapshot()
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )
                self.assertEqual(self._staging_snapshot(), before)

    def test_signed_runtime_lock_must_match_projected_wheel_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            _, _, _, seed_archive, _ = self._complete_release_fixture(seed)
            with zipfile.ZipFile(seed_archive) as package:
                closure_value = json.loads(
                    package.read("JobFlow-v1.2.3-windows-x64/runtime-closure.json")
                )
            closure_value["build_inputs"]["wheels"][0]["version"] = "9.9.9"
            script, manifest, signature, archive, _ = self._complete_release_fixture(
                root, raw_closure=self._canonical(closure_value)
            )
            before = self._staging_snapshot()
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=archive, expand=True)
            )
            self.assertEqual(self._staging_snapshot(), before)

    def test_signed_runtime_closure_requires_exact_launcher_layout(self) -> None:
        for omitted in (
            ".jobops-root",
            "app/jobops/__init__.py",
            "app/jobops/cli.py",
            "app/jobops/runtime_health.py",
            "config/windows-cp313-build.lock",
            "config/windows-cp313-runtime.lock",
            "runtime/python.exe",
            "runtime/python313._pth",
            "runtime/python313.dll",
            "runtime/python313.zip",
        ):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                script, manifest, signature, archive, _ = self._complete_release_fixture(
                    Path(raw), omit_files={omitted}
                )
                before = self._staging_snapshot()
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )
                self.assertEqual(self._staging_snapshot(), before)

    def test_stage_acl_failure_leaves_no_orphans(self) -> None:
        failure_points = (("stage_acl", "Set-CurrentUserOnlyDirectoryAcl $stage"),)
        for label, statement in failure_points:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                root = Path(raw)
                script, manifest, signature, archive, _ = self._complete_release_fixture(root)
                source = script.read_text(encoding="utf-8")
                self.assertEqual(source.count(statement), 1)
                script.write_text(
                    source.replace(statement, 'throw "JOBFLOW_BOOTSTRAP_TEST_FAULT"', 1),
                    encoding="utf-8",
                )
                stages_before = self._staging_snapshot()
                tokens_before = self._token_snapshot()
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )
                self.assertEqual(self._staging_snapshot(), stages_before)
                self.assertEqual(self._token_snapshot(), tokens_before)

    def test_consecutive_successful_diagnostics_leave_zero_orphans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root)
            stages_before = self._staging_snapshot()
            tokens_before = self._token_snapshot()
            for _ in range(2):
                completed = self._run(
                    manifest, signature, script=script, archive=archive, expand=True
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn("staging_token", completed.stdout)
                self.assertNotIn(str(self.local_app_data), completed.stdout + completed.stderr)
            self.assertEqual(self._staging_snapshot(), stages_before)
            self.assertEqual(self._token_snapshot(), tokens_before)

    def test_archive_hard_link_and_ads_are_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root)
            link_root = root / "linked"
            link_root.mkdir()
            hardlink = link_root / archive.name
            try:
                os.link(archive, hardlink)
            except (OSError, NotImplementedError) as error:
                hardlink = None
            if hardlink is not None:
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=hardlink, expand=True)
                )
            else:
                warnings.warn("archive hard links are unavailable; hard-link branch skipped", RuntimeWarning)

        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root)
            stream_path = Path(str(archive) + ":jobflow-test")
            try:
                stream_path.write_bytes(b"untrusted")
                supported = stream_path.exists()
            except OSError:
                supported = False
            if supported:
                self._assert_redacted_failure(
                    self._run(manifest, signature, script=script, archive=archive, expand=True)
                )
            else:
                warnings.warn("archive alternate data streams are unavailable; ADS branch skipped", RuntimeWarning)

    def test_archive_symbolic_link_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            script, manifest, signature, archive, _ = self._complete_release_fixture(root)
            link_root = root / "linked"
            link_root.mkdir()
            link = link_root / archive.name
            try:
                link.symlink_to(archive)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"archive symbolic links are unavailable: {type(error).__name__}")
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script, archive=link, expand=True)
            )

    def test_crypto_valid_v2_with_different_legal_attestation_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            wrong_key_id = "sha256:" + "a" * 64
            script, manifest, signature, _ = self._signed_v2_fixture(
                Path(raw), attested_key_id=wrong_key_id
            )
            self._assert_redacted_failure(
                self._run(manifest, signature, script=script), wrong_key_id
            )

    def test_published_v060_signature_is_cryptographically_valid_but_legacy_v1_is_rejected(self) -> None:
        envelope = json.loads(PRODUCTION_SIGNATURE.read_text(encoding="utf-8"))
        self.assertEqual(envelope["key_id"], PRODUCTION_KEY_ID)
        modulus = int.from_bytes(self._decode_base64url(PRODUCTION_MODULUS), "big")
        exponent = int.from_bytes(self._decode_base64url(PRODUCTION_EXPONENT), "big")
        production_public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        production_public_key.verify(
            self._decode_base64url(envelope["signature_b64url"]),
            _published_manifest_bytes(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            manifest, signature = self._fixture(Path(raw))
            self._assert_redacted_failure(self._run(manifest, signature))

    def test_tampered_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            manifest, signature = self._fixture(Path(raw))
            manifest.write_bytes(manifest.read_bytes().replace(b'"JobFlow"', b'"JobFl0w"', 1))
            self._assert_redacted_failure(self._run(manifest, signature), str(manifest))

    def test_tampered_signature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            manifest, signature = self._fixture(Path(raw))
            value = bytearray(signature.read_bytes())
            marker = value.index(b'"signature_b64url":"') + len(b'"signature_b64url":"')
            value[marker] = ord("A") if value[marker] != ord("A") else ord("B")
            signature.write_bytes(bytes(value))
            self._assert_redacted_failure(self._run(manifest, signature), str(signature))

    def test_wrong_key_id_and_algorithm_are_rejected(self) -> None:
        for field, old, new in (
            ("key", b"sha256:1037057f", b"sha256:2037057f"),
            ("algorithm", b"RSA-PKCS1-v1_5-SHA256", b"RSA-PKCS1-v1_5-SHA512"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                manifest, signature = self._fixture(Path(raw))
                signature.write_bytes(signature.read_bytes().replace(old, new, 1))
                self._assert_redacted_failure(self._run(manifest, signature))

    def test_oversized_inputs_are_rejected_before_parsing(self) -> None:
        cases = (("manifest", 64 * 1024 + 1), ("signature", 16 * 1024 + 1))
        for selected, size in cases:
            with self.subTest(selected=selected), tempfile.TemporaryDirectory(
                prefix="jobflow-bootstrap-"
            ) as raw:
                manifest, signature = self._fixture(Path(raw))
                target = manifest if selected == "manifest" else signature
                target.write_bytes(b"x" * size)
                self._assert_redacted_failure(self._run(manifest, signature))

    def test_symbolic_link_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            manifest, signature = self._fixture(root)
            link = root / "manifest-link.json"
            try:
                link.symlink_to(manifest)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links are unavailable: {type(error).__name__}")
            self._assert_redacted_failure(self._run(link, signature))

    def test_hard_link_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            root = Path(raw)
            manifest, signature = self._fixture(root)
            link = root / "manifest-hardlink.json"
            try:
                os.link(manifest, link)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"hard links are unavailable: {type(error).__name__}")
            self._assert_redacted_failure(self._run(link, signature))

    def test_alternate_data_stream_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            manifest, signature = self._fixture(Path(raw))
            stream_path = Path(str(manifest) + ":jobflow-test")
            try:
                stream_path.write_bytes(b"untrusted")
                if not stream_path.exists():
                    self.skipTest("alternate data streams are unavailable")
            except OSError as error:
                self.skipTest(f"alternate data streams are unavailable: {type(error).__name__}")
            self._assert_redacted_failure(self._run(manifest, signature))

    def test_unc_and_device_inputs_are_rejected_before_file_open(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            _, signature = self._fixture(Path(raw))
            for value in (
                Path(r"\\localhost\JobFlowBootstrapNoShare\manifest.json"),
                Path(r"\\.\NUL"),
            ):
                with self.subTest(path=str(value)):
                    self._assert_redacted_failure(self._run(value, signature), str(value))

    def test_local_app_data_junction_is_rejected_before_temp_creation_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            base = Path(raw)
            target = base / "target"
            target.mkdir()
            junction = base / "local-data-link"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if completed.returncode != 0 or not junction.exists():
                self.skipTest("directory junctions are unavailable")
            script, manifest, signature, _ = self._signed_v2_fixture(base)
            source = script.read_text(encoding="utf-8")
            expression = "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
            self.assertEqual(source.count(expression), 1)
            literal = "'" + str(junction).replace("'", "''") + "'"
            script.write_text(source.replace(expression, literal, 1), encoding="utf-8")
            before = set(target.iterdir())
            self._assert_redacted_failure(self._run(manifest, signature, script=script))
            self.assertEqual(set(target.iterdir()), before)

    def test_compiler_temp_validation_failure_does_not_leave_orphan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            base = Path(raw)
            local_data = base / "local-data"
            local_data.mkdir()
            script, manifest, signature, _ = self._signed_v2_fixture(base)
            source = script.read_text(encoding="utf-8")
            expression = "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
            validation = "Assert-ExistingLocalDirectoryChain $trustedTemporaryRoot | Out-Null"
            self.assertEqual(source.count(expression), 1)
            self.assertEqual(source.count(validation), 1)
            literal = "'" + str(local_data).replace("'", "''") + "'"
            script.write_text(
                source.replace(expression, literal, 1).replace(
                    validation, 'throw "JOBFLOW_BOOTSTRAP_TEST_TEMP_VALIDATION"', 1
                ),
                encoding="utf-8",
            )
            before = set(local_data.glob("JobFlowBootstrap-*"))
            self._assert_redacted_failure(self._run(manifest, signature, script=script))
            self.assertEqual(set(local_data.glob("JobFlowBootstrap-*")), before)

    def test_input_ancestor_junction_is_rejected_for_manifest_signature_and_archive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            base = Path(raw)
            target = base / "target"
            target.mkdir()
            junction = base / "input-link"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if completed.returncode != 0 or not junction.exists():
                self.skipTest("directory junctions are unavailable")

            script, manifest, signature, archive, _ = self._complete_release_fixture(target)
            linked_manifest = junction / manifest.name
            linked_signature = junction / signature.name
            linked_archive = junction / archive.name
            for label, selected_manifest, selected_signature, selected_archive, expand in (
                ("manifest", linked_manifest, signature, None, False),
                ("signature", manifest, linked_signature, None, False),
                ("archive", manifest, signature, linked_archive, True),
            ):
                with self.subTest(label=label):
                    self._assert_redacted_failure(
                        self._run(
                            selected_manifest,
                            selected_signature,
                            script=script,
                            archive=selected_archive,
                            expand=expand,
                        )
                    )

    def test_no_follow_cleanup_preserves_junction_target_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-") as raw:
            base = Path(raw)
            stage = base / "stage-test"
            target = base / "outside"
            stage.mkdir()
            target.mkdir()
            (stage / "ordinary.txt").write_text("delete me", encoding="utf-8")
            sentinel = target / "sentinel.txt"
            sentinel.write_text("preserve me", encoding="utf-8")
            junction = stage / "junction"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if completed.returncode != 0 or not junction.exists():
                self.skipTest("directory junctions are unavailable")

            source = SCRIPT.read_text(encoding="utf-8")
            match = re.search(
                r"Add-Type -TypeDefinition @'\r?\n(?P<csharp>.*?)\r?\n'@",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(match)
            helper = base / "cleanup-helper.ps1"
            helper.write_text(
                "param([string]$Target)\nAdd-Type -TypeDefinition @'\n"
                + match.group("csharp")  # type: ignore[union-attr]
                + "\n'@\n[JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollow($Target)\n",
                encoding="utf-8",
            )
            cleaned = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-Target",
                    str(stage),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stdout)
            self.assertFalse(stage.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me")

    def test_signature_gate_precedes_json_and_no_process_or_network_exists(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        marker = "# JOBFLOW_BOOTSTRAP_SIGNATURE_VERIFIED_BOUNDARY"
        self.assertEqual(source.count(marker), 1)
        before, after = source.split(marker, 1)
        main_parse = re.search(
            r"(?m)^\s*\$manifestValue\s*=\s*\$manifestText\s*\|\s*ConvertFrom-Json\s*$",
            source,
        )
        self.assertIsNotNone(main_parse)
        self.assertGreater(main_parse.start(), source.index(marker))  # type: ignore[union-attr]
        self.assertIn("$rsa.VerifyData(", before)
        self.assertIn("if (-not $signatureVerified)", before)
        self.assertNotIn("$manifestText | ConvertFrom-Json", before)
        self.assertIn("$manifestText | ConvertFrom-Json", after)
        for forbidden in (
            "Invoke-WebRequest",
            "Invoke-RestMethod",
            "System.Net.WebClient",
            "System.Net.Http.HttpClient",
            "Start-Process",
        ):
            self.assertNotIn(forbidden, source)
        module_invocations = re.findall(r'-m\s+([A-Za-z0-9_.-]+)', source)
        self.assertEqual(module_invocations, ["jobops.runtime_health"])
        self.assertNotIn("-m jobops.cli", source)
        self.assertNotIn("onboarding-center", source)
        self.assertNotIn("ProcessStartInfo", source)
        self.assertEqual(source.count("CreateProcessW("), 2)
        self.assertIn("function Invoke-CandidateRuntimeHealth", source)
        self.assertNotIn("$env:", source)
        self.assertLess(source.index("SetEnvironmentVariable"), source.index("Add-Type"))
        self.assertIn('@("SystemRoot", "WinDir", "TEMP", "TMP")', source)
        self.assertNotIn("PATH\"", source)
        self.assertNotIn("PSModulePath", source)
        self.assertIn(
            f'"{PRODUCTION_KEY_ID}"',
            source,
        )
        self.assertIn(f'"{PRODUCTION_MODULUS}"', source)
        self.assertNotIn("config\\update-channel.json", source)
        self.assertNotIn("OutputPath", source)
        self.assertIn('"BootstrapStagingV2"', source)
        self.assertIn("AssertNoDuplicateProperties($manifestText)", after)
        self.assertNotRegex(source, r"(?im)^\s*&\s+.*python")
        self.assertNotRegex(source, r"(?i)Get-ChildItem[^\r\n]*-Recurse")
        self.assertNotRegex(source, r"(?i)Remove-Item[^\r\n]*-Recurse")
        self.assertIn("DeleteDirectoryTreeNoFollow", source)
        self.assertIn("CreateNewDirectory", source)
        self.assertIn("GetDriveTypeW", source)
        self.assertIn("AssertLocalPathWithoutReparse(FullPath, false);", source)
        self.assertLess(
            source.index("AssertLocalPathWithoutReparse(FullPath, false);"),
            source.index("Stream = new FileStream("),
        )
        self.assertIn("localRanges.Sort", source)
        self.assertNotIn("[IO.Directory]::Delete($trustedTemporaryRoot, $true)", source)

    def test_powershell_51_parser_accepts_script(self) -> None:
        command = (
            "$tokens=$null;$errors=$null;"
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "'" + str(SCRIPT).replace("'", "''") + "',[ref]$tokens,[ref]$errors)|Out-Null;"
            "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
        )
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=PROJECT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)


if __name__ == "__main__":
    unittest.main()
