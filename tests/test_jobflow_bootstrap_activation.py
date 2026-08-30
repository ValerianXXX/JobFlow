from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from test_jobflow_bootstrap_trust import (
    POWERSHELL,
    PRODUCTION_KEY_ID,
    PRODUCTION_MODULUS,
    PROJECT,
    SCRIPT,
)
from test_runtime_health import (
    CONFIG_NAMES,
    HEALTH_SOURCE,
    PACKAGE_SENTINELS,
    SCHEMA_NAMES,
    _policy as runtime_health_policy,
    _python_support_policy as runtime_health_python_support_policy,
    _runtime_lock as runtime_health_lock,
    _runtime_source as runtime_health_source,
    _update_channel as runtime_health_update_channel,
)


class JobFlowBootstrapActivationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-bootstrap-activation-")
        self.root = Path(self.temporary.name)
        self.local_app_data = self.root / "LocalAppData"
        self.local_app_data.mkdir()
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()
        self.modulus = self._b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
        exponent = self._b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
        descriptor = {"algorithm": "RSA-PKCS1-v1_5-SHA256", "e": exponent, "n": self.modulus}
        self.key_id = self._sha(self._canonical(descriptor))
        self.script = self._write_script("bootstrap.ps1")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _sha(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _write_script(self, name: str, *, mutation: tuple[str, str] | None = None) -> Path:
        source = SCRIPT.read_text(encoding="utf-8")
        source = source.replace(PRODUCTION_KEY_ID, self.key_id).replace(PRODUCTION_MODULUS, self.modulus)
        expression = "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
        self.assertEqual(source.count(expression), 1)
        literal = "'" + str(self.local_app_data).replace("'", "''") + "'"
        source = source.replace(expression, literal, 1)
        if mutation is not None:
            self.assertEqual(source.count(mutation[0]), 1)
            source = source.replace(mutation[0], mutation[1], 1)
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        return path

    def _release(
        self,
        version: str,
        *,
        payload_tag: str = "default",
        predecessor_minimum: str = "0.6.0",
        minimum_bootstrap: str = "0.6.0",
        minimum_updater: str = "0.6.0",
        health_source: bytes | None = None,
        legacy_v1_predecessors: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        release_root = self.root / ("release-" + version + "-" + payload_tag)
        release_root.mkdir()
        prefix = f"JobFlow-v{version}-windows-x64/"
        commit = hashlib.sha1(f"{version}:{payload_tag}".encode()).hexdigest()
        base = Path(sys.base_prefix)
        runtime_python = Path(sys._base_executable).read_bytes()
        runtime_dll_name = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        runtime_dll = (base / runtime_dll_name).read_bytes()
        test_pth_name = f"python{sys.version_info.major}{sys.version_info.minor}._pth"
        test_pth = "\n".join(
            (
                str(base / f"python{sys.version_info.major}{sys.version_info.minor}.zip"),
                str(base / "DLLs"),
                str(base / "Lib"),
                "../app",
                "",
            )
        ).encode("utf-8")
        files = {
            ".jobops-root": b"jobops-root-v1\n",
            "app/jobflow-1.2.3.dist-info/METADATA": b"Metadata-Version: 2.3\nName: jobflow\n",
            "app/jobflow-1.2.3.dist-info/entry_points.txt": b"[console_scripts]\njobflow = jobops.cli:main\n",
            "app/jobops/__init__.py": f"__version__ = '{version}'\n".encode(),
            "app/jobops/cli.py": ("def main():\n    return '" + payload_tag + "'\n").encode(),
            "app/jobops/runtime_health.py": (
                HEALTH_SOURCE.read_bytes() if health_source is None else health_source
            ),
            "app/jobops/py.typed": b"",
            "runtime/python.exe": runtime_python,
            "runtime/python313.dll": b"MZ" + b"d" * 32,
            "runtime/python313._pth": b"python313.zip\n.\n../app\n",
            "runtime/python313.zip": b"synthetic-embedded-python",
            f"runtime/{runtime_dll_name}": runtime_dll,
            f"runtime/{test_pth_name}": test_pth,
        }
        if legacy_v1_predecessors is not None:
            for name in (
                "jobflow-bootstrap.ps1",
                "start-installed-jobflow.ps1",
                "check-installed-jobflow.ps1",
                "update-installed-jobflow.ps1",
                "rollback-installed-jobflow.ps1",
                "uninstall-installed-jobflow.ps1",
                "jobflow-runtime-locks.ps1",
                "manage-authorized-discovery-task.ps1",
                "run-authorized-discovery-task.ps1",
                "Start JobFlow.cmd",
                "Check JobFlow.cmd",
                "Update JobFlow.cmd",
                "Rollback JobFlow.cmd",
                "Uninstall JobFlow.cmd",
            ):
                files[f"scripts/windows-runtime/{name}"] = (
                    f"JOBFLOW_V2_LAUNCHER::{name}\n".encode("utf-8")
                )
        config_values: dict[str, object] = {
            name: {"schema_version": 1} for name in CONFIG_NAMES
        }
        config_values["policy.json"] = runtime_health_policy()
        config_values["python-support-policy.json"] = runtime_health_python_support_policy()
        config_values["windows-runtime-source.json"] = runtime_health_source()
        config_values["update-channel.json"] = runtime_health_update_channel()
        runtime_lock = runtime_health_lock()
        config_values["windows-cp313-runtime.lock"] = runtime_lock
        build_lock = {"schema_version": 1}
        config_values["windows-cp313-build.lock"] = build_lock
        for name, value in config_values.items():
            files[f"config/{name}"] = self._canonical(value)
        for name in SCHEMA_NAMES:
            files[f"schemas/{name}"] = self._canonical(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": f"https://jobflow.local/schemas/{name}",
                    "type": "object",
                }
            )
        for sentinels in PACKAGE_SENTINELS.values():
            for relative in sentinels:
                files[relative] = b"dependency-sentinel"
        records = [
            {"path": name, "sha256": self._sha(body), "size": len(body)}
            for name, body in sorted(files.items(), key=lambda item: item[0].upper())
        ]
        build_inputs = {
            "application_wheel_sha256": "sha256:" + "4" * 64,
            "application_wheel_provenance": {
                "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
                "source_commit": commit,
                "source_git_tree_oid": "a" * 40,
                "source_build_tree_sha256": "sha256:" + "7" * 64,
                "source_archive_sha256": "sha256:" + "8" * 64,
                "build_lock_sha256": self._sha(self._canonical(build_lock)),
                "build_recipe_sha256": "sha256:" + "9" * 64,
                "pass_a_wheel_sha256": "sha256:" + "4" * 64,
                "pass_b_wheel_sha256": "sha256:" + "4" * 64,
                "reproducible": True,
            },
            "builder_toolchain_sha256": "sha256:" + "5" * 64,
            "wheel_lock_sha256": self._sha(self._canonical(runtime_lock)),
            "wheelhouse_tree_sha256": "sha256:" + "3" * 64,
            "wheels": [
                {
                    "name": package["name"],
                    "version": package["version"],
                    "tag": package["filename"].removesuffix(".whl").rsplit("-", 3)[-3]
                    + "-"
                    + package["filename"].removesuffix(".whl").rsplit("-", 3)[-2]
                    + "-"
                    + package["filename"].removesuffix(".whl").rsplit("-", 3)[-1],
                    "size": package["size"],
                    "sha256": package["sha256"],
                }
                for package in runtime_lock["packages"]
            ],
        }
        closure = {
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
        closure_bytes = self._canonical(closure)
        archive = release_root / f"JobFlow-v{version}-windows-x64-complete.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name, body in files.items():
                package.writestr(prefix + name, body)
            package.writestr(prefix + "runtime-closure.json", closure_bytes)
        archive_bytes = archive.read_bytes()
        runtime_summary = {
            "build_inputs": {
                "application_wheel_sha256": build_inputs["application_wheel_sha256"],
                "application_wheel_provenance": build_inputs["application_wheel_provenance"],
                "builder_toolchain_sha256": build_inputs["builder_toolchain_sha256"],
                "python_artifact_sha256": closure["python"]["artifact_sha256"],
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
            "minimum_bootstrap_version": minimum_bootstrap,
            "minimum_updater_version": minimum_updater,
            "publisher_attestation_required": True,
            "required_structural_status": "BUILT_UNATTESTED",
        }
        issuance_clock = datetime.now(timezone.utc)
        issued = issuance_clock.isoformat(timespec="microseconds").replace("+00:00", "Z")
        evidence_expires = (issuance_clock + timedelta(hours=1)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        manifest_value = {
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
                "minimum_version": predecessor_minimum,
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
                "release_key_id": self.key_id,
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
        if legacy_v1_predecessors is not None:
            manifest_value["legacy_v1_predecessors"] = legacy_v1_predecessors
        manifest_bytes = self._canonical(manifest_value)
        signature_bytes = self.private_key.sign(manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
        manifest = release_root / "manifest-v2.json"
        signature = release_root / "manifest-v2.sig.json"
        manifest.write_bytes(manifest_bytes)
        signature.write_bytes(
            self._canonical(
                {
                    "algorithm": "RSA-PKCS1-v1_5-SHA256",
                    "key_id": self.key_id,
                    "schema_version": 1,
                    "signature_b64url": self._b64(signature_bytes),
                }
            )
        )
        return {"manifest": manifest, "signature": signature, "archive": archive, "value": manifest_value}

    def _run(
        self,
        release: dict[str, object],
        *,
        script: Path | None = None,
        activate: bool = True,
        expand: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script or self.script), "-ManifestPath", str(release["manifest"]),
            "-SignaturePath", str(release["signature"]), "-ArchivePath", str(release["archive"]),
        ]
        if activate:
            command.append("-Activate")
        if expand:
            command.append("-ExpandArchive")
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(self.local_app_data)
        return subprocess.run(
            command, cwd=PROJECT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
        )

    def _assert_failed(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_BOOTSTRAP_FAILED")
        self.assertNotIn(str(self.local_app_data), completed.stdout + completed.stderr)

    @property
    def install(self) -> Path:
        return self.local_app_data / "JobOps"

    def _pointer(self, name: str = "current.json") -> dict[str, object]:
        return json.loads((self.install / name).read_text(encoding="utf-8"))

    def _target(self, pointer: dict[str, object]) -> Path:
        return self.install / "Application" / "versions" / str(pointer["version_directory"])

    def _orphans(self) -> tuple[list[Path], list[Path]]:
        stages = list((self.install / "BootstrapStagingV2").glob("*")) if (self.install / "BootstrapStagingV2").exists() else []
        tokens = list((self.install / "BootstrapStagingTokensV2").glob("*")) if (self.install / "BootstrapStagingTokensV2").exists() else []
        return stages, tokens

    @staticmethod
    def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
        if not root.exists():
            return ()
        entries: list[tuple[str, str, bytes]] = [(".", "directory", b"")]
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                entries.append((relative, "directory", b""))
            elif path.is_file():
                entries.append((relative, "file", path.read_bytes()))
            else:
                entries.append((relative, "other", b""))
        return tuple(sorted(entries, key=lambda item: item[0]))

    def _preserved_data_snapshot(self) -> tuple[tuple[str, str, bytes], ...]:
        """Snapshot user-owned Data while excluding public activation receipts."""
        trust_prefix = "state/activation-trust/"
        return tuple(
            item
            for item in self._tree_snapshot(self.install / "Data")
            if item[0] != "state/activation-trust"
            and not item[0].startswith(trust_prefix)
        )

    @staticmethod
    def _legacy_v1_source_sha256(target: Path) -> str:
        root_files = (
            ".jobops-root",
            "AGENTS.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "Install JobFlow Browser Companion.cmd",
            "MANIFEST.in",
            "README.md",
            "SECURITY.md",
            "Update JobFlow.cmd",
            "pyproject.toml",
        )
        source_directories = (
            ".agents",
            "browser-companion",
            "config",
            "docs",
            "schemas",
            "scripts",
            "src",
            "tests",
        )
        excluded_directories = {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".tmp",
            ".git",
        }
        excluded_suffixes = {
            ".pyc",
            ".pyo",
            ".db",
            ".sqlite",
            ".sqlite3",
            ".dpapi",
            ".zip",
            ".7z",
            ".rar",
            ".log",
        }

        def excluded(relative: str) -> bool:
            lower = relative.casefold()
            parts = lower.split("/")
            return (
                lower in {"browser-companion/binding.json", "browser-companion-binding.json"}
                or any(part in excluded_directories for part in parts)
                or Path(lower).suffix in excluded_suffixes
            )

        records: list[tuple[str, Path]] = []
        for name in root_files:
            path = target / name
            if path.is_file():
                records.append((name, path))
        for directory_name in source_directories:
            directory = target / directory_name
            if not directory.is_dir():
                raise AssertionError(f"missing legacy v1 source directory: {directory_name}")
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(target).as_posix()
                if not excluded(relative):
                    records.append((relative, path))
        manifest = bytearray()
        for relative, path in sorted(records, key=lambda item: item[0]):
            body = path.read_bytes()
            manifest.extend(
                f"{relative}|{len(body)}|{hashlib.sha256(body).hexdigest()}\n".encode("utf-8")
            )
        return hashlib.sha256(manifest).hexdigest()

    def _install_exact_legacy_v1_fixture(self) -> dict[str, object]:
        versions = self.install / "Application" / "versions"
        data_state = self.install / "Data" / "state"
        bin_root = self.install / "bin"
        versions.mkdir(parents=True)
        data_state.mkdir(parents=True)
        bin_root.mkdir()

        staging = versions / "legacy-v1-build"
        staging.mkdir()
        for name in (
            ".agents",
            "browser-companion",
            "config",
            "docs",
            "schemas",
            "scripts",
            "src",
            "tests",
        ):
            (staging / name).mkdir()
        (staging / ".jobops-root").write_bytes(b"jobops-root-v1\n")
        source_file = staging / "src" / "jobops" / "legacy_source.py"
        source_file.parent.mkdir()
        source_file.write_bytes(b"LEGACY_V1_SIGNED_SOURCE = True\n")

        source_sha256 = self._legacy_v1_source_sha256(staging)
        version = "0.4.1"
        version_directory = f"v{version}-{source_sha256[:12]}"
        target = versions / version_directory
        staging.rename(target)
        source_file = target / "src" / "jobops" / "legacy_source.py"

        legacy_runtime = target / ".venv" / "Scripts" / "python.exe"
        legacy_runtime.parent.mkdir(parents=True)
        legacy_runtime_bytes = b"LEGACY_V1_RUNTIME_SENTINEL_MUST_NOT_EXECUTE"
        legacy_runtime.write_bytes(legacy_runtime_bytes)

        data_marker = self.install / "Data" / ".jobflow-data-root"
        data_marker.write_bytes(b'{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}')
        (data_state / ".jobflow-runtime-maintenance.lock").write_bytes(b"\x00")
        (data_state / ".authorized-discovery-task.lock").write_bytes(b"\x00")
        data_sentinel = data_state / "candidate.db"
        data_sentinel_bytes = b"LEGACY_PRIVATE_DATA_SENTINEL\x00\xff\x10"
        data_sentinel.write_bytes(data_sentinel_bytes)

        launcher_destinations: dict[str, bytes] = {}
        for name in (
            "jobflow-bootstrap.ps1",
            "start-installed-jobflow.ps1",
            "check-installed-jobflow.ps1",
            "update-installed-jobflow.ps1",
            "rollback-installed-jobflow.ps1",
            "uninstall-installed-jobflow.ps1",
            "jobflow-runtime-locks.ps1",
            "manage-authorized-discovery-task.ps1",
            "run-authorized-discovery-task.ps1",
        ):
            destination = bin_root / name
            sentinel = f"LEGACY_V1_LAUNCHER_SENTINEL::{name}\n".encode("utf-8")
            destination.write_bytes(sentinel)
            launcher_destinations[str(destination.relative_to(self.install))] = sentinel
        for name in (
            "Start JobFlow.cmd",
            "Check JobFlow.cmd",
            "Update JobFlow.cmd",
            "Rollback JobFlow.cmd",
            "Uninstall JobFlow.cmd",
        ):
            destination = self.install / name
            sentinel = f"LEGACY_V1_LAUNCHER_SENTINEL::{name}\n".encode("utf-8")
            destination.write_bytes(sentinel)
            launcher_destinations[str(destination.relative_to(self.install))] = sentinel

        identity: dict[str, object] = {
            "schema_version": 1,
            "version": version,
            "source_sha256": source_sha256,
            "version_directory": version_directory,
        }
        current_bytes = (
            '{"schema_version":1,"version_directory":"'
            + version_directory
            + '","version":"'
            + version
            + '","source_sha256":"'
            + source_sha256
            + '"}'
        ).encode("utf-8")
        (self.install / "current.json").write_bytes(current_bytes)
        return {
            "identity": identity,
            "target": target,
            "source_file": source_file,
            "current_bytes": current_bytes,
            "legacy_runtime": legacy_runtime,
            "legacy_runtime_bytes": legacy_runtime_bytes,
            "data_sentinel": data_sentinel,
            "data_sentinel_bytes": data_sentinel_bytes,
            "launcher_destinations": launcher_destinations,
        }

    def _legacy_data_read_probe_script(self, name: str) -> tuple[Path, Path]:
        probe = self.root / (name + ".probe")
        needle = "function Get-ExistingLegacyV1Layout([string]$JobOpsRoot) {"
        escaped = str(probe).replace("'", "''")
        injected = needle + f"\n    [IO.File]::WriteAllText('{escaped}', 'DATA_READ')"
        return self._write_script(name + ".ps1", mutation=(needle, injected)), probe

    def test_fresh_install_uses_fixed_layout_exact_pointer_and_data_marker(self) -> None:
        release = self._release("1.0.0")
        completed = self._run(release)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(
            set(result),
            {"status", "version", "source_payload_sha256", "runtime_tree_sha256", "activation_performed", "real_external_actions"},
        )
        self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
        self.assertTrue(result["activation_performed"])
        self.assertEqual(result["real_external_actions"], 0)
        self.assertNotIn(str(self.local_app_data), completed.stdout + completed.stderr)
        pointer = self._pointer()
        self.assertEqual(len(pointer), 11)
        self.assertEqual(pointer["bootstrap_version"], "0.6.0")
        target = self._target(pointer)
        self.assertTrue((target / "runtime-closure.json").is_file())
        self.assertFalse((self.install / str(pointer["version_directory"])).exists())
        self.assertEqual(
            (self.install / "Data" / ".jobflow-data-root").read_bytes(),
            b'{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}',
        )
        self.assertTrue((self.install / "Data" / "state" / ".jobflow-runtime-maintenance.lock").is_file())
        self.assertEqual(self._orphans(), ([], []))

    def test_upgrade_preserves_data_sets_previous_and_same_payload_is_idempotent(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        old_current = (self.install / "current.json").read_bytes()
        user_file = self.install / "Data" / "state" / "candidate.db"
        user_file.write_bytes(b"preserve-user-data")
        data_before = self._preserved_data_snapshot()
        second = self._release("1.1.0", predecessor_minimum="1.0.0")
        upgraded = self._run(second)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        self.assertEqual((self.install / "previous.json").read_bytes(), old_current)
        data_after = self._preserved_data_snapshot()
        self.assertEqual(data_after, data_before)
        current_before = (self.install / "current.json").read_bytes()
        previous_before = (self.install / "previous.json").read_bytes()
        again = self._run(second)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(json.loads(again.stdout.lstrip("\ufeff"))["activation_performed"])
        self.assertEqual((self.install / "current.json").read_bytes(), current_before)
        self.assertEqual((self.install / "previous.json").read_bytes(), previous_before)
        self.assertEqual(len(list((self.install / "Application" / "versions").iterdir())), 2)
        self.assertEqual(self._orphans(), ([], []))

    def test_same_version_different_payload_and_mismatched_preexisting_target_fail_closed(self) -> None:
        first = self._release("1.0.0", payload_tag="a")
        self.assertEqual(self._run(first).returncode, 0)
        pointers_before = (self.install / "current.json").read_bytes()
        different = self._release("1.0.0", payload_tag="b")
        different_pointer = different["value"]["runtime_closure"]["source_payload_sha256"]
        different_target = self.install / "Application" / "versions" / ("v1.0.0-" + str(different_pointer)[7:19])
        self._assert_failed(self._run(different))
        self.assertEqual((self.install / "current.json").read_bytes(), pointers_before)
        self.assertFalse(different_target.exists())

        isolated = self.root / "mismatch-local"
        isolated.mkdir()
        original_local = self.local_app_data
        original_script = self.script
        try:
            self.local_app_data = isolated
            self.script = self._write_script("mismatch-bootstrap.ps1")
            value = different["value"]
            digest = str(value["runtime_closure"]["source_payload_sha256"])[7:19]
            target = isolated / "JobOps" / "Application" / "versions" / f"v1.0.0-{digest}"
            target.mkdir(parents=True)
            (target / "junk.txt").write_text("mismatch", encoding="utf-8")
            self._assert_failed(self._run(different))
            self.assertFalse((isolated / "JobOps" / "current.json").exists())
            self.assertEqual((target / "junk.txt").read_text(encoding="utf-8"), "mismatch")
            self.assertFalse((isolated / "JobOps" / f"v1.0.0-{digest}").exists())
        finally:
            self.local_app_data = original_local
            self.script = original_script

    def test_current_runtime_and_pointer_identity_tampering_are_rejected(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        pointer = self._pointer()
        current_before = (self.install / "current.json").read_bytes()
        (self._target(pointer) / "runtime" / "python.exe").write_bytes(b"tampered")
        self._assert_failed(self._run(self._release("1.1.0", predecessor_minimum="1.0.0")))
        self.assertEqual((self.install / "current.json").read_bytes(), current_before)
        self.assertEqual(self._orphans(), ([], []))

    def test_current_pointer_identity_tampering_is_rejected_without_repair(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        pointer = self._pointer()
        pointer["source_commit"] = "f" * 40
        tampered = self._canonical(pointer)
        (self.install / "current.json").write_bytes(tampered)
        self._assert_failed(self._run(self._release("1.1.0", predecessor_minimum="1.0.0")))
        self.assertEqual((self.install / "current.json").read_bytes(), tampered)
        self.assertEqual(self._orphans(), ([], []))

    def test_downgrade_predecessor_and_future_minimums_fail_before_commit(self) -> None:
        current = self._release("1.1.0")
        self.assertEqual(self._run(current).returncode, 0)
        pointer_before = (self.install / "current.json").read_bytes()
        self._assert_failed(self._run(self._release("1.0.0")))
        self._assert_failed(self._run(self._release("1.2.0", predecessor_minimum="1.1.1")))
        self.assertEqual((self.install / "current.json").read_bytes(), pointer_before)

        for label, kwargs in (
            ("bootstrap", {"minimum_bootstrap": "0.6.1"}),
            ("updater", {"minimum_updater": "0.6.1"}),
        ):
            with self.subTest(label=label):
                other = self.root / ("future-" + label)
                other.mkdir()
                saved_local, saved_script = self.local_app_data, self.script
                try:
                    self.local_app_data = other
                    self.script = self._write_script("future-" + label + ".ps1")
                    self._assert_failed(self._run(self._release("1.3.0", payload_tag=label, **kwargs)))
                    self.assertFalse((other / "JobOps").exists())
                finally:
                    self.local_app_data, self.script = saved_local, saved_script

    def test_v1_current_requires_manual_migration(self) -> None:
        self.install.mkdir()
        (self.install / "current.json").write_text(
            json.dumps({"schema_version": 1, "source_sha256": "a" * 64, "version": "0.4.1", "version_directory": "v0.4.1"}),
            encoding="utf-8",
        )
        legacy_data = self.install / "Data"
        (legacy_data / "state" / "nested").mkdir(parents=True)
        (legacy_data / "state" / "candidate.db").write_bytes(b"legacy-private-data")
        (legacy_data / "state" / "nested" / "empty.bin").write_bytes(b"")
        (self.install / "legacy-runtime-note.txt").write_bytes(b"preserve exactly")
        before = self._tree_snapshot(self.install)
        completed = self._run(self._release("1.0.0"))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_MANUAL_MIGRATION_REQUIRED")
        self.assertEqual(self._tree_snapshot(self.install), before)
        self.assertFalse((self.install / ".jobflow-bootstrap.lock").exists())
        self.assertFalse((self.install / "Application").exists())
        self.assertFalse((legacy_data / ".jobflow-data-root").exists())
        self.assertFalse((legacy_data / "state" / ".jobflow-runtime-maintenance.lock").exists())
        self.assertEqual(self._orphans(), ([], []))

    def test_exact_signed_v1_predecessor_migrates_forward_without_executing_v1(self) -> None:
        fixture = self._install_exact_legacy_v1_fixture()
        data_before = self._preserved_data_snapshot()
        release = self._release(
            "1.0.0",
            predecessor_minimum="0.4.1",
            legacy_v1_predecessors=[fixture["identity"]],
        )

        completed = self._run(release)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
        self.assertTrue(result["activation_performed"])
        self.assertTrue(result["legacy_migration_performed"])
        self.assertEqual(result["real_external_actions"], 0)

        current = self._pointer()
        self.assertEqual(current["schema_version"], 2)
        self.assertEqual(current["version"], "1.0.0")
        self.assertFalse((self.install / "previous.json").exists())
        self.assertEqual(Path(fixture["legacy_runtime"]).read_bytes(), fixture["legacy_runtime_bytes"])
        self.assertEqual(Path(fixture["data_sentinel"]).read_bytes(), fixture["data_sentinel_bytes"])
        self.assertEqual(self._preserved_data_snapshot(), data_before)

        state = self.install / "Data" / "state"
        for path in (
            state / ".jobflow-v1-v2-migration-transaction-v1.json",
            state / ".jobflow-v1-v2-migration-transaction-v1.backup.json",
            state / ".jobflow-v1-v2-migration-transaction-v1.main.write.tmp",
            state / ".jobflow-v1-v2-migration-transaction-v1.backup.write.tmp",
            self.install / ".jobflow-v1-v2-current.pointer.quarantine",
            self.install / ".jobflow-v1-v2-previous.pointer.quarantine",
            self.install / ".jobflow-v1-v2-launchers.quarantine",
            self.install / ".jobflow-v1-v2-migration-completion-v1.write.tmp",
        ):
            self.assertFalse(path.exists(), path)
        self.assertTrue((self.install / ".jobflow-v1-v2-migration-completion-v1.json").is_file())

        v2_target = self._target(current)
        launcher_destinations = fixture["launcher_destinations"]
        self.assertEqual(len(launcher_destinations), 14)
        for relative, old_sentinel in launcher_destinations.items():
            destination = self.install / relative
            expected = v2_target / "scripts" / "windows-runtime" / destination.name
            self.assertTrue(destination.is_file(), destination)
            self.assertEqual(destination.read_bytes(), expected.read_bytes())
            self.assertNotEqual(destination.read_bytes(), old_sentinel)
        self.assertEqual(self._orphans(), ([], []))

    def test_exact_v1_without_signed_allowlist_stops_before_data_read_or_migration_write(self) -> None:
        self._install_exact_legacy_v1_fixture()
        before = self._tree_snapshot(self.install)
        script, data_read_probe = self._legacy_data_read_probe_script("no-v1-allowlist-data-read")

        completed = self._run(
            self._release("1.0.0", predecessor_minimum="0.4.1"),
            script=script,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_MANUAL_MIGRATION_REQUIRED")
        self.assertFalse(data_read_probe.exists())
        self.assertEqual(self._tree_snapshot(self.install), before)
        self.assertFalse((self.install / ".jobflow-bootstrap.lock").exists())

    def test_allowlisted_v1_source_tampering_stops_before_data_read_or_migration_write(self) -> None:
        fixture = self._install_exact_legacy_v1_fixture()
        release = self._release(
            "1.0.0",
            predecessor_minimum="0.4.1",
            legacy_v1_predecessors=[fixture["identity"]],
        )
        source_file = Path(fixture["source_file"])
        source_file.write_bytes(source_file.read_bytes() + b"# tampered after signing\n")
        before = self._tree_snapshot(self.install)
        script, data_read_probe = self._legacy_data_read_probe_script("tampered-v1-data-read")

        completed = self._run(release, script=script)
        self._assert_failed(completed)
        self.assertFalse(data_read_probe.exists())
        self.assertEqual(self._tree_snapshot(self.install), before)
        self.assertFalse((self.install / ".jobflow-bootstrap.lock").exists())
        self.assertFalse((self.install / ".jobflow-v1-v2-migration-completion-v1.json").exists())

    def test_root_migration_quarantine_without_data_state_fails_closed(self) -> None:
        fixture = self._install_exact_legacy_v1_fixture()
        release = self._release(
            "1.0.0",
            predecessor_minimum="0.4.1",
            legacy_v1_predecessors=[fixture["identity"]],
        )
        quarantine = self.install / ".jobflow-v1-v2-current.pointer.quarantine"
        os.replace(self.install / "current.json", quarantine)
        shutil.rmtree(self.install / "Data" / "state")
        quarantine_before = quarantine.read_bytes()
        versions_before = self._tree_snapshot(self.install / "Application" / "versions")

        completed = self._run(release)
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_ACTIVATION_RECOVERY_FAILED")
        self.assertNotIn(str(self.local_app_data), completed.stdout + completed.stderr)
        self.assertEqual(quarantine.read_bytes(), quarantine_before)
        self.assertFalse((self.install / "current.json").exists())
        self.assertFalse((self.install / "previous.json").exists())
        self.assertFalse((self.install / "Data" / "state").exists())
        self.assertEqual(
            (self.install / "Data" / ".jobflow-data-root").read_bytes(),
            b'{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}',
        )
        self.assertEqual(self._tree_snapshot(self.install / "Application" / "versions"), versions_before)
        self.assertFalse((self.install / ".jobflow-v1-v2-migration-completion-v1.json").exists())
        for relative, sentinel in fixture["launcher_destinations"].items():
            self.assertEqual((self.install / relative).read_bytes(), sentinel)
        self.assertEqual(self._orphans(), ([], []))

    def test_pointer_publish_failure_restores_pointers_and_removes_new_target(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        current_before = (self.install / "current.json").read_bytes()
        replacement = self._release("1.1.0", predecessor_minimum="1.0.0")
        needle = 'function Publish-AtomicPointer([string]$Temporary, [string]$Destination, [string]$Backup) {'
        injected = needle + '\n    if ([IO.Path]::GetFileName($Destination) -ceq "current.json") { throw "TEST_POINTER_FAILURE" }'
        faulty = self._write_script("faulty-pointer.ps1", mutation=(needle, injected))
        self._assert_failed(self._run(replacement, script=faulty))
        self.assertEqual((self.install / "current.json").read_bytes(), current_before)
        self.assertFalse((self.install / "previous.json").exists())
        value = replacement["value"]
        digest = str(value["runtime_closure"]["source_payload_sha256"])[7:19]
        self.assertFalse((self.install / "Application" / "versions" / f"v1.1.0-{digest}").exists())

    def test_verified_stage_tamper_before_activation_is_rejected_without_target(self) -> None:
        release = self._release("1.0.0")
        boundary = "        # JOBFLOW_BOOTSTRAP_VERIFIED_STAGE_READY_BOUNDARY"
        injected = (
            boundary
            + "\n        [IO.File]::WriteAllText("
            + "[IO.Path]::Combine([string]$staging.stage, "
            + "'config\\windows-cp313-build.lock'), "
            + "'{\"schema_version\":2}', [Text.UTF8Encoding]::new($false))"
        )
        faulty = self._write_script(
            "tampered-verified-stage.ps1", mutation=(boundary, injected)
        )

        completed = self._run(release, script=faulty)

        self._assert_failed(completed)
        self.assertFalse((self.install / "current.json").exists())
        self.assertFalse((self.install / "previous.json").exists())
        versions = self.install / "Application" / "versions"
        self.assertEqual(list(versions.iterdir()) if versions.exists() else [], [])
        self.assertEqual(self._orphans(), ([], []))
        self.assertFalse((self.install / ".jobflow-activation-journal-v1.json").exists())

    def test_post_pointer_failure_rolls_back_prepared_transaction_and_preserves_data(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        current_before = (self.install / "current.json").read_bytes()
        user_file = self.install / "Data" / "state" / "candidate.db"
        user_file.write_bytes(b"preserve-across-post-commit-failure")
        data_before = self._tree_snapshot(self.install / "Data")
        preserved_data_before = self._preserved_data_snapshot()
        replacement = self._release("1.1.0", predecessor_minimum="1.0.0")
        needle = "Publish-PointerPair $jobOpsRoot $candidate $oldCurrent $oldPrevious"
        injected = needle + '\n            throw "TEST_POST_COMMIT_FAILURE"'
        faulty = self._write_script("faulty-post-commit.ps1", mutation=(needle, injected))
        self._assert_failed(self._run(replacement, script=faulty))

        current = self._pointer()
        self.assertEqual((self.install / "current.json").read_bytes(), current_before)
        self.assertEqual(current["version"], "1.0.0")
        self.assertFalse((self.install / "previous.json").exists())
        self.assertTrue((self._target(current) / "runtime-closure.json").is_file())
        value = replacement["value"]
        digest = str(value["runtime_closure"]["source_payload_sha256"])[7:19]
        self.assertFalse((self.install / "Application" / "versions" / f"v1.1.0-{digest}").exists())
        self.assertEqual(self._tree_snapshot(self.install / "Data"), data_before)
        self.assertEqual(self._orphans(), ([], []))

        retry = self._run(replacement)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(json.loads(retry.stdout.lstrip("\ufeff"))["activation_performed"])
        self.assertEqual(self._preserved_data_snapshot(), preserved_data_before)

    def test_fresh_data_initialization_rolls_back_and_preexisting_data_is_never_deleted(self) -> None:
        release = self._release("1.0.0")
        boundary = "# JOBFLOW_BOOTSTRAP_FRESH_DATA_READY_BOUNDARY"
        faulty = self._write_script(
            "faulty-fresh-data.ps1",
            mutation=(boundary, boundary + '\n            throw "TEST_DATA_INIT_FAILURE"'),
        )
        self._assert_failed(self._run(release, script=faulty))
        self.assertFalse((self.install / "Data").exists())
        self.assertEqual(list(self.install.glob("Data-init-*.tmp")), [])

        retry = self._run(release)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        user_file = self.install / "Data" / "state" / "candidate.db"
        user_file.write_bytes(b"preexisting-private-data")
        data_before = self._tree_snapshot(self.install / "Data")
        replacement = self._release("1.1.0", predecessor_minimum="1.0.0")
        layout_call = "$dataLayout = Initialize-OrValidateDataRoot $jobOpsRoot"
        existing_fault = self._write_script(
            "faulty-existing-data.ps1",
            mutation=(layout_call, layout_call + '\n    throw "TEST_EXISTING_DATA_FAILURE"'),
        )
        self._assert_failed(self._run(replacement, script=existing_fault))
        self.assertEqual(self._tree_snapshot(self.install / "Data"), data_before)
        self.assertEqual(list(self.install.glob("Data-init-*.tmp")), [])

    def test_orphan_scavenging_is_bounded_and_success_leaves_no_stage_or_token(self) -> None:
        staging = self.install / "BootstrapStagingV2"
        tokens = self.install / "BootstrapStagingTokensV2"
        stale = staging / ("stage-" + "a" * 32)
        stale.mkdir(parents=True)
        (stale / "partial.bin").write_bytes(b"partial")
        tokens.mkdir()
        (tokens / ("b" * 32 + ".json")).write_text("{}", encoding="utf-8")
        completed = self._run(self._release("1.0.0"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self._orphans(), ([], []))

    def test_excessive_orphan_count_fails_closed_without_partial_scavenging(self) -> None:
        staging = self.install / "BootstrapStagingV2"
        staging.mkdir(parents=True)
        for index in range(65):
            (staging / ("stage-" + f"{index:032x}")).mkdir()
        self._assert_failed(self._run(self._release("1.0.0")))
        self.assertEqual(len(list(staging.iterdir())), 65)
        self.assertFalse((self.install / "current.json").exists())

    def test_concurrent_activate_serializes_without_deleting_live_stage(self) -> None:
        release = self._release("1.0.0")
        needle = "$operationLock = Enter-BootstrapOperationLock"
        slow = self._write_script(
            "slow-bootstrap.ps1",
            mutation=(needle, needle + "\n            Start-Sleep -Milliseconds 1800"),
        )
        command = [
            str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(slow), "-ManifestPath", str(release["manifest"]), "-SignaturePath", str(release["signature"]),
            "-ArchivePath", str(release["archive"]), "-Activate",
        ]
        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(self.local_app_data)
        first = subprocess.Popen(command, cwd=PROJECT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        lock = self.install / ".jobflow-bootstrap.lock"
        deadline = time.time() + 20
        while not lock.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(lock.exists())
        second = self._run(release)
        first_stdout, first_stderr = first.communicate(timeout=90)
        self.assertEqual(first.returncode, 0, first_stderr)
        self._assert_failed(second)
        self.assertEqual(json.loads(first_stdout.lstrip("\ufeff"))["status"], "JOBFLOW_BOOTSTRAP_ACTIVATED")
        pointer = self._pointer()
        self.assertTrue((self._target(pointer) / "runtime-closure.json").is_file())
        self.assertEqual(self._orphans(), ([], []))

    def test_runtime_maintenance_lock_blocks_activation_without_pointer_change(self) -> None:
        first = self._release("1.0.0")
        self.assertEqual(self._run(first).returncode, 0)
        current_before = (self.install / "current.json").read_bytes()
        previous_before = (
            (self.install / "previous.json").read_bytes()
            if (self.install / "previous.json").exists()
            else None
        )
        lock_path = self.install / "Data" / "state" / ".jobflow-runtime-maintenance.lock"
        ready = self.root / "maintenance-lock-ready"
        stop = self.root / "maintenance-lock-stop"
        helper = self.root / "hold-maintenance-lock.ps1"
        helper.write_text(
            "param([string]$LockPath,[string]$Ready,[string]$Stop)\n"
            "$ErrorActionPreference='Stop'\n"
            "$stream=[IO.File]::Open($LockPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)\n"
            "try { [IO.File]::WriteAllText($Ready,'ready'); while(-not [IO.File]::Exists($Stop)){ Start-Sleep -Milliseconds 50 } }\n"
            "finally { $stream.Dispose() }\n",
            encoding="utf-8",
        )
        holder = subprocess.Popen(
            [
                str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(helper),
                "-LockPath", str(lock_path), "-Ready", str(ready), "-Stop", str(stop),
            ],
            cwd=PROJECT,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.time() + 20
            while not ready.exists() and holder.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            self._assert_failed(self._run(self._release("1.1.0", predecessor_minimum="1.0.0")))
        finally:
            stop.write_text("stop", encoding="utf-8")
            holder_stdout, holder_stderr = holder.communicate(timeout=30)
        self.assertEqual(holder.returncode, 0, holder_stdout + holder_stderr)
        self.assertEqual((self.install / "current.json").read_bytes(), current_before)
        if previous_before is None:
            self.assertFalse((self.install / "previous.json").exists())
        else:
            self.assertEqual((self.install / "previous.json").read_bytes(), previous_before)
        self.assertEqual(len(list((self.install / "Application" / "versions").iterdir())), 1)
        self.assertEqual(self._orphans(), ([], []))

    def test_diagnostic_expand_does_not_create_data_or_maintenance_lock(self) -> None:
        completed = self._run(self._release("1.0.0"), activate=False, expand=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse((self.install / "Data").exists())
        self.assertEqual(self._orphans(), ([], []))

    def test_static_activation_order_and_no_token_or_candidate_execution(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("staging_token", source)
        self.assertNotIn("Write-StagingToken", source)
        self.assertIn("OpenExclusiveLockFile", source)
        dispatch = source.split("# JOBFLOW_BOOTSTRAP_SIGNATURE_VERIFIED_BOUNDARY", 1)[1]
        self.assertLess(dispatch.index("Assert-EmbeddedCompatibility $manifestValue"), dispatch.index("$operationLock = Enter-BootstrapOperationLock"))
        self.assertLess(dispatch.index("$operationLock = Enter-BootstrapOperationLock"), dispatch.index("Remove-BoundedBootstrapOrphans $trustedLocalDataRoot"))
        self.assertLess(dispatch.index("Remove-BoundedBootstrapOrphans $trustedLocalDataRoot"), dispatch.index("Expand-AndVerifySignedArchive $manifestValue $ArchivePath"))
        self.assertIn('"Application"', source)
        self.assertIn('"versions"', source)
        self.assertNotRegex(source, r"(?im)^\s*&\s+.*python")
        for forbidden in ("Invoke-WebRequest", "Invoke-RestMethod", "Start-Process", "ProcessStartInfo"):
            self.assertNotIn(forbidden, source)

    def test_cleanup_bound_covers_every_preflight_accepted_tree_entry_and_byte(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        maximum_files = 100000
        maximum_directories = maximum_files + 64
        extracted_root_and_closure = 2
        expected_bound = maximum_files + maximum_directories + extracted_root_and_closure
        self.assertEqual(expected_bound, (2 * maximum_files) + 66)
        self.assertIn("$maximumRuntimeFileCount = 100000", source)
        self.assertIn("$maximumExtractedTreeEntries = (2 * $maximumRuntimeFileCount) + 66", source)
        self.assertIn("directories.Count > expectedPayloadFiles + 64L", source)
        self.assertGreaterEqual(source.count("$maximumExtractedTreeEntries"), 5)
        self.assertIn(
            "$maximumExtractedTreeBytes = [long]$maximumArchiveBytes + [long]$maximumClosureManifestBytes",
            source,
        )
        self.assertGreaterEqual(source.count("$maximumExtractedTreeBytes"), 5)
        self.assertNotIn("100002", source)


if __name__ == "__main__":
    unittest.main()
