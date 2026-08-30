from __future__ import annotations

import unittest
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named
from jobops.util import canonical_json, sha256_bytes


PROJECT = Path(__file__).resolve().parents[1]
SCHEMAS = PROJECT / "schemas"
PRODUCTION_RELEASE_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"


def digest(character: str) -> str:
    return "sha256:" + character * 64


def provenance(*, wheel: str, commit: str = "a" * 40) -> dict[str, object]:
    return {
        "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
        "source_commit": commit,
        "source_git_tree_oid": "b" * 40,
        "source_build_tree_sha256": digest("c"),
        "source_archive_sha256": digest("d"),
        "build_lock_sha256": digest("e"),
        "build_recipe_sha256": digest("f"),
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


def valid_manifest() -> dict[str, object]:
    policy = {
        "minimum_updater_version": "0.6.0",
        "minimum_bootstrap_version": "0.6.0",
        "required_structural_status": "BUILT_UNATTESTED",
        "publisher_attestation_required": True,
        "final_submit_user_only": True,
        "automatic_retry_submission_unknown": False,
        "external_actions_during_update": 0,
    }
    return {
        "schema_version": 2,
        "product": "JobFlow",
        "channel": "stable",
        "release": {
            "version": "0.6.0",
            "source_commit": "a" * 40,
            "platform": "windows-x64",
        },
        "predecessor": {
            "minimum_version": "0.4.1",
            "maximum_version_exclusive": "0.6.0",
            "disallow_downgrade": True,
            "require_current_runtime_closure": True,
        },
        "asset": {
            "name": "JobFlow-v0.6.0-windows-x64-complete.zip",
            "bytes": 123456,
            "sha256": digest("1"),
            "archive_prefix": "JobFlow-v0.6.0-windows-x64/",
        },
        "runtime_closure": {
            "manifest_sha256": digest("2"),
            "tree_sha256": digest("3"),
            "structural_status": "BUILT_UNATTESTED",
            "source_commit": "a" * 40,
            "source_payload_sha256": digest("1"),
            "file_count": 412,
            "total_bytes": 9876543,
            "python_version": "3.13.15",
            "platform": "windows-x64",
            "build_inputs": {
                "python_artifact_sha256": digest("4"),
                "wheel_lock_sha256": digest("5"),
                "wheelhouse_tree_sha256": digest("6"),
                "application_wheel_sha256": digest("7"),
                "application_wheel_provenance": provenance(wheel=digest("7")),
                "builder_toolchain_sha256": digest("8"),
                "wheel_count": 16,
            },
        },
        "publisher_attestation": {
            "status": "ATTESTED",
            "format": "JOBFLOW_PUBLISHER_ATTESTATION_V2",
            "release_key_id": PRODUCTION_RELEASE_KEY_ID,
            "evidence_format": "JOBFLOW_PUBLISHER_EVIDENCE_V1",
            "runtime_build_evidence_sha256": digest("9"),
            "publisher_evidence_sha256": digest("a"),
            "evidence_expires_at_utc": "2026-08-28T16:00:00Z",
            "signer_readiness_challenge_sha256": digest("b"),
            "runtime_closure_manifest_sha256": digest("2"),
            "runtime_tree_sha256": digest("3"),
            "build_inputs_sha256": sha256_bytes(canonical_json({
                "python_artifact_sha256": digest("4"),
                "wheel_lock_sha256": digest("5"),
                "wheelhouse_tree_sha256": digest("6"),
                "application_wheel_sha256": digest("7"),
                "application_wheel_provenance": provenance(wheel=digest("7")),
                "builder_toolchain_sha256": digest("8"),
                "wheel_count": 16,
            })),
            "source_commit": "a" * 40,
            "source_payload_sha256": digest("1"),
            "file_count": 412,
            "total_bytes": 9876543,
            "policy_sha256": sha256_bytes(canonical_json(policy)),
            "issued_at_utc": "2026-08-28T12:00:00Z",
        },
        "policy": policy,
        "issued_at_utc": "2026-08-28T12:01:00Z",
    }


class UpdateManifestV2Tests(unittest.TestCase):
    def test_structural_closure_and_external_attestation_are_distinct_and_valid(self) -> None:
        value = valid_manifest()
        self.assertIs(validate_named("update-manifest-v2", value, SCHEMAS), value)
        self.assertNotIn("legacy_v1_predecessors", value)

    def test_exact_legacy_v1_predecessor_identities_are_optional_and_valid(self) -> None:
        value = valid_manifest()
        value["legacy_v1_predecessors"] = [
            legacy_identity("0.4.1", "1"),
            legacy_identity("0.5.0", "2"),
        ]
        self.assertIs(validate_named("update-manifest-v2", value, SCHEMAS), value)

    def test_legacy_v1_predecessor_fields_are_strict_and_non_coercible(self) -> None:
        mutations = {
            "missing": lambda item: item.pop("source_sha256"),
            "extra": lambda item: item.__setitem__("allow_any_local_install", True),
            "schema_string": lambda item: item.__setitem__("schema_version", "1"),
            "schema_float": lambda item: item.__setitem__("schema_version", 1.0),
            "schema_bool": lambda item: item.__setitem__("schema_version", True),
            "version": lambda item: item.__setitem__("version", "v0.4.1"),
            "hash": lambda item: item.__setitem__("source_sha256", digest("1")),
            "directory": lambda item: item.__setitem__("version_directory", "v0.4.1-deadbeefdead"),
        }
        for name, mutate in mutations.items():
            value = valid_manifest()
            item = legacy_identity("0.4.1", "1")
            mutate(item)
            value["legacy_v1_predecessors"] = [item]
            with self.subTest(mutation=name), self.assertRaises(JobOpsError):
                validate_named("update-manifest-v2", value, SCHEMAS)

    def test_legacy_v1_predecessors_reject_non_predecessors_and_ambiguous_duplicates(self) -> None:
        future = valid_manifest()
        future["legacy_v1_predecessors"] = [legacy_identity("0.6.0", "1")]
        with self.assertRaises(JobOpsError) as caught:
            validate_named("update-manifest-v2", future, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

        duplicate = valid_manifest()
        duplicate_identity = legacy_identity("0.4.1", "1")
        duplicate["legacy_v1_predecessors"] = [
            duplicate_identity,
            {
                "version_directory": duplicate_identity["version_directory"],
                "source_sha256": duplicate_identity["source_sha256"],
                "version": duplicate_identity["version"],
                "schema_version": duplicate_identity["schema_version"],
            },
        ]
        with self.assertRaises(JobOpsError) as caught:
            validate_named("update-manifest-v2", duplicate, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

        shared_prefix = "a" * 12
        first_hash = shared_prefix + "b" * 52
        second_hash = shared_prefix + "c" * 52
        ambiguous = valid_manifest()
        ambiguous["legacy_v1_predecessors"] = [
            {
                "schema_version": 1,
                "version": "0.4.1",
                "source_sha256": first_hash,
                "version_directory": "v0.4.1-" + shared_prefix,
            },
            {
                "schema_version": 1,
                "version": "0.4.1",
                "source_sha256": second_hash,
                "version_directory": "v0.4.1-" + shared_prefix,
            },
        ]
        with self.assertRaises(JobOpsError) as caught:
            validate_named("update-manifest-v2", ambiguous, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

    def test_legacy_v1_predecessors_require_canonical_numeric_version_order(self) -> None:
        value = valid_manifest()
        value["legacy_v1_predecessors"] = [
            legacy_identity("0.5.0", "1"),
            legacy_identity("0.4.1", "2"),
        ]
        with self.assertRaises(JobOpsError) as caught:
            validate_named("update-manifest-v2", value, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

    def test_legacy_v1_predecessor_authorization_set_is_bounded(self) -> None:
        value = valid_manifest()
        value["legacy_v1_predecessors"] = []
        for index in range(65):
            source_sha256 = f"{index:064x}"
            version = f"0.0.{index}"
            value["legacy_v1_predecessors"].append(
                {
                    "schema_version": 1,
                    "version": version,
                    "source_sha256": source_sha256,
                    "version_directory": f"v{version}-{source_sha256[:12]}",
                }
            )
        with self.assertRaises(JobOpsError) as caught:
            validate_named("update-manifest-v2", value, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_VALIDATION_FAILED")

    def test_structural_closure_cannot_self_assert_attested(self) -> None:
        value = valid_manifest()
        value["runtime_closure"]["structural_status"] = "ATTESTED"
        with self.assertRaises(JobOpsError):
            validate_named("update-manifest-v2", value, SCHEMAS)

    def test_external_attestation_is_required_and_strict(self) -> None:
        for mutation in ("missing", "wrong_status", "extra"):
            value = valid_manifest()
            if mutation == "missing":
                value.pop("publisher_attestation")
            elif mutation == "wrong_status":
                value["publisher_attestation"]["status"] = "BUILT_UNATTESTED"
            else:
                value["publisher_attestation"]["local_self_attested"] = True
            with self.subTest(mutation=mutation), self.assertRaises(JobOpsError):
                validate_named("update-manifest-v2", value, SCHEMAS)

    def test_external_attestation_must_use_the_pinned_production_release_key(self) -> None:
        value = valid_manifest()
        value["publisher_attestation"]["release_key_id"] = digest("9")
        with self.assertRaises(JobOpsError):
            validate_named("update-manifest-v2", value, SCHEMAS)

    def test_attestation_must_bind_every_critical_closure_input(self) -> None:
        mutations = {
            "manifest": ("runtime_closure_manifest_sha256", digest("a")),
            "tree": ("runtime_tree_sha256", digest("b")),
            "build_inputs": ("build_inputs_sha256", digest("d")),
            "commit": ("source_commit", "b" * 40),
            "payload": ("source_payload_sha256", digest("c")),
            "files": ("file_count", 411),
            "bytes": ("total_bytes", 9876542),
        }
        for name, (field, invalid) in mutations.items():
            value = valid_manifest()
            value["publisher_attestation"][field] = invalid
            with self.subTest(binding=name), self.assertRaises(JobOpsError) as caught:
                validate_named("update-manifest-v2", value, SCHEMAS)
            self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

    def test_attestation_binds_policy_and_cannot_postdate_manifest(self) -> None:
        changed_policy = valid_manifest()
        changed_policy["policy"]["minimum_bootstrap_version"] = "0.5.0"
        with self.assertRaises(JobOpsError):
            validate_named("update-manifest-v2", changed_policy, SCHEMAS)

        postdated = valid_manifest()
        postdated["publisher_attestation"]["issued_at_utc"] = "2026-08-28T12:02:00Z"
        with self.assertRaises(JobOpsError):
            validate_named("update-manifest-v2", postdated, SCHEMAS)

        expired = valid_manifest()
        expired["publisher_attestation"]["evidence_expires_at_utc"] = "2026-08-28T12:01:00Z"
        with self.assertRaises(JobOpsError):
            validate_named("update-manifest-v2", expired, SCHEMAS)

    def test_release_asset_and_structural_source_are_cross_bound(self) -> None:
        for name, mutate in {
            "asset_name": lambda value: value["asset"].__setitem__("name", "JobFlow-v0.6.1-windows-x64-complete.zip"),
            "archive_prefix": lambda value: value["asset"].__setitem__("archive_prefix", "JobFlow-v0.6.1-windows-x64/"),
            "predecessor": lambda value: value["predecessor"].__setitem__("maximum_version_exclusive", "0.6.1"),
            "predecessor_floor": lambda value: value["predecessor"].__setitem__("minimum_version", "0.6.0"),
            "future_bootstrap": lambda value: value["policy"].__setitem__("minimum_bootstrap_version", "0.6.1"),
            "source_commit": lambda value: value["runtime_closure"].__setitem__("source_commit", "b" * 40),
            "source_payload": lambda value: value["runtime_closure"].__setitem__("source_payload_sha256", digest("a")),
            "build_inputs": lambda value: value["runtime_closure"]["build_inputs"].__setitem__("wheel_count", 15),
        }.items():
            value = valid_manifest()
            mutate(value)
            with self.subTest(binding=name), self.assertRaises(JobOpsError):
                validate_named("update-manifest-v2", value, SCHEMAS)


if __name__ == "__main__":
    unittest.main()
