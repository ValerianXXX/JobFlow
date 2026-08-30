from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import ctypes
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import jobops.runtime_inputs as runtime_inputs

from jobops.runtime_inputs import (
    FetchResponse,
    RuntimeInputError,
    acquire_runtime_inputs,
    verify_runtime_inputs,
)


PROJECT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


class RuntimeInputTests(unittest.TestCase):
    ARTIFACT = b"python-runtime"
    SIGSTORE = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}'
    RUNTIME_WHEEL = b"runtime-wheel"
    BUILD_WHEEL = b"build-wheel"

    def _fixture(self, root: Path) -> tuple[Path, dict[str, bytes]]:
        project = root / "project"
        config = project / "config"
        config.mkdir(parents=True)
        for name in ("release-toolchain.json", "python-support-policy.json"):
            shutil.copy2(PROJECT / "config" / name, config / name)

        runtime_filename = "demo_runtime-1.0-cp313-cp313-win_amd64.whl"
        build_filename = "pip-26.2.1-py3-none-any.whl"
        runtime_lock = {
            "schema_version": 1,
            "lock_type": "runtime-wheelhouse",
            "python_tag": "cp313",
            "abi": "cp313-or-abi3",
            "platform": "win_amd64",
            "only_binary": True,
            "packages": [
                {
                    "name": "demo-runtime",
                    "version": "1.0",
                    "filename": runtime_filename,
                    "size": len(self.RUNTIME_WHEEL),
                    "sha256": _sha256(self.RUNTIME_WHEEL),
                }
            ],
        }
        build_lock = {
            "schema_version": 1,
            "lock_type": "protected-builder-wheelhouse",
            "python_tag": "py3",
            "platform": "any",
            "only_binary": True,
            "packages": [
                {
                    "name": "pip",
                    "version": "26.2.1",
                    "filename": build_filename,
                    "size": len(self.BUILD_WHEEL),
                    "sha256": _sha256(self.BUILD_WHEEL),
                }
            ],
        }
        runtime_bytes = _json_bytes(runtime_lock)
        build_bytes = _json_bytes(build_lock)
        (config / "windows-cp313-runtime.lock").write_bytes(runtime_bytes)
        (config / "windows-cp313-build.lock").write_bytes(build_bytes)

        source = json.loads(
            (PROJECT / "config" / "windows-runtime-source.json").read_text(encoding="utf-8")
        )
        python = source["python"]
        python["artifact_bytes"] = len(self.ARTIFACT)
        python["artifact_sha256"] = _sha256(self.ARTIFACT)
        python["sigstore_bundle_bytes"] = len(self.SIGSTORE)
        python["sigstore_bundle_sha256"] = _sha256(self.SIGSTORE)
        source["builder"]["runtime_lock_sha256"] = _sha256(runtime_bytes)
        source["builder"]["build_lock_sha256"] = _sha256(build_bytes)
        (config / "windows-runtime-source.json").write_bytes(_json_bytes(source))

        runtime_url = "https://files.pythonhosted.org/packages/aa/" + runtime_filename
        build_url = "https://files.pythonhosted.org/packages/bb/" + build_filename

        def metadata(filename: str, payload: bytes, url: str) -> bytes:
            return json.dumps(
                {
                    "urls": [
                        {
                            "filename": filename,
                            "packagetype": "bdist_wheel",
                            "size": len(payload),
                            "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
                            "url": url,
                        }
                    ]
                },
                separators=(",", ":"),
            ).encode("utf-8")

        responses = {
            python["artifact_url"]: self.ARTIFACT,
            python["sigstore_bundle_url"]: self.SIGSTORE,
            "https://pypi.org/pypi/demo-runtime/1.0/json": metadata(
                runtime_filename, self.RUNTIME_WHEEL, runtime_url
            ),
            runtime_url: self.RUNTIME_WHEEL,
            "https://pypi.org/pypi/pip/26.2.1/json": metadata(
                build_filename, self.BUILD_WHEEL, build_url
            ),
            build_url: self.BUILD_WHEEL,
        }
        return project, responses

    @staticmethod
    def _fetcher(responses: dict[str, bytes]):
        def fetch(url: str, maximum_bytes: int) -> FetchResponse:
            if url not in responses:
                raise AssertionError(f"unexpected public URL: {url}")
            body = responses[url]
            if len(body) > maximum_bytes:
                raise AssertionError("test response exceeded caller bound")
            headers = {
                "content-length": str(len(body)),
                "content-type": (
                    "application/json"
                    if url.endswith("/json")
                    else "application/octet-stream"
                    if url.endswith(".sigstore")
                    else "application/octet-stream"
                ),
            }
            return FetchResponse(status=200, url=url, headers=headers, body=body)

        return fetch

    def test_network_requires_explicit_opt_in_before_any_destination_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, _ = self._fixture(root)
            destination = root / "inputs"
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_NETWORK_OPT_IN_REQUIRED"):
                acquire_runtime_inputs(project, destination, allow_network=False)
            self.assertFalse(destination.exists())

    def test_acquire_and_verify_exact_pinned_bundle_without_real_network(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            result = acquire_runtime_inputs(
                project,
                destination,
                allow_network=True,
                fetch=self._fetcher(responses),
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["format"], "JOBFLOW_RUNTIME_INPUT_BUNDLE_V1")
            self.assertEqual(result["file_count"], 7)
            self.assertTrue(result["network_opt_in"])
            self.assertEqual(result["recruitment_external_actions"], 0)
            self.assertFalse(result["engineering_network_used"])
            self.assertEqual(result["network_transport"], "SIMULATED_FETCH")
            self.assertEqual(result["network_request_count"], 6)
            self.assertEqual(
                result["network_hosts"],
                ["files.pythonhosted.org", "pypi.org", "www.python.org"],
            )
            self.assertTrue(
                (destination / "wheelhouse" / "demo_runtime-1.0-cp313-cp313-win_amd64.whl").is_file()
            )
            self.assertTrue((destination / "wheelhouse" / "pip-26.2.1-py3-none-any.whl").is_file())
            verified = verify_runtime_inputs(project, destination)
            self.assertEqual(verified["status"], "PASS")
            manifest = json.loads((destination / "runtime-inputs.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["network_policy"]["redirects_allowed"])
            self.assertFalse(manifest["network_policy"]["proxy_environment_used"])
            self.assertNotIn(str(root), json.dumps(manifest))

    def test_policy_lock_digest_drift_fails_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            source_path = project / "config" / "windows-runtime-source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["builder"]["runtime_lock_sha256"] = "sha256:" + "0" * 64
            source_path.write_bytes(_json_bytes(source))
            called = False

            def fetch(url: str, maximum_bytes: int) -> FetchResponse:
                nonlocal called
                called = True
                return self._fetcher(responses)(url, maximum_bytes)

            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_POLICY_INVALID"):
                acquire_runtime_inputs(
                    project,
                    root / "inputs",
                    allow_network=True,
                    fetch=fetch,
                )
            self.assertFalse(called)

    def test_redirect_or_changed_final_url_is_rejected_and_staging_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            base = self._fetcher(responses)
            artifact_url = json.loads(
                (project / "config" / "windows-runtime-source.json").read_text(encoding="utf-8")
            )["python"]["artifact_url"]

            def redirected(url: str, maximum_bytes: int) -> FetchResponse:
                response = base(url, maximum_bytes)
                if url == artifact_url:
                    return FetchResponse(
                        status=200,
                        url="https://www.python.org/redirected/runtime.zip",
                        headers=response.headers,
                        body=response.body,
                    )
                return response

            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_RESPONSE_INVALID"):
                acquire_runtime_inputs(
                    project,
                    destination,
                    allow_network=True,
                    fetch=redirected,
                )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".inputs.jfi-*")), [])

    def test_wrong_payload_digest_is_rejected_and_staging_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            base = self._fetcher(responses)
            artifact_url = json.loads(
                (project / "config" / "windows-runtime-source.json").read_text(encoding="utf-8")
            )["python"]["artifact_url"]

            def tampered(url: str, maximum_bytes: int) -> FetchResponse:
                response = base(url, maximum_bytes)
                if url == artifact_url:
                    body = b"X" + response.body[1:]
                    return FetchResponse(
                        status=200,
                        url=url,
                        headers={**response.headers, "content-length": str(len(body))},
                        body=body,
                    )
                return response

            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_DIGEST_MISMATCH"):
                acquire_runtime_inputs(
                    project,
                    destination,
                    allow_network=True,
                    fetch=tampered,
                )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".inputs.jfi-*")), [])

    def test_sigstore_transport_media_type_must_match_pinned_server_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            base = self._fetcher(responses)
            sigstore_url = json.loads(
                (project / "config" / "windows-runtime-source.json").read_text(encoding="utf-8")
            )["python"]["sigstore_bundle_url"]

            def wrong_transport(url: str, maximum_bytes: int) -> FetchResponse:
                response = base(url, maximum_bytes)
                if url == sigstore_url:
                    return FetchResponse(
                        status=response.status,
                        url=response.url,
                        headers={**response.headers, "content-type": "text/plain"},
                        body=response.body,
                    )
                return response

            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_MEDIA_TYPE_MISMATCH"):
                acquire_runtime_inputs(
                    project,
                    destination,
                    allow_network=True,
                    fetch=wrong_transport,
                )
            self.assertFalse(destination.exists())

    def test_sigstore_payload_media_type_is_verified_independently(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            source_path = project / "config" / "windows-runtime-source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            sigstore_url = source["python"]["sigstore_bundle_url"]
            wrong_payload = b'{"mediaType":"application/example+json"}'
            responses[sigstore_url] = wrong_payload
            source["python"]["sigstore_bundle_bytes"] = len(wrong_payload)
            source["python"]["sigstore_bundle_sha256"] = _sha256(wrong_payload)
            source_path.write_bytes(_json_bytes(source))

            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_SIGSTORE_INVALID"):
                acquire_runtime_inputs(
                    project,
                    root / "inputs",
                    allow_network=True,
                    fetch=self._fetcher(responses),
                )

    def test_pypi_metadata_cannot_redirect_wheel_to_another_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            metadata_url = "https://pypi.org/pypi/demo-runtime/1.0/json"
            value = json.loads(responses[metadata_url])
            value["urls"][0]["url"] = "https://example.invalid/demo.whl"
            responses[metadata_url] = json.dumps(value).encode("utf-8")
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_URL_INVALID"):
                acquire_runtime_inputs(
                    project,
                    root / "inputs",
                    allow_network=True,
                    fetch=self._fetcher(responses),
                )

    def test_existing_destination_and_extra_bundle_file_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_DESTINATION_INVALID"):
                acquire_runtime_inputs(
                    project,
                    destination,
                    allow_network=True,
                    fetch=self._fetcher(responses),
                )
            destination.rmdir()
            acquire_runtime_inputs(
                project,
                destination,
                allow_network=True,
                fetch=self._fetcher(responses),
            )
            (destination / "unexpected.bin").write_bytes(b"unexpected")
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_BUNDLE_INVALID"):
                verify_runtime_inputs(project, destination)

    def test_noncanonical_or_duplicate_key_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            acquire_runtime_inputs(
                project, destination, allow_network=True, fetch=self._fetcher(responses)
            )
            manifest_path = destination / "runtime-inputs.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_path.write_bytes(_json_bytes(value))
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_BUNDLE_INVALID"):
                verify_runtime_inputs(project, destination)

            shutil.rmtree(destination)
            acquire_runtime_inputs(
                project, destination, allow_network=True, fetch=self._fetcher(responses)
            )
            payload = (destination / "runtime-inputs.json").read_bytes()
            duplicate = payload.replace(b'"files":', b'"format":"duplicate","files":', 1)
            (destination / "runtime-inputs.json").write_bytes(duplicate)
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_BUNDLE_INVALID"):
                verify_runtime_inputs(project, destination)

    def test_pep503_duplicate_package_and_policy_extra_field_fail_before_fetch(self) -> None:
        for mutate in ("duplicate-package", "extra-field"):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                project, responses = self._fixture(root)
                if mutate == "duplicate-package":
                    lock_path = project / "config" / "windows-cp313-build.lock"
                    value = json.loads(lock_path.read_text(encoding="utf-8"))
                    value["packages"][0]["name"] = "demo_runtime"
                    lock_path.write_bytes(_json_bytes(value))
                    source_path = project / "config" / "windows-runtime-source.json"
                    source = json.loads(source_path.read_text(encoding="utf-8"))
                    source["builder"]["build_lock_sha256"] = _sha256(lock_path.read_bytes())
                    source_path.write_bytes(_json_bytes(source))
                else:
                    source_path = project / "config" / "windows-runtime-source.json"
                    source = json.loads(source_path.read_text(encoding="utf-8"))
                    source["unexpected"] = True
                    source_path.write_bytes(_json_bytes(source))
                called = False

                def fetch(url: str, maximum_bytes: int) -> FetchResponse:
                    nonlocal called
                    called = True
                    return self._fetcher(responses)(url, maximum_bytes)

                expected_failure = (
                    "RUNTIME_INPUT_LOCK_INVALID"
                    if mutate == "duplicate-package"
                    else "RUNTIME_INPUT_POLICY_INVALID"
                )
                with self.assertRaisesRegex(RuntimeInputError, expected_failure):
                    acquire_runtime_inputs(
                        project, root / "inputs", allow_network=True, fetch=fetch
                    )
                self.assertFalse(called)

    @unittest.skipUnless(os.name == "nt", "Windows hard-link semantics")
    def test_hardlinked_bundle_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            acquire_runtime_inputs(
                project, destination, allow_network=True, fetch=self._fetcher(responses)
            )
            source = destination / "wheelhouse" / "demo_runtime-1.0-cp313-cp313-win_amd64.whl"
            os.link(source, root / "second-link.whl")
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_BUNDLE_INVALID"):
                verify_runtime_inputs(project, destination)

    def test_exclusive_write_never_overwrites_existing_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "policy" / "value.json"
            target.parent.mkdir()
            target.write_bytes(b"sentinel")
            with self.assertRaisesRegex(RuntimeInputError, "RUNTIME_INPUT_DESTINATION_INVALID"):
                runtime_inputs._safe_write(root, "policy/value.json", b"replacement")
            self.assertEqual(target.read_bytes(), b"sentinel")

    @unittest.skipUnless(os.name == "nt", "Windows FILE_RENAME_INFO contract")
    def test_windows_rename_buffer_has_an_excluded_utf16_terminator(self) -> None:
        captured: dict[str, bytes] = {}

        class RenameFunction:
            argtypes: object = None
            restype: object = None

            def __call__(self, handle: object, info_class: int, buffer: object, size: int) -> int:
                captured["payload"] = ctypes.string_at(buffer, size)
                return 1

        class Kernel32:
            SetFileInformationByHandle = RenameFunction()

        parent = Path("C:/JobFlow/runtime-inputs")
        with (
            patch.object(runtime_inputs, "_win_final_path", return_value=parent),
            patch.object(runtime_inputs.ctypes, "WinDLL", return_value=Kernel32()),
        ):
            runtime_inputs._win_rename_relative(
                1,
                2,
                "python-3.13.15-v1",
                "RUNTIME_INPUT_DESTINATION_INVALID",
            )

        payload = captured["payload"]
        pointer_offset = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
        length_offset = pointer_offset + ctypes.sizeof(ctypes.c_void_p)
        name_offset = length_offset + 4
        filename_bytes = struct.unpack_from("<I", payload, length_offset)[0]
        expected = str(parent / "python-3.13.15-v1").encode("utf-16-le")
        self.assertEqual(filename_bytes, len(expected))
        self.assertEqual(payload[name_offset : name_offset + filename_bytes], expected)
        self.assertEqual(payload[name_offset + filename_bytes :], b"\x00\x00")

    @unittest.skipUnless(os.name == "nt", "Windows exact-name commit semantics")
    def test_windows_commit_preserves_the_exact_destination_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            destination = parent / "python-3.13.15-v1"
            staging = runtime_inputs._RuntimeInputStaging(parent, destination.name)
            try:
                staging.write("policy/value.json", b"{}")
                staging.commit(destination)
                self.assertEqual(staging.path, destination)
                self.assertEqual([entry.name for entry in parent.iterdir()], [destination.name])
                self.assertTrue((destination / "policy" / "value.json").is_file())
            finally:
                staging.cleanup()
                staging.release()
            self.assertEqual(list(parent.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows identity-bound cleanup")
    def test_post_commit_verification_failure_removes_only_owned_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project, responses = self._fixture(root)
            destination = root / "inputs"
            original = runtime_inputs.verify_runtime_inputs
            calls = 0

            def fail_after_commit(project_path: Path, bundle: Path) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original(project_path, bundle)
                if bundle == destination:
                    raise RuntimeInputError("POST_COMMIT_TEST_FAILURE")
                return result

            with patch.object(runtime_inputs, "verify_runtime_inputs", side_effect=fail_after_commit):
                with self.assertRaisesRegex(RuntimeInputError, "POST_COMMIT_TEST_FAILURE"):
                    acquire_runtime_inputs(
                        project,
                        destination,
                        allow_network=True,
                        fetch=self._fetcher(responses),
                    )
            self.assertGreaterEqual(calls, 2)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".inputs.jfi-*")), [])

    def test_cli_redacts_invalid_project_path_and_unexpected_failures(self) -> None:
        private_marker = "PRIVATE_SENTINEL_PATH"
        output = io.StringIO()
        with redirect_stdout(output):
            status = runtime_inputs.main(
                ["--project", private_marker, "verify", "--bundle", private_marker]
            )
        self.assertEqual(status, 1)
        self.assertNotIn(private_marker, output.getvalue())
        self.assertIn("RUNTIME_INPUT_OPERATION_FAILED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
