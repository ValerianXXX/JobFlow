from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import zipfile
from pathlib import Path

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.public_release import REQUIRED_PUBLIC_FILES
from jobops.release_candidate import WINDOWS_POWERSHELL_UTF8_BOM_FILES
from jobops.update_manifest import (
    attest_extracted_payload,
    build_legacy_update_manifest_v1,
    inventory_archive_payload,
    inspect_legacy_signed_update_v1,
    validate_update_channel,
    verify_legacy_signed_update_bundle_v1,
)
from jobops.util import canonical_json


_WINDOWS_POWERSHELL = shutil.which("powershell.exe")
if not _WINDOWS_POWERSHELL:
    raise RuntimeError("Windows PowerShell is required for signed-update tests.")
WINDOWS_POWERSHELL = Path(_WINDOWS_POWERSHELL).resolve(strict=True)
_GIT = shutil.which("git.exe")
if not _GIT:
    raise RuntimeError("Git for Windows is required for signed-update tests.")
GIT = Path(_GIT).resolve(strict=True)
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
    @staticmethod
    def _builder_text() -> str:
        return (PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")

    @classmethod
    def _builder_function_block(cls) -> str:
        builder = cls._builder_text()
        start = builder.index("function Get-StreamSha256")
        end = builder.index("function Invoke-ProtectedSigningHandoff", start)
        return builder[start:end]

    @staticmethod
    def _valid_release_candidate() -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "RELEASE_CANDIDATE_BUILT",
            "uploaded": False,
            "version": "0.4.2",
            "commit": "a" * 40,
            "artifact_name": "JobFlow-v0.4.2-aaaaaaaaaaaa-source.zip",
            "artifact_sha256": "sha256:" + "b" * 64,
            "artifact_bytes": 123,
            "reproducible_builds": 2,
            "archive": {
                "status": "PASS", "file_count": 12, "finding_count": 0, "findings": [],
            },
            "source_smoke": {
                "status": "PASS",
                "binding": "127.0.0.1",
                "supported_locales": ["zh", "en"],
                "offline_discovery": "PASS",
                "offline_candidates": 2,
                "snapshot_persisted": False,
                "candidate_queue_mutations": 0,
                "private_values_emitted": 0,
                "external_network_actions": 0,
                "real_external_actions": 0,
                "private_store_health": "PASS",
                "private_ciphertext_files": 0,
                "loopback_requests": 5,
                "security_headers": "PASS",
                "project_state_isolated": True,
                "local_app_data_isolated": True,
            },
            "repository_content_status": "PASS",
            "author_identity_status": "PASS",
            "external_network_actions": 0,
            "real_external_actions": 0,
        }

    @staticmethod
    def _run_powershell(script: Path, *arguments: Path | str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script), *(str(argument) for argument in arguments),
            ],
            cwd=script.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _git(project: Path, *arguments: str) -> str:
        completed = subprocess.run(
            [str(GIT), *arguments], cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr + completed.stdout)
        return completed.stdout.strip()

    @staticmethod
    def _authenticode_identity(path: Path) -> tuple[str, str] | None:
        if not path.is_file():
            return None
        encoded_path = base64.b64encode(str(path.resolve()).encode("utf-16-le")).decode("ascii")
        command = (
            "$ProgressPreference='SilentlyContinue';"
            "$m=Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1';"
            "Import-Module -Name $m -ErrorAction Stop;"
            "$p=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('"
            + encoded_path
            + "'));"
            "$s=Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath $p;"
            "if($s.Status -ne [Management.Automation.SignatureStatus]::Valid -or $null -eq $s.SignerCertificate){exit 41};"
            "[pscustomobject]@{subject=[string]$s.SignerCertificate.Subject;"
            "thumbprint=([string]$s.SignerCertificate.Thumbprint).ToUpperInvariant()}|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0 or completed.stderr.strip():
            return None
        try:
            value = json.loads(completed.stdout.lstrip("\ufeff"))
        except json.JSONDecodeError:
            return None
        return str(value["subject"]), str(value["thumbprint"]).upper()

    def _install_signed_fixture_python_launcher(self, fixture: Path) -> None:
        policy = json.loads((fixture / "config" / "release-toolchain.json").read_text(encoding="utf-8"))
        expected = {
            (str(value["subject"]), str(value["thumbprint"]).upper())
            for value in policy["tools"]["python"]["allowed_signers"]
        }
        target = fixture / ".venv" / "Scripts" / "python.exe"
        candidates = (target, PROJECT / ".venv" / "Scripts" / "python.exe", Path(sys.executable))
        source = next(
            (candidate for candidate in candidates if self._authenticode_identity(candidate) in expected),
            None,
        )
        self.assertIsNotNone(source, "No exact PSF-signed Python launcher is available for the fixture")
        if Path(source).resolve() != target.resolve():
            shutil.copy2(source, target)
        self.assertIn(self._authenticode_identity(target), expected)

    def _prepare_bundle_fixture(
        self, root: Path, *, forge_archive: bool = False,
    ) -> tuple[Path, dict[str, object], dict[str, str]]:
        fixture = root / "project"
        fixture.mkdir(parents=True)
        tracked = self._git(PROJECT, "ls-files").splitlines()
        for relative in tracked:
            source = PROJECT / relative
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        # The release toolchain policy is new in this worktree and may not yet
        # appear in the baseline commit used to construct the isolated fixture.
        # Copy the exact production policy explicitly, then create the only
        # interpreter location accepted by the builder.  The venv launcher is
        # PSF Authenticode-signed and remains ignored by the fixture repository.
        toolchain_policy = fixture / "config" / "release-toolchain.json"
        toolchain_policy.parent.mkdir(parents=True, exist_ok=True)
        for name in (
            "python-support-policy.json",
            "release-toolchain.json",
            "windows-cp313-build.lock",
            "windows-cp313-runtime.lock",
            "windows-runtime-source.json",
        ):
            shutil.copy2(PROJECT / "config" / name, fixture / "config" / name)
        venv_created = subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(fixture / ".venv")],
            cwd=fixture,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(venv_created.returncode, 0, venv_created.stderr + venv_created.stdout)
        self._install_signed_fixture_python_launcher(fixture)

        channel, environment = self._initialize_signer(root / "signer")
        (fixture / "config" / "update-channel.json").write_bytes(canonical_json(channel))
        update_module = fixture / "src" / "jobops" / "update_manifest.py"
        update_source = update_module.read_text(encoding="utf-8")
        production_key = json.loads(
            (PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8")
        )["signature"]["key_id"]
        update_module.write_text(
            update_source.replace(production_key, str(channel["signature"]["key_id"])),
            encoding="utf-8",
        )
        # The production builder must stop before either Python release gate
        # and before the signing helper.  Keep the production modules intact
        # so no fixture can accidentally reintroduce a synthetic READY path.
        signer_path = fixture / "scripts" / "release-signing.ps1"
        signer_source = signer_path.read_text(encoding="utf-8-sig")
        signer_path.write_text(
            signer_source.replace(
                '$ErrorActionPreference = "Stop"',
                '$ErrorActionPreference = "Stop"\n'
                'if (-not [string]::IsNullOrWhiteSpace($env:JOBFLOW_TEST_SIGNING_HELPER_MARKER)) {\n'
                '    [IO.File]::WriteAllText($env:JOBFLOW_TEST_SIGNING_HELPER_MARKER, "invoked")\n'
                '}',
                1,
            ),
            encoding="utf-8-sig",
        )

        self._git(fixture, "init")
        self._git(fixture, "config", "user.name", "JobFlow Test")
        self._git(fixture, "config", "user.email", "jobflow-test" + "@" + "example.invalid")
        self._git(fixture, "add", "--all")
        self._git(fixture, "commit", "-m", "signed update fixture")
        commit = self._git(fixture, "rev-parse", "HEAD")
        version = str(tomllib.loads((fixture / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
        archive_name = f"JobFlow-v{version}-{commit[:12]}-source.zip"
        archive_path = fixture / "dist" / archive_name
        archive_path.parent.mkdir()
        completed = subprocess.run(
            [
                str(GIT), "archive", "--format=zip", f"--prefix=JobFlow-v{version}/",
                f"--output={archive_path}", commit,
            ],
            cwd=fixture, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        if forge_archive:
            archive_path.write_bytes(archive_path.read_bytes() + b"forged-ignored-candidate")

        candidate = self._valid_release_candidate()
        candidate.update(
            version=version,
            commit=commit,
            artifact_name=archive_name,
            artifact_sha256="sha256:" + hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            artifact_bytes=archive_path.stat().st_size,
        )
        (fixture / "reports").mkdir(exist_ok=True)
        (fixture / "reports" / "release-candidate.json").write_text(
            json.dumps(candidate), encoding="utf-8",
        )
        environment["LOCALAPPDATA"] = str(root / "signer" / "LocalAppData")
        return fixture, candidate, environment

    def test_release_signer_cannot_silently_rotate_an_existing_key(self) -> None:
        signer = (PROJECT / "scripts" / "release-signing.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("[switch]$Force", signer)
        self.assertNotIn('$env:SystemRoot\\System32\\icacls.exe', signer)
        self.assertIn('Join-Path ([Environment]::SystemDirectory) "icacls.exe"', signer)
        self.assertIn(
            "if ([IO.File]::Exists((ConvertTo-ExtendedFileSystemPath $keyPath)))",
            signer,
        )

    def test_signed_release_bundle_builder_requires_exact_clean_candidate(self) -> None:
        builder = self._builder_text()
        self.assertFalse((PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("JOBFLOW_RELEASE_WORKTREE_NOT_CLEAN", builder)
        self.assertIn("JOBFLOW_RELEASE_COMMIT_MISMATCH", builder)
        self.assertIn("Find-GitApplication", builder)
        self.assertIn(".cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\native\\git\\mingw64\\bin\\git.exe", builder)
        self.assertIn('Join-Path $programFiles "Git\\mingw64\\bin\\git.exe"', builder)
        self.assertNotIn("Get-Command", builder)
        self.assertNotIn("function Find-Python {", builder)
        self.assertNotIn("Find-PythonApplication", builder)
        self.assertNotIn('Join-Path $projectRoot ".venv\\Scripts\\python.exe"', builder)
        self.assertIn("[string]$ReleasePythonArtifactPath", builder)
        self.assertIn("Expand-LockedReleasePythonRuntime", builder)
        self.assertIn("$script:pythonApplication = [string]$releasePythonRuntime.python_path", builder)
        self.assertIn("Get-AuthenticodeSignature", builder)
        self.assertIn("signer_thumbprint", builder)
        self.assertIn("[string]$signer.subject -ceq $subject", builder)
        self.assertIn(".ToUpperInvariant() -ceq $thumbprint", builder)
        self.assertIn("Assert-NoReparsePath", builder)
        self.assertIn("Invoke-IsolatedPythonModule", builder)
        self.assertIn('"-I", "-P", "-S", "-B"', builder)
        self.assertIn("New-SealedReleaseProducerArchive", builder)
        self.assertIn('"producer.pyz"', builder)
        self.assertIn("$script:isolatedPythonSource = [string]$sealedProducerLock.path", builder)
        self.assertNotIn("pycache_prefix=", builder)
        self.assertIn("EnvironmentVariables.Clear", builder)
        self.assertNotIn("$env:PYTHONPATH", builder)
        self.assertIn("Invoke-DeterministicGitArchive", builder)
        self.assertIn("RedirectStandardOutput", builder)
        self.assertIn("function Get-FileSha256", builder)
        self.assertIn("function Get-StreamSha256", builder)
        self.assertIn("[IO.FileShare]::Read", builder)
        self.assertIn("function Assert-PresignManifestArchiveIdentity", builder)
        self.assertIn(
            "Assert-PresignManifestArchiveIdentity $builtManifestValue $candidate $archiveLock",
            builder,
        )
        self.assertIn("Commit-TemporaryOutput", builder)
        self.assertIn("JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED", builder)
        self.assertIn("JOBFLOW_RELEASE_PROJECT_VERSION_MISMATCH", builder)
        self.assertIn("reproducible_builds", builder)
        self.assertIn("Recover-PendingOutputTransaction", builder)
        self.assertIn("[IO.FileMode]::CreateNew", builder)
        self.assertNotIn("$stream.SetLength(0)", builder)
        self.assertNotIn("authenticate-signing-evidence", builder)
        self.assertIn("JOBFLOW_PROTECTED_SIGNING_STAGE_REQUIRED", builder)
        self.assertIn("JOBFLOW_PROTECTED_SIGNATURE_REQUIRED", builder)
        self.assertIn("JobFlow-update-manifest.presign.json", builder)
        self.assertIn("JobFlow-update-signing-request.json", builder)
        self.assertIn('"--validation-time-utc", $currentTrustedTime', builder)
        self.assertIn("Test-LockedBytesEqual", builder)
        self.assertNotIn("signing-transaction", builder)
        self.assertNotIn("jobops.release_readiness", builder)
        self.assertNotIn("FRESH_PUBLIC_RELEASE_AUTHORIZED", builder)
        self.assertNotIn("transaction_nonce_sha256", builder)
        self.assertNotIn('"-Action", "Sign"', builder)
        self.assertNotIn("release-signing.ps1", builder)
        self.assertIn('status = "SIGNED_UPDATE_BUNDLE_READY"', builder)
        self.assertIn('$builtManifestLock = Invoke-RequiredPythonCanonicalOutput', builder)
        self.assertIn('$signatureCommitTemporary = Copy-StreamToExclusiveOutput', builder)
        self.assertTrue(builder.rstrip().endswith("}"))
        self.assertIn(
            'Move-OutputFileReplaceExisting $temporary $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"',
            builder,
        )
        self.assertNotIn("Get-FileHash", builder)

    def test_current_store_attestation_intentionally_blocks_signing_readiness(self) -> None:
        manifest = json.loads((PROJECT / "browser-companion" / "manifest.json").read_text(encoding="utf-8"))
        release = json.loads((PROJECT / "config" / "github-release.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.9.2")
        self.assertEqual(release["browser_companion_chrome_published_version"], "0.9.1")
        self.assertEqual(release["browser_companion_edge_published_version"], "0.9.1")

    def test_manifest_and_signature_prewrite_hardlinks_never_truncate_the_sentinel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-prewrite-hardlink-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            sentinel = root / "sentinel.bin"
            sentinel_payload = b"DO-NOT-TRUNCATE-JOBFLOW-SENTINEL"
            sentinel.write_bytes(sentinel_payload)

            archive = root / ("JobFlow-v0.6.0-" + "a" * 12 + "-source.zip")
            archive.write_bytes(b"archive")
            manifest_output = root / "manifest.json"
            os.link(sentinel, manifest_output)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(PROJECT / "src")
            completed = subprocess.run(
                [
                    sys.executable, "-m", "jobops.update_manifest", "build",
                    "--archive", str(archive), "--version", "0.6.0", "--commit", "a" * 40,
                    "--output", str(manifest_output),
                ],
                cwd=PROJECT, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(sentinel.read_bytes(), sentinel_payload)

            channel, signing_environment = self._initialize_signer(root / "signer")
            self.assertIn("signature", channel)
            manifest = root / "input-manifest.json"
            manifest.write_text('{"schema_version":1}', encoding="utf-8")
            signature_output = root / "signature.json"
            os.link(sentinel, signature_output)
            signed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                    "-Action", "SignDevelopmentFixture", "-ManifestPath", str(manifest),
                    "-SignatureOutput", str(signature_output),
                ],
                cwd=PROJECT, env=signing_environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(sentinel.read_bytes(), sentinel_payload)
            if signed.returncode == 0:
                self.assertFalse(os.path.samefile(sentinel, signature_output))

    def test_bundle_builder_sha256_helper_does_not_depend_on_powershell_modules(self) -> None:
        builder = self._builder_text()
        helper_start = builder.index("function Get-StreamSha256")
        helper_end = builder.index("function Assert-ProjectPath", helper_start)
        helper = builder[helper_start:helper_end]
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-hash-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            payload = root / "artifact.bin"
            payload.write_bytes(b"JobFlow signed update hash fixture\x00\xff")
            harness = root / "hash-helper.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + helper
                + "\nfunction global:Get-FileHash { throw 'POISONED_GET_FILE_HASH' }\n"
                + "$result = Get-FileSha256 -Path $args[0]\n"
                + "[Console]::Out.Write($result)\n",
                encoding="utf-8-sig",
            )
            environment = os.environ.copy()
            environment["PSModulePath"] = str(root / "empty-modules")
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness), str(payload),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("POISONED_GET_FILE_HASH", completed.stderr)
            self.assertEqual(
                completed.stdout.lstrip("\ufeff"),
                hashlib.sha256(payload.read_bytes()).hexdigest(),
            )

    def test_candidate_json_types_are_rejected_instead_of_coerced(self) -> None:
        valid = self._valid_release_candidate()
        mutations = {
            "schema_boolean": {**valid, "schema_version": True},
            "schema_string": {**valid, "schema_version": "1"},
            "uploaded_integer": {**valid, "uploaded": 0},
            "uploaded_string": {**valid, "uploaded": "false"},
            "bytes_string": {**valid, "artifact_bytes": "123"},
            "bytes_float": {**valid, "artifact_bytes": 123.5},
            "wrong_artifact_version": {**valid, "artifact_name": "JobFlow-v9.9.9-aaaaaaaaaaaa-source.zip"},
            "reproducible_string": {**valid, "reproducible_builds": "2"},
            "reproducible_one": {**valid, "reproducible_builds": 1},
            "archive_finding": {
                **valid,
                "archive": {"status": "PASS", "file_count": 12, "finding_count": 1, "findings": ["bad"]},
            },
            "source_network_action": {
                **valid,
                "source_smoke": {**valid["source_smoke"], "external_network_actions": 1},
            },
            "private_ciphertext_present": {
                **valid,
                "source_smoke": {**valid["source_smoke"], "private_ciphertext_files": 1},
            },
            "unexpected_top_level_key": {**valid, "unverified": True},
            "unexpected_archive_key": {
                **valid,
                "archive": {**valid["archive"], "unverified": True},
            },
            "unexpected_source_smoke_key": {
                **valid,
                "source_smoke": {**valid["source_smoke"], "unverified": True},
            },
            "repository_missing": {key: value for key, value in valid.items() if key != "repository_content_status"},
            "top_action_string": {**valid, "real_external_actions": "0"},
        }
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-types-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            harness = root / "validate-candidate.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$value = Get-Content -LiteralPath $args[1] -Raw | ConvertFrom-Json\n"
                + "try { Assert-ReleaseCandidate $value; [Console]::Out.Write('ACCEPTED') } "
                  "catch { [Console]::Out.Write([string]$_.Exception.Message) }\n",
                encoding="utf-8-sig",
            )
            accepted = root / "valid.json"
            accepted.write_text(json.dumps(valid), encoding="utf-8")
            completed = self._run_powershell(harness, root, accepted)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.lstrip("\ufeff"), "ACCEPTED")
            for label, value in mutations.items():
                path = root / f"{label}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                completed = self._run_powershell(harness, root, path)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    completed.stdout.lstrip("\ufeff"),
                    "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID",
                    label,
                )

    def test_archive_identity_handle_blocks_write_and_replacement_after_hash(self) -> None:
        builder = self._builder_text()
        start = builder.index("function Get-StreamSha256")
        end = builder.index("function Assert-ProjectPath", start)
        helpers = builder[start:end]
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-lock-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            archive = root / "candidate.zip"
            archive.write_bytes(b"locked archive identity")
            replacement = root / "replacement.zip"
            replacement.write_bytes(b"replacement")
            ready = root / "ready"
            stop = root / "stop"
            harness = root / "hold-archive.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + helpers
                + "\n$stream = [IO.File]::Open($args[0],[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)\n"
                + "try { $hash = Get-StreamSha256 $stream; [IO.File]::WriteAllText($args[1],$hash); "
                  "while (-not [IO.File]::Exists($args[2])) { Start-Sleep -Milliseconds 20 } } "
                  "finally { $stream.Dispose() }\n",
                encoding="utf-8-sig",
            )
            process = subprocess.Popen(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness), str(archive), str(ready), str(stop),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), process.stderr.read() if process.poll() is not None else "")
                with self.assertRaises(PermissionError):
                    archive.write_bytes(b"mutated")
                with self.assertRaises(PermissionError):
                    os.replace(replacement, archive)
                self.assertEqual(
                    ready.read_text(encoding="utf-8-sig"),
                    hashlib.sha256(b"locked archive identity").hexdigest(),
                )
            finally:
                stop.write_text("stop", encoding="utf-8")
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr + stdout)

    def test_candidate_version_must_equal_pyproject_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-project-version-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            pyproject = root / "pyproject.toml"
            pyproject.write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
            builder = self._builder_text()
            no_reparse_block = builder[
                builder.index("function Assert-NoReparsePath") : builder.index(
                    "function Get-ReleaseToolchainPolicy"
                )
            ]
            harness = root / "candidate-version.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + no_reparse_block
                + self._builder_function_block()
                + "\nfunction Invoke-SanitizedGit {\n"
                + "  param([string]$GitApplication,[string[]]$Arguments)\n"
                + "  if ($Arguments[0] -ceq 'status') { return [pscustomobject]@{exit_code=0;stdout='';stderr=''} }\n"
                + "  return [pscustomobject]@{exit_code=0;stdout=('a' * 40);stderr=''}\n"
                + "}\n"
                + "Initialize-JobFlowReleaseFileIdentityApi\n"
                + "$pyproject = Enter-InputFileLock $args[1] 262144 'JOBFLOW_RELEASE_PROJECT_VERSION_INVALID'\n"
                + "$candidate = [pscustomobject]@{version='0.4.2';commit=('a' * 40)}\n"
                + "try { Assert-CleanReleaseCandidateRepository $candidate $pyproject } finally { $pyproject.stream.Dispose() }\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, root, pyproject.resolve())
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_RELEASE_PROJECT_VERSION_MISMATCH", completed.stderr)

    def test_fixed_output_hardlink_is_atomically_replaced_without_touching_its_other_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-hardlink-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            external = root / "external-sentinel.json"
            external.write_bytes(b"external-old")
            destination = dist / "JobFlow-update-manifest.json"
            os.link(external, destination)
            temporary = dist / "manifest.random.tmp"
            temporary.write_bytes(b"new-manifest")
            harness = root / "commit-output.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$record = New-OutputCommitRecord $args[1] $args[2]\n"
                + "Commit-TemporaryOutput $record\nRemove-OutputCommitBackup $record\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, root, temporary, destination)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(destination.read_bytes(), b"new-manifest")
            self.assertEqual(external.read_bytes(), b"external-old")

    def test_fixed_output_reparse_leaf_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-reparse-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            external = root / "external-target"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            destination = dist / "JobFlow-update-manifest.json"
            linked = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(destination), str(external)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr + linked.stdout)
            temporary = dist / "manifest.random.tmp"
            temporary.write_bytes(b"new-manifest")
            harness = root / "commit-output.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$record = New-OutputCommitRecord $args[1] $args[2]\nCommit-TemporaryOutput $record\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, root, temporary, destination)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED", completed.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_temporary_output_hardlink_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-temp-hardlink-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            temporary = root / "manifest.random.tmp"
            temporary.write_bytes(b"reserved-output")
            alias = root / "attacker-alias.tmp"
            os.link(temporary, alias)
            harness = root / "validate-temporary.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\nAssert-OrdinaryOutputLeaf $args[1] 'TEMP_LINK_REJECTED' -MustExist -SingleLink\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, root, temporary)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("TEMP_LINK_REJECTED", completed.stderr)
            self.assertEqual(alias.read_bytes(), b"reserved-output")

    def test_interrupted_two_output_commit_is_durably_recovered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-transaction-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            manifest = dist / "JobFlow-update-manifest.json"
            signature = dist / "JobFlow-update-manifest.sig.json"
            manifest.write_bytes(b"old-manifest")
            signature.write_bytes(b"old-signature")
            manifest_temp = dist / "manifest.random.tmp"
            signature_temp = dist / "signature.random.tmp"
            manifest_temp.write_bytes(b"new-manifest")
            signature_temp.write_bytes(b"new-signature")
            marker = dist / "JobFlow-update-bundle.transaction.json"
            interrupt = root / "interrupt-commit.ps1"
            interrupt.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$manifest = New-OutputCommitRecord $args[1] $args[2]\n"
                + "$signature = New-OutputCommitRecord $args[3] $args[4]\n"
                + "$manifest.new_hash = 'sha256:' + (Get-FileSha256 $args[1])\n"
                + "$signature.new_hash = 'sha256:' + (Get-FileSha256 $args[3])\n"
                + "Write-OutputTransactionMarker $args[5] $manifest $signature\n"
                + "Commit-TemporaryOutput $signature\n"
                + "[Environment]::Exit(77)\n",
                encoding="utf-8-sig",
            )
            interrupted = self._run_powershell(
                interrupt,
                root,
                manifest_temp,
                manifest,
                signature_temp,
                signature,
                marker,
            )
            self.assertEqual(interrupted.returncode, 77, interrupted.stderr + interrupted.stdout)
            self.assertTrue(marker.is_file())
            self.assertEqual(signature.read_bytes(), b"new-signature")
            self.assertEqual(manifest.read_bytes(), b"old-manifest")

            recover = root / "recover-commit.ps1"
            recover.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\nRecover-PendingOutputTransaction $args[1] $args[2] | Out-Null\n",
                encoding="utf-8-sig",
            )
            recovered = self._run_powershell(recover, root, marker, dist)
            self.assertEqual(recovered.returncode, 0, recovered.stderr + recovered.stdout)
            self.assertEqual(manifest.read_bytes(), b"old-manifest")
            self.assertEqual(signature.read_bytes(), b"old-signature")
            self.assertFalse(marker.exists())
            leftovers = [
                path.name for path in dist.iterdir()
                if path.name.endswith((".bak", ".rollback", ".transaction.json"))
            ]
            self.assertEqual(leftovers, [])

    def test_transaction_lock_rejects_a_concurrent_builder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-lock-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / "JobFlow-update-bundle.lock"
            ready = root / "ready"
            stop = root / "stop"
            holder = root / "hold-lock.ps1"
            holder.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$lock = Enter-OutputTransactionLock $args[1]\n"
                + "try { [IO.File]::WriteAllText($args[2], 'ready'); "
                  "while (-not [IO.File]::Exists($args[3])) { Start-Sleep -Milliseconds 20 } } "
                  "finally { $lock.Dispose() }\n",
                encoding="utf-8-sig",
            )
            contender = root / "contend-lock.ps1"
            contender.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$lock = Enter-OutputTransactionLock $args[1]\n$lock.Dispose()\n",
                encoding="utf-8-sig",
            )
            process = subprocess.Popen(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(holder), str(root), str(lock), str(ready), str(stop),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), process.stderr.read() if process.poll() is not None else "")
                blocked = self._run_powershell(contender, root, lock)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("JOBFLOW_RELEASE_OUTPUT_TRANSACTION_ACTIVE", blocked.stderr)
            finally:
                stop.write_text("stop", encoding="utf-8")
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr + stdout)
            acquired = self._run_powershell(contender, root, lock)
            self.assertEqual(acquired.returncode, 0, acquired.stderr + acquired.stdout)

    def test_post_commit_validation_failure_restores_the_previous_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-postcommit-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            manifest = dist / "JobFlow-update-manifest.json"
            signature = dist / "JobFlow-update-manifest.sig.json"
            manifest.write_bytes(b"old-manifest")
            signature.write_bytes(b"old-signature")
            manifest_temp = dist / "manifest.random.tmp"
            signature_temp = dist / "signature.random.tmp"
            manifest_temp.write_bytes(b"new-manifest")
            signature_temp.write_bytes(b"new-signature")
            marker = dist / "JobFlow-update-bundle.transaction.json"
            harness = root / "postcommit-failure.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$manifest = New-OutputCommitRecord $args[1] $args[2]\n"
                + "$signature = New-OutputCommitRecord $args[3] $args[4]\n"
                + "$manifest.new_hash = 'sha256:' + (Get-FileSha256 $args[1])\n"
                + "$signature.new_hash = 'sha256:' + (Get-FileSha256 $args[3])\n"
                + "Write-OutputTransactionMarker $args[5] $manifest $signature\n"
                + "Commit-TemporaryOutput $signature\nCommit-TemporaryOutput $manifest\n"
                + "Recover-PendingOutputTransaction $args[5] ([IO.Path]::GetDirectoryName($args[2])) -ForceRollback | Out-Null\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(
                harness,
                root,
                manifest_temp,
                manifest,
                signature_temp,
                signature,
                marker,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(manifest.read_bytes(), b"old-manifest")
            self.assertEqual(signature.read_bytes(), b"old-signature")
            self.assertFalse(marker.exists())

    def test_protected_handoff_has_no_local_signing_path(self) -> None:
        builder = self._builder_text()
        guard = 'throw "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED"'
        self.assertEqual(builder.count(guard), 1)
        self.assertIn('$Stage -cne "Prepare"', builder)
        self.assertIn('$Stage -cne "Finalize"', builder)
        for forbidden in (
            '"-Action", "Sign"',
            "release-signing.ps1",
            "private_key",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, builder.casefold() if forbidden == "private_key" else builder)

    def test_python_path_excludes_git_closure_while_git_path_keeps_required_helpers(self) -> None:
        builder = self._builder_text()
        environment_functions = builder[
            builder.index("function Get-KnownFolderPath") : builder.index(
                "function Invoke-SanitizedTextCommand"
            )
        ]
        git_root = GIT.parent.parent
        git_application = git_root / "mingw64" / "bin" / "git.exe"
        self.assertTrue(git_application.is_file(), str(git_application))
        with tempfile.TemporaryDirectory(
            prefix="jobflow-tool-path-isolation-", dir=SIGNED_UPDATE_TEST_ROOT
        ) as raw:
            root = Path(raw)
            harness = root / "inspect-tool-paths.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + environment_functions
                + "\n$python = New-Object Diagnostics.ProcessStartInfo\n"
                + "Set-SanitizedProcessEnvironment $python 'python' $args[0] $args[1]\n"
                + "$git = New-Object Diagnostics.ProcessStartInfo\n"
                + "Set-SanitizedProcessEnvironment $git 'git' $args[1] $null\n"
                + "[ordered]@{\n"
                + "  python_path = [string]$python.EnvironmentVariables['PATH']\n"
                + "  python_git_config = $python.EnvironmentVariables['GIT_CONFIG_NOSYSTEM']\n"
                + "  git_path = [string]$git.EnvironmentVariables['PATH']\n"
                + "  git_config_nosystem = [string]$git.EnvironmentVariables['GIT_CONFIG_NOSYSTEM']\n"
                + "  git_config_global = [string]$git.EnvironmentVariables['GIT_CONFIG_GLOBAL']\n"
                + "} | ConvertTo-Json -Compress\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(
                harness, Path(sys.executable).resolve(), git_application.resolve()
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            value = json.loads(completed.stdout.lstrip("\ufeff"))

        def normalized_entries(value: str) -> list[str]:
            return [
                os.path.normcase(os.path.normpath(entry))
                for entry in value.split(os.pathsep)
                if entry
            ]

        python_path = normalized_entries(str(value["python_path"]))
        git_path = normalized_entries(str(value["git_path"]))
        normalized_git_root = os.path.normcase(os.path.normpath(git_root))
        git_prefix = normalized_git_root + os.sep
        self.assertFalse(
            any(entry == normalized_git_root or entry.startswith(git_prefix) for entry in python_path),
            python_path,
        )
        for required in (
            git_application.parent,
            git_root / "mingw64" / "bin",
            git_root / "usr" / "bin",
        ):
            if required.is_dir():
                self.assertIn(os.path.normcase(os.path.normpath(required)), git_path)
        self.assertIsNone(value["python_git_config"])
        self.assertEqual(value["git_config_nosystem"], "1")
        self.assertEqual(value["git_config_global"], "NUL")

    def test_signed_manifest_archive_identity_rejects_coercive_types(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-manifest-types-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            harness = root / "validate-manifest.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + self._builder_function_block()
                + "\n$value = Get-Content -LiteralPath $args[1] -Raw | ConvertFrom-Json\n"
                + "$candidate = [pscustomobject]@{version='0.4.2';commit=('a' * 40)}\n"
                + "$archive = [pscustomobject]@{length=12;sha256=('sha256:' + ('a' * 64))}\n"
                + "Assert-PresignManifestArchiveIdentity $value $candidate $archive\n",
                encoding="utf-8-sig",
            )
            for label, value in {
                "schema_bool": {
                    "schema_version": True,
                    "release": {"version": "0.4.2", "source_commit": "a" * 40},
                    "asset": {"name": "JobFlow-v0.4.2-windows-x64-complete.zip", "sha256": "sha256:" + "a" * 64, "bytes": 12},
                },
                "bytes_string": {
                    "schema_version": 2,
                    "release": {"version": "0.4.2", "source_commit": "a" * 40},
                    "asset": {"name": "JobFlow-v0.4.2-windows-x64-complete.zip", "sha256": "sha256:" + "a" * 64, "bytes": "12"},
                },
            }.items():
                path = root / f"{label}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                completed = self._run_powershell(harness, root, path)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("JOBFLOW_RELEASE_SIGNED_ARCHIVE_IDENTITY_MISMATCH", completed.stderr)

    def test_bundle_builder_fails_closed_before_signer_or_formal_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-e2e-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            fixture, _candidate, environment = self._prepare_bundle_fixture(root)
            manifest = fixture / "dist" / "JobFlow-update-manifest.json"
            signature = fixture / "dist" / "JobFlow-update-manifest.sig.json"
            signing_marker = root / "signing-helper-invoked.marker"
            key_path = (
                root / "signer" / "LocalAppData" / "JobOps" /
                "DevelopmentFixtureSigning" / "development-fixture-signing-key.dpapi"
            )
            key_before = _read_bytes(key_path)
            key_mtime_before = key_path.stat().st_mtime_ns
            poison = root / "caller-controlled-tool-environment"
            environment.update(
                {
                    "PYTHONHOME": str(poison / "python-home"),
                    "PYTHONPATH": str(poison / "python-path"),
                    "PYTHONSTARTUP": str(poison / "python-startup.py"),
                    "PYTHONWARNINGS": "error",
                    "NODE_OPTIONS": "--require=" + str(poison / "missing-node-preload.cjs"),
                    "NODE_PATH": str(poison / "node-path"),
                    "GIT_DIR": str(poison / "wrong-git-dir"),
                    "GIT_WORK_TREE": str(poison / "wrong-work-tree"),
                    "GIT_CONFIG_GLOBAL": str(poison / "wrong-git-config"),
                    "GIT_EXEC_PATH": str(poison / "wrong-git-exec-path"),
                    "JOBFLOW_TEST_SIGNING_HELPER_MARKER": str(signing_marker),
                    "JOBFLOW_PUBLIC_SIGNING_ACK": "I_ACCEPT",
                    "JOBFLOW_PUBLIC_SIGNING_FORCE": "1",
                    "JOBFLOW_RELEASE_RUNTIME_CLOSURE_ATTESTED": "true",
                }
            )
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(fixture / "scripts" / "build-signed-update-bundle.ps1"),
                ],
                cwd=fixture,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_PROTECTED_SIGNING_STAGE_REQUIRED", completed.stderr)
            self.assertFalse(signing_marker.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(signature.exists())
            self.assertEqual(_read_bytes(key_path), key_before)
            self.assertEqual(key_path.stat().st_mtime_ns, key_mtime_before)
            leftovers = [
                path.name for path in (fixture / "dist").iterdir()
                if path.name.endswith((".tmp", ".bak", ".rollback", ".transaction.json"))
            ]
            self.assertEqual(leftovers, [])

    def test_runtime_closure_guard_precedes_ignored_archive_processing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-bundle-forged-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            fixture, _candidate, environment = self._prepare_bundle_fixture(root, forge_archive=True)
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(fixture / "scripts" / "build-signed-update-bundle.ps1"),
                ],
                cwd=fixture,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_PROTECTED_SIGNING_STAGE_REQUIRED", completed.stderr)
            self.assertFalse((fixture / "dist" / "JobFlow-update-manifest.json").exists())
            self.assertFalse((fixture / "dist" / "JobFlow-update-manifest.sig.json").exists())

    @staticmethod
    def _key_id(channel_path: Path) -> str:
        return str(json.loads(channel_path.read_text(encoding="utf-8"))["signature"]["key_id"])

    def _initialize_signer(self, root: Path) -> tuple[dict[str, object], dict[str, str]]:
        root.mkdir(parents=True, exist_ok=True)
        local_app_data = root / "LocalAppData"
        # The deep-path test deliberately crosses the legacy Windows MAX_PATH
        # boundary.  Create its next directory through the extended path too,
        # so the fixture reaches the signer instead of failing in pathlib.
        Path(_windows_extended_path(local_app_data)).mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(local_app_data)
        completed = subprocess.run(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                "-Action", "InitializeDevelopmentFixture", "-EmitDevelopmentFixtureChannel",
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
        self.assertEqual(result["status"], "DEVELOPMENT_FIXTURE_SIGNING_KEY_READY")
        self.assertEqual(result["signing_scope"], "development-fixture")
        self.assertNotIn(str(local_app_data), completed.stdout)
        key_path = (
            local_app_data / "JobOps" / "DevelopmentFixtureSigning" /
            "development-fixture-signing-key.dpapi"
        )
        protected = _read_bytes(key_path)
        self.assertGreater(len(protected), 512)
        self.assertNotIn(b'"Modulus"', protected)
        return result["development_fixture_channel"], environment

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
                if relative == "Install JobFlow.cmd":
                    payload = (
                        b"@echo off\r\n"
                        b"powershell.exe -NoProfile -ExecutionPolicy Bypass "
                        b"-File scripts\\install-jobflow-v2.ps1\r\n"
                    )
                if relative in WINDOWS_POWERSHELL_UTF8_BOM_FILES:
                    payload = b"\xef\xbb\xbf" + payload
                archive.writestr(prefix + relative, payload)
        return archive_path

    def _signed_bundle(
        self, root: Path, *, preserve_development_scope: bool = False,
    ) -> tuple[Path, Path, Path, Path, dict[str, str]]:
        channel, environment = self._initialize_signer(root)
        channel_path = root / "update-channel.json"
        channel_path.write_bytes(canonical_json(channel))
        version, commit = "0.4.2", "a" * 40
        archive_path = self._candidate_archive(root, version=version, commit=commit)
        manifest = build_legacy_update_manifest_v1(archive_path=archive_path, version=version, commit=commit)
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
                "-Action", "SignDevelopmentFixture", "-ManifestPath", str(manifest_path),
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
        self.assertEqual(sign_result["status"], "DEVELOPMENT_FIXTURE_MANIFEST_SIGNED")
        self.assertEqual(sign_result["signing_scope"], "development-fixture")
        self.assertNotIn(str(root), completed.stdout)
        envelope = json.loads(signature_path.read_text(encoding="utf-8"))
        self.assertEqual(envelope["scope"], "development-fixture")
        if not preserve_development_scope:
            # Production verification tests below exercise the cryptographic
            # primitive with an ephemeral trusted key.  Remove only the local
            # signer's explicit fixture-domain marker for those isolated tests;
            # production callers never receive this transformed envelope.
            envelope.pop("scope")
            signature_path.write_bytes(canonical_json(envelope))
        return channel_path, manifest_path, signature_path, archive_path, environment

    def test_production_channel_has_a_strong_self_identifying_public_key(self) -> None:
        channel = json.loads((PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8"))
        validated = validate_update_channel(channel)
        self.assertEqual(validated["repository"], "ValerianXXX/JobFlow")
        self.assertEqual(validated["channel"], "stable")
        self.assertEqual(validated["signature"]["algorithm"], "RSA-PKCS1-v1_5-SHA256")

    def test_local_development_key_is_distinct_from_the_production_pin(self) -> None:
        production = json.loads(
            (PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory(prefix="jobflow-development-key-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            development, _ = self._initialize_signer(root)
            self.assertNotEqual(
                development["signature"]["key_id"],
                production["signature"]["key_id"],
            )
            self.assertFalse(
                (root / "LocalAppData" / "JobOps" / "ReleaseSigning" /
                 "release-signing-key.dpapi").exists()
            )

    def test_legacy_local_publisher_state_is_never_read_or_migrated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-legacy-publisher-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            local_app_data = root / "LocalAppData"
            legacy = (
                local_app_data / "JobOps" / "ReleaseSigning" /
                "release-signing-key.dpapi"
            )
            legacy.parent.mkdir(parents=True)
            sentinel = b"opaque-legacy-key-must-not-be-read-or-changed"
            legacy.write_bytes(sentinel)
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                    "-Action", "InitializeDevelopmentFixture",
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
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "JOBFLOW_LEGACY_PUBLISHER_SIGNING_STATE_PRESENT",
                completed.stderr,
            )
            self.assertEqual(legacy.read_bytes(), sentinel)
            self.assertFalse(
                (local_app_data / "JobOps" / "DevelopmentFixtureSigning").exists()
            )

    def test_development_signer_output_is_rejected_even_when_its_key_is_explicitly_trusted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-development-envelope-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, manifest, signature, _, _ = self._signed_bundle(
                root, preserve_development_scope=True,
            )
            with self.assertRaises(JobOpsError) as rejected:
                inspect_legacy_signed_update_v1(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(
                rejected.exception.code,
                "UPDATE_DEVELOPMENT_SIGNATURE_FORBIDDEN",
            )

    def test_development_key_cannot_replace_the_default_production_trust_anchor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-development-pin-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, manifest, signature, _, _ = self._signed_bundle(root)
            with self.assertRaises(JobOpsError) as rejected:
                inspect_legacy_signed_update_v1(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                )
            self.assertEqual(rejected.exception.code, "UPDATE_CHANNEL_INVALID")

    def test_development_signer_refuses_a_matching_project_pin_before_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-development-match-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            channel, environment = self._initialize_signer(root / "signer")
            fixture = root / "project"
            (fixture / "scripts").mkdir(parents=True)
            (fixture / "config").mkdir()
            (fixture / ".jobops-root").write_text("fixture", encoding="utf-8")
            shutil.copy2(
                PROJECT / "scripts" / "release-signing.ps1",
                fixture / "scripts" / "release-signing.ps1",
            )
            pinned = json.loads(
                (PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8")
            )
            pinned["signature"]["key_id"] = channel["signature"]["key_id"]
            (fixture / "config" / "update-channel.json").write_text(
                json.dumps(pinned), encoding="utf-8",
            )
            manifest = fixture / "manifest.json"
            manifest.write_text('{"schema_version":1}', encoding="utf-8")
            signature = fixture / "signature.json"
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(fixture / "scripts" / "release-signing.ps1"),
                    "-Action", "SignDevelopmentFixture", "-ManifestPath", str(manifest),
                    "-SignatureOutput", str(signature),
                ],
                cwd=fixture,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "JOBFLOW_DEVELOPMENT_FIXTURE_KEY_MATCHES_PRODUCTION",
                completed.stderr,
            )
            self.assertFalse(signature.exists())

    def test_generic_production_sign_action_is_not_exposed_by_the_local_helper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-generic-sign-blocked-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            _, environment = self._initialize_signer(root)
            key_path = (
                root / "LocalAppData" / "JobOps" / "DevelopmentFixtureSigning" /
                "development-fixture-signing-key.dpapi"
            )
            key_before = key_path.read_bytes()
            manifest = root / "manifest.json"
            manifest.write_text('{"schema_version":1}', encoding="utf-8")
            signature = root / "signature.json"
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PROJECT / "scripts" / "release-signing.ps1"),
                    "-Action", "Sign", "-ManifestPath", str(manifest),
                    "-SignatureOutput", str(signature),
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
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(signature.exists())
            self.assertEqual(key_path.read_bytes(), key_before)

    def test_initialize_reuses_the_same_dpapi_key_without_rotation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-signing-key-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            first, _ = self._initialize_signer(root)
            key_path = (
                root / "LocalAppData" / "JobOps" / "DevelopmentFixtureSigning" /
                "development-fixture-signing-key.dpapi"
            )
            protected_before = _read_bytes(key_path)
            second, _ = self._initialize_signer(root)
            self.assertEqual(first["signature"]["key_id"], second["signature"]["key_id"])
            self.assertEqual(protected_before, _read_bytes(key_path))

    def test_release_signer_supports_deep_source_archive_paths(self) -> None:
        prefix_base = "jobflow-signing-long-path-"
        key_relative = (
            Path("LocalAppData") / "JobOps" / "DevelopmentFixtureSigning" /
            "development-fixture-signing-key.dpapi"
        )
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
            key_path = (
                root / "LocalAppData" / "JobOps" / "DevelopmentFixtureSigning" /
                "development-fixture-signing-key.dpapi"
            )
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
            inspected = inspect_legacy_signed_update_v1(
                manifest,
                signature,
                current_version="0.4.1",
                channel_path=channel,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(inspected["status"], "UPDATE_AVAILABLE")
            self.assertTrue(inspected["signature_verified"])
            verified = verify_legacy_signed_update_bundle_v1(
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
            current = inspect_legacy_signed_update_v1(
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
                inspect_legacy_signed_update_v1(
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
                inspect_legacy_signed_update_v1(
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
                verify_legacy_signed_update_bundle_v1(
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
                inspect_legacy_signed_update_v1(
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
                inspect_legacy_signed_update_v1(
                    manifest,
                    signature,
                    current_version="0.4.1",
                    channel_path=channel,
                    trusted_key_id=self._key_id(channel),
                )
            self.assertEqual(noncanonical.exception.code, "UPDATE_MANIFEST_INVALID")

            channel, manifest, signature, _, _ = self._signed_bundle(root / "current")
            result = inspect_legacy_signed_update_v1(
                manifest,
                signature,
                current_version="9.0.0",
                channel_path=channel,
                trusted_key_id=self._key_id(channel),
            )
            self.assertEqual(result["status"], "UPDATE_CURRENT")

    def test_archive_derived_payload_attestation_is_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-payload-attestation-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            archive = root / "payload.zip"
            prefix = "JobFlow-v1.2.3/"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(prefix, b"")
                bundle.writestr(prefix + ".jobops-root", b"root")
                bundle.writestr(prefix + "scripts/install-jobflow.ps1", b"installer")
            inventory = inventory_archive_payload(archive, prefix)
            self.assertEqual(inventory["status"], "UPDATE_ARCHIVE_PAYLOAD_INVENTORIED")
            self.assertEqual(inventory["file_count"], 2)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root / "extracted")
            package = root / "extracted" / "JobFlow-v1.2.3"
            attested = attest_extracted_payload(archive, prefix, package)
            self.assertEqual(attested["status"], "UPDATE_EXTRACTED_PAYLOAD_ATTESTED")
            self.assertEqual(attested["records"], inventory["records"])

            (package / "scripts" / "install-jobflow.ps1").write_bytes(b"changed")
            with self.assertRaises(JobOpsError) as changed:
                attest_extracted_payload(archive, prefix, package)
            self.assertEqual(changed.exception.code, "UPDATE_EXTRACTED_PAYLOAD_MISMATCH")

            (package / "scripts" / "install-jobflow.ps1").write_bytes(b"installer")
            (package / "extra.txt").write_bytes(b"late extra")
            with self.assertRaises(JobOpsError) as extra:
                attest_extracted_payload(archive, prefix, package)
            self.assertEqual(extra.exception.code, "UPDATE_EXTRACTED_PAYLOAD_MISMATCH")

    def test_payload_inventory_rejects_aliases_unsafe_paths_and_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-payload-adversarial-", dir=SIGNED_UPDATE_TEST_ROOT) as raw:
            root = Path(raw)
            prefix = "JobFlow-v1.2.3/"
            duplicate = root / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as bundle:
                bundle.writestr(prefix + "Readme.txt", b"one")
                bundle.writestr(prefix + "README.TXT", b"two")
            with self.assertRaises(JobOpsError) as duplicate_error:
                inventory_archive_payload(duplicate, prefix)
            self.assertEqual(duplicate_error.exception.code, "UPDATE_ARCHIVE_PAYLOAD_INVALID")

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as bundle:
                bundle.writestr(prefix + "../outside.txt", b"escape")
            with self.assertRaises(JobOpsError) as unsafe_error:
                inventory_archive_payload(unsafe, prefix)
            self.assertEqual(unsafe_error.exception.code, "UPDATE_ARCHIVE_PAYLOAD_INVALID")

            for index, unsafe_name in enumerate(
                (
                    "control-\x01.txt",
                    "delete-\x7f.txt",
                    "unicode-\u2028.txt",
                    'quote-".txt',
                    "less-than-<.txt",
                    "greater-than->.txt",
                    "pipe-|.txt",
                    "question-?.txt",
                    "asterisk-*.txt",
                )
            ):
                unsafe_ascii = root / f"unsafe-ascii-{index}.zip"
                with zipfile.ZipFile(unsafe_ascii, "w") as bundle:
                    bundle.writestr(prefix + unsafe_name, b"unsafe")
                with self.subTest(unsafe_name=unsafe_name), self.assertRaises(JobOpsError) as unsafe_ascii_error:
                    inventory_archive_payload(unsafe_ascii, prefix)
                self.assertEqual(unsafe_ascii_error.exception.code, "UPDATE_ARCHIVE_PAYLOAD_INVALID")

            linked = root / "linked.zip"
            link = zipfile.ZipInfo(prefix + "linked.txt")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(linked, "w") as bundle:
                bundle.writestr(link, b"target.txt")
            with self.assertRaises(JobOpsError) as linked_error:
                inventory_archive_payload(linked, prefix)
            self.assertEqual(linked_error.exception.code, "UPDATE_ARCHIVE_PAYLOAD_INVALID")


if __name__ == "__main__":
    unittest.main()
