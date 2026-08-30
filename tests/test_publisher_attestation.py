from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.publisher_attestation import (
    EvidenceDocument,
    signer_readiness_challenge_sha256,
    validate_clean_windows_acceptance,
    validate_publisher_evidence,
    validate_runtime_build_evidence,
)
from jobops.util import canonical_json, load_json, sha256_bytes


PROJECT = Path(__file__).resolve().parents[1]
SCHEMAS = PROJECT / "schemas"
NOW = datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc)


def digest(character: str) -> str:
    return "sha256:" + character * 64


def provenance(*, wheel: str, commit: str, build_lock: str) -> dict[str, object]:
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


def _pinned_values() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    source = load_json(PROJECT / "config" / "windows-runtime-source.json")
    runtime_lock = load_json(PROJECT / "config" / "windows-cp313-runtime.lock")
    build_lock = load_json(PROJECT / "config" / "windows-cp313-build.lock")
    return source, runtime_lock, build_lock


def valid_runtime_build() -> dict[str, object]:
    source, runtime_lock, build_lock = _pinned_values()
    python = source["python"]
    builder = source["builder"]
    archive_sha256 = digest("1")
    manifest_sha256 = digest("2")
    tree_sha256 = digest("3")
    application_wheel_sha256 = digest("5")
    build_inputs = {
        "runtime_wheel_lock_sha256": builder["runtime_lock_sha256"],
        "build_wheel_lock_sha256": builder["build_lock_sha256"],
        "wheelhouse_tree_sha256": digest("4"),
        "application_wheel_sha256": application_wheel_sha256,
        "application_wheel_provenance": provenance(
            wheel=application_wheel_sha256,
            commit="a" * 40,
            build_lock=builder["build_lock_sha256"],
        ),
        "builder_toolchain_sha256": digest("6"),
        "runtime_wheel_count": len(runtime_lock["packages"]),
        "build_wheel_count": len(build_lock["packages"]),
    }
    return {
        "schema_version": 1,
        "format": "JOBFLOW_RUNTIME_BUILD_EVIDENCE_V1",
        "evidence_kind": "SANITIZED_BUILD_OBSERVATION",
        "issued_at_utc": "2026-08-28T12:00:00Z",
        "expires_at_utc": "2026-08-29T12:00:00Z",
        "application_version": "0.6.0",
        "source_commit": "a" * 40,
        "platform": "windows-x64",
        "structural_status": "BUILT_UNATTESTED",
        "archive": {
            "name": "JobFlow-v0.6.0-windows-x64-complete.zip",
            "bytes": 123456,
            "sha256": archive_sha256,
            "archive_prefix": "JobFlow-v0.6.0-windows-x64/",
        },
        "runtime_closure": {
            "manifest_sha256": manifest_sha256,
            "tree_sha256": tree_sha256,
            "source_payload_sha256": archive_sha256,
            "file_count": 412,
            "total_bytes": 9876543,
            "python_version": "3.13.15",
            "platform": "windows-x64",
        },
        "python_source": {
            "version": python["version"],
            "artifact_name": python["artifact_name"],
            "artifact_bytes": python["artifact_bytes"],
            "artifact_sha256": python["artifact_sha256"],
            "sigstore_bundle_name": str(python["artifact_name"]) + ".sigstore",
            "sigstore_bundle_bytes": python["sigstore_bundle_bytes"],
            "sigstore_bundle_sha256": python["sigstore_bundle_sha256"],
        },
        "build_inputs": build_inputs,
        "build_inputs_sha256": sha256_bytes(canonical_json(build_inputs)),
        "deterministic_build": {
            "pass_a_archive_sha256": archive_sha256,
            "pass_b_archive_sha256": archive_sha256,
            "pass_a_tree_sha256": tree_sha256,
            "pass_b_tree_sha256": tree_sha256,
            "match": True,
        },
        "independent_verification": {
            "status": "PASS",
            "verifier_sha256": digest("7"),
            "archive_sha256": archive_sha256,
            "closure_manifest_sha256": manifest_sha256,
            "tree_sha256": tree_sha256,
        },
        "offline_smoke": {
            "status": "PASS",
            "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
            "archive_sha256": archive_sha256,
            "closure_manifest_sha256": manifest_sha256,
            "tree_sha256": tree_sha256,
            "external_actions": 0,
        },
        "closure_self_claims": {
            "sigstore_verified": False,
            "outer_signature_ready": False,
        },
        "external_actions": 0,
    }


