from __future__ import annotations

import unittest
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named


PROJECT = Path(__file__).resolve().parents[1]
SCHEMAS = PROJECT / "schemas"
PRODUCTION_RELEASE_KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"


def digest(character: str) -> str:
    return "sha256:" + character * 64


def valid_pointer() -> dict[str, object]:
    return {
        "schema_version": 2,
        "product": "JobFlow",
        "version_directory": "v0.6.0-111111111111",
        "version": "0.6.0",
        "source_commit": "a" * 40,
        "source_payload_sha256": digest("1"),
        "runtime_closure_manifest_sha256": digest("2"),
        "runtime_tree_sha256": digest("3"),
        "release_key_id": PRODUCTION_RELEASE_KEY_ID,
        "bootstrap_version": "0.6.0",
        "platform": "windows-x64",
    }


class InstalledPointerV2Tests(unittest.TestCase):
    def test_valid_pointer_binds_all_release_and_runtime_identities(self) -> None:
        value = valid_pointer()
        self.assertIs(validate_named("installed-pointer-v2", value, SCHEMAS), value)

    def test_pointer_rejects_missing_extra_and_coercible_fields(self) -> None:
        mutations = []
        missing = valid_pointer(); missing.pop("runtime_tree_sha256"); mutations.append(missing)
        extra = valid_pointer(); extra["legacy_source_sha256"] = digest("5"); mutations.append(extra)
        wrong_schema = valid_pointer(); wrong_schema["schema_version"] = "2"; mutations.append(wrong_schema)
        wrong_platform = valid_pointer(); wrong_platform["platform"] = "windows-arm64"; mutations.append(wrong_platform)
        for index, value in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(JobOpsError):
                validate_named("installed-pointer-v2", value, SCHEMAS)

    def test_pointer_directory_is_derived_from_version_and_source_payload(self) -> None:
        for directory in (
            "v0.6.1-111111111111",
            "v0.6.0-222222222222",
            "../v0.6.0-111111111111",
            "V0.6.0-111111111111",
        ):
            value = valid_pointer()
            value["version_directory"] = directory
            with self.subTest(directory=directory), self.assertRaises(JobOpsError):
                validate_named("installed-pointer-v2", value, SCHEMAS)

    def test_pointer_hashes_commits_keys_and_versions_are_strict(self) -> None:
        mutations = {
            "source_commit": "A" * 40,
            "source_payload_sha256": "1" * 64,
            "runtime_closure_manifest_sha256": digest("g"),
            "runtime_tree_sha256": "sha256:" + "A" * 64,
            "release_key_id": "key-1",
            "bootstrap_version": "0.6",
        }
        for field, invalid in mutations.items():
            value = valid_pointer()
            value[field] = invalid
            with self.subTest(field=field), self.assertRaises(JobOpsError):
                validate_named("installed-pointer-v2", value, SCHEMAS)

    def test_pointer_release_key_is_the_pinned_production_key(self) -> None:
        value = valid_pointer()
        value["release_key_id"] = digest("4")
        with self.assertRaises(JobOpsError):
            validate_named("installed-pointer-v2", value, SCHEMAS)

    def test_pointer_rejects_bootstrap_newer_than_installed_release(self) -> None:
        value = valid_pointer()
        value["bootstrap_version"] = "0.6.1"
        with self.assertRaises(JobOpsError) as caught:
            validate_named("installed-pointer-v2", value, SCHEMAS)
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")


if __name__ == "__main__":
    unittest.main()
