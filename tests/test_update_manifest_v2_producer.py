from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import jobops.update_manifest as update_manifest_module
from jobops.errors import JobOpsError
from jobops.publisher_attestation import signer_readiness_challenge_sha256
from jobops.update_manifest import (
    TRUSTED_RELEASE_KEY_ID,
    build_update_manifest,
    inspect_signed_update,
    validate_update_channel,
    validate_update_manifest,
)
from jobops.util import canonical_json, load_json, sha256_bytes, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
SCHEMAS = PROJECT / "schemas"


def digest(character: str) -> str:
    return "sha256:" + character * 64


def provenance(*, wheel: str, build_lock: str, commit: str = "a" * 40) -> dict[str, object]:
    return {
        "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
        "source_commit": commit,
        "source_git_tree_oid": "b" * 40,
        "source_build_tree_sha256": digest("c"),
        "source_archive_sha256": digest("d"),
        "build_lock_sha256": build_lock,
        "build_recipe_sha256": digest("e"),
        "pass_a_wheel_sha256": wheel,
        "pass_b_wheel_sha256": wheel,
        "reproducible": True,
    }


def legacy_identity(version: str, character: str) -> dict[str, object]:
    source_sha256 = character * 64
    return {
        "schema_version": 1,
        "version": version,
        "source_sha256": source_sha256,
        "version_directory": f"v{version}-{source_sha256[:12]}",
    }


