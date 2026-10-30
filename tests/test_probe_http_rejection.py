"""Frozen rejection-response and bounded-read contracts; no real browser proof."""
from email.message import Message
from http import HTTPStatus
import http.client
import io
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import _support
import test_clean_windows_browser_acceptance as fixtures
from jobops.clean_windows_acceptance import _ProbeHandler
from jobops.errors import JobOpsError
from jobops.onboarding_server import OnboardingRequestHandler


class SocketState:
    def __init__(self):
        self.timeout = 7
    def gettimeout(self):
        return self.timeout
    def settimeout(self, value):
        self.timeout = value


class SlowReader:
    def __init__(self):
        self.elapsed = 0.0
        self.bytes_read = 0
    def read(self, count):
        self.elapsed += 0.11
        size = min(1024, count)
        self.bytes_read += size
        return b"x" * size
    read1 = read


def fake_handler(kind, *, reader=None, length=12):
    handler = object.__new__(kind)
    handler.command = "POST"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(length)
    handler.rfile = reader if reader is not None else io.BytesIO(b"x" * length)
    handler.connection = SocketState()
    handler._send_json = lambda *args, **kwargs: None
    return handler


class ProbeRejectionContractTests(unittest.TestCase):
    def test_small_sent_body_is_consumed_before_rejection_response(self):
        handler = fake_handler(_ProbeHandler)
        observed = []
        handler._send_json = lambda *args, **kwargs: observed.append(handler.rfile.tell())
        handler._blocked(HTTPStatus.FORBIDDEN, "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
        self.assertEqual(observed, [12])
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.connection.timeout, 7)

    def test_probe_rejection_has_one_total_read_deadline(self):
        reader = SlowReader()
        handler = fake_handler(_ProbeHandler, reader=reader, length=65536)
        with patch("time.monotonic", side_effect=lambda: reader.elapsed):
            handler._blocked(HTTPStatus.FORBIDDEN, "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
        self.assertGreater(reader.bytes_read, 0)
        self.assertLessEqual(reader.bytes_read, 65536)
        self.assertLessEqual(reader.elapsed, 0.4)
        self.assertEqual(handler.connection.timeout, 7)

    def test_onboarding_rejection_has_one_total_read_deadline(self):
        reader = SlowReader()
        handler = fake_handler(OnboardingRequestHandler, reader=reader, length=65536)
        with patch("time.monotonic", side_effect=lambda: reader.elapsed):
            handler._discard_small_declared_body()
        self.assertGreater(reader.bytes_read, 0)
        self.assertLessEqual(reader.elapsed, 0.4)
        self.assertEqual(handler.connection.timeout, 7)

    def test_invalid_json_body_already_read_is_not_consumed_twice(self):
        handler = fake_handler(_ProbeHandler, reader=io.BytesIO(b"{]sentinel"), length=2)
        handler.path = "/pair"
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Origin"] = "chrome-extension://" + fixtures.CHROME_ID
        handler.headers["User-Agent"] = fixtures.CHROME_UA
        handler.headers["Sec-CH-UA"] = '"Chromium";v="140", "Google Chrome";v="140"'
        handler._valid_host = lambda: True
        handler.server = SimpleNamespace(probe=SimpleNamespace(channel_for_pair_path=lambda _: "chrome", origin_matches=lambda *_: True))
        handler.do_POST()
        self.assertEqual(handler.rfile.read(), b"sentinel")


class ProbeRejectionHTTPTests(unittest.TestCase):
    setUp = fixtures.CleanWindowsBrowserAcceptanceTests.setUp
    tearDown = fixtures.CleanWindowsBrowserAcceptanceTests.tearDown
    request_binding = fixtures.CleanWindowsBrowserAcceptanceTests.request_binding
    post_pair = fixtures.CleanWindowsBrowserAcceptanceTests.post_pair

    def malformed_post(self, *, duplicate=False, transfer=False, incomplete=False):
        payload = {"protocol_version": 2, "extension_version": fixtures.VERSION, "companion_binding": self.request_binding()}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.probe._server.server_port, timeout=2)
        connection.putrequest("POST", self.probe._pair_paths["chrome"])
        connection.putheader("Origin", "chrome-extension://" + (fixtures.DEV_ID if incomplete else fixtures.CHROME_ID))
        connection.putheader("User-Agent", fixtures.CHROME_UA)
        connection.putheader("Sec-CH-UA", '"Chromium";v="140", "Google Chrome";v="140"')
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", len(raw))
        if duplicate:
            connection.putheader("Content-Length", len(raw))
        if transfer:
            connection.putheader("Transfer-Encoding", "chunked")
        started = time.monotonic()
        try:
            connection.endheaders(b"{" if incomplete else raw)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), time.monotonic() - started
        finally:
            connection.close()

    def test_duplicate_lengths_never_satisfy_a_channel(self):
        status, body, _ = self.malformed_post(duplicate=True)
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "CLEAN_WINDOWS_PAIR_BODY_INVALID")
        with self.assertRaises(JobOpsError) as caught:
            self.probe.wait(0.02)
        self.assertEqual(caught.exception.details["observed_channels"], 0)

    def test_transfer_encoded_rejection_preserves_structured_error(self):
        status, body, elapsed = self.malformed_post(transfer=True)
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "CLEAN_WINDOWS_PAIR_BODY_INVALID")
        self.assertLess(elapsed, 1.5)

    def test_small_rejection_burst_preserves_code_without_pairing(self):
        for _ in range(20):
            status, body = self.post_pair("chrome", extension_id=fixtures.DEV_ID)
            self.assertEqual(status, 403)
            self.assertEqual(body["code"], "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
        with self.assertRaises(JobOpsError) as caught:
            self.probe.wait(0.02)
        self.assertEqual(caught.exception.details["observed_channels"], 0)

    def test_incomplete_rejection_is_bounded_and_valid_pairing_still_works(self):
        status, body, elapsed = self.malformed_post(incomplete=True)
        self.assertEqual(status, 403)
        self.assertFalse(body["automatic_retry"])
        self.assertLess(elapsed, 1.5)
        self.assertEqual(self.post_pair("chrome")[0], 200)
        self.assertEqual(self.post_pair("edge")[0], 200)
        self.assertEqual(self.probe.wait(1)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
