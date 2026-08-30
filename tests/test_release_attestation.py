from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobops.publisher_attestation import EvidenceDocument
from jobops.release_attestation import verify_public_release_attestation
from jobops.util import canonical_json, sha256_bytes


VERSION = "0.6.0"
COMMIT = "a" * 40
ARCHIVE_NAME = f"JobFlow-v{VERSION}-windows-x64-complete.zip"


def document(schema_name: str, value: dict[str, object]) -> EvidenceDocument:
    raw = canonical_json(value)
    return EvidenceDocument(schema_name=schema_name, canonical_bytes=raw, sha256=sha256_bytes(raw))


class ReleaseAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.dist = self.project / "dist"
        self.dist.mkdir(parents=True)
        (self.project / "config").mkdir()
        (self.project / "schemas").mkdir()
        (self.project / "browser-companion").mkdir()
        (self.project / "browser-companion" / "manifest.json").write_text(
            '{"version":"0.9.2"}', encoding="utf-8"
        )
        (self.project / "config" / "update-channel.json").write_text("{}", encoding="utf-8")
        self.paths = {
            "manifest": self.dist / "JobFlow-update-manifest.json",
            "signature": self.dist / "JobFlow-update-manifest.sig.json",
            "archive": self.dist / ARCHIVE_NAME,
            "runtime": self.dist / "JobFlow-runtime-build-evidence.json",
            "publisher": self.dist / "JobFlow-publisher-evidence.json",
            "clean": self.dist / "JobFlow-clean-windows-acceptance.json",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_runtime_chain(self, *, include_clean: bool = True) -> None:
        for name in ("manifest", "signature", "runtime", "publisher"):
            self.paths[name].write_bytes(b"{}")
        self.paths["archive"].write_bytes(b"runtime")
        if include_clean:
            self.paths["clean"].write_bytes(b"{}")

    def _values(self) -> tuple[EvidenceDocument, EvidenceDocument, EvidenceDocument, dict[str, object]]:
        archive = {
            "name": ARCHIVE_NAME,
            "bytes": 7,
            "sha256": "sha256:" + "1" * 64,
            "archive_prefix": f"JobFlow-v{VERSION}-windows-x64/",
        }
        runtime_closure = {
            "manifest_sha256": "sha256:" + "2" * 64,
            "tree_sha256": "sha256:" + "3" * 64,
            "source_payload_sha256": archive["sha256"],
            "file_count": 12,
            "total_bytes": 3456,
        }
        build = document(
            "runtime-build-evidence-v1",
            {
                "application_version": VERSION,
                "source_commit": COMMIT,
                "archive": archive,
                "runtime_closure": runtime_closure,
            },
        )
        publisher_value = {
            "issued_at_utc": "2026-08-28T12:10:00Z",
            "expires_at_utc": "2026-08-28T16:10:00Z",
            "release": {
                "version": VERSION,
                "source_commit": COMMIT,
                "platform": "windows-x64",
                "archive_name": archive["name"],
                "archive_bytes": archive["bytes"],
                "archive_sha256": archive["sha256"],
                "archive_prefix": archive["archive_prefix"],
            },
            "runtime_closure": {
                **runtime_closure,
                "structural_status": "BUILT_UNATTESTED",
            },
            "build_inputs_sha256": "sha256:" + "4" * 64,
            "outer_signing_readiness": {
                "release_key_id": "sha256:" + "5" * 64,
                "provider_policy_sha256": "sha256:" + "6" * 64,
                "challenge_sha256": "sha256:" + "7" * 64,
            },
        }
        publisher = document("publisher-evidence-v1", publisher_value)
        bundle = {
            "status": "RELEASE_BUNDLE_VERIFIED",
            "signature_verified": True,
            "archive_verified": True,
            "runtime_closure_verified": True,
            "publisher_attestation_status": "ATTESTED",
            "available_version": VERSION,
            "commit": COMMIT,
            "release_platform": "windows-x64",
            "runtime_build_evidence_sha256": build.sha256,
            "publisher_evidence_sha256": publisher.sha256,
            "publisher_evidence_expires_at_utc": publisher_value["expires_at_utc"],
            "publisher_attestation_issued_at_utc": publisher_value["issued_at_utc"],
            "publisher_build_inputs_sha256": publisher_value["build_inputs_sha256"],
            "publisher_policy_sha256": publisher_value["outer_signing_readiness"]["provider_policy_sha256"],
            "signer_readiness_challenge_sha256": publisher_value["outer_signing_readiness"]["challenge_sha256"],
            "key_id": publisher_value["outer_signing_readiness"]["release_key_id"],
            "asset_name": archive["name"],
            "asset_bytes": archive["bytes"],
            "asset_sha256": archive["sha256"],
            "archive_prefix": archive["archive_prefix"],
            "runtime_closure_manifest_sha256": runtime_closure["manifest_sha256"],
            "runtime_tree_sha256": runtime_closure["tree_sha256"],
            "source_payload_sha256": runtime_closure["source_payload_sha256"],
            "runtime_file_count": runtime_closure["file_count"],
            "runtime_total_bytes": runtime_closure["total_bytes"],
            "manifest_sha256": "sha256:" + "8" * 64,
            "signature_sha256": "sha256:" + "9" * 64,
        }
        clean = document(
            "clean-windows-acceptance-v1",
            {
                "publisher_evidence_sha256": publisher.sha256,
                "release": {
                    "version": VERSION,
                    "source_commit": COMMIT,
                    "platform": "windows-x64",
                },
                "signed_bundle": {
                    "manifest_sha256": bundle["manifest_sha256"],
                    "signature_sha256": bundle["signature_sha256"],
                    "archive_name": bundle["asset_name"],
                    "archive_bytes": bundle["asset_bytes"],
                    "archive_sha256": bundle["asset_sha256"],
                    "release_key_id": bundle["key_id"],
                },
                "runtime_closure": {
                    "manifest_sha256": bundle["runtime_closure_manifest_sha256"],
                    "tree_sha256": bundle["runtime_tree_sha256"],
                },
                "browser_companion": {
                    "version": "0.9.2",
                    "chrome_store_version": "0.9.2",
                    "edge_store_version": "0.9.2",
                },
            },
        )
        return build, publisher, clean, bundle

    def _verify(self, *, mutate_bundle=None, mutate_clean=None) -> dict[str, object]:
        build, publisher, clean, bundle = self._values()
        if mutate_bundle is not None:
            mutate_bundle(bundle)
        clean_value = clean.value
        if mutate_clean is not None:
            mutate_clean(clean_value)
            clean = document("clean-windows-acceptance-v1", clean_value)
        with (
            patch("jobops.release_attestation.validate_runtime_build_evidence", return_value=build),
            patch("jobops.release_attestation.validate_publisher_evidence", return_value=publisher),
            patch("jobops.release_attestation.validate_clean_windows_acceptance", return_value=clean),
            patch("jobops.release_attestation.verify_signed_release_bundle", return_value=bundle),
        ):
            return verify_public_release_attestation(
                self.project,
                version=VERSION,
                commit=COMMIT,
            )

    def test_missing_and_partial_release_chains_fail_closed(self) -> None:
        missing = verify_public_release_attestation(self.project, version=VERSION, commit=COMMIT)
        self.assertEqual(missing["release_attestation_status"], "MISSING")
        self.assertEqual(missing["runtime_closure_status"], "UNATTESTED")
        self.paths["manifest"].write_bytes(b"{}")
        partial = verify_public_release_attestation(self.project, version=VERSION, commit=COMMIT)
        self.assertEqual(partial["release_attestation_status"], "INVALID")
        self.assertEqual(partial["failure_code"], "RELEASE_ATTESTATION_INCOMPLETE")

    def test_exact_bound_chain_attests_runtime_and_clean_windows(self) -> None:
        self._write_runtime_chain()
        result = self._verify()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["release_attestation_status"], "PASS")
        self.assertEqual(result["clean_windows_evidence_status"], "PASS")
        self.assertEqual(result["runtime_closure_status"], "ATTESTED")
        self.assertIsNone(result["failure_code"])
        self.assertTrue(result["signature_verified"])

    def test_signed_binding_mismatch_never_attests_runtime(self) -> None:
        self._write_runtime_chain()
        result = self._verify(mutate_bundle=lambda value: value.__setitem__("commit", "b" * 40))
        self.assertEqual(result["release_attestation_status"], "INVALID")
        self.assertEqual(result["runtime_closure_status"], "UNATTESTED")
        self.assertEqual(result["failure_code"], "RELEASE_ATTESTATION_BINDING_MISMATCH")

    def test_missing_or_mismatched_clean_windows_evidence_does_not_undo_runtime_attestation(self) -> None:
        self._write_runtime_chain(include_clean=False)
        missing = self._verify()
        self.assertEqual(missing["release_attestation_status"], "PASS")
        self.assertEqual(missing["runtime_closure_status"], "ATTESTED")
        self.assertEqual(missing["clean_windows_evidence_status"], "MISSING")

        self.paths["clean"].write_bytes(b"{}")
        invalid = self._verify(
            mutate_clean=lambda value: value["browser_companion"].__setitem__("edge_store_version", "0.9.1")
        )
        self.assertEqual(invalid["runtime_closure_status"], "ATTESTED")
        self.assertEqual(invalid["clean_windows_evidence_status"], "INVALID")

    def test_multiply_linked_evidence_is_rejected(self) -> None:
        self._write_runtime_chain()
        extra = self.dist / "duplicate-runtime-evidence.json"
        try:
            os.link(self.paths["runtime"], extra)
        except OSError:
            self.skipTest("hard links are unavailable on this test filesystem")
        result = self._verify()
        self.assertEqual(result["release_attestation_status"], "INVALID")
        self.assertEqual(result["failure_code"], "RELEASE_RUNTIME_BUILD_EVIDENCE_INVALID")


if __name__ == "__main__":
    unittest.main()