class UpdateManifestV2ProducerTests(unittest.TestCase):
    version = "0.6.0"
    commit = "a" * 40
    issued_at = "2026-08-28T12:01:00Z"
    runtime_evidence_issued_at = "2026-08-28T11:50:00Z"
    runtime_evidence_expires_at = "2026-08-29T11:50:00Z"
    publisher_evidence_issued_at = "2026-08-28T12:00:00Z"
    publisher_evidence_expires_at = "2026-08-28T16:00:00Z"

    def test_cli_build_can_emit_only_the_canonical_document_to_stdout(self) -> None:
        result = {"schema_version": 2, "product": "JobFlow", "channel": "stable"}
        arguments = [
            "jobops.update_manifest",
            "build",
            "--archive", "archive.zip",
            "--version", "0.6.0",
            "--commit", "a" * 40,
            "--runtime-closure", "closure.json",
            "--runtime-build-evidence", "runtime-evidence.json",
            "--publisher-evidence", "publisher-evidence.json",
            "--predecessor-minimum-version", "0.4.1",
            "--minimum-updater-version", "0.4.1",
            "--minimum-bootstrap-version", "0.4.1",
            "--issued-at-utc", self.issued_at,
            "--validation-time-utc", self.publisher_evidence_issued_at,
            "--channel", "channel.json",
            "--schema-dir", "sealed-schemas",
            "--emit-canonical-stdout",
        ]
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)
        with (
            mock.patch.object(sys, "argv", arguments),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(update_manifest_module, "build_update_manifest", return_value=result) as build,
        ):
            self.assertEqual(update_manifest_module.main(), 0)
        self.assertEqual(raw.getvalue(), canonical_json(result))
        self.assertNotIn(b"UPDATE_MANIFEST_BUILT", raw.getvalue())
        self.assertEqual(build.call_args.kwargs["channel_path"], Path("channel.json"))
        self.assertEqual(build.call_args.kwargs["schema_dir"], Path("sealed-schemas"))

    def test_cli_presign_can_emit_only_the_canonical_document_to_stdout(self) -> None:
        result = {
            "schema_version": 1,
            "format": "JOBFLOW_UPDATE_SIGNING_REQUEST_V2",
            "status": "AWAITING_PROTECTED_SIGNATURE",
        }
        arguments = [
            "jobops.update_manifest",
            "presign-request",
            "--manifest", "manifest.json",
            "--runtime-closure", "closure.json",
            "--runtime-build-evidence", "runtime-evidence.json",
            "--publisher-evidence", "publisher-evidence.json",
            "--channel", "channel.json",
            "--schema-dir", "sealed-schemas",
            "--emit-canonical-stdout",
        ]
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)
        with (
            mock.patch.object(sys, "argv", arguments),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(
                update_manifest_module,
                "build_update_signing_request",
                return_value=result,
            ) as build,
        ):
            self.assertEqual(update_manifest_module.main(), 0)
        self.assertEqual(raw.getvalue(), canonical_json(result))
        self.assertNotIn(b"UPDATE_SIGNING_REQUEST_BUILT", raw.getvalue())
        self.assertEqual(build.call_args.kwargs["channel_path"], Path("channel.json"))
        self.assertEqual(build.call_args.kwargs["schema_dir"], Path("sealed-schemas"))

    def test_cli_requires_exactly_one_build_output_mode(self) -> None:
        common = [
            "jobops.update_manifest", "build",
            "--archive", "archive.zip", "--version", "0.6.0", "--commit", "a" * 40,
            "--runtime-closure", "closure.json",
            "--runtime-build-evidence", "runtime-evidence.json",
            "--publisher-evidence", "publisher-evidence.json",
            "--predecessor-minimum-version", "0.4.1",
            "--minimum-updater-version", "0.4.1",
            "--minimum-bootstrap-version", "0.4.1",
            "--issued-at-utc", self.issued_at,
            "--validation-time-utc", self.publisher_evidence_issued_at,
        ]
        for suffix in ([], ["--output", "out.json", "--emit-canonical-stdout"]):
            with self.subTest(suffix=suffix):
                with (
                    mock.patch.object(sys, "argv", common + suffix),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    update_manifest_module.main()
                self.assertEqual(raised.exception.code, 2)

    @staticmethod
    def _runtime_payloads() -> dict[str, bytes]:
        return {
            ".jobops-root": b"JobFlow runtime\n",
            "app/jobops/__init__.py": b"x=1\n",
            "app/jobops/cli.py": b"def main(): return 0\n",
            "app/jobops/runtime_health.py": b"def check(): return True\n",
            "config/windows-cp313-build.lock": (PROJECT / "config" / "windows-cp313-build.lock").read_bytes(),
            "config/windows-cp313-runtime.lock": (PROJECT / "config" / "windows-cp313-runtime.lock").read_bytes(),
            "runtime/python.exe": b"python",
            "runtime/python313.dll": b"python dll",
            "runtime/python313._pth": b"python313.zip\n.\n../app\n",
            "runtime/python313.zip": b"stdlib!",
        }

    @staticmethod
    def _closure() -> dict[str, object]:
        source = load_json(PROJECT / "config" / "windows-runtime-source.json")
        runtime_lock = load_json(PROJECT / "config" / "windows-cp313-runtime.lock")
        payloads = UpdateManifestV2ProducerTests._runtime_payloads()
        files = [
            {"path": path, "size": len(body), "sha256": sha256_bytes(body)}
            for path, body in sorted(payloads.items(), key=lambda item: item[0].upper())
        ]
        return {
            "schema_version": 1,
            "status": "BUILT_UNATTESTED",
            "artifact_type": "complete-runtime",
            "platform": "windows-x64",
            "application_version": "0.6.0",
            "source_commit": "a" * 40,
            "python": {
                "version": "3.13.15",
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_sha256": source["python"]["artifact_sha256"],
                "sigstore_identity": "https://www.python.org/",
                "sigstore_verified": False,
            },
            "build_inputs": {
                "wheel_lock_sha256": source["builder"]["runtime_lock_sha256"],
                "wheelhouse_tree_sha256": digest("7"),
                "application_wheel_sha256": digest("8"),
                "application_wheel_provenance": provenance(
                    wheel=digest("8"), build_lock=source["builder"]["build_lock_sha256"]
                ),
                "builder_toolchain_sha256": digest("9"),
                "wheels": [
                    {
                        "name": f"jobflow_fixture_{index}",
                        "version": "1.0.0",
                        "tag": "py3-none-any",
                        "size": 100 + index,
                        "sha256": "sha256:" + f"{index + 1:064x}",
                    }
                    for index, _ in enumerate(runtime_lock["packages"])
                ],
            },
            "layout": {
                "python": "runtime/python.exe",
                "python_pth": "runtime/python313._pth",
                "application_root": "app",
                "module": "jobops.cli",
            },
            "file_count": len(files),
            "total_bytes": sum(int(item["size"]) for item in files),
            "tree_sha256": sha256_bytes(canonical_json(files)),
            "files": files,
            "offline_smoke_tests": {
                "import_passed": True,
                "schema_passed": True,
                "external_actions": 0,
            },
            "protected_builder": {
                "evidence_sha256": digest("c"),
                "deterministic_rebuild_match": True,
                "outer_signature_ready": False,
            },
        }

    def _prepare(
        self, root: Path, *, closure: dict[str, object] | None = None
    ) -> tuple[Path, Path, Path, Path]:
        value = closure or self._closure()
        closure_path = root / "runtime-closure.json"
        closure_path.write_bytes(canonical_json(value))
        archive = root / f"JobFlow-v{self.version}-windows-x64-complete.zip"
        prefix = f"JobFlow-v{self.version}-windows-x64/"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            payloads = {
                "runtime-closure.json": closure_path.read_bytes(),
                **self._runtime_payloads(),
            }
            for relative, payload in payloads.items():
                name = prefix + relative
                entry = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 0
                entry.external_attr = 0
                output.writestr(entry, payload)
        source = load_json(PROJECT / "config" / "windows-runtime-source.json")
        runtime_lock = load_json(PROJECT / "config" / "windows-cp313-runtime.lock")
        build_lock = load_json(PROJECT / "config" / "windows-cp313-build.lock")
        archive_sha256 = sha256_file(archive)
        archive_bytes = archive.stat().st_size
        build_inputs = {
            "runtime_wheel_lock_sha256": source["builder"]["runtime_lock_sha256"],
            "build_wheel_lock_sha256": source["builder"]["build_lock_sha256"],
            "wheelhouse_tree_sha256": value["build_inputs"]["wheelhouse_tree_sha256"],
            "application_wheel_sha256": value["build_inputs"]["application_wheel_sha256"],
            "application_wheel_provenance": value["build_inputs"]["application_wheel_provenance"],
            "builder_toolchain_sha256": value["build_inputs"]["builder_toolchain_sha256"],
            "runtime_wheel_count": len(runtime_lock["packages"]),
            "build_wheel_count": len(build_lock["packages"]),
        }
        runtime_build = {
            "schema_version": 1,
            "format": "JOBFLOW_RUNTIME_BUILD_EVIDENCE_V1",
            "evidence_kind": "SANITIZED_BUILD_OBSERVATION",
            "issued_at_utc": self.runtime_evidence_issued_at,
            "expires_at_utc": self.runtime_evidence_expires_at,
            "application_version": self.version,
            "source_commit": self.commit,
            "platform": "windows-x64",
            "structural_status": "BUILT_UNATTESTED",
            "archive": {
                "name": archive.name,
                "bytes": archive_bytes,
                "sha256": archive_sha256,
                "archive_prefix": prefix,
            },
            "runtime_closure": {
                "manifest_sha256": sha256_file(closure_path),
                "tree_sha256": value["tree_sha256"],
                "source_payload_sha256": archive_sha256,
                "file_count": value["file_count"],
                "total_bytes": value["total_bytes"],
                "python_version": value["python"]["version"],
                "platform": value["platform"],
            },
            "python_source": {
                "version": source["python"]["version"],
                "artifact_name": source["python"]["artifact_name"],
                "artifact_bytes": source["python"]["artifact_bytes"],
                "artifact_sha256": source["python"]["artifact_sha256"],
                "sigstore_bundle_name": source["python"]["artifact_name"] + ".sigstore",
                "sigstore_bundle_bytes": source["python"]["sigstore_bundle_bytes"],
                "sigstore_bundle_sha256": source["python"]["sigstore_bundle_sha256"],
            },
            "build_inputs": build_inputs,
            "build_inputs_sha256": sha256_bytes(canonical_json(build_inputs)),
            "deterministic_build": {
                "pass_a_archive_sha256": archive_sha256,
                "pass_b_archive_sha256": archive_sha256,
                "pass_a_tree_sha256": value["tree_sha256"],
                "pass_b_tree_sha256": value["tree_sha256"],
                "match": True,
            },
            "independent_verification": {
                "status": "PASS",
                "verifier_sha256": digest("d"),
                "archive_sha256": archive_sha256,
                "closure_manifest_sha256": sha256_file(closure_path),
                "tree_sha256": value["tree_sha256"],
            },
            "offline_smoke": {
                "status": "PASS",
                "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
                "archive_sha256": archive_sha256,
                "closure_manifest_sha256": sha256_file(closure_path),
                "tree_sha256": value["tree_sha256"],
                "external_actions": 0,
            },
            "closure_self_claims": {
                "sigstore_verified": False,
                "outer_signature_ready": False,
            },
            "external_actions": 0,
        }
        runtime_build_path = root / "runtime-build-evidence.json"
        runtime_build_path.write_bytes(canonical_json(runtime_build))
        runtime_build_sha256 = sha256_file(runtime_build_path)
        provider_policy_sha256 = digest("e")
        challenge_sha256 = signer_readiness_challenge_sha256(
            runtime_build_evidence_sha256=runtime_build_sha256,
            archive_sha256=archive_sha256,
            source_commit=self.commit,
            provider_policy_sha256=provider_policy_sha256,
            release_key_id=TRUSTED_RELEASE_KEY_ID,
        )
        publisher_evidence = {
            "schema_version": 1,
            "format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
            "evidence_kind": "SANITIZED_PUBLISHER_OBSERVATION",
            "status": "READY_FOR_PROTECTED_SIGNING",
            "issued_at_utc": self.publisher_evidence_issued_at,
            "expires_at_utc": self.publisher_evidence_expires_at,
            "runtime_build_evidence_sha256": runtime_build_sha256,
            "release": {
                "version": self.version,
                "source_commit": self.commit,
                "platform": "windows-x64",
                "archive_name": archive.name,
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_sha256,
                "archive_prefix": prefix,
            },
            "runtime_closure": {
                "manifest_sha256": sha256_file(closure_path),
                "tree_sha256": value["tree_sha256"],
                "source_payload_sha256": archive_sha256,
                "file_count": value["file_count"],
                "total_bytes": value["total_bytes"],
                "structural_status": value["status"],
            },
            "build_inputs_sha256": runtime_build["build_inputs_sha256"],
            "psf_sigstore": {
                "status": "VERIFIED",
                "python_artifact_sha256": source["python"]["artifact_sha256"],
                "sigstore_bundle_sha256": source["python"]["sigstore_bundle_sha256"],
                "trusted_root_sha256": digest("f"),
                "verifier_sha256": digest("1"),
                "verifier_version": "3.7.2",
                "certificate_identity": source["python"]["sigstore_certificate_identity"],
                "certificate_oidc_issuer": source["python"]["sigstore_certificate_oidc_issuer"],
                "signature_verified": True,
                "transparency_log_inclusion_verified": True,
                "offline_verification": True,
                "network_access": 0,
            },
            "deterministic_rebuild": {
                "verified": True,
                "pass_a_archive_sha256": archive_sha256,
                "pass_b_archive_sha256": archive_sha256,
                "pass_a_tree_sha256": value["tree_sha256"],
                "pass_b_tree_sha256": value["tree_sha256"],
            },
            "independent_verification": {
                "status": "PASS",
                "runtime_build_evidence_sha256": runtime_build_sha256,
                "verifier_sha256": digest("d"),
                "archive_sha256": archive_sha256,
                "closure_manifest_sha256": sha256_file(closure_path),
                "tree_sha256": value["tree_sha256"],
            },
            "offline_smoke": {
                "status": "PASS",
                "runtime_build_evidence_sha256": runtime_build_sha256,
                "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
                "external_actions": 0,
            },
            "outer_signing_readiness": {
                "status": "VERIFIED",
                "release_key_id": TRUSTED_RELEASE_KEY_ID,
                "provider_policy_sha256": provider_policy_sha256,
                "challenge_format": "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1",
                "challenge_sha256": challenge_sha256,
                "challenge_signature_sha256": digest("2"),
                "verified_with_pinned_trust": True,
                "secret_material_in_evidence": False,
            },
            "release_safety": {
                "closure_relabelled": False,
                "closure_bytes_modified": False,
                "secret_material_in_evidence": False,
                "external_actions": 0,
            },
        }
        publisher_evidence_path = root / "publisher-evidence.json"
        publisher_evidence_path.write_bytes(canonical_json(publisher_evidence))
        return archive, closure_path, runtime_build_path, publisher_evidence_path

    def _build(self, root: Path, **overrides: object) -> dict[str, object]:
        archive, closure, runtime_build_evidence, publisher_evidence = self._prepare(root)
        arguments: dict[str, object] = {
            "archive_path": archive,
            "version": self.version,
            "commit": self.commit,
            "runtime_closure_path": closure,
            "runtime_build_evidence_path": runtime_build_evidence,
            "publisher_evidence_path": publisher_evidence,
            "predecessor_minimum_version": "0.4.1",
            "minimum_updater_version": "0.6.0",
            "minimum_bootstrap_version": "0.6.0",
            "issued_at_utc": self.issued_at,
            "validation_time_utc": self.issued_at,
            "schema_dir": SCHEMAS,
        }
        arguments.update(overrides)
        return build_update_manifest(**arguments)  # type: ignore[arg-type]

    @staticmethod
    def _write_legacy_input(root: Path, predecessors: list[dict[str, object]]) -> Path:
        path = root / "legacy-v1-predecessors.json"
        path.write_bytes(
            canonical_json(
                {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": predecessors,
                }
            )
        )
        return path

    def test_builds_deterministic_complete_v2_manifest_without_self_attesting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-producer-") as raw:
            root = Path(raw)
            first = self._build(root)
            second = self._build(root)
            self.assertEqual(canonical_json(first), canonical_json(second))
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["runtime_closure"]["structural_status"], "BUILT_UNATTESTED")
            self.assertEqual(first["publisher_attestation"]["status"], "ATTESTED")
            self.assertEqual(
                first["publisher_attestation"]["release_key_id"], TRUSTED_RELEASE_KEY_ID
            )
            self.assertEqual(first["asset"]["sha256"], first["runtime_closure"]["source_payload_sha256"])
            self.assertNotIn("legacy_v1_predecessors", first)

    def test_builds_canonical_exact_legacy_v1_authorization_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-legacy-auth-") as raw:
            root = Path(raw)
            input_path = self._write_legacy_input(
                root,
                [
                    legacy_identity("0.5.0", "2"),
                    legacy_identity("0.4.1", "1"),
                ],
            )
            first = self._build(root, legacy_v1_predecessors_path=input_path)
            second = self._build(root, legacy_v1_predecessors_path=input_path)
            expected = [
                legacy_identity("0.4.1", "1"),
                legacy_identity("0.5.0", "2"),
            ]
            self.assertEqual(first["legacy_v1_predecessors"], expected)
            self.assertEqual(canonical_json(first), canonical_json(second))

    def test_legacy_v1_authorization_input_rejects_coercion_extras_and_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-legacy-invalid-") as raw:
            root = Path(raw)
            base_item = legacy_identity("0.4.1", "1")
            shared_prefix = "a" * 12
            cases: dict[str, dict[str, object]] = {
                "wrapper_extra": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [base_item],
                    "allow_local": True,
                },
                "wrapper_schema_string": {
                    "schema_version": "1",
                    "product": "JobFlow",
                    "predecessors": [base_item],
                },
                "empty": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [],
                },
                "item_extra": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [{**base_item, "allow_any_hash": True}],
                },
                "item_schema_float": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [{**base_item, "schema_version": 1.0}],
                },
                "prefixed_hash": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [{**base_item, "source_sha256": digest("1")}],
                },
                "wrong_directory": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [{**base_item, "version_directory": "v0.4.1-deadbeefdead"}],
                },
                "duplicate": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [base_item, copy.deepcopy(base_item)],
                },
                "directory_collision": {
                    "schema_version": 1,
                    "product": "JobFlow",
                    "predecessors": [
                        {
                            "schema_version": 1,
                            "version": "0.4.1",
                            "source_sha256": shared_prefix + "b" * 52,
                            "version_directory": "v0.4.1-" + shared_prefix,
                        },
                        {
                            "schema_version": 1,
                            "version": "0.4.1",
                            "source_sha256": shared_prefix + "c" * 52,
                            "version_directory": "v0.4.1-" + shared_prefix,
                        },
                    ],
                },
            }
            input_path = root / "legacy-v1-predecessors.json"
            for name, payload in cases.items():
                input_path.write_bytes(canonical_json(payload))
                with self.subTest(case=name), self.assertRaises(JobOpsError) as caught:
                    self._build(root, legacy_v1_predecessors_path=input_path)
                self.assertEqual(caught.exception.code, "UPDATE_LEGACY_V1_PREDECESSORS_INVALID")

    def test_legacy_v1_authorization_input_rejects_duplicate_json_properties(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-legacy-duplicate-json-") as raw:
            root = Path(raw)
            input_path = self._write_legacy_input(root, [legacy_identity("0.4.1", "1")])
            duplicate = input_path.read_bytes()[:-1] + b',"product":"JobFlow"}'
            input_path.write_bytes(duplicate)
            with self.assertRaises(JobOpsError) as caught:
                self._build(root, legacy_v1_predecessors_path=input_path)
            self.assertEqual(caught.exception.code, "UPDATE_LEGACY_V1_PREDECESSORS_INVALID")

    def test_missing_evidence_chain_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-no-attestation-") as raw:
            root = Path(raw)
            archive, closure, runtime_evidence, publisher_evidence = self._prepare(root)
            for missing, expected_code in (
                ("runtime", "UPDATE_RUNTIME_BUILD_EVIDENCE_REQUIRED"),
                ("publisher", "UPDATE_PUBLISHER_EVIDENCE_REQUIRED"),
            ):
                with self.subTest(missing=missing), self.assertRaises(JobOpsError) as caught:
                    build_update_manifest(
                        archive_path=archive,
                        version=self.version,
                        commit=self.commit,
                        runtime_closure_path=closure,
                        runtime_build_evidence_path=(None if missing == "runtime" else runtime_evidence),
                        publisher_evidence_path=(None if missing == "publisher" else publisher_evidence),
                        predecessor_minimum_version="0.4.1",
                        minimum_updater_version="0.6.0",
                        minimum_bootstrap_version="0.6.0",
                        issued_at_utc=self.issued_at,
                        validation_time_utc=self.issued_at,
                        schema_dir=SCHEMAS,
                    )
                self.assertEqual(caught.exception.code, expected_code)

    def test_runtime_evidence_cannot_project_different_wheel_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-provenance-mismatch-") as raw:
            root = Path(raw)
            archive, closure, runtime_evidence, publisher_evidence = self._prepare(root)
            runtime_value = json.loads(runtime_evidence.read_text(encoding="utf-8"))
            runtime_value["build_inputs"]["application_wheel_provenance"][
                "source_archive_sha256"
            ] = digest("f")
            runtime_value["build_inputs_sha256"] = sha256_bytes(
                canonical_json(runtime_value["build_inputs"])
            )
            runtime_evidence.write_bytes(canonical_json(runtime_value))
            runtime_evidence_sha256 = sha256_file(runtime_evidence)

            publisher_value = json.loads(publisher_evidence.read_text(encoding="utf-8"))
            publisher_value["runtime_build_evidence_sha256"] = runtime_evidence_sha256
            publisher_value["build_inputs_sha256"] = runtime_value["build_inputs_sha256"]
            publisher_value["independent_verification"][
                "runtime_build_evidence_sha256"
            ] = runtime_evidence_sha256
            publisher_value["offline_smoke"][
                "runtime_build_evidence_sha256"
            ] = runtime_evidence_sha256
            signer = publisher_value["outer_signing_readiness"]
            signer["challenge_sha256"] = signer_readiness_challenge_sha256(
                runtime_build_evidence_sha256=runtime_evidence_sha256,
                archive_sha256=publisher_value["release"]["archive_sha256"],
                source_commit=self.commit,
                provider_policy_sha256=signer["provider_policy_sha256"],
                release_key_id=signer["release_key_id"],
            )
            publisher_evidence.write_bytes(canonical_json(publisher_value))

            with self.assertRaises(JobOpsError) as caught:
                build_update_manifest(
                    archive_path=archive,
                    version=self.version,
                    commit=self.commit,
                    runtime_closure_path=closure,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    predecessor_minimum_version="0.4.1",
                    minimum_updater_version="0.6.0",
                    minimum_bootstrap_version="0.6.0",
                    issued_at_utc=self.issued_at,
                    validation_time_utc=self.issued_at,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(
                caught.exception.code, "UPDATE_APPLICATION_WHEEL_PROVENANCE_MISMATCH"
            )

    def test_structural_status_cannot_be_relabelled_as_attested(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-self-attested-") as raw:
            root = Path(raw)
            closure = self._closure()
            closure["status"] = "ATTESTED"
            archive, closure_path, runtime_evidence, publisher_evidence = self._prepare(
                root, closure=closure
            )
            with self.assertRaises(JobOpsError):
                build_update_manifest(
                    archive_path=archive,
                    version=self.version,
                    commit=self.commit,
                    runtime_closure_path=closure_path,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    predecessor_minimum_version="0.4.1",
                    minimum_updater_version="0.6.0",
                    minimum_bootstrap_version="0.6.0",
                    issued_at_utc=self.issued_at,
                    validation_time_utc=self.issued_at,
                    schema_dir=SCHEMAS,
                )

    def test_publisher_evidence_tamper_unknown_key_and_duplicate_key_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-attestation-tamper-") as raw:
            root = Path(raw)
            archive, closure, runtime_evidence, publisher_evidence_path = self._prepare(root)
            original = json.loads(publisher_evidence_path.read_text(encoding="utf-8"))
            mutations = []
            wrong_payload = copy.deepcopy(original)
            wrong_payload["release"]["archive_sha256"] = digest("f")
            mutations.append(("wrong_binding", canonical_json(wrong_payload)))
            extra = copy.deepcopy(original)
            extra["local_self_attested"] = True
            mutations.append(("unknown_key", canonical_json(extra)))
            duplicate = canonical_json(original)[:-1] + b',"status":"ATTESTED"}'
            mutations.append(("duplicate_key", duplicate))
            for name, payload in mutations:
                publisher_evidence_path.write_bytes(payload)
                with self.subTest(name=name), self.assertRaises(JobOpsError):
                    build_update_manifest(
                        archive_path=archive,
                        version=self.version,
                        commit=self.commit,
                        runtime_closure_path=closure,
                        runtime_build_evidence_path=runtime_evidence,
                        publisher_evidence_path=publisher_evidence_path,
                        predecessor_minimum_version="0.4.1",
                        minimum_updater_version="0.6.0",
                        minimum_bootstrap_version="0.6.0",
                        issued_at_utc=self.issued_at,
                        validation_time_utc=self.issued_at,
                        schema_dir=SCHEMAS,
                    )
            publisher_evidence_path.write_bytes(canonical_json(original))

    def test_closure_path_with_windows_illegal_character_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-bad-path-") as raw:
            root = Path(raw)
            closure = self._closure()
            closure["files"][0]["path"] = "app/bad?.py"
            archive, closure_path, runtime_evidence, publisher_evidence = self._prepare(
                root, closure=closure
            )
            with self.assertRaises(JobOpsError) as caught:
                build_update_manifest(
                    archive_path=archive,
                    version=self.version,
                    commit=self.commit,
                    runtime_closure_path=closure_path,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    predecessor_minimum_version="0.4.1",
                    minimum_updater_version="0.6.0",
                    minimum_bootstrap_version="0.6.0",
                    issued_at_utc=self.issued_at,
                    validation_time_utc=self.issued_at,
                    schema_dir=SCHEMAS,
                )
            self.assertIn(
                caught.exception.code,
                {"UPDATE_ARCHIVE_PAYLOAD_INVALID", "RUNTIME_CLOSURE_PATH_INVALID", "SCHEMA_SEMANTIC_CONFLICT"},
            )

    def test_archive_change_during_build_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-changing-archive-") as raw:
            root = Path(raw)
            archive, closure, runtime_evidence, publisher_evidence = self._prepare(root)
            original_loader = update_manifest_module._read_bounded_bytes

            def load_then_change(
                path: Path, *, maximum: int, code: str
            ) -> bytes:
                result = original_loader(path, maximum=maximum, code=code)
                if path == publisher_evidence:
                    with archive.open("ab") as output:
                        output.write(b"changed-after-publisher-evidence")
                return result

            with mock.patch.object(
                update_manifest_module,
                "_read_bounded_bytes",
                side_effect=load_then_change,
            ), self.assertRaises(JobOpsError) as caught:
                build_update_manifest(
                    archive_path=archive,
                    version=self.version,
                    commit=self.commit,
                    runtime_closure_path=closure,
                    runtime_build_evidence_path=runtime_evidence,
                    publisher_evidence_path=publisher_evidence,
                    predecessor_minimum_version="0.4.1",
                    minimum_updater_version="0.6.0",
                    minimum_bootstrap_version="0.6.0",
                    issued_at_utc=self.issued_at,
                    validation_time_utc=self.issued_at,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "UPDATE_ARCHIVE_CHANGED")

    def test_archived_payload_must_exactly_match_runtime_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-closure-payload-") as raw:
            root = Path(raw)
            archive, _, _, _ = self._prepare(root)
            prefix = f"JobFlow-v{self.version}-windows-x64/"
            with zipfile.ZipFile(archive, "r") as source:
                original = {
                    info.filename.removeprefix(prefix): source.read(info)
                    for info in source.infolist()
                    if not info.is_dir()
                }
            mutations = {
                "missing": {
                    key: value
                    for key, value in original.items()
                    if key != "runtime/python313.zip"
                },
                "extra": {**original, "scripts/unlisted.ps1": b"Write-Output bad\n"},
                "content": {**original, "runtime/python.exe": b"changed"},
            }
            for name, payloads in mutations.items():
                mutant = root / f"{name}.zip"
                with zipfile.ZipFile(mutant, "w", compression=zipfile.ZIP_DEFLATED) as output:
                    for relative, payload in payloads.items():
                        entry = zipfile.ZipInfo(
                            prefix + relative, date_time=(2024, 1, 1, 0, 0, 0)
                        )
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        entry.create_system = 0
                        entry.external_attr = 0
                        output.writestr(entry, payload)
                inventory, _, closure, _ = (
                    update_manifest_module._archive_runtime_closure_record(
                        mutant, prefix
                    )
                )
                with self.subTest(name=name), self.assertRaises(JobOpsError) as caught:
                    update_manifest_module._assert_archive_matches_runtime_closure(
                        inventory, closure
                    )
                self.assertEqual(
                    caught.exception.code,
                    "UPDATE_RUNTIME_CLOSURE_INVENTORY_MISMATCH",
                )

    def test_inspector_verifies_exact_bytes_before_rejecting_duplicate_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-v2-inspect-order-") as raw:
            root = Path(raw)
            manifest = self._build(root)
            manifest_path = root / "manifest.json"
            duplicate_bytes = canonical_json(manifest)[:-1] + b',"product":"JobFlow"}'
            manifest_path.write_bytes(duplicate_bytes)
            channel_path = root / "channel.json"
            channel_path.write_bytes(
                canonical_json(
                    json.loads(
                        (PROJECT / "config" / "update-channel.json").read_text(
                            encoding="utf-8"
                        )
                    )
                )
            )
            signature_path = root / "manifest.sig.json"
            signature_path.write_bytes(
                canonical_json(
                    {
                        "schema_version": 1,
                        "algorithm": "RSA-PKCS1-v1_5-SHA256",
                        "key_id": TRUSTED_RELEASE_KEY_ID,
                        "signature_b64url": base64.urlsafe_b64encode(b"s" * 256)
                        .rstrip(b"=")
                        .decode("ascii"),
                    }
                )
            )
            with mock.patch.object(
                update_manifest_module, "_verify_rsa_pkcs1_sha256"
            ) as verifier, self.assertRaises(JobOpsError) as caught:
                inspect_signed_update(
                    manifest_path,
                    signature_path,
                    current_version="0.4.1",
                    channel_path=channel_path,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "UPDATE_MANIFEST_INVALID")
            verifier.assert_called_once()
            self.assertEqual(verifier.call_args.args[0], duplicate_bytes)

    def test_default_validator_rejects_legacy_v1(self) -> None:
        channel_value = json.loads((PROJECT / "config" / "update-channel.json").read_text(encoding="utf-8"))
        channel = validate_update_channel(channel_value)
        legacy = {
            "schema_version": 1,
            "product": "JobFlow",
            "channel": "stable",
            "repository": "ValerianXXX/JobFlow",
        }
        with self.assertRaises(JobOpsError):
            validate_update_manifest(legacy, channel, schema_dir=SCHEMAS)


if __name__ == "__main__":
    unittest.main()
