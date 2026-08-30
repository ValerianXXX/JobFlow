from __future__ import annotations

import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jobops.errors import JobOpsError
from jobops.cli import main
from jobops.publisher_attestation import EvidenceDocument
from jobops.release_attestation import import_clean_windows_acceptance
from jobops.util import sha256_bytes


VERSION = "0.6.0"
COMMIT = "a" * 40
RAW = b'{"status":"PASS"}'


def accepted_document(raw: bytes = RAW) -> EvidenceDocument:
    return EvidenceDocument(
        schema_name="clean-windows-acceptance-v1",
        canonical_bytes=raw,
        sha256=sha256_bytes(raw),
    )


def passing_attestation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "PASS",
        "release_attestation_status": "PASS",
        "clean_windows_evidence_status": "PASS",
        "runtime_closure_status": "ATTESTED",
    }


class CleanWindowsEvidenceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-clean-import-")
        self.project = Path(self.temporary.name) / "project"
        self.dist = self.project / "dist"
        self.dist.mkdir(parents=True)
        (self.project / ".jobops-root").write_text("jobops-root-v1\n", encoding="ascii")
        self.source = Path(self.temporary.name) / "clean-evidence.json"
        self.source.write_bytes(RAW)
        self.destination = self.dist / "JobFlow-clean-windows-acceptance.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _import(self) -> dict[str, object]:
        with (
            patch(
                "jobops.release_attestation._validate_clean_import_candidate",
                return_value=accepted_document(),
            ),
            patch(
                "jobops.release_attestation.verify_public_release_attestation",
                return_value=passing_attestation(),
            ),
        ):
            return import_clean_windows_acceptance(
                self.project,
                self.source,
                version=VERSION,
                commit=COMMIT,
            )

    def test_validated_candidate_is_committed_to_the_only_fixed_name(self) -> None:
        result = self._import()
        self.assertEqual(self.destination.read_bytes(), RAW)
        self.assertEqual(result["status"], "CLEAN_WINDOWS_EVIDENCE_IMPORTED")
        self.assertEqual(result["evidence_sha256"], sha256_bytes(RAW))
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)
        serialized = repr(result)
        self.assertNotIn(str(self.project), serialized)
        self.assertNotIn(str(self.source), serialized)

    def test_invalid_candidate_never_replaces_existing_evidence(self) -> None:
        previous = b'{"previous":true}'
        self.destination.write_bytes(previous)
        with patch(
            "jobops.release_attestation._validate_clean_import_candidate",
            side_effect=JobOpsError("PUBLISHER_EVIDENCE_BINDING_MISMATCH", "mismatch"),
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "PUBLISHER_EVIDENCE_BINDING_MISMATCH")
        self.assertEqual(self.destination.read_bytes(), previous)

    def test_failed_postverification_restores_previous_evidence(self) -> None:
        previous = b'{"previous":true}'
        self.destination.write_bytes(previous)
        with (
            patch(
                "jobops.release_attestation._validate_clean_import_candidate",
                return_value=accepted_document(),
            ),
            patch(
                "jobops.release_attestation.verify_public_release_attestation",
                return_value={**passing_attestation(), "status": "BLOCKED"},
            ),
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_IMPORT_POSTVERIFY_FAILED")
        self.assertEqual(self.destination.read_bytes(), previous)

    def test_failed_postverification_removes_new_file_when_none_existed(self) -> None:
        with (
            patch(
                "jobops.release_attestation._validate_clean_import_candidate",
                return_value=accepted_document(),
            ),
            patch(
                "jobops.release_attestation.verify_public_release_attestation",
                side_effect=JobOpsError("TEST_POSTVERIFY_FAILURE", "failure"),
            ),
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_IMPORT_POSTVERIFY_FAILED")
        self.assertFalse(self.destination.exists())

    def test_failed_postverification_never_unlinks_a_swapped_hardlink(self) -> None:
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(b'{"outside":true}')

        def swap_destination(*args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            self.destination.unlink()
            try:
                os.link(outside, self.destination)
            except OSError as error:
                self.skipTest(f"hard links unavailable: {error}")
            return {**passing_attestation(), "status": "BLOCKED"}

        with (
            patch(
                "jobops.release_attestation._validate_clean_import_candidate",
                return_value=accepted_document(),
            ),
            patch(
                "jobops.release_attestation.verify_public_release_attestation",
                side_effect=swap_destination,
            ),
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_EVIDENCE_ROLLBACK_FAILED")
        self.assertEqual(outside.read_bytes(), b'{"outside":true}')
        self.assertFalse(self.destination.exists())

    def test_source_hardlink_is_rejected_before_validation(self) -> None:
        alias = Path(self.temporary.name) / "clean-evidence-alias.json"
        try:
            os.link(self.source, alias)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with patch("jobops.release_attestation._validate_clean_import_candidate") as validate:
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE")
        validate.assert_not_called()

    def test_source_reparse_signal_is_rejected_before_validation(self) -> None:
        def reparse(path: Path, stop_at: Path | None = None) -> bool:
            return stop_at is None and Path(path) == self.source

        with (
            patch("jobops.release_attestation.has_reparse_component", side_effect=reparse),
            patch("jobops.release_attestation._validate_clean_import_candidate") as validate,
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE")
        validate.assert_not_called()

    def test_existing_destination_hardlink_is_not_overwritten(self) -> None:
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(b'{"previous":true}')
        try:
            os.link(outside, self.destination)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with patch(
            "jobops.release_attestation._validate_clean_import_candidate",
            return_value=accepted_document(),
        ):
            with self.assertRaises(JobOpsError) as blocked:
                import_clean_windows_acceptance(
                    self.project,
                    self.source,
                    version=VERSION,
                    commit=COMMIT,
                )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_EVIDENCE_DESTINATION_UNSAFE")
        self.assertEqual(outside.read_bytes(), b'{"previous":true}')

    def test_authoritative_destination_cannot_be_used_as_import_source(self) -> None:
        self.destination.write_bytes(RAW)
        with self.assertRaises(JobOpsError) as blocked:
            import_clean_windows_acceptance(
                self.project,
                self.destination,
                version=VERSION,
                commit=COMMIT,
            )
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_EVIDENCE_SOURCE_UNSAFE")

    def test_release_identity_is_strictly_validated_before_file_access(self) -> None:
        for version, commit in (("../0.6.0", COMMIT), (VERSION, "A" * 40)):
            with self.subTest(version=version, commit=commit):
                with self.assertRaises(JobOpsError) as blocked:
                    import_clean_windows_acceptance(
                        self.project,
                        self.source,
                        version=version,
                        commit=commit,
                    )
                self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_IMPORT_IDENTITY_INVALID")

    def test_cli_exposes_only_redacted_import_result(self) -> None:
        expected = {
            "schema_version": 1,
            "status": "CLEAN_WINDOWS_EVIDENCE_IMPORTED",
            "version": VERSION,
            "source_commit": COMMIT,
            "evidence_sha256": sha256_bytes(RAW),
            "external_actions": 0,
            "real_external_actions": 0,
        }
        output = io.StringIO()
        with (
            patch("jobops.cli.project_root", return_value=self.project),
            patch("jobops.cli.import_clean_windows_acceptance", return_value=expected) as imported,
            redirect_stdout(output),
        ):
            code = main(
                [
                    "import-clean-windows-acceptance",
                    "--input",
                    str(self.source),
                    "--version",
                    VERSION,
                    "--commit",
                    COMMIT,
                ]
            )
        self.assertEqual(code, 0)
        imported.assert_called_once_with(
            self.project,
            self.source,
            version=VERSION,
            commit=COMMIT,
        )
        rendered = output.getvalue()
        self.assertIn("CLEAN_WINDOWS_EVIDENCE_IMPORTED", rendered)
        self.assertIn("CHECK_RELEASE_READINESS", rendered)
        self.assertNotIn(str(self.project), rendered)
        self.assertNotIn(str(self.source), rendered)


if __name__ == "__main__":
    unittest.main()
