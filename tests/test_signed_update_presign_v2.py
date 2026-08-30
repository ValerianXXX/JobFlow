from __future__ import annotations

import json
import hashlib
import base64
import io
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.update_manifest import (
    TRUSTED_RELEASE_KEY_ID,
    UPDATE_SIGNING_REQUEST_FORMAT,
    build_update_manifest,
    build_update_signing_request,
)
from jobops.util import canonical_json, sha256_bytes, sha256_file
import test_update_manifest_v2_producer as producer_fixture


PROJECT = Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
import test_signed_updates as signed_update_fixtures  # noqa: E402


class SignedUpdatePresignV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._runtime_fixture_directory = tempfile.TemporaryDirectory(
            prefix="jobflow-embedded-python-fixture-"
        )
        # The dynamic fixture is a truthful, self-consistent embeddable-style
        # runtime assembled from the interpreter that executes this test.  It
        # never relabels a host binary as the production-pinned CPython 3.13.15
        # artifact.  Production policy remains pinned and is asserted
        # separately; only this copied test repository is patched to the
        # actual host version, tag, names, bytes and digest.
        cls.runtime_version = ".".join(str(part) for part in sys.version_info[:3])
        cls.runtime_tag = f"python{sys.version_info.major}{sys.version_info.minor}"
        cls.runtime_artifact_name = (
            f"python-{cls.runtime_version}-embed-amd64.zip"
        )
        cls.runtime_artifact = (
            Path(cls._runtime_fixture_directory.name) / cls.runtime_artifact_name
        )
        cls.runtime_required_entries = cls._build_embedded_python_fixture(
            cls.runtime_artifact
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._runtime_fixture_directory.cleanup()

    @classmethod
    def _build_embedded_python_fixture(cls, destination: Path) -> list[str]:
        base = Path(sys.base_prefix)
        library = base / "Lib"
        dll_directory = base / "DLLs"
        required_source = [
            base / "python.exe",
            base / "python3.dll",
            base / f"{cls.runtime_tag}.dll",
            base / "vcruntime140.dll",
            base / "vcruntime140_1.dll",
            dll_directory / "_hashlib.pyd",
            dll_directory / "unicodedata.pyd",
            dll_directory / "select.pyd",
        ]
        missing = [path.name for path in required_source if not path.is_file()]
        if missing:
            raise AssertionError("embedded Python fixture inputs missing: " + ", ".join(missing))

        def entry(name: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
            value = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            value.compress_type = zipfile.ZIP_DEFLATED
            value.create_system = 0
            value.external_attr = 0
            return value, payload

        stdlib = io.BytesIO()
        with zipfile.ZipFile(stdlib, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for source in sorted(library.rglob("*.py")):
                relative = source.relative_to(library)
                if "site-packages" in {part.casefold() for part in relative.parts}:
                    continue
                value, payload = entry(relative.as_posix(), source.read_bytes())
                output.writestr(value, payload)

        payloads: dict[str, bytes] = {
            source.name: source.read_bytes() for source in required_source
        }
        for source in sorted(dll_directory.iterdir()):
            if source.is_file() and source.suffix.casefold() in {".pyd", ".dll"}:
                payloads.setdefault(source.name, source.read_bytes())
        payloads[f"{cls.runtime_tag}.zip"] = stdlib.getvalue()
        payloads[f"{cls.runtime_tag}._pth"] = (
            f"{cls.runtime_tag}.zip\n.\n".encode("utf-8")
        )
        payloads["LICENSE.txt"] = (base / "LICENSE.txt").read_bytes()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name in sorted(payloads, key=str.casefold):
                value, payload = entry(name, payloads[name])
                output.writestr(value, payload)
        return [
            "python.exe",
            "python3.dll",
            f"{cls.runtime_tag}.dll",
            f"{cls.runtime_tag}.zip",
            f"{cls.runtime_tag}._pth",
            "vcruntime140.dll",
            "vcruntime140_1.dll",
            "_hashlib.pyd",
            "unicodedata.pyd",
            "select.pyd",
        ]

    @staticmethod
    def _producer() -> producer_fixture.UpdateManifestV2ProducerTests:
        return producer_fixture.UpdateManifestV2ProducerTests(methodName="runTest")

    def _build_presign_fixture(
        self, root: Path
    ) -> tuple[Path, Path, Path, Path, Path, dict[str, object]]:
        producer = self._producer()
        archive, closure, runtime_evidence, publisher_evidence = producer._prepare(root)
        manifest = build_update_manifest(
            archive_path=archive,
            version=producer.version,
            commit=producer.commit,
            runtime_closure_path=closure,
            runtime_build_evidence_path=runtime_evidence,
            publisher_evidence_path=publisher_evidence,
            predecessor_minimum_version="0.4.1",
            minimum_updater_version="0.6.0",
            minimum_bootstrap_version="0.6.0",
            issued_at_utc=producer.issued_at,
            validation_time_utc=producer.issued_at,
            schema_dir=producer_fixture.SCHEMAS,
        )
        manifest_path = root / "JobFlow-update-manifest.presign.json"
        manifest_path.write_bytes(canonical_json(manifest))
        return (
            archive,
            closure,
            runtime_evidence,
            publisher_evidence,
            manifest_path,
            manifest,
        )

    def _prepare_powershell_fixture(
        self, root: Path
    ) -> tuple[Path, dict[str, Path], dict[str, str]]:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        fixture = root / "project"
        fixture.mkdir()
        tracked = helper._git(PROJECT, "ls-files").splitlines()
        for relative in tracked:
            source = PROJECT / relative
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for relative in (
            "config/python-support-policy.json",
            "config/release-toolchain.json",
            "config/windows-runtime-source.json",
            "config/windows-cp313-runtime.lock",
            "config/windows-cp313-build.lock",
            "schemas/runtime-closure.schema.json",
            "schemas/runtime-build-evidence-v1.schema.json",
            "schemas/publisher-evidence-v1.schema.json",
            "schemas/update-manifest-v2.schema.json",
            "src/jobops/publisher_attestation.py",
            "src/jobops/release_toolchain.py",
        ):
            source = PROJECT / relative
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        artifacts = root / "release-inputs"
        artifacts.mkdir()
        release_python = artifacts / self.runtime_artifact_name
        shutil.copy2(self.runtime_artifact, release_python)
        runtime_source_path = fixture / "config" / "windows-runtime-source.json"
        runtime_source = json.loads(runtime_source_path.read_text(encoding="utf-8"))
        test_bundle = b"jobflow-test-only-runtime-source-attestation\n"
        runtime_source["status"] = "TEST_ONLY_LOCAL_RUNTIME"
        runtime_source["python"].update(
            version=self.runtime_version,
            artifact_name=self.runtime_artifact_name,
            artifact_url=f"https://example.invalid/{self.runtime_artifact_name}",
            artifact_bytes=release_python.stat().st_size,
            artifact_sha256="sha256:" + hashlib.sha256(release_python.read_bytes()).hexdigest(),
            release_page_url=f"https://example.invalid/python-{self.runtime_version}/",
            sigstore_bundle_url=(
                f"https://example.invalid/{self.runtime_artifact_name}.sigstore"
            ),
            sigstore_bundle_bytes=len(test_bundle),
            sigstore_bundle_sha256="sha256:" + hashlib.sha256(test_bundle).hexdigest(),
            sigstore_certificate_identity="jobflow-test" + chr(64) + "example.invalid",
            sigstore_certificate_oidc_issuer="https://example.invalid",
        )
        runtime_source["builder"]["python_version"] = self.runtime_version
        runtime_source["isolation"]["python_pth"][0] = f"{self.runtime_tag}.zip"
        runtime_source_path.write_text(json.dumps(runtime_source, indent=2) + "\n", encoding="utf-8")
        # The copied fixture is explicitly test-only.  Production code and
        # production policy still require PINNED_OFFICIAL_SOURCE; only the
        # disposable fixture copies accept this marker so the dynamic test
        # never describes host-version bytes as an official PSF artifact.
        fixture_status_patches = {
            fixture / "scripts" / "build-signed-update-bundle.ps1": (
                '$runtimeSource.status -cne "PINNED_OFFICIAL_SOURCE"',
                '$runtimeSource.status -cne "TEST_ONLY_LOCAL_RUNTIME"',
            ),
        }
        for path, (production_text, fixture_text) in fixture_status_patches.items():
            value = path.read_text(encoding="utf-8-sig")
            if production_text not in value:
                raise AssertionError(f"fixture status gate missing: {path.name}")
            path.write_text(value.replace(production_text, fixture_text), encoding="utf-8")
        # Dynamic PowerShell tests exercise orchestration and byte/handle
        # binding with the real host interpreter, not production evidence
        # attestation.  This explicit test-only module emits a non-release-
        # eligible manifest shape sufficient for PowerShell identity checks.
        # Production Python producer semantics are covered by the dedicated
        # update-manifest suites against the pinned 3.13.15 policy.
        test_only_producer = fixture / "src" / "jobops" / "update_manifest.py"
        test_only_producer.write_text(
            """from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value)); stream.flush()

def emit(value):
    sys.stdout.buffer.write(canonical(value)); sys.stdout.buffer.flush()

def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for flag in ("archive", "version", "commit", "runtime-closure", "runtime-build-evidence", "publisher-evidence", "predecessor-minimum-version", "minimum-updater-version", "minimum-bootstrap-version", "issued-at-utc", "validation-time-utc", "schema-dir", "channel"):
        build.add_argument("--" + flag, required=True)
    build.add_argument("--legacy-v1-predecessors")
    build_output = build.add_mutually_exclusive_group(required=True)
    build_output.add_argument("--output")
    build_output.add_argument("--emit-canonical-stdout", action="store_true")
    request = commands.add_parser("presign-request")
    for flag in ("manifest", "runtime-closure", "runtime-build-evidence", "publisher-evidence", "schema-dir", "channel"):
        request.add_argument("--" + flag, required=True)
    request.add_argument("--legacy-v1-predecessors")
    request_output = request.add_mutually_exclusive_group(required=True)
    request_output.add_argument("--output")
    request_output.add_argument("--emit-canonical-stdout", action="store_true")
    inspect = commands.add_parser("inspect")
    for flag in ("manifest", "signature", "current-version"):
        inspect.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    if args.command == "build":
        archive = Path(args.archive)
        value = {
            "schema_version": 2,
            "format": "JOBFLOW_TEST_ONLY_PRESIGN_V2",
            "status": "TEST_ONLY_NOT_RELEASE_ELIGIBLE",
            "issued_at_utc": args.issued_at_utc,
            "release": {"version": args.version, "source_commit": args.commit},
            "asset": {"name": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive)},
            "external_actions": 0,
        }
        if args.emit_canonical_stdout:
            emit(value)
        else:
            write_new(args.output, value)
            print(json.dumps({"schema_version": 2, "status": "TEST_ONLY_MANIFEST_BUILT"}, separators=(",", ":")))
        return 0
    if args.command == "presign-request":
        value = {
            "schema_version": 1,
            "format": "JOBFLOW_UPDATE_SIGNING_REQUEST_V2",
            "status": "AWAITING_PROTECTED_SIGNATURE",
            "signature": {"algorithm": "RSA-PKCS1-v1_5-SHA256", "key_id": KEY_ID, "manifest_sha256": digest(args.manifest)},
            "evidence": {
                "runtime_closure_sha256": digest(args.runtime_closure),
                "runtime_build_evidence_sha256": digest(args.runtime_build_evidence),
                "publisher_evidence_sha256": digest(args.publisher_evidence),
            },
            "test_only": True,
            "external_actions": 0,
        }
        if args.emit_canonical_stdout:
            emit(value)
        else:
            write_new(args.output, value)
            print(json.dumps({"schema_version": 1, "status": "TEST_ONLY_SIGNING_REQUEST_BUILT"}, separators=(",", ":")))
        return 0
    print(json.dumps({"code": "JOBFLOW_PROTECTED_SIGNATURE_INVALID"}, separators=(",", ":")))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
""",
            encoding="utf-8",
        )
        toolchain_path = fixture / "config" / "release-toolchain.json"
        toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
        toolchain["python_execution_runtime"].update(
            python_tag=self.runtime_tag,
            required_entries=self.runtime_required_entries,
            active_pth_entries=[f"{self.runtime_tag}.zip", "."],
        )
        toolchain_path.write_text(json.dumps(toolchain, indent=2) + "\n", encoding="utf-8")
        helper._git(fixture, "init")
        helper._git(fixture, "config", "user.name", "JobFlow Test")
        helper._git(
            fixture,
            "config",
            "user.email",
            "jobflow-presign-test" + "@" + "example.invalid",
        )
        helper._git(fixture, "add", "--all")
        helper._git(fixture, "commit", "-m", "protected presign fixture")
        commit = helper._git(fixture, "rev-parse", "HEAD")
        version = str(
            tomllib.loads((fixture / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ]
        )
        dist = fixture / "dist"
        dist.mkdir()
        source_archive = dist / f"JobFlow-v{version}-{commit[:12]}-source.zip"
        source_archive.write_bytes(b"bounded source candidate identity")
        candidate = helper._valid_release_candidate()
        candidate.update(
            version=version,
            commit=commit,
            artifact_name=source_archive.name,
            artifact_sha256="sha256:" + hashlib.sha256(source_archive.read_bytes()).hexdigest(),
            artifact_bytes=source_archive.stat().st_size,
        )
        reports = fixture / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "release-candidate.json").write_text(
            json.dumps(candidate), encoding="utf-8"
        )

        producer = self._producer()
        producer.version = version
        producer.commit = commit
        now = datetime.now(timezone.utc).replace(microsecond=0)
        producer.issued_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        producer.runtime_evidence_issued_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        producer.runtime_evidence_expires_at = (now + timedelta(hours=8)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        producer.publisher_evidence_issued_at = (now - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        producer.publisher_evidence_expires_at = (now + timedelta(hours=3, minutes=55)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        closure_value = producer._closure()
        closure_value["application_version"] = version
        closure_value["source_commit"] = commit
        archive, closure, runtime_evidence, publisher_evidence = producer._prepare(
            artifacts, closure=closure_value
        )
        paths = {
            "archive": archive.resolve(),
            "closure": closure.resolve(),
            "runtime_evidence": runtime_evidence.resolve(),
            "publisher_evidence": publisher_evidence.resolve(),
            "release_python": release_python.resolve(),
            "presign": (dist / "JobFlow-update-manifest.presign.json").resolve(),
            "request": (dist / "JobFlow-update-signing-request.json").resolve(),
            "formal_manifest": (dist / "JobFlow-update-manifest.json").resolve(),
            "formal_signature": (dist / "JobFlow-update-manifest.sig.json").resolve(),
        }
        policy = {
            "predecessor": "0.4.1",
            "updater": version,
            "bootstrap": version,
        }
        return fixture, paths, policy

    @staticmethod
    def _run_handoff(
        fixture: Path,
        paths: dict[str, Path],
        policy: dict[str, str],
        *,
        stage: str,
        archive_override: str | None = None,
        closure_override: str | None = None,
        runtime_evidence_override: str | None = None,
        publisher_evidence_override: str | None = None,
        release_python_override: str | None = None,
        presign_override: Path | None = None,
        signature: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            str(signed_update_fixtures.WINDOWS_POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(fixture / "scripts" / "build-signed-update-bundle.ps1"),
            "-Stage",
            stage,
            "-CompleteRuntimeArchivePath",
            archive_override or str(paths["archive"]),
            "-RuntimeClosurePath",
            closure_override or str(paths["closure"]),
            "-RuntimeBuildEvidencePath",
            runtime_evidence_override or str(paths["runtime_evidence"]),
            "-PublisherEvidencePath",
            publisher_evidence_override or str(paths["publisher_evidence"]),
            "-ReleasePythonArtifactPath",
            release_python_override or str(paths["release_python"]),
            "-PredecessorMinimumVersion",
            policy["predecessor"],
            "-MinimumUpdaterVersion",
            policy["updater"],
            "-MinimumBootstrapVersion",
            policy["bootstrap"],
        ]
        if stage == "Finalize":
            arguments.extend(
                [
                    "-PresignManifestPath",
                    str(presign_override or paths["presign"]),
                    "-SigningRequestPath",
                    str(paths["request"]),
                    "-SignatureEnvelopePath",
                    str(signature),
                ]
            )
        return subprocess.run(
            arguments,
            cwd=fixture,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )

    @classmethod
    def _write_extractor_test_archive(cls, destination: Path, variant: str) -> None:
        def opaque_payload(name: str, size: int = 2048) -> bytes:
            chunks = [
                hashlib.sha512(f"{name}:{index}".encode("utf-8")).digest()
                for index in range((size + 63) // 64)
            ]
            return b"".join(chunks)[:size]

        payloads = {
            name: opaque_payload(name)
            for name in cls.runtime_required_entries
            if name != f"{cls.runtime_tag}._pth"
        }
        payloads[f"{cls.runtime_tag}._pth"] = (
            f"{cls.runtime_tag}.zip\n.\n".encode("utf-8")
        )
        payloads["LICENSE.txt"] = opaque_payload("LICENSE.txt")
        attributes: dict[str, tuple[int, int]] = {}
        if variant == "missing_select":
            payloads.pop("select.pyd")
        elif variant == "missing_vcruntime140_1":
            payloads.pop("vcruntime140_1.dll")
        elif variant == "import_site":
            payloads[f"{cls.runtime_tag}._pth"] += b"import site\n"
        elif variant == "extra_pth":
            payloads[f"{cls.runtime_tag}._pth"] += b"../app\n"
        elif variant == "absolute_pth":
            payloads[f"{cls.runtime_tag}._pth"] += b"C:\\private-runtime\n"
        elif variant == "duplicate_casefold":
            payloads["SELECT.PYD"] = opaque_payload("SELECT.PYD")
        elif variant == "traversal":
            payloads["../escape.dll"] = opaque_payload("traversal")
        elif variant == "rooted":
            payloads["/escape.dll"] = opaque_payload("rooted")
        elif variant == "unc":
            payloads[r"\\server\share\escape.dll"] = opaque_payload("unc")
        elif variant == "reparse_metadata":
            attributes["LICENSE.txt"] = (0, 0x400)
        elif variant == "symlink_metadata":
            attributes["LICENSE.txt"] = (3, (stat.S_IFLNK | 0o777) << 16)
        elif variant == "compression_bomb":
            payloads["LICENSE.txt"] = b"\x00" * (2 * 1024 * 1024)
        elif variant not in {"valid", "corrupt"}:
            raise AssertionError(f"unknown runtime archive variant: {variant}")
        if variant == "corrupt":
            destination.write_bytes(b"not-a-zip-file")
            return
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name in sorted(payloads, key=str.casefold):
                value = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
                value.compress_type = zipfile.ZIP_DEFLATED
                value.create_system, value.external_attr = attributes.get(name, (0, 0))
                output.writestr(value, payloads[name])

    @classmethod
    def _run_runtime_extractor_harness(
        cls, root: Path, variant: str
    ) -> subprocess.CompletedProcess[str]:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        project = root / "TEST_ONLY_RUNTIME_PROJECT"
        config = project / "config"
        staging = project / "dist"
        staging.mkdir(parents=True)
        config.mkdir(parents=True)
        artifact_directory = root / "TEST_ONLY_RUNTIME_INPUT"
        artifact_directory.mkdir()
        artifact = artifact_directory / cls.runtime_artifact_name
        cls._write_extractor_test_archive(artifact, variant)

        source = json.loads(
            (PROJECT / "config" / "windows-runtime-source.json").read_text(
                encoding="utf-8"
            )
        )
        source["status"] = "TEST_ONLY_LOCAL_RUNTIME"
        source["python"].update(
            version=cls.runtime_version,
            artifact_name=cls.runtime_artifact_name,
            artifact_url=f"https://example.invalid/{cls.runtime_artifact_name}",
            artifact_bytes=artifact.stat().st_size,
            artifact_sha256="sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest(),
            release_page_url=f"https://example.invalid/python-{cls.runtime_version}/",
            sigstore_bundle_url=f"https://example.invalid/{cls.runtime_artifact_name}.sigstore",
            sigstore_bundle_bytes=1,
            sigstore_bundle_sha256="sha256:" + hashlib.sha256(b"x").hexdigest(),
            sigstore_certificate_identity="jobflow-test" + chr(64) + "example.invalid",
            sigstore_certificate_oidc_issuer="https://example.invalid",
        )
        source["builder"]["python_version"] = cls.runtime_version
        source["isolation"]["python_pth"][0] = f"{cls.runtime_tag}.zip"
        (config / "windows-runtime-source.json").write_text(
            json.dumps(source, indent=2) + "\n", encoding="utf-8"
        )
        toolchain = json.loads(
            (PROJECT / "config" / "release-toolchain.json").read_text(encoding="utf-8")
        )
        toolchain["python_execution_runtime"].update(
            python_tag=cls.runtime_tag,
            required_entries=cls.runtime_required_entries,
            active_pth_entries=[f"{cls.runtime_tag}.zip", "."],
        )
        (config / "release-toolchain.json").write_text(
            json.dumps(toolchain, indent=2) + "\n", encoding="utf-8"
        )

        builder_text = helper._builder_text()
        no_reparse_block = builder_text[
            builder_text.index("function Assert-NoReparsePath") : builder_text.index(
                "function Find-GitApplication"
            )
        ]
        function_block = helper._builder_function_block().replace(
            '$runtimeSource.status -cne "PINNED_OFFICIAL_SOURCE"',
            '$runtimeSource.status -cne "TEST_ONLY_LOCAL_RUNTIME"',
        )
        harness = root / "runtime-extractor.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
            + no_reparse_block
            + function_block
            + "\n$inputLocks = New-Object Collections.Generic.List[object]\n"
            + "$stagingLocks = New-Object Collections.Generic.List[object]\n"
            + "$stagingPaths = New-Object Collections.Generic.List[string]\n"
            + "$stagingContext = $null\n"
            + "$failure = $null\n"
            + "try {\n"
            + "  Initialize-JobFlowReleaseFileIdentityApi\n"
            + "  $policyLock = Enter-InputFileLock $args[1] 262144 'JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID'\n"
            + "  $sourceLock = Enter-InputFileLock $args[2] 262144 'JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID'\n"
            + "  $archiveLock = Enter-InputFileLock $args[3] 134217728 'JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID'\n"
            + "  $inputLocks.Add($policyLock); $inputLocks.Add($sourceLock); $inputLocks.Add($archiveLock)\n"
            + "  $policy = Get-ReleaseToolchainPolicy $policyLock\n"
            + "  $runtimePolicy = Get-ReleasePythonRuntimePolicy $policy $sourceLock\n"
            + "  $stagingContext = New-ProtectedInputStagingRoot $args[4]\n"
            + "  [void](New-ProtectedStagingDirectory $stagingContext 'python-runtime')\n"
            + "  $result = Expand-LockedReleasePythonRuntime $archiveLock $runtimePolicy $stagingContext $stagingLocks $stagingPaths\n"
            + "  Assert-AllInputFileLocksUnchanged $inputLocks\n"
            + "  Assert-AllInputFileLocksUnchanged $result.locks\n"
            + "}\ncatch { $failure = [string]$_.Exception.Message }\n"
            + "finally {\n"
            + "  foreach($lock in $stagingLocks) { try { Remove-ProtectedStagedFileLock $lock } catch { if($null -eq $failure){$failure='JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED'} } }\n"
            + "  if($null -ne $stagingContext) { try { Remove-ProtectedInputStagingRoot $stagingContext } catch { if($null -eq $failure){$failure='JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED'} } }\n"
            + "  foreach($lock in $inputLocks) { if($null -ne $lock.stream){$lock.stream.Dispose()} }\n"
            + "}\n"
            + "if(-not [string]::IsNullOrWhiteSpace($failure)) { [Console]::Error.WriteLine($failure); exit 2 }\n"
            + "[Console]::Out.Write('JOBFLOW_TEST_RUNTIME_ACCEPTED')\n",
            encoding="utf-8-sig",
        )
        return subprocess.run(
            [
                str(signed_update_fixtures.WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(harness),
                str(project.resolve()),
                str((config / "release-toolchain.json").resolve()),
                str((config / "windows-runtime-source.json").resolve()),
                str(artifact.resolve()),
                str(staging.resolve()),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def assert_fixed_failure(
        self, completed: subprocess.CompletedProcess[str], expected: str | tuple[str, ...]
    ) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip().lstrip("\ufeff"), "")
        token = completed.stderr.strip().lstrip("\ufeff")
        if isinstance(expected, tuple):
            self.assertIn(token, expected)
        else:
            self.assertEqual(token, expected)
        self.assertNotIn(str(PROJECT), completed.stderr)

    @staticmethod
    def _assert_canonical_json_file(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AssertionError(f"{path.name} is not a JSON object")
        if canonical_json(value) != raw:
            raise AssertionError(f"{path.name} is not canonical JSON")
        return value

    def test_signing_request_is_canonical_pathless_and_bound_to_production_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-v2-") as raw:
            root = Path(raw)
            archive, closure, runtime_evidence, publisher_evidence, manifest_path, manifest = (
                self._build_presign_fixture(root)
            )
            request = build_update_signing_request(
                manifest_path=manifest_path,
                runtime_closure_path=closure,
                runtime_build_evidence_path=runtime_evidence,
                publisher_evidence_path=publisher_evidence,
                schema_dir=producer_fixture.SCHEMAS,
            )
            encoded = canonical_json(request)
            self.assertEqual(canonical_json(json.loads(encoded)), encoded)
            self.assertEqual(request["format"], UPDATE_SIGNING_REQUEST_FORMAT)
            self.assertEqual(request["status"], "AWAITING_PROTECTED_SIGNATURE")
            self.assertEqual(request["signature"]["key_id"], TRUSTED_RELEASE_KEY_ID)
            self.assertEqual(request["signature"]["manifest_schema_version"], 2)
            self.assertEqual(request["signature"]["manifest_sha256"], sha256_file(manifest_path))
            self.assertEqual(
                request["asset"],
                {key: manifest["asset"][key] for key in ("name", "bytes", "sha256")},
            )
            self.assertEqual(
                request["evidence"]["runtime_build_evidence_sha256"],
                sha256_file(runtime_evidence),
            )
            self.assertEqual(
                request["evidence"]["publisher_evidence_sha256"],
                sha256_file(publisher_evidence),
            )
            self.assertEqual(request["legacy_v1_predecessors"], {
                "included": False,
                "sha256": None,
                "count": 0,
            })
            self.assertEqual(request["external_actions"], 0)
            text = encoded.decode("utf-8")
            self.assertNotIn(str(root), text)
            self.assertNotIn(str(archive), text)
            self.assertNotIn("private", text.casefold())

    def test_signing_request_rejects_tampered_inputs_and_legacy_v1_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-v2-tamper-") as raw:
            root = Path(raw)
            _, closure, runtime_evidence, publisher_evidence, manifest_path, _ = (
                self._build_presign_fixture(root)
            )
            original = runtime_evidence.read_bytes()
            runtime_evidence.write_bytes(original[:-1] + b" " + original[-1:])
            with self.assertRaises(JobOpsError) as tampered:
                build_update_signing_request(
                    manifest_path=manifest_path,
                    runtime_closure_path=closure,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    schema_dir=producer_fixture.SCHEMAS,
                )
            self.assertEqual(tampered.exception.code, "UPDATE_PRESIGN_BINDING_MISMATCH")

            runtime_evidence.write_bytes(original)
            manifest_path.write_bytes(canonical_json({"schema_version": 1}))
            with self.assertRaises(JobOpsError) as legacy:
                build_update_signing_request(
                    manifest_path=manifest_path,
                    runtime_closure_path=closure,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    schema_dir=producer_fixture.SCHEMAS,
                )
            self.assertEqual(legacy.exception.code, "SCHEMA_VALIDATION_FAILED")

    def test_evidence_freshness_uses_explicit_validation_time_not_issued_at(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-v2-time-") as raw:
            root = Path(raw)
            producer = self._producer()
            archive, closure, runtime_evidence, publisher_evidence = producer._prepare(root)
            with self.assertRaises(JobOpsError) as stale:
                build_update_manifest(
                    archive_path=archive,
                    version=producer.version,
                    commit=producer.commit,
                    runtime_closure_path=closure,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    predecessor_minimum_version="0.4.1",
                    minimum_updater_version="0.6.0",
                    minimum_bootstrap_version="0.6.0",
                    issued_at_utc=producer.issued_at,
                    validation_time_utc="2026-08-30T12:00:00Z",
                    schema_dir=producer_fixture.SCHEMAS,
                )
            self.assertEqual(stale.exception.code, "PUBLISHER_EVIDENCE_STALE")

    def test_validation_time_is_required_and_manifest_cannot_be_future_dated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-v2-required-time-") as raw:
            root = Path(raw)
            producer = self._producer()
            archive, closure, runtime_evidence, publisher_evidence = producer._prepare(root)
            common = dict(
                archive_path=archive,
                version=producer.version,
                commit=producer.commit,
                runtime_closure_path=closure,
                runtime_build_evidence_path=runtime_evidence,
                publisher_evidence_path=publisher_evidence,
                predecessor_minimum_version="0.4.1",
                minimum_updater_version="0.6.0",
                minimum_bootstrap_version="0.6.0",
                issued_at_utc=producer.issued_at,
                schema_dir=producer_fixture.SCHEMAS,
            )
            with self.assertRaises(JobOpsError) as missing:
                build_update_manifest(**common)
            self.assertEqual(missing.exception.code, "UPDATE_MANIFEST_V2_INPUT_REQUIRED")
            with self.assertRaises(JobOpsError) as future:
                build_update_manifest(
                    **common,
                    validation_time_utc="2026-08-28T12:00:00Z",
                )
            self.assertEqual(future.exception.code, "UPDATE_MANIFEST_TIME_INVALID")

    def test_powershell_handoff_has_two_stages_and_no_signing_implementation(self) -> None:
        script = (PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('$Stage -cne "Prepare"', script)
        self.assertIn('$Stage -cne "Finalize"', script)
        self.assertIn("Get-TrustedUtcNow", script)
        self.assertIn('"--validation-time-utc", $currentTrustedTime', script)
        self.assertIn("Assert-ExplicitAbsoluteInputFile", script)
        self.assertIn("[IO.FileShare]::Read", script)
        self.assertIn("Get-OpenOutputFileIdentity", script)
        self.assertIn("[long]$identity.link_count -ne 1", script)
        self.assertIn("Assert-NoReparsePath", script)
        self.assertIn("JOBFLOW_RELEASE_INPUT_CHANGED", script)
        self.assertIn('relative = "pyproject.toml"', script)
        self.assertIn("Get-ProjectVersion $PyprojectLock", script)
        self.assertIn("Copy-LockedInputToProtectedStaging", script)
        self.assertIn("Test-SameOutputFileIdentity", script)
        self.assertIn("New-OutputCommitRecordPair", script)
        self.assertIn("JobFlow-update-manifest.presign.json", script)
        self.assertIn("JobFlow-update-signing-request.json", script)
        self.assertIn('throw "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED"', script)
        self.assertIn("Test-LockedBytesEqual", script)
        self.assertIn('"inspect",', script)
        self.assertIn("Write-OutputTransactionMarker", script)
        self.assertIn("Recover-PendingOutputTransaction", script)
        prepare_block = script[
            script.index('if ($Stage -ceq "Prepare")', script.index("function Invoke-ProtectedSigningHandoff")) :
            script.index('if (-not (Test-LockedBytesEqual $stagedPresignManifestLock', script.index("function Invoke-ProtectedSigningHandoff"))
        ]
        self.assertIn("Enter-OutputTransactionLock", prepare_block)
        self.assertIn("foreach ($record in @($requestRecord, $presignRecord))", prepare_block)
        self.assertIn("Restore-CommittedOutput $record", prepare_block)
        self.assertIn("JOBFLOW_RELEASE_PRESIGN_RECOVERY_REQUIRED", prepare_block)
        self.assertNotIn('"-Action", "Sign"', script)
        self.assertNotIn("release-signing.ps1", script)
        self.assertNotIn("private_key", script.casefold())
        self.assertLess(
            script.index('"inspect",'),
            script.index("Write-OutputTransactionMarker", script.index("function Invoke-ProtectedSigningHandoff")),
        )

    def test_prepare_executes_canonically_and_stops_at_external_signature_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-pwsh-") as raw:
            fixture, paths, policy = self._prepare_powershell_fixture(Path(raw))
            completed = self._run_handoff(fixture, paths, policy, stage="Prepare")
            self.assert_fixed_failure(completed, "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED")
            manifest = self._assert_canonical_json_file(paths["presign"])
            request = self._assert_canonical_json_file(paths["request"])
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(request["format"], UPDATE_SIGNING_REQUEST_FORMAT)
            self.assertEqual(request["status"], "AWAITING_PROTECTED_SIGNATURE")
            self.assertEqual(request["signature"]["key_id"], TRUSTED_RELEASE_KEY_ID)
            self.assertEqual(request["signature"]["manifest_sha256"], sha256_file(paths["presign"]))
            self.assertFalse(paths["formal_manifest"].exists())
            self.assertFalse(paths["formal_signature"].exists())
            encoded = paths["request"].read_text(encoding="utf-8")
            for private_path in paths.values():
                self.assertNotIn(str(private_path), encoded)
            self.assertNotIn(str(fixture), encoded)

    def test_release_python_archive_extractor_accepts_only_exact_bounded_flat_runtime(self) -> None:
        variants = (
            "corrupt",
            "missing_select",
            "missing_vcruntime140_1",
            "import_site",
            "extra_pth",
            "absolute_pth",
            "duplicate_casefold",
            "traversal",
            "rooted",
            "unc",
            "reparse_metadata",
            "symlink_metadata",
            "compression_bomb",
        )
        with tempfile.TemporaryDirectory(prefix="jobflow-runtime-extractor-") as raw:
            accepted_root = Path(raw) / "valid"
            accepted_root.mkdir()
            accepted = self._run_runtime_extractor_harness(accepted_root, "valid")
            self.assertEqual(accepted.returncode, 0, accepted.stderr + accepted.stdout)
            self.assertEqual(accepted.stdout.strip().lstrip("\ufeff"), "JOBFLOW_TEST_RUNTIME_ACCEPTED")
            self.assertEqual(accepted.stderr.strip().lstrip("\ufeff"), "")
            for variant in variants:
                with self.subTest(variant=variant):
                    case_root = Path(raw) / variant
                    case_root.mkdir()
                    rejected = self._run_runtime_extractor_harness(case_root, variant)
                    self.assert_fixed_failure(
                        rejected, "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
                    )
                    self.assertNotIn("TEST_ONLY_RUNTIME_PROJECT", rejected.stderr)

    def test_failed_formal_rollback_preserves_marker_and_backups_until_recovery(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        with tempfile.TemporaryDirectory(prefix="jobflow-formal-recovery-") as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            manifest = dist / "JobFlow-update-manifest.json"
            signature = dist / "JobFlow-update-manifest.sig.json"
            manifest.write_bytes(b"old-manifest")
            signature.write_bytes(b"old-signature")
            manifest_temporary = dist / "manifest.random.tmp"
            signature_temporary = dist / "signature.random.tmp"
            manifest_temporary.write_bytes(b"new-manifest")
            signature_temporary.write_bytes(b"new-signature")
            marker = dist / ".signed-update-output.transaction.json"

            inject = root / "inject-rollback-failure.ps1"
            inject.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + helper._builder_function_block()
                + "\n$manifest = New-OutputCommitRecord $args[1] $args[2]\n"
                + "$signature = New-OutputCommitRecord $args[3] $args[4]\n"
                + "$manifest.new_hash = 'sha256:' + (Get-FileSha256 $args[1])\n"
                + "$signature.new_hash = 'sha256:' + (Get-FileSha256 $args[3])\n"
                + "Write-OutputTransactionMarker $args[5] $manifest $signature\n"
                + "Commit-TemporaryOutput $signature\n"
                + "[IO.File]::WriteAllBytes($manifest.backup, [Text.Encoding]::UTF8.GetBytes('corrupt-backup'))\n"
                + "try { Invoke-FormalOutputRollbackOrRequireRecovery $args[5] ([IO.Path]::GetDirectoryName($args[2])); exit 91 } "
                + "catch { [Console]::Error.WriteLine([string]$_.Exception.Message); exit 2 }\n",
                encoding="utf-8-sig",
            )
            failed = helper._run_powershell(
                inject,
                root,
                manifest_temporary,
                manifest,
                signature_temporary,
                signature,
                marker,
            )
            self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
            self.assertEqual(
                failed.stderr.strip().lstrip("\ufeff"),
                "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED",
            )
            self.assertTrue(marker.is_file())
            backups = list(dist.glob("*.bak"))
            self.assertEqual(len(backups), 2)
            corrupted = [path for path in backups if path.read_bytes() == b"corrupt-backup"]
            self.assertEqual(len(corrupted), 1)
            self.assertEqual(manifest.read_bytes(), b"old-manifest")
            self.assertEqual(signature.read_bytes(), b"new-signature")

            corrupted[0].write_bytes(b"old-manifest")
            recover = root / "recover-formal-outputs.ps1"
            recover.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + helper._builder_function_block()
                + "\nRecover-PendingOutputTransaction $args[1] $args[2] -ForceRollback | Out-Null\n",
                encoding="utf-8-sig",
            )
            recovered = helper._run_powershell(recover, root, marker, dist)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual(manifest.read_bytes(), b"old-manifest")
            self.assertEqual(signature.read_bytes(), b"old-signature")
            self.assertFalse(marker.exists())
            self.assertEqual(list(dist.glob("*.bak")), [])

    def test_second_commit_record_failure_leaves_formal_pair_and_transaction_area_clean(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        with tempfile.TemporaryDirectory(prefix="jobflow-record-pair-failure-") as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            manifest = dist / "JobFlow-update-manifest.json"
            signature = dist / "JobFlow-update-manifest.sig.json"
            manifest.write_bytes(b"old-manifest")
            signature.write_bytes(b"old-signature")
            first_temporary = dist / "manifest.first.tmp"
            first_temporary.write_bytes(b"new-manifest")
            missing_second = dist / "signature.missing.tmp"
            harness = root / "record-pair-failure.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + helper._builder_function_block()
                + "\nInitialize-JobFlowReleaseFileIdentityApi\n"
                + "try { New-OutputCommitRecordPair $args[1] $args[2] $args[3] $args[4] | Out-Null; exit 91 }\n"
                + "catch { [Console]::Error.WriteLine([string]$_.Exception.Message); exit 2 }\n"
                + "finally { if ([IO.File]::Exists($args[1])) { Remove-TemporaryOutput $args[1] } }\n",
                encoding="utf-8-sig",
            )
            failed = helper._run_powershell(
                harness,
                root,
                first_temporary,
                manifest,
                missing_second,
                signature,
            )
            self.assert_fixed_failure(
                failed, "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED"
            )
            self.assertEqual(manifest.read_bytes(), b"old-manifest")
            self.assertEqual(signature.read_bytes(), b"old-signature")
            self.assertEqual(list(dist.glob("*.bak")), [])
            self.assertEqual(list(dist.glob("*.tmp")), [])
            self.assertEqual(list(dist.glob("*.transaction.json")), [])

    def test_commit_rejects_hash_to_move_path_swap_and_restores_old_output(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        with tempfile.TemporaryDirectory(prefix="jobflow-output-swap-") as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            destination = dist / "JobFlow-update-manifest.json"
            destination.write_bytes(b"old-manifest")
            temporary = dist / "manifest.commit.tmp"
            temporary.write_bytes(b"expected-manifest")
            replacement = dist / "replacement.private.tmp"
            replacement.write_bytes(b"attacker-manifest")
            aside = dist / "original.private.tmp"
            harness = root / "swap-between-hash-and-move.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + helper._builder_function_block()
                + "\nInitialize-JobFlowReleaseFileIdentityApi\n"
                + "$record = New-OutputCommitRecord $args[1] $args[2]\n"
                + "$record.new_hash = 'sha256:' + (Get-FileSha256 $args[1])\n"
                + "$script:swapReplacement = $args[3]\n$script:swapAside = $args[4]\n"
                + "function Move-OutputFileAtomic([string]$Source,[string]$Destination,[bool]$ReplaceExisting) {\n"
                + "  [IO.File]::Move($Source,$script:swapAside)\n"
                + "  [IO.File]::Move($script:swapReplacement,$Source)\n"
                + "  Move-OutputFileReplaceExisting $Source $Destination 'JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED'\n"
                + "}\n"
                + "try { Commit-TemporaryOutput $record; exit 91 }\n"
                + "catch { $code=[string]$_.Exception.Message; Restore-CommittedOutput $record; Remove-OutputCommitBackup $record; [Console]::Error.WriteLine($code); exit 2 }\n"
                + "finally { foreach($path in @($args[1],$args[3],$args[4])) { if([IO.File]::Exists($path)){[IO.File]::Delete($path)} } }\n",
                encoding="utf-8-sig",
            )
            rejected = helper._run_powershell(
                harness, root, temporary, destination, replacement, aside
            )
            self.assert_fixed_failure(
                rejected, "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_CHANGED"
            )
            self.assertEqual(destination.read_bytes(), b"old-manifest")
            self.assertEqual(list(dist.glob("*.bak")), [])
            self.assertEqual(list(dist.glob("*.tmp")), [])

    def test_policy_source_pyproject_and_generated_bytes_remain_locked(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        builder_text = helper._builder_text()
        no_reparse_block = builder_text[
            builder_text.index("function Assert-NoReparsePath") : builder_text.index(
                "function Get-ReleaseToolchainPolicy"
            )
        ]
        with tempfile.TemporaryDirectory(
            prefix="jobflow-held-inputs-",
            dir=signed_update_fixtures.SIGNED_UPDATE_TEST_ROOT,
        ) as raw:
            root = Path(raw)
            (root / "config").mkdir()
            (root / "src" / "jobops").mkdir(parents=True)
            (root / "dist").mkdir()
            held_paths = [
                root / "config" / "release-toolchain.json",
                root / "src" / "jobops" / "update_manifest.py",
                root / "pyproject.toml",
                root / "dist" / "JobFlow-update-manifest.presign.random.tmp",
            ]
            held_paths[0].write_text('{"schema_version":1,"tools":{}}', encoding="utf-8")
            held_paths[1].write_text("VALUE = 'trusted'\n", encoding="utf-8")
            held_paths[2].write_text('[project]\nversion = "0.6.0"\n', encoding="utf-8")
            held_paths[3].write_bytes(b'{"schema_version":2}')
            ready = root / "ready.txt"
            stop = root / "stop.txt"
            harness = root / "hold-inputs.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + no_reparse_block
                + helper._builder_function_block()
                + "\nInitialize-JobFlowReleaseFileIdentityApi\n"
                + "$locks = New-Object Collections.Generic.List[object]\n"
                + "try {\n"
                + "  for($index = 1; $index -le 4; $index++) {\n"
                + "    $inputPath = [string]$args[$index]\n"
                + "    $locks.Add((Enter-InputFileLock $inputPath 262144 'JOBFLOW_RELEASE_INPUT_INVALID'))\n"
                + "  }\n"
                + "  [IO.File]::WriteAllText($args[5],'ready')\n"
                + "  while(-not [IO.File]::Exists($args[6])) { Start-Sleep -Milliseconds 20 }\n"
                + "  foreach($lock in $locks) { Assert-InputFileLockUnchanged $lock }\n"
                + "}\nfinally { foreach($lock in $locks) { $lock.stream.Dispose() } }\n",
                encoding="utf-8-sig",
            )
            process = subprocess.Popen(
                [
                    str(signed_update_fixtures.WINDOWS_POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                    str(root),
                    *(str(path.resolve()) for path in held_paths),
                    str(ready),
                    str(stop),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                deadline = time.monotonic() + 15
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                if not ready.exists() and process.poll() is not None:
                    early_stdout, early_stderr = process.communicate(timeout=5)
                    self.fail("lock harness exited before ready: " + early_stderr + early_stdout)
                self.assertTrue(ready.exists(), "lock harness did not become ready before timeout")
                for index, path in enumerate(held_paths):
                    replacement = root / f"replacement-{index}.tmp"
                    replacement.write_bytes(b"replacement")
                    with self.subTest(path=path.name):
                        with self.assertRaises(PermissionError):
                            path.write_bytes(b"mutated")
                        with self.assertRaises(PermissionError):
                            os.replace(replacement, path)
                    if replacement.exists():
                        replacement.unlink()
            finally:
                stop.write_text("stop", encoding="utf-8")
                stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr + stdout)

    def test_cleanup_residue_is_pathless_and_never_masks_recovery_gate(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        with tempfile.TemporaryDirectory(prefix="jobflow-cleanup-outcome-") as raw:
            root = Path(raw)
            canary = "PRIVATE-CANARY-USER-PATH"
            harness = root / "cleanup-outcome.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + helper._builder_function_block()
                + "\ntry { Complete-ProtectedSigningOutcome "
                + "'JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED' $true '"
                + canary
                + "' | Out-Null }\n"
                + "catch { [Console]::Error.WriteLine([string]$_.Exception.Message); exit 2 }\n",
                encoding="utf-8-sig",
            )
            completed = subprocess.run(
                [
                    str(signed_update_fixtures.WINDOWS_POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                completed.stderr.strip().lstrip("\ufeff").splitlines(),
                [
                    "JOBFLOW_RELEASE_CLEANUP_RESIDUE_WARNING",
                    "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED",
                ],
            )
            self.assertNotIn(canary, completed.stdout + completed.stderr)
            self.assertNotIn(str(root), completed.stdout + completed.stderr)

    def test_tool_executable_is_held_before_path_based_authentication(self) -> None:
        helper = signed_update_fixtures.SignedUpdateTests(methodName="runTest")
        builder_text = helper._builder_text()
        no_reparse_block = builder_text[
            builder_text.index("function Assert-NoReparsePath") : builder_text.index(
                "function Get-ReleaseToolchainPolicy"
            )
        ]
        with tempfile.TemporaryDirectory(prefix="jobflow-tool-auth-lock-") as raw:
            root = Path(raw)
            executable = root / "python.exe"
            executable.write_bytes(b"trusted-tool-bytes")
            harness = root / "tool-auth-lock.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = (Resolve-Path -LiteralPath $args[0]).Path\n"
                + no_reparse_block
                + helper._builder_function_block()
                + "\nfunction Get-OpenOutputFileIdentity([IO.FileStream]$Stream,[string]$Code) { "
                + "return [pscustomobject]@{ link_count = 1 } }\n"
                + "function Get-AuthenticatedToolIdentity([string]$Tool,[string]$Path,[object]$Policy) {\n"
                + "  $script:replacementBlocked = $false\n"
                + "  try { [IO.File]::WriteAllBytes($Path,[Text.Encoding]::UTF8.GetBytes('replacement')) }\n"
                + "  catch { $script:replacementBlocked = $true }\n"
                + "  if(-not $script:replacementBlocked) { throw 'JOBFLOW_TEST_REPLACEMENT_WAS_NOT_BLOCKED' }\n"
                + "  return [pscustomobject]@{ tool=$Tool; path=[IO.Path]::GetFullPath($Path); "
                + "sha256=('sha256:' + (Get-FileSha256 $Path)); signer_subject='fixture'; signer_thumbprint='FIXTURE' }\n"
                + "}\n"
                + "$lock = $null\n"
                + "try {\n"
                + "  $lock = Enter-AuthenticatedToolLock 'python' $args[1] ([pscustomobject]@{})\n"
                + "  if(-not $script:replacementBlocked) { throw 'JOBFLOW_TEST_REPLACEMENT_WAS_NOT_BLOCKED' }\n"
                + "  if((Get-StreamSha256 $lock.stream) -cne (Get-FileSha256 $args[1])) { "
                + "throw 'JOBFLOW_TEST_HELD_BYTES_CHANGED' }\n"
                + "}\nfinally { if($null -ne $lock){$lock.stream.Dispose()} }\n",
                encoding="utf-8-sig",
            )
            completed = helper._run_powershell(harness, root, executable)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stdout.strip().lstrip("\ufeff"), "")
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(executable.read_bytes(), b"trusted-tool-bytes")

    def test_missing_project_root_is_a_single_fixed_pathless_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-root-canary-") as raw:
            root = Path(raw)
            canary = root / "PRIVATE-CANARY-WORKSPACE" / "scripts"
            canary.mkdir(parents=True)
            copied_builder = canary / "build-signed-update-bundle.ps1"
            shutil.copy2(PROJECT / "scripts" / "build-signed-update-bundle.ps1", copied_builder)
            completed = subprocess.run(
                [
                    str(signed_update_fixtures.WINDOWS_POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(copied_builder),
                ],
                cwd=canary,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assert_fixed_failure(completed, "JOBFLOW_PROJECT_ROOT_NOT_FOUND")
            combined = completed.stdout + completed.stderr
            self.assertNotIn(str(root), combined)
            self.assertNotIn("PRIVATE-CANARY-WORKSPACE", combined)

    def test_dynamic_handoff_rejects_ambiguous_inputs_and_untrusted_signatures_before_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-presign-negative-") as raw:
            root = Path(raw)
            fixture, paths, policy = self._prepare_powershell_fixture(root)
            prepared = self._run_handoff(fixture, paths, policy, stage="Prepare")
            self.assert_fixed_failure(prepared, "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED")
            original_manifest = paths["presign"].read_bytes()
            original_request = paths["request"].read_bytes()

            rerun = self._run_handoff(fixture, paths, policy, stage="Prepare")
            self.assert_fixed_failure(rerun, "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED")
            self._assert_canonical_json_file(paths["presign"])
            self._assert_canonical_json_file(paths["request"])
            dist = fixture / "dist"
            self.assertEqual(list(dist.glob("*.tmp")), [])
            self.assertEqual(list(dist.glob("*.bak")), [])
            self.assertEqual(list(dist.glob("*.transaction.json")), [])

            stable_manifest = paths["presign"].read_bytes()
            stable_request = paths["request"].read_bytes()
            sentinel = root / "outside-request-sentinel.json"
            sentinel.write_bytes(b"outside-sentinel")
            paths["request"].unlink()
            os.link(sentinel, paths["request"])
            destination_attack = self._run_handoff(fixture, paths, policy, stage="Prepare")
            self.assert_fixed_failure(
                destination_attack, "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
            )
            self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
            self.assertEqual(sentinel.read_bytes(), b"outside-sentinel")
            paths["request"].unlink()
            paths["request"].write_bytes(stable_request)

            negative_inputs: list[tuple[dict[str, str], str | tuple[str, ...]]] = [
                (
                    {"archive_override": "relative-complete-runtime.zip"},
                    "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID",
                ),
                (
                    {"archive_override": str(paths["archive"]) + ":alternate"},
                    "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID",
                ),
                (
                    {"archive_override": r"\\canary.invalid\release\private-user.zip"},
                    "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID",
                ),
                (
                    {"archive_override": r"\\?\C:\private-canary\release.zip"},
                    "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID",
                ),
                (
                    {"archive_override": r"\\.\C:\private-canary\release.zip"},
                    "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID",
                ),
                (
                    {"release_python_override": self.runtime_artifact_name},
                    "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID",
                ),
                (
                    {
                        "release_python_override": (
                            str(paths["release_python"]) + ":alternate"
                        )
                    },
                    "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID",
                ),
                (
                    {
                        "release_python_override": (
                            r"\\canary.invalid\release\python-embed.zip"
                        )
                    },
                    "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID",
                ),
                (
                    {
                        "release_python_override": (
                            r"\\?\C:\private-canary\python-embed.zip"
                        )
                    },
                    "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID",
                ),
                (
                    {
                        "release_python_override": (
                            r"\\.\C:\private-canary\python-embed.zip"
                        )
                    },
                    "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID",
                ),
            ]
            tampered = root / "publisher-tampered.json"
            tampered.write_bytes(paths["publisher_evidence"].read_bytes() + b" ")
            negative_inputs.append(
                (
                    {"publisher_evidence_override": str(tampered)},
                    "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED",
                )
            )
            runtime_tampered = root / "runtime-evidence-tampered.json"
            runtime_tampered.write_bytes(paths["runtime_evidence"].read_bytes() + b" ")
            negative_inputs.append(
                (
                    {"runtime_evidence_override": str(runtime_tampered)},
                    "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED",
                )
            )
            stale_value = json.loads(paths["publisher_evidence"].read_bytes())
            stale_now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
            stale_value["issued_at_utc"] = stale_now.strftime("%Y-%m-%dT%H:%M:%SZ")
            stale_value["expires_at_utc"] = (stale_now + timedelta(minutes=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            stale = root / "publisher-stale.json"
            stale.write_bytes(canonical_json(stale_value))
            negative_inputs.append(
                (
                    {"publisher_evidence_override": str(stale)},
                    "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED",
                )
            )
            for overrides, expected in negative_inputs:
                with self.subTest(overrides=overrides):
                    rejected = self._run_handoff(
                        fixture, paths, policy, stage="Prepare", **overrides
                    )
                    self.assert_fixed_failure(rejected, expected)
                    self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
                    self.assertEqual(paths["request"].read_bytes(), stable_request)
                    self.assertFalse(paths["formal_manifest"].exists())
                    self.assertFalse(paths["formal_signature"].exists())

            hardlink = root / "closure-hardlink.json"
            os.link(paths["closure"], hardlink)
            rejected_hardlink = self._run_handoff(
                fixture,
                paths,
                policy,
                stage="Prepare",
                closure_override=str(hardlink),
            )
            self.assert_fixed_failure(
                rejected_hardlink, "JOBFLOW_RELEASE_CLOSURE_INPUT_INVALID"
            )
            self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
            self.assertEqual(paths["request"].read_bytes(), stable_request)
            hardlink.unlink()

            runtime_hardlink_directory = root / "runtime-hardlink"
            runtime_hardlink_directory.mkdir()
            runtime_hardlink = runtime_hardlink_directory / self.runtime_artifact_name
            os.link(paths["release_python"], runtime_hardlink)
            rejected_runtime_hardlink = self._run_handoff(
                fixture,
                paths,
                policy,
                stage="Prepare",
                release_python_override=str(runtime_hardlink),
            )
            self.assert_fixed_failure(
                rejected_runtime_hardlink, "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
            )
            self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
            self.assertEqual(paths["request"].read_bytes(), stable_request)
            runtime_hardlink.unlink()

            runtime_junction_target = root / "runtime-junction-target"
            runtime_junction_target.mkdir()
            shutil.copy2(
                paths["release_python"],
                runtime_junction_target / self.runtime_artifact_name,
            )
            runtime_junction = root / "runtime-junction"
            junction_created = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/s",
                    "/c",
                    "mklink",
                    "/J",
                    str(runtime_junction),
                    str(runtime_junction_target),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(
                junction_created.returncode,
                0,
                junction_created.stdout + junction_created.stderr,
            )
            rejected_runtime_junction = self._run_handoff(
                fixture,
                paths,
                policy,
                stage="Prepare",
                release_python_override=str(
                    runtime_junction / self.runtime_artifact_name
                ),
            )
            self.assert_fixed_failure(
                rejected_runtime_junction, "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
            )
            self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
            self.assertEqual(paths["request"].read_bytes(), stable_request)
            runtime_junction.rmdir()

            reparse = root / "closure-reparse.json"
            try:
                os.symlink(paths["closure"], reparse)
            except OSError:
                reparse = None
            if reparse is not None:
                rejected_reparse = self._run_handoff(
                    fixture,
                    paths,
                    policy,
                    stage="Prepare",
                    closure_override=str(reparse),
                )
                self.assert_fixed_failure(
                    rejected_reparse, "JOBFLOW_RELEASE_CLOSURE_INPUT_INVALID"
                )
                self.assertEqual(paths["presign"].read_bytes(), stable_manifest)
                self.assertEqual(paths["request"].read_bytes(), stable_request)

            paths["formal_manifest"].write_bytes(b"formal-manifest-sentinel")
            paths["formal_signature"].write_bytes(b"formal-signature-sentinel")
            signature_bytes = base64.urlsafe_b64encode(b"\x01" * 256).rstrip(b"=").decode("ascii")
            wrong_key = root / "wrong-key-signature.json"
            wrong_key.write_bytes(
                canonical_json(
                    {
                        "schema_version": 1,
                        "algorithm": "RSA-PKCS1-v1_5-SHA256",
                        "key_id": "sha256:" + "0" * 64,
                        "signature_b64url": signature_bytes,
                    }
                )
            )
            development = root / "development-signature.json"
            development.write_bytes(
                canonical_json(
                    {
                        "schema_version": 1,
                        "scope": "development-fixture",
                        "algorithm": "RSA-PKCS1-v1_5-SHA256",
                        "key_id": TRUSTED_RELEASE_KEY_ID,
                        "signature_b64url": signature_bytes,
                    }
                )
            )
            legacy_presign = root / "legacy-v1-manifest.json"
            legacy_presign.write_bytes(canonical_json({"schema_version": 1}))
            for signature_path, presign_path, expected in (
                (wrong_key, paths["presign"], "JOBFLOW_PROTECTED_SIGNATURE_INVALID"),
                (development, paths["presign"], "JOBFLOW_PROTECTED_SIGNATURE_INVALID"),
                (wrong_key, legacy_presign, "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID"),
            ):
                rejected = self._run_handoff(
                    fixture,
                    paths,
                    policy,
                    stage="Finalize",
                    presign_override=presign_path,
                    signature=signature_path,
                )
                self.assert_fixed_failure(rejected, expected)
                self.assertEqual(paths["formal_manifest"].read_bytes(), b"formal-manifest-sentinel")
                self.assertEqual(paths["formal_signature"].read_bytes(), b"formal-signature-sentinel")


if __name__ == "__main__":
    unittest.main()
