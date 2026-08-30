from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT / "config"
SCRIPTS = PROJECT / "scripts"
SCHEMAS = PROJECT / "schemas"
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_text_sha256(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    if text.startswith("\ufeff") or "\r" in text.replace("\r\n", ""):
        raise AssertionError("lock document is not portable UTF-8 text")
    return "sha256:" + hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


class WindowsCompleteRuntimeContractTests(unittest.TestCase):
    def test_runtime_lock_files_are_exported_with_canonical_lf_bytes(self) -> None:
        attributes = (PROJECT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.lock text eol=lf", attributes.splitlines())

    def test_complete_runtime_normalizes_installed_record_files(self) -> None:
        script = (SCRIPTS / "build-windows-runtime-closure.ps1").read_text(encoding="utf-8")
        self.assertIn("function Normalize-InstalledRecords", script)
        self.assertIn("JOBFLOW_INSTALLED_RECORDS_NORMALIZED", script)
        self.assertIn("INSTALLED_RECORD_TARGET_MISSING", script)
        self.assertIn("writer = csv.writer(output, lineterminator=\"\\n\")", script)
        self.assertIn("Normalize-InstalledRecords $BuildRoot $AppRoot", script)
        build_tools = script[
            script.index("function Initialize-PinnedBuildTools") :
            script.index("function Get-SourceApplicationVersion")
        ]
        self.assertIn("Normalize-InstalledRecords $Root $target", build_tools)
        self.assertIn("direct_url.json", build_tools)
        self.assertIn("REQUESTED", build_tools)
        self.assertRegex(build_tools, r"\(Scripts\|bin\)")
        self.assertLess(
            build_tools.index("Normalize-InstalledRecords $Root $target"),
            build_tools.index("Get-RetainedTreeSnapshot $target"),
        )

    def test_python_role_policy_matches_metadata_ci_installer_and_complete_runtime(self) -> None:
        policy = _json(CONFIG / "python-support-policy.json")
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(policy["status"], "ROLE_SPLIT_INTENTIONAL")
        self.assertEqual(
            policy["source_package"],
            {
                "requires_python": ">=3.11,<3.14",
                "tested_minors": ["3.11", "3.12", "3.13"],
            },
        )
        self.assertEqual(
            policy["legacy_windows_source_installer"],
            {
                "allowed_minors": ["3.11", "3.12"],
                "distribution_policy": "PYTHON_SOFTWARE_FOUNDATION_SIGNED_SYSTEM_INSTALLATION",
            },
        )
        complete = policy["production_complete_windows_runtime"]
        source = _json(PROJECT / complete["source_policy"])
        runtime_lock = _json(PROJECT / complete["runtime_lock"])
        self.assertEqual(complete["exact_version"], source["python"]["version"])
        self.assertEqual(complete["python_tag"], runtime_lock["python_tag"])
        self.assertEqual(complete["runtime_tag"], "python313")
        installer = (SCRIPTS / "install-jobflow.ps1").read_text(encoding="utf-8")
        self.assertIn("64-bit Python 3.11 or 3.12 installation is required", installer)

        schema = _json(SCHEMAS / "python-support-policy.schema.json")
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])

    def test_project_metadata_and_ci_support_the_pinned_python_runtime(self) -> None:
        pyproject = tomllib.loads(
            (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertEqual(pyproject["requires-python"], ">=3.11,<3.14")
        self.assertIn("Programming Language :: Python :: 3.13", pyproject["classifiers"])
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', workflow)

    def test_official_cpython_source_is_exactly_pinned(self) -> None:
        source = _json(CONFIG / "windows-runtime-source.json")
        self.assertEqual(source["schema_version"], 1)
        self.assertEqual(source["status"], "PINNED_OFFICIAL_SOURCE")
        self.assertEqual(source["platform"], "windows-x64")
        self.assertEqual(source["architecture"], "AMD64")
        python = source["python"]
        self.assertEqual(
            python,
            {
                "version": "3.13.15",
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_url": "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip",
                "artifact_bytes": 11009825,
                "artifact_sha256": "sha256:d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",
                "release_page_url": "https://www.python.org/downloads/release/python-31315/",
                "sigstore_bundle_url": "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip.sigstore",
                "sigstore_bundle_bytes": 7164,
                "sigstore_bundle_sha256": "sha256:3e487c064a40d94a59476eb05e2d6225c325665590797e0a03cd33592b617137",
                "sigstore_transport_media_types": ["application/octet-stream"],
                "sigstore_media_type": "application/vnd.dev.sigstore.bundle.v0.3+json",
                "sigstore_certificate_identity": "thomas@python.org",
                "sigstore_certificate_oidc_issuer": "https://accounts.google.com",
            },
        )
        self.assertTrue(python["artifact_url"].startswith("https://www.python.org/"))
        self.assertTrue(python["sigstore_bundle_url"].startswith("https://www.python.org/"))
        self.assertEqual(
            source["attestation_policy"]["required_for_attested_status"],
            [
                "verified_psf_sigstore_evidence",
                "deterministic_double_build_match",
                "offline_smoke_passed",
                "outer_signing_readiness_evidence",
                "detached_signature_verified_with_pinned_trust",
            ],
        )

    def test_lock_documents_are_portably_hash_bound_and_runtime_has_no_packager(self) -> None:
        source = _json(CONFIG / "windows-runtime-source.json")
        builder = source["builder"]
        runtime_path = PROJECT / builder["runtime_lock"]
        build_path = PROJECT / builder["build_lock"]
        self.assertEqual(builder["runtime_lock_sha256"], _portable_text_sha256(runtime_path))
        self.assertEqual(builder["build_lock_sha256"], _portable_text_sha256(build_path))
        for path in (runtime_path, build_path):
            lf = path.read_bytes()
            crlf = lf.replace(b"\n", b"\r\n")
            self.assertEqual(
                hashlib.sha256(lf.replace(b"\r\n", b"\n")).digest(),
                hashlib.sha256(crlf.replace(b"\r\n", b"\n")).digest(),
            )

        runtime = _json(runtime_path)
        build = _json(build_path)
        self.assertEqual(
            (runtime["lock_type"], runtime["python_tag"], runtime["abi"], runtime["platform"], runtime["only_binary"]),
            ("runtime-wheelhouse", "cp313", "cp313-or-abi3", "win_amd64", True),
        )
        self.assertEqual(
            (build["lock_type"], build["python_tag"], build["platform"], build["only_binary"]),
            ("protected-builder-wheelhouse", "py3", "any", True),
        )
        runtime_names = {item["name"] for item in runtime["packages"]}
        self.assertEqual(
            runtime_names,
            {
                "cffi",
                "charset-normalizer",
                "cryptography",
                "lxml",
                "packaging",
                "pdfminer.six",
                "pdfplumber",
                "pillow",
                "pycparser",
                "pypdf",
                "pypdfium2",
                "python-docx",
                "typing-extensions",
            },
        )
        self.assertTrue(runtime_names.isdisjoint({"pip", "setuptools", "wheel"}))
        self.assertEqual({item["name"] for item in build["packages"]}, {"pip", "setuptools", "wheel"})
        for document in (runtime, build):
            filenames: set[str] = set()
            names: set[str] = set()
            for item in document["packages"]:
                self.assertGreater(item["size"], 0)
                self.assertRegex(item["sha256"], SHA256)
                self.assertTrue(item["filename"].endswith(".whl"))
                self.assertNotIn(item["filename"].casefold(), filenames)
                self.assertNotIn(item["name"].casefold(), names)
                filenames.add(item["filename"].casefold())
                names.add(item["name"].casefold())

    def test_runtime_lock_exact_versions_and_hashes_are_stable(self) -> None:
        runtime = _json(CONFIG / "windows-cp313-runtime.lock")
        actual = {
            item["name"]: (item["version"], item["filename"], item["size"], item["sha256"])
            for item in runtime["packages"]
        }
        self.assertEqual(
            actual,
            {
                "cffi": ("2.1.1", "cffi-2.1.1-cp313-cp313-win_amd64.whl", 185688, "sha256:1aa5645c30469b09530c4ebca77ebf8f17618293c58f8549cb1a543a50236e7d"),
                "charset-normalizer": ("3.5.1", "charset_normalizer-3.5.1-cp313-cp313-win_amd64.whl", 199295, "sha256:aea996a6aba25260827c9ea511d1addfde2da9eb686ac961838509086188b7e6"),
                "cryptography": ("50.0.0", "cryptography-50.0.0-cp311-abi3-win_amd64.whl", 3840395, "sha256:bd1c592e4d5974f0d08d4888e432157adba757c66da0246918e43677fafa2d30"),
                "lxml": ("6.1.1", "lxml-6.1.1-cp313-cp313-win_amd64.whl", 3995869, "sha256:a10bd2fd62e8ce916ececb342f348f190724a098c1faa056fdfb2a22ad5e8660"),
                "packaging": ("26.3", "packaging-26.3-py3-none-any.whl", 129956, "sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"),
                "pdfminer.six": ("20251230", "pdfminer_six-20251230-py3-none-any.whl", 6591909, "sha256:9ff2e3466a7dfc6de6fd779478850b6b7c2d9e9405aa2a5869376a822771f485"),
                "pdfplumber": ("0.11.9", "pdfplumber-0.11.9-py3-none-any.whl", 60045, "sha256:33ec5580959ba524e9100138746e090879504c42955df1b8a997604dd326c443"),
                "pillow": ("11.3.0", "pillow-11.3.0-cp313-cp313-win_amd64.whl", 6978450, "sha256:0bce5c4fd0921f99d2e858dc4d4d64193407e1b99478bc5cacecba2311abde51"),
                "pycparser": ("3.0", "pycparser-3.0-py3-none-any.whl", 48172, "sha256:b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992"),
                "pypdf": ("5.9.0", "pypdf-5.9.0-py3-none-any.whl", 313193, "sha256:be10a4c54202f46d9daceaa8788be07aa8cd5ea8c25c529c50dd509206382c35"),
                "pypdfium2": ("5.13.0", "pypdfium2-5.13.0-py3-none-win_amd64.whl", 3885553, "sha256:47dcca2a8d507b5fd24f94c3c9d48fb379430f097bc20f01beff6c963ffbcedb"),
                "python-docx": ("1.2.0", "python_docx-1.2.0-py3-none-any.whl", 252987, "sha256:3fd478f3250fbbbfd3b94fe1e985955737c145627498896a8a6bf81f4baf66c7"),
                "typing-extensions": ("4.16.0", "typing_extensions-4.16.0-py3-none-any.whl", 45571, "sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
            },
        )

    def test_builder_is_offline_double_build_and_cannot_self_attest(self) -> None:
        script = (SCRIPTS / "build-windows-runtime-closure.ps1").read_text(encoding="utf-8")
        lowered = script.casefold()
        for forbidden in (
            "invoke-webrequest",
            "start-bitstransfer",
            "downloadstring",
            "httpclient",
            "psfsigstoreevidencepath",
            "outersigningevidencepath",
            "requireattested",
        ):
            self.assertNotIn(forbidden, lowered)
        for required in (
            '"--require-hashes"',
            '"--only-binary=:all:"',
            '"--no-index"',
            '"--no-deps"',
            '"--no-compile"',
            '$status = "BUILT_UNATTESTED"',
            'public_release_blocked = ($status -cne "ATTESTED")',
            'attestation_required = "POST_BUILD_DETACHED_SIGNATURE_WITH_PINNED_TRUST"',
            '"pass-a"',
            '"pass-b"',
            "JOBFLOW_RUNTIME_DETERMINISTIC_REBUILD_MISMATCH",
        ):
            self.assertIn(required, script)
        self.assertNotRegex(script, r'\$status\s*=\s*if\s*\(')
        self.assertLess(script.index("Invoke-IndependentVerifier $closure $false"), script.index("Invoke-OfflineSmoke $closure"))
        self.assertIn('Write-Utf8NoBom $pth "python313.zip`n.`n../app`n"', script)
        self.assertIn("external_actions=0", script)
        self.assertIn("1980, 1, 1, 0, 0, 0", script)
        self.assertIn("Write-Utf8NoBom $path (ConvertTo-CanonicalJson $manifest)", script)
        self.assertNotIn('Write-Utf8NoBom $path (($manifest | ConvertTo-Json', script)
        self.assertIn('digest_format = "JOBFLOW_BUILDER_TOOLCHAIN_DIGEST_V1"', script)
        self.assertIn('digest_format = "JOBFLOW_PROTECTED_BUILDER_EVIDENCE_DIGEST_V1"', script)

    def test_builder_emits_and_revalidates_canonical_runtime_build_evidence(self) -> None:
        script = (SCRIPTS / "build-windows-runtime-closure.ps1").read_text(
            encoding="utf-8"
        )
        producer = script[
            script.index("function New-RuntimeBuildEvidence") :
            script.index("function Remove-SafeBuildRoot")
        ]
        for required in (
            'format = "JOBFLOW_RUNTIME_BUILD_EVIDENCE_V1"',
            'evidence_kind = "SANITIZED_BUILD_OBSERVATION"',
            'structural_status = "BUILT_UNATTESTED"',
            "runtime_wheel_lock_sha256",
            "build_wheel_lock_sha256",
            "application_wheel_provenance",
            "pass_a_archive_sha256",
            "pass_b_archive_sha256",
            'status = "PASS"',
            'result_token = "JOBFLOW_OFFLINE_SMOKE_OK"',
            "sigstore_verified = $false",
            "outer_signature_ready = $false",
            "external_actions = 0",
        ):
            self.assertIn(required, producer)
        self.assertIn(
            '$evidenceOutputName = "JobFlow-runtime-build-evidence.json"', script
        )
        committed = script.index(
            "Invoke-IndependentArchiveVerifier $outputInput $false"
        )
        evidence_written = script.index(
            "Write-Utf8NoBom $evidenceOutputPath", committed
        )
        evidence_verified = script.index(
            "Invoke-RuntimeBuildEvidenceVerifier $first.closure $evidenceInput",
            evidence_written,
        )
        succeeded = script.index("$script:RuntimeBuildSucceeded = $true")
        self.assertLess(committed, evidence_written)
        self.assertLess(evidence_written, evidence_verified)
        self.assertLess(evidence_verified, succeeded)
        self.assertIn(
            "validate_runtime_build_evidence(Path(sys.argv[1]).read_bytes())",
            script,
        )
        self.assertIn(
            '"JOBFLOW_RUNTIME_BUILD_EVIDENCE_VERIFY_DETAIL=" + $failureCode',
            script,
        )
        self.assertIn("RUNTIME_BUILD_EVIDENCE_UNKNOWN", script)
        self.assertNotIn("WriteLine($errorText)", script)
        self.assertIn("$start.CreateNoWindow = $true", script)
        self.assertIn(
            "runtime_build_evidence_sha256 = [string]$evidenceInput.sha256",
            script,
        )

    def test_failed_runtime_evidence_commit_removes_only_this_build_outputs(self) -> None:
        script = (SCRIPTS / "build-windows-runtime-closure.ps1").read_text(
            encoding="utf-8"
        )
        cleanup = script[script.rindex("finally {") :]
        self.assertIn("Close-RetainedRuntimeInputs", cleanup)
        self.assertIn("if (-not $script:RuntimeBuildSucceeded", cleanup)
        self.assertIn("$script:CreatedRuntimeOutputs", cleanup)
        self.assertIn("JOBFLOW_RUNTIME_OUTPUT_CLEANUP_REFUSED", cleanup)
        self.assertLess(
            cleanup.index("Close-RetainedRuntimeInputs"),
            cleanup.index("if (-not $script:RuntimeBuildSucceeded"),
        )
        self.assertNotIn("Remove-Item -LiteralPath $script:RuntimeOutputRoot", cleanup)

        verifier = (SCRIPTS / "verify-windows-runtime-closure.ps1").read_text(encoding="utf-8")
        self.assertIn('throw "JOBFLOW_RUNTIME_ATTESTATION_UNVERIFIABLE"', verifier)
        self.assertNotIn('$Manifest.status -notin @("BUILT_UNATTESTED", "ATTESTED")', verifier)

    def test_builder_canonicalizer_matches_update_manifest_exact_byte_contract(self) -> None:
        script = (SCRIPTS / "build-windows-runtime-closure.ps1").read_text(encoding="utf-8")
        match = re.search(
            r"function ConvertTo-CanonicalJson\(\[object\]\$Value\).*?\$program = @'\n(?P<program>.*?)\n'@",
            script,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        value = {"z": 3, "a": {"second": False, "first": [2, 1]}, "m": "text"}
        transport = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(transport).decode("ascii")
        completed = subprocess.run(
            [sys.executable, "-I", "-c", match.group("program")],  # type: ignore[union-attr]
            input=encoded,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        expected = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, expected)
        self.assertFalse(completed.stdout.endswith("\n"))

    def test_stock_windows_powershell_parses_both_scripts(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows PowerShell is Windows-specific")
        powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        command = (
            "$ErrorActionPreference='Stop';"
            "$all=@();"
            "foreach($p in @('scripts\\build-windows-runtime-closure.ps1','scripts\\verify-windows-runtime-closure.ps1')){"
            "$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $p),[ref]$t,[ref]$e)|Out-Null;"
            "if($e.Count -ne 0){$all += $e}};"
            "if($all.Count -ne 0){$all|ForEach-Object{$_.Message};exit 1};"
            "'PS51_PARSE_OK'"
        )
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=PROJECT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("PS51_PARSE_OK", completed.stdout)

    def test_no_ps7_only_process_or_hash_apis_remain(self) -> None:
        for path in (
            SCRIPTS / "build-windows-runtime-closure.ps1",
            SCRIPTS / "verify-windows-runtime-closure.ps1",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("[Convert]::ToHexString", text)
            self.assertNotIn("::HashData", text)
            self.assertNotIn(".ArgumentList", text)
            self.assertNotIn(".Environment.Clear", text)

    def test_update_manifest_v2_is_fail_closed_for_complete_runtime(self) -> None:
        schema = _json(SCHEMAS / "update-manifest-v2.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        closure = schema["properties"]["runtime_closure"]["properties"]
        self.assertEqual(closure["structural_status"]["const"], "BUILT_UNATTESTED")
        attestation = schema["properties"]["publisher_attestation"]["properties"]
        self.assertEqual(attestation["status"]["const"], "ATTESTED")
        self.assertEqual(
            attestation["format"]["const"], "JOBFLOW_PUBLISHER_ATTESTATION_V2"
        )
        self.assertEqual(
            attestation["evidence_format"]["const"], "JOBFLOW_PUBLISHER_EVIDENCE_V1"
        )
        policy = schema["properties"]["policy"]["properties"]
        self.assertIs(policy["final_submit_user_only"]["const"], True)
        self.assertIs(policy["automatic_retry_submission_unknown"]["const"], False)
        self.assertEqual(policy["external_actions_during_update"]["const"], 0)
        predecessor = schema["properties"]["predecessor"]["properties"]
        self.assertIs(predecessor["disallow_downgrade"]["const"], True)
        self.assertIs(predecessor["require_current_runtime_closure"]["const"], True)


if __name__ == "__main__":
    unittest.main()
