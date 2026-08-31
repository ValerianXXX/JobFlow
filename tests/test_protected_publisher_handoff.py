from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.protected_publisher_handoff import (
    build_protected_publisher_request,
    validate_protected_publisher_request,
    validate_protected_publisher_response,
    write_protected_publisher_request,
)
from jobops.publisher_attestation import validate_runtime_build_evidence
from jobops.util import canonical_json, sha256_bytes
from test_publisher_attestation import (
    NOW,
    SCHEMAS,
    valid_publisher,
    valid_runtime_build,
)


class ProtectedPublisherHandoffTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path, dict[str, object]]:
        archive = root / "JobFlow-v0.6.0-windows-x64-complete.zip"
        archive.write_bytes(b"synthetic complete runtime archive")
        archive_sha256 = sha256_bytes(archive.read_bytes())
        value = valid_runtime_build()
        value["archive"]["bytes"] = archive.stat().st_size
        value["archive"]["sha256"] = archive_sha256
        value["runtime_closure"]["source_payload_sha256"] = archive_sha256
        value["deterministic_build"]["pass_a_archive_sha256"] = archive_sha256
        value["deterministic_build"]["pass_b_archive_sha256"] = archive_sha256
        value["independent_verification"]["archive_sha256"] = archive_sha256
        value["offline_smoke"]["archive_sha256"] = archive_sha256
        evidence = root / "runtime-build-evidence.json"
        evidence.write_bytes(canonical_json(value))
        return archive, evidence, value

    def test_request_is_deterministic_canonical_pathless_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, runtime_path, _ = self._inputs(root)
            first = build_protected_publisher_request(
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            second = build_protected_publisher_request(
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            self.assertEqual(first, second)
            raw = canonical_json(first)
            self.assertEqual(validate_protected_publisher_request(raw, schema_dir=SCHEMAS), first)
            decoded = raw.decode("utf-8")
            self.assertNotIn(str(root), decoded)
            self.assertNotRegex(decoded, r"(?i)[a-z]:\\|-----BEGIN|github_pat_|password=")

            output = root / "JobFlow-protected-publisher-request.json"
            result = write_protected_publisher_request(output, first, schema_dir=SCHEMAS)
            self.assertEqual(output.read_bytes(), raw)
            self.assertEqual(result["status"], "PROTECTED_PUBLISHER_REQUEST_READY")
            self.assertEqual(result["sha256"], sha256_bytes(raw))
            self.assertFalse(list(root.glob(f".{output.name}.*.tmp")))

    def test_archive_change_or_wrong_archive_name_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, runtime_path, _ = self._inputs(root)
            archive.write_bytes(b"changed")
            with self.assertRaises(JobOpsError) as caught:
                build_protected_publisher_request(
                    archive_path=archive,
                    runtime_build_evidence_path=runtime_path,
                    now=NOW,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "PROTECTED_PUBLISHER_ARCHIVE_BINDING_MISMATCH")

            archive, runtime_path, _ = self._inputs(root)
            renamed = root / "not-the-release.zip"
            archive.rename(renamed)
            with self.assertRaises(JobOpsError) as caught:
                build_protected_publisher_request(
                    archive_path=renamed,
                    runtime_build_evidence_path=runtime_path,
                    now=NOW,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "PROTECTED_PUBLISHER_ARCHIVE_BINDING_MISMATCH")

    def test_valid_protected_response_is_revalidated_and_cross_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, runtime_path, _ = self._inputs(root)
            request = build_protected_publisher_request(
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            runtime = validate_runtime_build_evidence(runtime_path.read_bytes(), now=NOW, schema_dir=SCHEMAS)
            publisher_value = valid_publisher(runtime)
            publisher_path = root / "publisher-evidence.json"
            publisher_path.write_bytes(canonical_json(publisher_value))
            result = validate_protected_publisher_response(
                request_raw=canonical_json(request),
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                publisher_evidence_path=publisher_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            self.assertEqual(result["status"], "PROTECTED_PUBLISHER_RESPONSE_VERIFIED")
            self.assertTrue(result["ready_for_presign"])
            self.assertEqual(result["secret_material_read"], 0)
            self.assertEqual(result["external_actions"], 0)

            tampered = copy.deepcopy(publisher_value)
            tampered["release"]["archive_sha256"] = "sha256:" + "f" * 64
            publisher_path.write_bytes(canonical_json(tampered))
            with self.assertRaises(JobOpsError) as caught:
                validate_protected_publisher_response(
                    request_raw=canonical_json(request),
                    archive_path=archive,
                    runtime_build_evidence_path=runtime_path,
                    publisher_evidence_path=publisher_path,
                    now=NOW,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "PUBLISHER_EVIDENCE_BINDING_MISMATCH")

    def test_noncanonical_duplicate_and_policy_tamper_requests_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, runtime_path, _ = self._inputs(root)
            request = build_protected_publisher_request(
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            raw = canonical_json(request)
            variants = [
                json.dumps(request, indent=2).encode("utf-8"),
                raw + b"\n",
                raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1),
            ]
            for variant in variants:
                with self.subTest(size=len(variant)), self.assertRaises(JobOpsError):
                    validate_protected_publisher_request(variant, schema_dir=SCHEMAS)

            changed = copy.deepcopy(request)
            changed["pinned_policy"]["update_channel_sha256"] = "sha256:" + "f" * 64
            with self.assertRaises(JobOpsError) as caught:
                validate_protected_publisher_request(canonical_json(changed), schema_dir=SCHEMAS)
            self.assertEqual(caught.exception.code, "PROTECTED_PUBLISHER_POLICY_MISMATCH")

    def test_response_rejects_a_request_rebound_to_other_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, runtime_path, _ = self._inputs(root)
            request = build_protected_publisher_request(
                archive_path=archive,
                runtime_build_evidence_path=runtime_path,
                now=NOW,
                schema_dir=SCHEMAS,
            )
            changed = copy.deepcopy(request)
            changed["archive"]["bytes"] += 1
            runtime = validate_runtime_build_evidence(runtime_path.read_bytes(), now=NOW, schema_dir=SCHEMAS)
            publisher_path = root / "publisher-evidence.json"
            publisher_path.write_bytes(canonical_json(valid_publisher(runtime)))
            with self.assertRaises(JobOpsError) as caught:
                validate_protected_publisher_response(
                    request_raw=canonical_json(changed),
                    archive_path=archive,
                    runtime_build_evidence_path=runtime_path,
                    publisher_evidence_path=publisher_path,
                    now=NOW,
                    schema_dir=SCHEMAS,
                )
            self.assertEqual(caught.exception.code, "PROTECTED_PUBLISHER_REQUEST_BINDING_MISMATCH")


if __name__ == "__main__":
    unittest.main()