def validated_runtime(value: dict[str, object] | None = None) -> EvidenceDocument:
    return validate_runtime_build_evidence(canonical_json(value or valid_runtime_build()), now=NOW, schema_dir=SCHEMAS)


def valid_publisher(runtime: EvidenceDocument) -> dict[str, object]:
    build = runtime.value
    source = load_json(PROJECT / "config" / "windows-runtime-source.json")
    channel = load_json(PROJECT / "config" / "update-channel.json")
    release_key_id = channel["signature"]["key_id"]
    provider_policy_sha256 = digest("8")
    challenge_sha256 = signer_readiness_challenge_sha256(
        runtime_build_evidence_sha256=runtime.sha256,
        archive_sha256=build["archive"]["sha256"],
        source_commit=build["source_commit"],
        provider_policy_sha256=provider_policy_sha256,
        release_key_id=release_key_id,
    )
    return {
        "schema_version": 1,
        "format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
        "evidence_kind": "SANITIZED_PUBLISHER_OBSERVATION",
        "status": "READY_FOR_PROTECTED_SIGNING",
        "issued_at_utc": "2026-08-28T12:10:00Z",
        "expires_at_utc": "2026-08-28T16:10:00Z",
        "runtime_build_evidence_sha256": runtime.sha256,
        "release": {
            "version": build["application_version"],
            "source_commit": build["source_commit"],
            "platform": build["platform"],
            "archive_name": build["archive"]["name"],
            "archive_bytes": build["archive"]["bytes"],
            "archive_sha256": build["archive"]["sha256"],
            "archive_prefix": build["archive"]["archive_prefix"],
        },
        "runtime_closure": {
            "manifest_sha256": build["runtime_closure"]["manifest_sha256"],
            "tree_sha256": build["runtime_closure"]["tree_sha256"],
            "source_payload_sha256": build["runtime_closure"]["source_payload_sha256"],
            "file_count": build["runtime_closure"]["file_count"],
            "total_bytes": build["runtime_closure"]["total_bytes"],
            "structural_status": build["structural_status"],
        },
        "build_inputs_sha256": build["build_inputs_sha256"],
        "psf_sigstore": {
            "status": "VERIFIED",
            "python_artifact_sha256": build["python_source"]["artifact_sha256"],
            "sigstore_bundle_sha256": build["python_source"]["sigstore_bundle_sha256"],
            "trusted_root_sha256": digest("9"),
            "verifier_sha256": digest("a"),
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
            "pass_a_archive_sha256": build["deterministic_build"]["pass_a_archive_sha256"],
            "pass_b_archive_sha256": build["deterministic_build"]["pass_b_archive_sha256"],
            "pass_a_tree_sha256": build["deterministic_build"]["pass_a_tree_sha256"],
            "pass_b_tree_sha256": build["deterministic_build"]["pass_b_tree_sha256"],
        },
        "independent_verification": {
            "status": "PASS",
            "runtime_build_evidence_sha256": runtime.sha256,
            "verifier_sha256": build["independent_verification"]["verifier_sha256"],
            "archive_sha256": build["archive"]["sha256"],
            "closure_manifest_sha256": build["runtime_closure"]["manifest_sha256"],
            "tree_sha256": build["runtime_closure"]["tree_sha256"],
        },
        "offline_smoke": {
            "status": "PASS",
            "runtime_build_evidence_sha256": runtime.sha256,
            "result_token": "JOBFLOW_OFFLINE_SMOKE_OK",
            "external_actions": 0,
        },
        "outer_signing_readiness": {
            "status": "VERIFIED",
            "release_key_id": release_key_id,
            "provider_policy_sha256": provider_policy_sha256,
            "challenge_format": "JOBFLOW_SIGNER_READINESS_CHALLENGE_V1",
            "challenge_sha256": challenge_sha256,
            "challenge_signature_sha256": digest("b"),
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


def validated_publisher(runtime: EvidenceDocument, value: dict[str, object] | None = None) -> EvidenceDocument:
    return validate_publisher_evidence(
        canonical_json(value or valid_publisher(runtime)),
        runtime_build=runtime,
        now=NOW,
        schema_dir=SCHEMAS,
    )


def valid_clean_windows(publisher: EvidenceDocument) -> dict[str, object]:
    evidence = publisher.value
    return {
        "schema_version": 1,
        "format": "JOBFLOW_CLEAN_WINDOWS_ACCEPTANCE_V1",
        "evidence_kind": "SANITIZED_CLEAN_WINDOWS_OBSERVATION",
        "status": "PASS",
        "issued_at_utc": "2026-08-28T12:20:00Z",
        "expires_at_utc": "2026-08-29T12:20:00Z",
        "publisher_evidence_sha256": publisher.sha256,
        "release": {
            "version": evidence["release"]["version"],
            "source_commit": evidence["release"]["source_commit"],
            "platform": evidence["release"]["platform"],
        },
        "signed_bundle": {
            "manifest_sha256": digest("c"),
            "signature_sha256": digest("d"),
            "archive_name": evidence["release"]["archive_name"],
            "archive_bytes": evidence["release"]["archive_bytes"],
            "archive_sha256": evidence["release"]["archive_sha256"],
            "release_key_id": evidence["outer_signing_readiness"]["release_key_id"],
            "signature_verified_with_pinned_trust": True,
        },
        "runtime_closure": {
            "manifest_sha256": evidence["runtime_closure"]["manifest_sha256"],
            "tree_sha256": evidence["runtime_closure"]["tree_sha256"],
            "structural_status": evidence["runtime_closure"]["structural_status"],
        },
        "environment": {
            "os_family": "Windows",
            "architecture": "AMD64",
            "account_profile": "FRESH_STANDARD_USER",
            "preexisting_jobflow": False,
        },
        "browser_companion": {
            "version": "0.9.1",
            "chrome_store_version": "0.9.1",
            "edge_store_version": "0.9.1",
            "chrome_install_passed": True,
            "edge_install_passed": True,
            "native_host_registration_passed": True,
            "chrome_pairing_passed": True,
            "edge_pairing_passed": True,
        },
        "checks": {
            "install_passed": True,
            "startup_passed": True,
            "health_passed": True,
            "update_passed": True,
            "rollback_passed": True,
            "uninstall_passed": True,
        },
        "safety": {
            "external_actions": 0,
            "real_job_site_visits": 0,
            "final_submit_attempts": 0,
            "secret_material_in_evidence": False,
        },
    }


class PublisherAttestationFoundationTests(unittest.TestCase):
    def test_valid_evidence_chain_is_canonical_hash_bound_and_not_an_attestation(self) -> None:
        runtime = validated_runtime()
        publisher = validated_publisher(runtime)
        clean_value = valid_clean_windows(publisher)
        clean = validate_clean_windows_acceptance(
            canonical_json(clean_value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
        )

        self.assertEqual(runtime.value["structural_status"], "BUILT_UNATTESTED")
        self.assertEqual(runtime.value["closure_self_claims"], {
            "outer_signature_ready": False,
            "sigstore_verified": False,
        })
        self.assertEqual(publisher.value["status"], "READY_FOR_PROTECTED_SIGNING")
        self.assertEqual(publisher.value["runtime_closure"]["structural_status"], "BUILT_UNATTESTED")
        self.assertNotIn("publisher_attestation", publisher.value)
        self.assertEqual(clean.value["runtime_closure"]["structural_status"], "BUILT_UNATTESTED")
        self.assertEqual(runtime.sha256, sha256_bytes(runtime.canonical_bytes))
        self.assertEqual(publisher.sha256, clean.value["publisher_evidence_sha256"])

    def test_nested_duplicate_keys_and_non_json_numbers_are_rejected(self) -> None:
        raw = canonical_json(valid_runtime_build())
        duplicate = raw.replace(b'"external_actions":0', b'"external_actions":0,"external_actions":0', 1)
        with self.assertRaises(JobOpsError) as caught:
            validate_runtime_build_evidence(duplicate, now=NOW, schema_dir=SCHEMAS)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INVALID")

        non_finite = raw.replace(b'"external_actions":0', b'"external_actions":NaN', 1)
        with self.assertRaises(JobOpsError) as caught:
            validate_runtime_build_evidence(non_finite, now=NOW, schema_dir=SCHEMAS)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INVALID")

    def test_pretty_trailing_or_non_bytes_json_is_not_canonical(self) -> None:
        value = valid_runtime_build()
        variants = [
            json.dumps(value, indent=2).encode("utf-8"),
            canonical_json(value) + b"\n",
            bytearray(canonical_json(value)),
        ]
        for raw in variants:
            with self.subTest(kind=type(raw).__name__, length=len(raw)), self.assertRaises(JobOpsError) as caught:
                validate_runtime_build_evidence(raw, now=NOW, schema_dir=SCHEMAS)  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INVALID")

    def test_extra_and_coercible_fields_fail_closed(self) -> None:
        runtime_extra = valid_runtime_build()
        runtime_extra["builder_path"] = "redacted"
        runtime_string = valid_runtime_build()
        runtime_string["external_actions"] = "0"
        runtime_boolean = valid_runtime_build()
        runtime_boolean["external_actions"] = False
        for value in (runtime_extra, runtime_string, runtime_boolean):
            with self.assertRaises(JobOpsError):
                validated_runtime(value)

        runtime = validated_runtime()
        publisher = valid_publisher(runtime)
        publisher["psf_sigstore"]["signature_verified"] = 1
        with self.assertRaises(JobOpsError):
            validated_publisher(runtime, publisher)

        publisher_document = validated_publisher(runtime)
        clean = valid_clean_windows(publisher_document)
        clean["checks"]["health_passed"] = "true"
        with self.assertRaises(JobOpsError):
            validate_clean_windows_acceptance(
                canonical_json(clean), publisher_evidence=publisher_document, now=NOW, schema_dir=SCHEMAS
            )

    def test_runtime_tamper_and_pinned_input_drift_are_rejected(self) -> None:
        mutations = {
            "archive": lambda value: value["deterministic_build"].__setitem__("pass_b_archive_sha256", digest("e")),
            "tree": lambda value: value["offline_smoke"].__setitem__("tree_sha256", digest("e")),
            "payload": lambda value: value["runtime_closure"].__setitem__("source_payload_sha256", digest("e")),
            "build_inputs": lambda value: value["build_inputs"].__setitem__("wheelhouse_tree_sha256", digest("e")),
            "python": lambda value: value["python_source"].__setitem__("artifact_sha256", digest("e")),
        }
        for name, mutate in mutations.items():
            value = valid_runtime_build()
            mutate(value)
            with self.subTest(binding=name), self.assertRaises(JobOpsError) as caught:
                validated_runtime(value)
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_BINDING_MISMATCH")

    def test_application_wheel_provenance_tamper_is_rejected_causally(self) -> None:
        mutations = {
            "source_commit": (
                "APPLICATION_WHEEL_SOURCE_COMMIT_MISMATCH",
                lambda provenance: provenance.__setitem__("source_commit", "b" * 40),
            ),
            "second_build": (
                "APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH",
                lambda provenance: provenance.__setitem__("pass_b_wheel_sha256", digest("f")),
            ),
            "build_lock": (
                "APPLICATION_WHEEL_BUILD_LOCK_MISMATCH",
                lambda provenance: provenance.__setitem__("build_lock_sha256", digest("f")),
            ),
        }
        for name, (code, mutate) in mutations.items():
            value = valid_runtime_build()
            mutate(value["build_inputs"]["application_wheel_provenance"])
            with self.subTest(binding=name), self.assertRaises(JobOpsError) as caught:
                validated_runtime(value)
            self.assertEqual(caught.exception.code, code)

    def test_publisher_cross_binding_and_signer_challenge_tamper_are_rejected(self) -> None:
        runtime = validated_runtime()
        mutations = {
            "runtime_digest": lambda value: value.__setitem__("runtime_build_evidence_sha256", digest("e")),
            "archive": lambda value: value["release"].__setitem__("archive_sha256", digest("e")),
            "closure": lambda value: value["runtime_closure"].__setitem__("tree_sha256", digest("e")),
            "sigstore": lambda value: value["psf_sigstore"].__setitem__("python_artifact_sha256", digest("e")),
            "verifier": lambda value: value["independent_verification"].__setitem__("verifier_sha256", digest("e")),
            "challenge": lambda value: value["outer_signing_readiness"].__setitem__("challenge_sha256", digest("e")),
        }
        for name, mutate in mutations.items():
            value = valid_publisher(runtime)
            mutate(value)
            with self.subTest(binding=name), self.assertRaises(JobOpsError) as caught:
                validated_publisher(runtime, value)
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_BINDING_MISMATCH")

    def test_clean_windows_cross_binding_tamper_is_rejected(self) -> None:
        runtime = validated_runtime()
        publisher = validated_publisher(runtime)
        mutations = {
            "publisher_digest": lambda value: value.__setitem__("publisher_evidence_sha256", digest("e")),
            "archive": lambda value: value["signed_bundle"].__setitem__("archive_sha256", digest("e")),
            "closure": lambda value: value["runtime_closure"].__setitem__("tree_sha256", digest("e")),
            "version": lambda value: value["release"].__setitem__("version", "0.6.1"),
        }
        for name, mutate in mutations.items():
            value = valid_clean_windows(publisher)
            mutate(value)
            with self.subTest(binding=name), self.assertRaises(JobOpsError) as caught:
                validate_clean_windows_acceptance(
                    canonical_json(value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
                )
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_BINDING_MISMATCH")

    def test_stale_future_and_overlong_evidence_windows_fail_closed(self) -> None:
        runtime_value = valid_runtime_build()
        runtime_value["expires_at_utc"] = "2026-08-28T12:30:00Z"
        with self.assertRaises(JobOpsError) as caught:
            validated_runtime(runtime_value)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_STALE")

        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["issued_at_utc"] = "2026-08-28T12:36:00Z"
        publisher_value["expires_at_utc"] = "2026-08-28T13:36:00Z"
        with self.assertRaises(JobOpsError) as caught:
            validated_publisher(runtime, publisher_value)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

        publisher_value = valid_publisher(runtime)
        publisher_value["expires_at_utc"] = "2026-08-28T16:10:01Z"
        with self.assertRaises(JobOpsError) as caught:
            validated_publisher(runtime, publisher_value)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

        publisher = validated_publisher(runtime)
        clean_value = valid_clean_windows(publisher)
        clean_value["expires_at_utc"] = "2026-08-28T12:30:00Z"
        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(clean_value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_STALE")

    def test_evidence_cannot_predate_the_input_it_claims_to_observe(self) -> None:
        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["issued_at_utc"] = "2026-08-28T11:59:59Z"
        publisher_value["expires_at_utc"] = "2026-08-28T12:59:59Z"
        with self.assertRaises(JobOpsError) as caught:
            validated_publisher(runtime, publisher_value)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

        publisher = validated_publisher(runtime)
        clean_value = valid_clean_windows(publisher)
        clean_value["issued_at_utc"] = "2026-08-28T12:09:59Z"
        clean_value["expires_at_utc"] = "2026-08-28T13:09:59Z"
        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(clean_value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

    def test_derived_evidence_must_be_issued_before_parent_expiry(self) -> None:
        runtime_value = valid_runtime_build()
        runtime_value["expires_at_utc"] = "2026-08-28T12:31:00Z"
        runtime = validated_runtime(runtime_value)
        publisher_value = valid_publisher(runtime)
        publisher_value["issued_at_utc"] = "2026-08-28T12:35:00Z"
        publisher_value["expires_at_utc"] = "2026-08-28T13:35:00Z"
        with self.assertRaises(JobOpsError) as caught:
            validated_publisher(runtime, publisher_value)
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["expires_at_utc"] = "2026-08-28T12:31:00Z"
        publisher = validated_publisher(runtime, publisher_value)
        clean_value = valid_clean_windows(publisher)
        clean_value["issued_at_utc"] = "2026-08-28T12:35:00Z"
        clean_value["expires_at_utc"] = "2026-08-28T13:35:00Z"
        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(clean_value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_TIME_INVALID")

    def test_evidence_timestamps_require_an_explicit_utc_zone(self) -> None:
        runtime_value = valid_runtime_build()
        runtime_value["issued_at_utc"] = "2026-08-28T12:00:00"
        with self.assertRaises(JobOpsError):
            validated_runtime(runtime_value)

        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["issued_at_utc"] = "2026-08-28T12:10:00"
        with self.assertRaises(JobOpsError):
            validated_publisher(runtime, publisher_value)

        publisher = validated_publisher(runtime)
        clean_value = valid_clean_windows(publisher)
        clean_value["issued_at_utc"] = "2026-08-28T12:20:00"
        with self.assertRaises(JobOpsError):
            validate_clean_windows_acceptance(
                canonical_json(clean_value), publisher_evidence=publisher, now=NOW, schema_dir=SCHEMAS
            )

    def test_local_paths_secret_fields_and_secret_like_values_are_rejected(self) -> None:
        runtime_value = valid_runtime_build()
        runtime_value["local_builder_path"] = "C:\\Users\\Example\\builder"
        with self.assertRaises(JobOpsError):
            validated_runtime(runtime_value)

        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["private_key"] = "-----BEGIN " + "PRIVATE KEY-----"
        with self.assertRaises(JobOpsError):
            validated_publisher(runtime, publisher_value)

        publisher_value = valid_publisher(runtime)
        publisher_value["psf_sigstore"]["verifier_version"] = "password=example"
        with self.assertRaises(JobOpsError):
            validated_publisher(runtime, publisher_value)

        safe = canonical_json(valid_publisher(runtime)).decode("utf-8")
        self.assertNotRegex(safe, r"(?i)[a-z]:\\|\\\\|-----BEGIN|github_pat_|password=")

    def test_built_unattested_invariants_are_non_coercible(self) -> None:
        runtime_status = valid_runtime_build()
        runtime_status["structural_status"] = "ATTESTED"
        runtime_sigstore = valid_runtime_build()
        runtime_sigstore["closure_self_claims"]["sigstore_verified"] = True
        runtime_outer = valid_runtime_build()
        runtime_outer["closure_self_claims"]["outer_signature_ready"] = True
        for value in (runtime_status, runtime_sigstore, runtime_outer):
            with self.assertRaises(JobOpsError):
                validated_runtime(value)

        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["runtime_closure"]["structural_status"] = "ATTESTED"
        with self.assertRaises(JobOpsError):
            validated_publisher(runtime, publisher_value)

    def test_validated_document_cannot_be_mutated_through_value_projection(self) -> None:
        runtime = validated_runtime()
        projected = runtime.value
        projected["structural_status"] = "ATTESTED"
        self.assertEqual(runtime.value["structural_status"], "BUILT_UNATTESTED")
        self.assertEqual(runtime.sha256, sha256_bytes(runtime.canonical_bytes))

    def test_cross_binding_requires_the_validated_evidence_type(self) -> None:
        runtime = validated_runtime()
        publisher = validated_publisher(runtime)
        with self.assertRaises(JobOpsError) as caught:
            validate_publisher_evidence(
                canonical_json(valid_publisher(runtime)),
                runtime_build=publisher,
                now=NOW,
                schema_dir=SCHEMAS,
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(valid_clean_windows(publisher)),
                publisher_evidence=runtime,
                now=NOW,
                schema_dir=SCHEMAS,
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

    def test_forged_runtime_evidence_document_is_revalidated_at_boundary(self) -> None:
        mutations = {
            "schema": lambda value: value.__setitem__("external_actions", 999),
            "expired": lambda value: value.__setitem__(
                "expires_at_utc", "2026-08-28T12:30:00Z"
            ),
            "policy": lambda value: value["python_source"].__setitem__(
                "artifact_sha256", digest("f")
            ),
        }
        for name, mutate in mutations.items():
            value = valid_runtime_build()
            mutate(value)
            raw = canonical_json(value)
            forged = EvidenceDocument(
                schema_name="runtime-build-evidence-v1",
                canonical_bytes=raw,
                sha256=sha256_bytes(raw),
            )
            with self.subTest(kind=name), self.assertRaises(JobOpsError) as caught:
                validate_publisher_evidence(
                    canonical_json(valid_publisher(forged)),
                    runtime_build=forged,
                    now=NOW,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

        runtime = validated_runtime()
        forged = EvidenceDocument(
            schema_name="runtime-build-evidence-v1",
            canonical_bytes=runtime.canonical_bytes,
            sha256=digest("f"),
        )
        with self.assertRaises(JobOpsError) as caught:
            validate_publisher_evidence(
                canonical_json(valid_publisher(forged)),
                runtime_build=forged,
                now=NOW,
                schema_dir=SCHEMAS,
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

    def test_forged_publisher_evidence_document_is_revalidated_at_boundary(self) -> None:
        runtime = validated_runtime()
        publisher_value = valid_publisher(runtime)
        publisher_value["release_safety"]["external_actions"] = 1
        raw = canonical_json(publisher_value)
        forged = EvidenceDocument(
            schema_name="publisher-evidence-v1",
            canonical_bytes=raw,
            sha256=sha256_bytes(raw),
        )
        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(valid_clean_windows(forged)),
                publisher_evidence=forged,
                now=NOW,
                schema_dir=SCHEMAS,
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

        publisher = validated_publisher(runtime)
        forged = EvidenceDocument(
            schema_name="publisher-evidence-v1",
            canonical_bytes=publisher.canonical_bytes,
            sha256=digest("f"),
        )
        with self.assertRaises(JobOpsError) as caught:
            validate_clean_windows_acceptance(
                canonical_json(valid_clean_windows(forged)),
                publisher_evidence=forged,
                now=NOW,
                schema_dir=SCHEMAS,
            )
        self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_INPUT_INVALID")

if __name__ == "__main__":
    unittest.main()
