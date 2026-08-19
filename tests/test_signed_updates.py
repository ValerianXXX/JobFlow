from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.public_release import REQUIRED_PUBLIC_FILES
from jobops.release_candidate import WINDOWS_POWERSHELL_UTF8_BOM_FILES
from jobops.update_manifest import (
    build_update_manifest,
    inspect_signed_update,
    validate_update_channel,
    verify_signed_update_bundle,
)
from jobops.util import canonical_json


_WINDOWS_POWERSHELL = shutil.which("powershell.exe")
if not _WINDOWS_POWERSHELL:
    raise RuntimeError("Windows PowerShell is required for signed-update tests.")
WINDOWS_POWERSHELL = Path(_WINDOWS_POWERSHELL).resolve(strict=True)
SIGNED_UPDATE_TEST_ROOT = PROJECT / "tests" / ".tmp"
SIGNED_UPDATE_TEST_ROOT.mkdir(parents=True, exist_ok=True)


def _windows_extended_path(path: Path) -> str:
    absolute = str(path.resolve(strict=False))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _read_bytes(path: Path) -> bytes:
    return Path(_windows_extended_path(path)).read_bytes()


def _is_file(path: Path) -> bool:
    return Path(_windows_extended_path(path)).is_file()


class SignedUpdateTests(unittest.TestCase):
    def test_release_signer_cannot_silently_rotate_an_existing_key(self) -> None:
        signer = (PROJECT / "scripts" / "release-signing.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("[switch]$Force", signer)
        self.assertIn(
            "if ([IO.File]::Exists((ConvertTo-ExtendedFileSystemPath $keyPath)))",
            signer,
        )

    def test_signed_release_bundle_builder_requires_exact_clean_candidate(self) -> None:
        builder = (PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("JOBFLOW_RELEASE_WORKTREE_NOT_CLEAN", builder)
        self.assertIn("JOBFLOW_RELEASE_COMMIT_MISMATCH", builder)
        self.assertIn("JOBFLOW_RELEASE_ARCHIVE_IDENTITY_MISMATCH", builder)
        self.assertIn("JobFlow-update-manifest.json", builder)
        self.assertIn("JobFlow-update-manifest.sig.json", builder)
        self.assertIn("jobops.update_manifest verify", builder)
        self.assertIn('uploaded = $false', builder)

    @staticmethod
    def _key_id(channel_path: Path) -> str:
        return str(json.loads(channel_path.read_text(encoding="utf-8"))["signature"]["key_id"])

    def _initialize_signer(self, root: Path) -> tuple[dict[str, object], dict[str, str]]:
        root.mkdir(parents=True, exist_ok=True)
        local_app_data = root / "LocalAppData"
        local_app_data.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(local_app_data)
        completed = subprocess.run(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                "-Action", "Initialize", "-EmitChannel",
            ],
            cwd=PROJECT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["status"], "RELEASE_SIGNING_KEY_READY")
        self.assertNotIn(str(local_app_data), completed.stdout)
        key_path = local_app_data / "JobOps" / "ReleaseSigning" / "release-signing-key.dpapi"
        protected = _read_bytes(key_path)
        self.assertGreater(len(protected), 512)
        self.assertNotIn(b'"Modulus"', protected)
        return result["channel"], environment

    def _candidate_archive(self, root: Path, *, version: str, commit: str) -> Path:
        archive_path = root / f"JobFlow-v{version}-{commit[:12]}-source.zip"
        prefix = f"JobFlow-v{version}/"
        required = set(REQUIRED_PUBLIC_FILES) | {
            ".jobops-root", "Install JobFlow.cmd", "Start JobFlow.cmd", "Update JobFlow.cmd"
        }
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(required):
                payload = b"safe synthetic release fixture"
                if relative.endswith(".json"):
                    payload = b"{}"
                if relative in WINDOWS_POWERSHELL_UTF8_BOM_FILES:
                    payload = b"\xef\xbb\xbf" + payload
                archive.writestr(prefix + relative, payload)
        return archive_path

    def _signed_bundle(self, root: Path) -> tuple[Path, Path, Path, Path, dict[str, str]]:
        channel, environment = self._initialize_signer(root)
        channel_path = root / "update-channel.json"
        channel_path.write_bytes(canonical_json(channel))
        version, commit = "0.4.2", "a" * 40
        archive_path = self._candidate_archive(root, version=version, commit=commit)
        manifest = build_update_manifest(archive_path=archive_path, version=version, commit=commit)
        manifest_path = root / "JobFlow-update-manifest.json"
        signature_path = root / "JobFlow-update-manifest.sig.json"
        manifest_path.write_bytes(canonical_json(manifest))
        # Release builds overwrite the stable signature asset from the prior
        # version.  Exercise that path instead of only signing a new file.
        signature_path.write_text('{"stale":true}', encoding="utf-8")
        completed = subprocess.run(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                "-Action", "Sign", "-ManifestPath", str(manifest_path),
                "-SignatureOutput", str(signature_path),
            ],
            cwd=PROJECT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        sign_result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(sign_result["status"], "UPDATE_MANIFEST_SIGNED")
        self.assertNotIn(str(root), completed.stdout)
        return channel_path, manifest_path, signature_path, archive_path, environment

    def test_production_channel_has_a_strong_self_identifying_public_key(self) -> None:
        channel = json.loads((PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8"))
        validated = validate_update_channel(channel)
        self.assertEqual(validated["repository"], "ValerianXXX/JobFlow")
        self.assertEqual(validated["channel"], "stable")
        self.assertEqual(validated["signature"]["algorithm"], "RSA-PKCS1-v1_5-SHA256")

    def test_initialize_reuses_the_same_dpapi_key_without_rotation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-signing-key-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            first, _ = self._initialize_signer(root)
            key_path = root / "LocalAppData" / "JobOps" / "ReleaseSigning" / "release-signing-key.dpapi"
            protected_before = _read_bytes(key_path)
            second, _ = self._initialize_signer(root)
            self.assertEqual(first["signature"]["key_id"], second["signature"]["key_id"])
            self.assertEqual(protected_before, _read_bytes(key_path))

    def test_release_signer_supports_deep_source_archive_paths(self) -> None:
        prefix_base = "jobflow-signing-long-path-"
        key_relative = Path("LocalAppData") / "JobOps" / "ReleaseSigning" / "release-signing-key.dpapi"
        # tempfile adds eight random characters.  Size the final component
        # from the actual checkout location so this test always crosses the
        # legacy 260-character boundary, including on short GitHub runner
        # paths, without creating an overlong individual path component.
        fixed_length = (
            len(str(SIGNED_UPDATE_TEST_ROOT))
            + 1
            + len(prefix_base)
            + 8
            + 1
            + len(str(key_relative))
            + 1
            + 32
            + len(".tmp")
        )
        filler_length = max(80, 300 - fixed_length)
        self.assertLessEqual(len(prefix_base) + filler_length + 8, 240)
        prefix = prefix_base + ("x" * filler_length)
        raw = tempfile.mkdtemp(prefix=prefix, dir=SIGNED_UPDATE_TEST_ROOT)
        try:
            root = Path(raw)
            channel, _ = self._initialize_signer(root)
            key_path = root / "LocalAppData" / "JobOps" / "ReleaseSigning" / "release-signing-key.dpapi"
            atomic_temporary_length = len(str(key_path)) + 1 + 32 + len(".tmp")
            self.assertGreater(atomic_temporary_length, 260)
            self.assertTrue(_is_file(key_path))
            self.assertTrue(str(channel["signature"]["key_id"]).startswith("sha256:"))
        finally:
            shutil.rmtree(_windows_extended_path(Path(raw)))

    def test_signed_manifest_and_archive_verify_and_current_version_is_not_reinstalled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-signed-update-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, manifest, signature, archive, _ = self._signed_bundle(root)
            inspected = inspect_signed_update(
                manifest,
                signature,
                current_version="0.4.1",
                channel_path=channel,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(inspected["status"], "UPDATE_AVAILABLE")
            self.assertTrue(inspected["signature_verified"])
            verified = verify_signed_update_bundle(
                manifest,
                signature,
                archive,
                current_version="0.4.1",
                channel_path=channel,
                project=PROJECT,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(verified["status"], "UPDATE_BUNDLE_VERIFIED")
            self.assertEqual(verified["finding_count"], 0)
            current = inspect_signed_update(
                manifest,
                signature,
                current_version="0.4.2",
                channel_path=channel,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(current["status"], "UPDATE_CURRENT")

    def test_manifest_signature_archive_and_channel_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-signed-update-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, manifest, signature, archive, _ = self._signed_bundle(root)

            changed = json.loads(manifest.read_text(encoding="utf-8"))
            changed["commit"] = "b" * 40
            manifest.write_bytes(canonical_json(changed))
            with self.assertRaises(JobOpsError) as manifest_error:
                inspect_signed_update(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(manifest_error.exception.code, "UPDATE_MANIFEST_INVALID")

            channel, manifest, signature, archive, _ = self._signed_bundle(root / "second")
            envelope = json.loads(signature.read_text(encoding="utf-8"))
            envelope["signature_b64url"] = ("A" if envelope["signature_b64url"][0] != "A" else "B") + envelope["signature_b64url"][1:]
            signature.write_bytes(canonical_json(envelope))
            with self.assertRaises(JobOpsError) as signature_error:
                inspect_signed_update(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(signature_error.exception.code, "UPDATE_SIGNATURE_INVALID")

            channel, manifest, signature, archive, _ = self._signed_bundle(root / "third")
            with archive.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaises(JobOpsError) as archive_error:
                verify_signed_update_bundle(
                    manifest,
                    signature,
                    archive,
                    current_version="0.4.1",
                    channel_path=channel,
                    project=PROJECT,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertIn(
                archive_error.exception.code,
                {"UPDATE_ARCHIVE_IDENTITY_MISMATCH", "UPDATE_ARCHIVE_DIGEST_MISMATCH"},
            )

            channel_value = json.loads(channel.read_text(encoding="utf-8"))
            channel_value["repository"] = "Other/Repository"
            channel.write_bytes(canonical_json(channel_value))
            with self.assertRaises(JobOpsError) as channel_error:
                inspect_signed_update(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(channel_error.exception.code, "UPDATE_CHANNEL_INVALID")

    def test_noncanonical_or_downgrade_metadata_never_installs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-signed-update-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, manifest, signature, _, _ = self._signed_bundle(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            manifest.write_text(json.dumps(value, indent=2), encoding="utf-8")
            with self.assertRaises(JobOpsError) as noncanonical:
                inspect_signed_update(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(noncanonical.exception.code, "UPDATE_MANIFEST_INVALID")

            channel, manifest, signature, _, _ = self._signed_bundle(root / "current")
            result = inspect_signed_update(
                manifest,
                signature,
                current_version="9.0.0",
                channel_path=channel,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(result["status"], "UPDATE_CURRENT")


if __name__ == "__main__":
    unittest.main()
