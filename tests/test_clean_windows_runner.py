from __future__ import annotations

import inspect
import io
import json
import tempfile
import unittest
import warnings
import zipfile
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from jobops.clean_windows_runner import (
    _CONTROL_FILES,
    _REQUIRED_CONTROL_MEMBERS,
    CommandResult,
    ReleaseMaterial,
    WindowsAcceptanceBackend,
    _extract_control_plane,
    _orchestrate,
    _read_startup_readiness,
    _validated_readiness,
    _validated_session_url,
    _write_evidence,
    run_clean_windows_acceptance,
)
from jobops.cli import main
from jobops.errors import JobOpsError
from jobops.publisher_attestation import EvidenceDocument
from jobops.util import canonical_json, sha256_bytes


VERSION = "0.6.0"
PREDECESSOR = "0.5.1"
COMMIT = "a" * 40
PREDECESSOR_COMMIT = "b" * 40
H = "sha256:" + "1" * 64
KEY = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"


def evidence(name: str, value: dict[str, object]) -> EvidenceDocument:
    raw = canonical_json(value)
    return EvidenceDocument(name, raw, sha256_bytes(raw))


def bundle(version: str, commit: str, marker: str) -> dict[str, object]:
    return {
        "status": "RELEASE_BUNDLE_VERIFIED",
        "available_version": version,
        "asset_name": f"JobFlow-v{version}-windows-x64-complete.zip",
        "asset_bytes": 8,
        "asset_sha256": "sha256:" + marker * 64,
        "archive_prefix": f"JobFlow-v{version}-windows-x64/",
        "commit": commit,
        "release_platform": "windows-x64",
        "runtime_closure_manifest_sha256": "sha256:" + "2" * 64,
        "runtime_tree_sha256": "sha256:" + "3" * 64,
        "manifest_sha256": "sha256:" + "4" * 64,
        "signature_sha256": "sha256:" + "5" * 64,
        "publisher_evidence_sha256": "PUBLISHER_PLACEHOLDER",
        "key_id": KEY,
    }


class FakeBackend:
    events: list[tuple[object, ...]] = []

    def __init__(self, _temporary_root: Path) -> None:
        type(self).events = []

    def preflight(self) -> None:
        self.events.append(("preflight",))

    def install(self, control_root: Path, archive: Path) -> None:
        self.events.append(("install", control_root.name, archive.name))

    def pointer(self, *, version: str, commit: str, previous: bool = False) -> dict[str, object]:
        self.events.append(("pointer", version, commit, previous))
        return {"version": version, "source_commit": commit}

    def health(self, *, version: str) -> None:
        self.events.append(("health", version))

    def startup(self) -> None:
        self.events.append(("startup",))

    def native_host(self, _project: Path) -> None:
        self.events.append(("native_host",))

    def browsers(self, _project: Path, timeout_seconds: int) -> dict[str, object]:
        self.events.append(("browsers", timeout_seconds))
        return {
            "browser_companion": {
                "version": "0.9.2",
                "chrome_store_install_observed": True,
                "edge_store_install_observed": True,
                "chrome_pairing_observed": True,
                "edge_pairing_observed": True,
                "native_binding_proof_observed": True,
            }
        }

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def uninstall(self) -> None:
        self.events.append(("uninstall",))

    def assert_clean(self) -> None:
        self.events.append(("assert_clean",))


class CleanWindowsRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-clean-runner-")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.dist = self.project / "dist"
        (self.project / "schemas").mkdir(parents=True)
        (self.project / "config").mkdir()
        self.dist.mkdir()
        (self.project / ".jobops-root").write_text("jobops-root-v1\n", encoding="ascii")
        self.current = ReleaseMaterial(
            VERSION,
            self.dist / "JobFlow-update-manifest.json",
            self.dist / "JobFlow-update-manifest.sig.json",
            self.dist / f"JobFlow-v{VERSION}-windows-x64-complete.zip",
        )
        self.predecessor = ReleaseMaterial(
            PREDECESSOR,
            self.root / "predecessor-manifest.json",
            self.root / "predecessor-signature.json",
            self.root / "predecessor.zip",
        )
        for path in (
            self.current.manifest,
            self.current.signature,
            self.current.archive,
            self.predecessor.manifest,
            self.predecessor.signature,
            self.predecessor.archive,
            self.dist / "JobFlow-runtime-build-evidence.json",
            self.dist / "JobFlow-publisher-evidence.json",
        ):
            path.write_bytes(b"ordinary")
        issued = datetime.now(timezone.utc).replace(microsecond=0)
        self.runtime = evidence("runtime-build-evidence-v1", {"status": "PASS"})
        self.publisher = evidence(
            "publisher-evidence-v1",
            {
                "issued_at_utc": issued.isoformat().replace("+00:00", "Z"),
                "expires_at_utc": (issued + timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
            },
        )
        self.now = issued + timedelta(minutes=1)
        self.current_bundle = bundle(VERSION, COMMIT, "6")
        self.current_bundle["publisher_evidence_sha256"] = self.publisher.sha256
        self.predecessor_bundle = bundle(PREDECESSOR, PREDECESSOR_COMMIT, "7")
        self.output = self.root / "acceptance.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    @contextmanager
    def locked(path: Path, _label: str):
        with path.open("rb") as handle:
            yield handle

    def test_orchestration_cannot_skip_install_update_rollback_or_uninstall(self) -> None:
        clean = evidence("clean-windows-acceptance-v1", {"status": "PASS"})

        def context(*_args: object, **_kwargs: object):
            return self.runtime, self.publisher, dict(self.current_bundle)

        def stage(root: Path, label: str, *_args: object):
            controls = root / f"{label}-controls"
            archive = root / f"{label}.zip"
            controls.mkdir()
            archive.write_bytes(b"archive")
            return controls, archive

        with (
            patch("jobops.clean_windows_runner._open_locked_runtime_file", side_effect=self.locked),
            patch("jobops.clean_windows_runner._clean_import_context", side_effect=context),
            patch("jobops.clean_windows_runner.verify_signed_release_bundle", return_value=dict(self.predecessor_bundle)),
            patch("jobops.clean_windows_runner.verify_signed_update_bundle", return_value={**self.current_bundle, "status": "UPDATE_BUNDLE_VERIFIED"}),
            patch("jobops.clean_windows_runner._stage_release", side_effect=stage),
            patch("jobops.clean_windows_runner.validate_clean_windows_acceptance", return_value=clean),
        ):
            result = _orchestrate(
                self.project,
                current=self.current,
                predecessor=self.predecessor,
                commit=COMMIT,
                output=self.output,
                browser_timeout_seconds=300,
                backend_factory=FakeBackend,
                now=self.now,
            )

        self.assertEqual(result["status"], "CLEAN_WINDOWS_ACCEPTANCE_PASS")
        self.assertEqual(result["real_job_site_visits"], 0)
        event_names = [event[0] for event in FakeBackend.events]
        self.assertEqual(
            event_names,
            [
                "preflight", "install", "pointer", "health", "install", "pointer", "pointer",
                "health", "startup", "native_host", "browsers", "rollback", "pointer", "health",
                "install", "pointer", "pointer", "health", "uninstall", "assert_clean",
            ],
        )
        self.assertTrue(self.output.is_file())

    def test_public_api_accepts_no_pass_flags_or_observations(self) -> None:
        parameters = set(inspect.signature(run_clean_windows_acceptance).parameters)
        for forbidden in (
            "install_passed", "startup_passed", "health_passed", "update_passed",
            "rollback_passed", "uninstall_passed", "browser_observation",
        ):
            self.assertNotIn(forbidden, parameters)

    def test_output_is_exclusive_and_must_be_outside_project(self) -> None:
        raw = b'{"status":"PASS"}'
        existing = self.root / "existing.json"
        existing.write_bytes(b"previous")
        with self.assertRaises(JobOpsError) as collision:
            _write_evidence(existing, raw, project=self.project)
        self.assertEqual(collision.exception.code, "CLEAN_WINDOWS_OUTPUT_INVALID")
        self.assertEqual(existing.read_bytes(), b"previous")
        inside = self.project / "inside.json"
        with self.assertRaises(JobOpsError):
            _write_evidence(inside, raw, project=self.project)
        self.assertFalse(inside.exists())

    def test_control_plane_extracts_only_required_members(self) -> None:
        archive = self.root / "complete.zip"
        prefix = f"JobFlow-v{VERSION}-windows-x64/"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for relative in _REQUIRED_CONTROL_MEMBERS:
                package.writestr(prefix + relative, f"content:{relative}\n")
            package.writestr(prefix + "private/unrelated.txt", "must not extract")
        target = self.root / "control"
        target.mkdir()
        _extract_control_plane(archive, prefix, target)
        self.assertTrue((target / "scripts" / "install-jobflow-v2.ps1").is_file())
        self.assertEqual(
            sorted(path.name for path in (target / "scripts" / "windows-runtime").iterdir()),
            sorted(_CONTROL_FILES),
        )
        self.assertFalse((target / "private").exists())

    def test_duplicate_archive_member_fails_closed(self) -> None:
        archive = self.root / "duplicate.zip"
        prefix = f"JobFlow-v{VERSION}-windows-x64/"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive, "w") as package:
                for relative in _REQUIRED_CONTROL_MEMBERS:
                    package.writestr(prefix + relative, "x")
                package.writestr(prefix + ".jobops-root", "again")
        target = self.root / "duplicate-control"
        target.mkdir()
        with self.assertRaises(JobOpsError) as blocked:
            _extract_control_plane(archive, prefix, target)
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_ARCHIVE_CONTROL_INVALID")

    def test_preflight_rejects_admin_membership_even_at_medium_integrity(self) -> None:
        backend = object.__new__(WindowsAcceptanceBackend)
        backend._run = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
            0,
            b'"Medium Mandatory Level","Label","S-1-16-8192",""\n'
            b'"BUILTIN\\Administrators","Alias","S-1-5-32-544","Deny only"\n',
            b"",
        )
        backend._has_preexisting = lambda: False  # type: ignore[method-assign]
        with patch("jobops.clean_windows_runner.windows_system_directory", return_value=self.root):
            with self.assertRaises(JobOpsError) as blocked:
                backend.preflight()
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_FRESH_STANDARD_USER_REQUIRED")

    def test_preflight_accepts_medium_integrity_non_admin(self) -> None:
        backend = object.__new__(WindowsAcceptanceBackend)
        backend._run = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
            0,
            b'"Medium Mandatory Level","Label","S-1-16-8192",""\n'
            b'"Users","Alias","S-1-5-32-545","Enabled"\n',
            b"",
        )
        backend._has_preexisting = lambda: False  # type: ignore[method-assign]
        with patch("jobops.clean_windows_runner.windows_system_directory", return_value=self.root):
            backend.preflight()

    def test_scheduled_task_absence_requires_a_readable_full_inventory(self) -> None:
        backend = object.__new__(WindowsAcceptanceBackend)
        backend._run = Mock(  # type: ignore[method-assign]
            side_effect=[
                CommandResult(1, b"", b"specific query failed"),
                CommandResult(0, b'"\\Ordinary Task","N/A"\n', b""),
            ]
        )
        with patch("jobops.clean_windows_runner.windows_system_directory", return_value=self.root):
            self.assertFalse(backend._task_present())
        self.assertEqual(backend._run.call_count, 2)

    def test_scheduled_task_query_failure_never_means_absent(self) -> None:
        backend = object.__new__(WindowsAcceptanceBackend)
        backend._run = Mock(  # type: ignore[method-assign]
            side_effect=[CommandResult(1, b"", b""), CommandResult(1, b"", b"access denied")]
        )
        with patch("jobops.clean_windows_runner.windows_system_directory", return_value=self.root):
            with self.assertRaises(JobOpsError) as blocked:
                backend._task_present()
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_SCHEDULED_TASK_CHECK_FAILED")

    def test_scheduled_task_inventory_catches_an_ambiguous_exact_query(self) -> None:
        backend = object.__new__(WindowsAcceptanceBackend)
        backend._run = Mock(  # type: ignore[method-assign]
            side_effect=[
                CommandResult(1, b"", b""),
                CommandResult(0, b'"\\JobFlow Authorized Read-Only Discovery","N/A"\n', b""),
            ]
        )
        with patch("jobops.clean_windows_runner.windows_system_directory", return_value=self.root):
            self.assertTrue(backend._task_present())

    def test_startup_readiness_tolerates_only_a_still_partial_utf8_write(self) -> None:
        output = self.root / "startup.stdout"
        output.write_bytes(b"prefix\n{\"status\": \"ONBOARDING_CENTER_READY\", \"note\": \xe4")
        self.assertIsNone(_read_startup_readiness(output, final=False))
        with self.assertRaises(JobOpsError) as blocked:
            _read_startup_readiness(output, final=True)
        self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_STARTUP_OUTPUT_INVALID")
        output.write_text(
            "prefix\n" + json.dumps(
                {
                    "status": "ONBOARDING_CENTER_READY",
                    "url": "http://127.0.0.1:12345/session/" + "A" * 43 + "/",
                    "note": "双语",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        value = _read_startup_readiness(output, final=True)
        self.assertEqual(value["status"], "ONBOARDING_CENTER_READY")

    def test_startup_url_is_an_exact_unadorned_loopback_session(self) -> None:
        token = "A" * 43
        parsed = _validated_session_url(f"http://127.0.0.1:12345/session/{token}/")
        self.assertEqual(parsed.port, 12345)
        for invalid in (
            f"http://127.0.0.1:12345/session/{token}/?redirect=outside",
            f"http://127.0.0.1:12345/session/{token}/../outside/",
            f"http://user@127.0.0.1:12345/session/{token}/",
            f"http://localhost:12345/session/{token}/",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(JobOpsError) as blocked:
                _validated_session_url(invalid)
            self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_STARTUP_BINDING_INVALID")
        with self.assertRaises(JobOpsError) as invalid_port:
            _validated_session_url(f"http://127.0.0.1:not-a-port/session/{token}/")
        self.assertEqual(invalid_port.exception.code, "CLEAN_WINDOWS_STARTUP_BINDING_INVALID")

    def test_startup_readiness_requires_local_private_bilingual_metadata(self) -> None:
        token = "A" * 43
        value = {
            "status": "ONBOARDING_CENTER_READY",
            "url": f"http://127.0.0.1:12345/session/{token}/",
            "binding": "127.0.0.1",
            "supported_locales": ["zh", "en"],
            "private_values_emitted": 0,
            "real_external_actions": 0,
        }
        self.assertEqual(_validated_readiness(value).port, 12345)
        for key, replacement in (
            ("binding", "0.0.0.0"),
            ("supported_locales", ["en"]),
            ("private_values_emitted", 1),
            ("real_external_actions", 1),
        ):
            changed = dict(value)
            changed[key] = replacement
            with self.subTest(key=key), self.assertRaises(JobOpsError) as blocked:
                _validated_readiness(changed)
            self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_STARTUP_METADATA_INVALID")

    def test_cli_binds_every_signed_input_and_emits_no_paths(self) -> None:
        expected = {
            "schema_version": 1,
            "status": "CLEAN_WINDOWS_ACCEPTANCE_PASS",
            "version": VERSION,
            "source_commit": COMMIT,
            "evidence_sha256": H,
            "external_actions": 0,
            "real_job_site_visits": 0,
            "final_submit_attempts": 0,
        }
        output = io.StringIO()
        with (
            patch("jobops.cli.project_root", return_value=self.project),
            patch("jobops.cli.run_clean_windows_acceptance", return_value=expected) as runner,
            redirect_stdout(output),
        ):
            code = main(
                [
                    "run-clean-windows-acceptance",
                    "--version", VERSION,
                    "--commit", COMMIT,
                    "--predecessor-version", PREDECESSOR,
                    "--predecessor-manifest", str(self.predecessor.manifest),
                    "--predecessor-signature", str(self.predecessor.signature),
                    "--predecessor-archive", str(self.predecessor.archive),
                    "--output", str(self.output),
                    "--browser-timeout-seconds", "420",
                ]
            )
        self.assertEqual(code, 0)
        runner.assert_called_once_with(
            self.project,
            version=VERSION,
            commit=COMMIT,
            predecessor_version=PREDECESSOR,
            predecessor_manifest=self.predecessor.manifest,
            predecessor_signature=self.predecessor.signature,
            predecessor_archive=self.predecessor.archive,
            output=self.output,
            browser_timeout_seconds=420,
        )
        rendered = output.getvalue()
        self.assertIn("CLEAN_WINDOWS_ACCEPTANCE_PASS", rendered)
        self.assertIn("IMPORT_CLEAN_WINDOWS_ACCEPTANCE", rendered)
        for private_path in (self.project, self.predecessor.manifest, self.output):
            self.assertNotIn(str(private_path), rendered)


if __name__ == "__main__":
    unittest.main()
