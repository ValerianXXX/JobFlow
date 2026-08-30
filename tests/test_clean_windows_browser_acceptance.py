from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jobops.clean_windows_acceptance import (
    BrowserAcceptanceProbe,
    _browser_signer_matches,
    _locked_authenticated_browser,
)
from jobops.companion_binding import (
    BINDING_ALGORITHM,
    BINDING_SCHEMA_VERSION,
    canonical_pair_message,
)
from jobops.errors import JobOpsError


VERSION = "0.9.2"
CHROME_ID = "pgcnlkfakkacphkdojdbphccjnbbefic"
EDGE_ID = "cebejbohadiofomfiplljnpdefjeiccp"
DEV_ID = "hhlliaaafegldkmcgmaoaelabipcaooj"
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
EDGE_UA = CHROME_UA + " Edg/140.0.0.0"


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class CleanWindowsBrowserAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-clean-browser-")
        root = Path(self.temporary.name)
        self.project = root / "project"
        (self.project / "browser-companion").mkdir(parents=True)
        (self.project / "config").mkdir()
        (self.project / "browser-companion" / "manifest.json").write_text(
            json.dumps({"manifest_version": 3, "version": VERSION}),
            encoding="utf-8",
        )
        (self.project / "config" / "browser-companion-stores.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_host_name": "com.jobflow.browser_companion",
                    "chrome_web_store_url": f"https://chromewebstore.google.com/detail/{CHROME_ID}",
                    "edge_addons_url": f"https://microsoftedge.microsoft.com/addons/detail/{EDGE_ID}",
                    "extension_ids": [DEV_ID, CHROME_ID, EDGE_ID],
                }
            ),
            encoding="utf-8",
        )
        self.local_app_data = root / "local-app-data"
        binding_root = self.local_app_data / "JobOps"
        binding_root.mkdir(parents=True)
        self.installation_id = "a" * 32
        self.secret = b"s" * 32
        (binding_root / "browser-companion-binding.json").write_text(
            json.dumps(
                {
                    "schema_version": BINDING_SCHEMA_VERSION,
                    "installation_id": self.installation_id,
                    "secret_b64url": b64url(self.secret),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        self.probe = BrowserAcceptanceProbe(self.project, local_app_data=self.local_app_data).start()

    def tearDown(self) -> None:
        self.probe.close()
        self.temporary.cleanup()

    def request_binding(self, challenge: bytes = b"c" * 32) -> dict[str, object]:
        return {
            "schema_version": BINDING_SCHEMA_VERSION,
            "algorithm": BINDING_ALGORITHM,
            "installation_id": self.installation_id,
            "challenge": b64url(challenge),
        }

    def post_pair(
        self,
        channel: str,
        *,
        extension_id: str | None = None,
        version: str = VERSION,
        user_agent: str | None = None,
        payload: dict[str, object] | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, object]]:
        extension_id = extension_id or (CHROME_ID if channel == "chrome" else EDGE_ID)
        user_agent = user_agent or (CHROME_UA if channel == "chrome" else EDGE_UA)
        payload = payload or {
            "protocol_version": 2,
            "extension_version": version,
            "companion_binding": self.request_binding(),
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.probe._server.server_port, timeout=3)
        connection.request(
            "POST",
            self.probe._pair_paths[channel],
            body=raw,
            headers={
                "Content-Type": content_type,
                "Origin": f"chrome-extension://{extension_id}",
                "User-Agent": user_agent,
                "Sec-CH-UA": '"Chromium";v="140", "Google Chrome";v="140"' if channel == "chrome" else '"Chromium";v="140", "Microsoft Edge";v="140"',
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, body

    def test_exact_store_extensions_and_native_binding_are_observed(self) -> None:
        chrome_status, chrome = self.post_pair("chrome")
        edge_status, _edge = self.post_pair("edge", payload={
            "protocol_version": 2,
            "extension_version": VERSION,
            "companion_binding": self.request_binding(b"e" * 32),
        })
        self.assertEqual((chrome_status, edge_status), (200, 200))
        proof = chrome["companion_binding"]
        message = canonical_pair_message(
            protocol_version=2,
            extension_version=VERSION,
            base_url=self.probe.base_url,
            assist_path=self.probe._pair_paths["chrome"].removesuffix("/pair"),
            installation_id=self.installation_id,
            challenge=str(proof["challenge"]),
            response=chrome,
        )
        self.assertEqual(
            proof["proof"],
            b64url(hmac.new(self.secret, message, hashlib.sha256).digest()),
        )
        observation = self.probe.wait(1)
        self.assertEqual(observation["status"], "PASS")
        self.assertEqual(observation["browser_companion"]["version"], VERSION)
        self.assertEqual(observation["safety"]["external_actions"], 0)
        serialized = json.dumps(observation)
        self.assertNotIn(self.installation_id, serialized)
        self.assertNotIn(b64url(self.secret), serialized)
        self.assertNotIn("chrome-extension", serialized)
        self.assertNotIn(str(self.local_app_data), serialized)

    def test_acceptance_page_contains_only_the_expected_store_channel(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.probe._server.server_port, timeout=3)
        connection.request("GET", self.probe._page_paths["chrome"])
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn(CHROME_ID, body)
        self.assertNotIn(EDGE_ID, body)
        self.assertNotIn(DEV_ID, body)
        self.assertIn("不会打开或读取招聘网站", body)
        self.assertNotIn(str(self.local_app_data), body)

    def test_development_extension_and_wrong_browser_cannot_satisfy_a_channel(self) -> None:
        status, body = self.post_pair("chrome", extension_id=DEV_ID)
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
        status, body = self.post_pair("chrome", user_agent=EDGE_UA)
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "CLEAN_WINDOWS_BROWSER_IDENTITY_MISMATCH")
        with self.assertRaises(JobOpsError) as timeout:
            self.probe.wait(0.02)
        self.assertEqual(timeout.exception.code, "CLEAN_WINDOWS_BROWSER_ACCEPTANCE_TIMEOUT")

    def test_wrong_version_protocol_keys_and_content_type_fail_closed(self) -> None:
        cases = [
            ({"protocol_version": 2, "extension_version": "0.9.1", "companion_binding": self.request_binding()}, "application/json", "CLEAN_WINDOWS_EXTENSION_VERSION_MISMATCH"),
            ({"protocol_version": 1, "extension_version": VERSION, "companion_binding": self.request_binding()}, "application/json", "CLEAN_WINDOWS_PROTOCOL_VERSION_MISMATCH"),
            ({"protocol_version": 2, "extension_version": VERSION, "companion_binding": self.request_binding(), "extra": True}, "application/json", "CLEAN_WINDOWS_PAIR_SCHEMA_INVALID"),
            ({"protocol_version": 2, "extension_version": VERSION, "companion_binding": self.request_binding()}, "text/plain", "CLEAN_WINDOWS_PAIR_CONTENT_TYPE_INVALID"),
        ]
        for payload, content_type, expected_code in cases:
            with self.subTest(payload=payload, content_type=content_type):
                status, body = self.post_pair("chrome", payload=payload, content_type=content_type)
                self.assertGreaterEqual(status, 400)
                self.assertEqual(body["status"], "BLOCKED")
                self.assertEqual(body["code"], expected_code)
        with self.assertRaises(JobOpsError):
            self.probe.wait(0.02)

    def test_store_policy_requires_exact_official_hosts_and_string_ids(self) -> None:
        stores_path = self.project / "config" / "browser-companion-stores.json"
        base = json.loads(stores_path.read_text(encoding="utf-8"))
        invalid_values = [
            {**base, "chrome_web_store_url": f"https://example.com/detail/{CHROME_ID}"},
            {**base, "edge_addons_url": f"https://microsoftedge.microsoft.com.evil.invalid/addons/detail/{EDGE_ID}"},
            {**base, "chrome_web_store_url": f"https://chromewebstore.google.com/detail/{CHROME_ID}?next=evil"},
            {**base, "extension_ids": [DEV_ID, CHROME_ID, {"id": EDGE_ID}]},
            {**base, "extension_ids": [DEV_ID, CHROME_ID, EDGE_ID, EDGE_ID]},
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                stores_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(JobOpsError) as blocked:
                    BrowserAcceptanceProbe(self.project, local_app_data=self.local_app_data)
                self.assertEqual(blocked.exception.code, "CLEAN_WINDOWS_STORE_POLICY_INVALID")
        stores_path.write_text(json.dumps(base), encoding="utf-8")

    def test_duplicate_same_channel_does_not_substitute_for_edge(self) -> None:
        self.assertEqual(self.post_pair("chrome")[0], 200)
        self.assertEqual(self.post_pair("chrome", payload={
            "protocol_version": 2,
            "extension_version": VERSION,
            "companion_binding": self.request_binding(b"d" * 32),
        })[0], 200)
        with self.assertRaises(JobOpsError) as timeout:
            self.probe.wait(0.02)
        self.assertEqual(timeout.exception.details["observed_channels"], 1)

    def test_browser_launcher_binds_each_registered_executable_to_its_own_route(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def resolver(name: str) -> Path:
            return Path("C:/trusted") / name

        def launcher(command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append((command, kwargs))
            return SimpleNamespace()

        @contextmanager
        def trusted(path: Path, *, channel: str):
            self.assertEqual(path.name, "chrome.exe" if channel == "chrome" else "msedge.exe")
            yield path

        with patch("jobops.clean_windows_acceptance._locked_authenticated_browser", trusted):
            self.assertEqual(
                self.probe.open_browsers(launcher=launcher, resolver=resolver),
                ("chrome", "edge"),
            )
        self.assertEqual(Path(calls[0][0][0]).name, "chrome.exe")
        self.assertEqual(Path(calls[1][0][0]).name, "msedge.exe")
        self.assertEqual(calls[0][0][-1], self.probe.page_url("chrome"))
        self.assertEqual(calls[1][0][-1], self.probe.page_url("edge"))
        self.assertNotEqual(calls[0][0][-1], calls[1][0][-1])
        self.assertIn("creationflags", calls[0][1])

    def test_browser_publishers_are_exactly_pinned(self) -> None:
        self.assertTrue(_browser_signer_matches("chrome", "CN=Google LLC, O=Google LLC, C=US"))
        self.assertTrue(
            _browser_signer_matches(
                "edge",
                "CN=Microsoft Corporation, O=Microsoft Corporation, C=US",
            )
        )
        self.assertFalse(_browser_signer_matches("chrome", "CN=Google LLC Evil, O=Google LLC"))
        self.assertFalse(_browser_signer_matches("edge", "CN=Microsoft Corporation, O=Other"))
        self.assertFalse(_browser_signer_matches("unknown", "CN=Google LLC, O=Google LLC"))

    def test_browser_executable_is_locked_and_authenticated_before_launch(self) -> None:
        browser = Path(self.temporary.name) / "chrome.exe"
        browser.write_bytes(b"signed-browser-placeholder")
        handles = iter(range(100, 200))
        closed: list[int] = []
        with (
            patch("jobops.clean_windows_acceptance._has_absolute_reparse_component", return_value=False),
            patch("jobops.clean_windows_acceptance._open_locked_directory", side_effect=lambda _path: next(handles)),
            patch("jobops.clean_windows_acceptance._open_locked_read", return_value=999),
            patch("jobops.clean_windows_acceptance._handle_information", return_value=(7, 8, 9)) as identity,
            patch("jobops.clean_windows_acceptance._windows_signature_valid", return_value=True),
            patch(
                "jobops.clean_windows_acceptance._embedded_signer_identity",
                return_value=("CN=Google LLC, O=Google LLC, C=US", "A" * 40),
            ),
            patch("jobops.clean_windows_acceptance._close_handle", side_effect=closed.append),
        ):
            with _locked_authenticated_browser(browser, channel="chrome") as trusted:
                self.assertEqual(trusted, browser.resolve())
        self.assertEqual(identity.call_count, 2)
        self.assertIn(999, closed)

    def test_unsigned_wrong_publisher_and_reparse_browsers_fail_closed(self) -> None:
        browser = Path(self.temporary.name) / "chrome.exe"
        browser.write_bytes(b"browser-placeholder")

        def attempt(*, signature: bool, subject: str, reparse: bool = False) -> str:
            with (
                patch("jobops.clean_windows_acceptance._has_absolute_reparse_component", return_value=reparse),
                patch("jobops.clean_windows_acceptance._open_locked_directory", return_value=101),
                patch("jobops.clean_windows_acceptance._open_locked_read", return_value=999),
                patch("jobops.clean_windows_acceptance._handle_information", return_value=(7, 8, 9)),
                patch("jobops.clean_windows_acceptance._windows_signature_valid", return_value=signature),
                patch(
                    "jobops.clean_windows_acceptance._embedded_signer_identity",
                    return_value=(subject, "A" * 40),
                ),
                patch("jobops.clean_windows_acceptance._close_handle"),
            ):
                with self.assertRaises(JobOpsError) as blocked:
                    with _locked_authenticated_browser(browser, channel="chrome"):
                        pass
                return blocked.exception.code

        self.assertEqual(
            attempt(signature=False, subject="CN=Google LLC, O=Google LLC"),
            "CLEAN_WINDOWS_BROWSER_SIGNATURE_INVALID",
        )
        self.assertEqual(
            attempt(signature=True, subject="CN=Other, O=Other"),
            "CLEAN_WINDOWS_BROWSER_PUBLISHER_INVALID",
        )
        self.assertEqual(
            attempt(signature=True, subject="CN=Google LLC, O=Google LLC", reparse=True),
            "CLEAN_WINDOWS_BROWSER_IDENTITY_UNSAFE",
        )

    def test_timeout_never_returns_partial_pass(self) -> None:
        self.assertEqual(self.post_pair("chrome")[0], 200)
        with self.assertRaises(JobOpsError) as timeout:
            self.probe.wait(0.02)
        self.assertEqual(timeout.exception.code, "CLEAN_WINDOWS_BROWSER_ACCEPTANCE_TIMEOUT")
        self.assertFalse(hasattr(timeout.exception, "observation"))


if __name__ == "__main__":
    unittest.main()
