from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-jobflow-v2.ps1"
_BASE_SPEC = importlib.util.spec_from_file_location(
    "jobflow_install_v2_test_base", ROOT / "tests" / "test_install_jobflow_v2.py"
)
assert _BASE_SPEC is not None and _BASE_SPEC.loader is not None
_BASE = importlib.util.module_from_spec(_BASE_SPEC)
sys.modules[_BASE_SPEC.name] = _BASE
_BASE_SPEC.loader.exec_module(_BASE)

POWERSHELL: Path = _BASE.POWERSHELL
BOOTSTRAP_FIXTURE: str = _BASE.BOOTSTRAP_FIXTURE
STABLE_FILES: tuple[str, ...] = _BASE.STABLE_FILES
KEY_ID: str = _BASE.KEY_ID


class LocalArchiveV2InstallerTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        if not POWERSHELL.exists():
            raise unittest.SkipTest("Windows PowerShell 5.1 is required")
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.qa_root = Path(tempfile.gettempdir()) / f"jobflow-v2-install-qa-{suffix}"
        self.project = self.qa_root / "project"
        self.local_app_data = self.qa_root / "LocalAppData"
        self.bundle = self.qa_root / "offline-bundle"
        (self.project / "scripts" / "windows-runtime").mkdir(parents=True)
        self.local_app_data.mkdir(parents=True)
        self.bundle.mkdir(parents=True)
        (self.project / ".jobops-root").write_text("jobops-root-v1\n", encoding="utf-8")
        shutil.copy2(INSTALLER, self.project / "scripts" / "install-jobflow-v2.ps1")
        runtime = self.project / "scripts" / "windows-runtime"
        for name in STABLE_FILES:
            body = BOOTSTRAP_FIXTURE if name == "jobflow-bootstrap.ps1" else f"CONTROL::{name}\n"
            (runtime / name).write_text(body, encoding="utf-8")
        self.archive = self._prepare_bundle()

    def tearDown(self) -> None:
        shutil.rmtree(self.qa_root, ignore_errors=True)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _prepare_bundle(self) -> Path:
        version = "0.7.0"
        archive = self.bundle / f"JobFlow-v{version}-windows-x64-complete.zip"
        archive_bytes = b"SYNTHETIC_SIGNED_V2_COMPLETE_RUNTIME\x00"
        archive.write_bytes(archive_bytes)
        manifest = {
            "schema_version": 2,
            "version": version,
            "archive_sha256": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
            "synthetic_signature": "synthetic-signed-v2",
        }
        signature = {
            "schema_version": 1,
            "algorithm": "RSA-PKCS1-v1_5-SHA256",
            "key_id": KEY_ID,
            "signature_b64url": "synthetic",
        }
        (self.bundle / "JobFlow-update-manifest.json").write_bytes(self._canonical(manifest))
        (self.bundle / "JobFlow-update-manifest.sig.json").write_bytes(self._canonical(signature))
        return archive

    def _run(self, archive: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "LOCALAPPDATA": str(self.local_app_data),
                "JOBFLOW_INSTALL_V2_ACCEPTANCE_CORE_ONLY": "1",
            }
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
                str(self.project / "scripts" / "install-jobflow-v2.ps1"),
                "-NoLaunch",
                "-ArchivePath",
                str(archive or self.archive),
            ],
            cwd=self.project,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )

    def _assert_local_rejected_before_activation(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID", result.stderr)
        self.assertNotIn(str(self.qa_root), result.stdout + result.stderr)
        self.assertFalse((self.local_app_data / "JobOps").exists())
        state = self.local_app_data / "JobFlowInstaller"
        self.assertEqual(list(state.glob(".jfi-*")), [])
        log = self.local_app_data / "bootstrap-modes.log"
        if log.exists():
            self.assertNotIn("Activate", log.read_text(encoding="utf-8").splitlines())

    def test_local_bundle_installs_without_release_metadata_or_network_path(self) -> None:
        self.assertFalse((self.bundle / "release.json").exists())
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pointer = json.loads(
            (self.local_app_data / "JobOps" / "current.json").read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["schema_version"], 2)
        self.assertEqual(pointer["version"], "0.7.0")
        self.assertEqual(
            (self.local_app_data / "bootstrap-modes.log")
            .read_text(encoding="utf-8")
            .splitlines(),
            [
                "RecoverOnly",
                "DescribeManifest",
                "DescribeManifest",
                "Activate",
                "VerifyInstalled",
                "VerifyInstalled",
            ],
        )
        self.assertEqual(list((self.local_app_data / "JobFlowInstaller").glob(".jfi-*")), [])

    def test_archive_hash_mismatch_is_rejected_before_staging_or_activation(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"tampered")
        self._assert_local_rejected_before_activation(self._run())

    def test_signature_identity_mismatch_is_rejected_before_staging_or_activation(self) -> None:
        signature_path = self.bundle / "JobFlow-update-manifest.sig.json"
        signature = json.loads(signature_path.read_text(encoding="utf-8"))
        signature["key_id"] = "sha256:" + "0" * 64
        signature_path.write_bytes(self._canonical(signature))
        self._assert_local_rejected_before_activation(self._run())

    def test_missing_fixed_name_signature_sidecar_is_rejected(self) -> None:
        (self.bundle / "JobFlow-update-manifest.sig.json").unlink()
        self._assert_local_rejected_before_activation(self._run())

    def test_mismatched_archive_asset_name_is_rejected(self) -> None:
        mismatched = self.bundle / "JobFlow-v0.7.1-windows-x64-complete.zip"
        mismatched.write_bytes(self.archive.read_bytes())
        self._assert_local_rejected_before_activation(self._run(mismatched))

    def test_archive_hardlink_is_rejected(self) -> None:
        original = self.bundle / "retained-original.zip"
        self.archive.replace(original)
        os.link(original, self.archive)
        self._assert_local_rejected_before_activation(self._run())

    def test_archive_alternate_data_stream_is_rejected(self) -> None:
        try:
            with open(str(self.archive) + ":unexpected", "wb") as stream:
                stream.write(b"blocked")
        except OSError as exc:
            self.skipTest(f"NTFS alternate streams are unavailable: {exc}")
        self._assert_local_rejected_before_activation(self._run())

    def test_reparse_parent_is_rejected(self) -> None:
        junction = self.qa_root / "offline-bundle-junction"
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(self.bundle)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            self.skipTest("Directory junction creation is unavailable")
        self._assert_local_rejected_before_activation(self._run(junction / self.archive.name))

    def test_unc_archive_path_is_rejected_without_opening_remote_source(self) -> None:
        self._assert_local_rejected_before_activation(
            self._run(Path(r"\\localhost\C$\JobFlow-v0.7.0-windows-x64-complete.zip"))
        )

    def test_static_local_branch_verifies_source_before_private_staging_and_keeps_online_path(self) -> None:
        branch_start = self.source.index("if ($localArchiveMode)")
        online_start = self.source.index("    else {", branch_start)
        branch = self.source[branch_start:online_start]
        self.assertLess(
            branch.index('Invoke-StableBootstrap `\n            "DescribeManifest"'),
            branch.index("New-StableUpdaterDirectoryRoot"),
        )
        self.assertLess(
            branch.index("Assert-LocalArchiveManifestBinding"),
            branch.index("New-StableUpdaterDirectoryRoot"),
        )
        self.assertNotIn("Receive-InstallerAsset", branch)
        online = self.source[online_start:]
        self.assertIn("[Uri]$expectedApiUrl", online)
        self.assertIn("Receive-InstallerAsset", online)
        self.assertIn("Read-AndValidateV2CurrentPointer", online)
        self.assertIn("if ($NoLaunch -or $localArchiveMode)", self.source)


if __name__ == "__main__":
    unittest.main()
