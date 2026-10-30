"""Additional adversarial cases for rejected loopback request disposal."""
from email.message import Message
from http import HTTPStatus
import http.client
import io
import json
import socket
import time
import unittest
from unittest.mock import Mock

import _support
import test_clean_windows_browser_acceptance as fixtures
import test_probe_http_rejection as frozen
from jobops.clean_windows_acceptance import _ProbeHandler
from jobops.errors import JobOpsError
from jobops.http_rejection import discard_rejected_prefix, small_declared_body_length
from jobops.onboarding_server import OnboardingRequestHandler


class RejectionBoundariesTests(unittest.TestCase):
    def test_sized_body_does_not_consume_a_trailing_request(self):
        handler = frozen.fake_handler(OnboardingRequestHandler, reader=io.BytesIO(b"abcNEXT"), length=3)
        handler._discard_small_declared_body()
        self.assertEqual(handler.rfile.read(), b"NEXT")

    def test_small_body_helper_never_chooses_ambiguous_or_large_length(self):
        cases = [[], ["no"], ["-1"], ["0"], ["65537"], ["1", "1"]]
        for values in cases:
            with self.subTest(values=values):
                headers = Message()
                for value in values:
                    headers["Content-Length"] = value
                self.assertIsNone(small_declared_body_length(headers))
        headers = Message()
        headers["Content-Length"] = "4"
        headers["Transfer-Encoding"] = "chunked"
        self.assertIsNone(small_declared_body_length(headers))

    def test_absolute_byte_cap_and_read_error_restore_the_connection(self):
        stream = io.BytesIO(b"x" * 70000)
        connection = frozen.SocketState()
        discard_rejected_prefix(stream, connection, byte_limit=70000, deadline=time.monotonic() + 1)
        self.assertEqual(stream.tell(), 65536)
        self.assertEqual(connection.timeout, 7)
        stream = Mock()
        stream.read1.side_effect = ConnectionResetError()
        discard_rejected_prefix(stream, connection, byte_limit=3, deadline=time.monotonic() + 1)
        self.assertEqual(connection.timeout, 7)

    def test_get_rejection_never_reads_request_bytes(self):
        handler = frozen.fake_handler(_ProbeHandler)
        handler.command = "GET"
        handler._blocked(HTTPStatus.NOT_FOUND, "CLEAN_WINDOWS_PROBE_NOT_FOUND")
        self.assertEqual(handler.rfile.tell(), 0)


class RejectionConnectionTests(unittest.TestCase):
    setUp = fixtures.CleanWindowsBrowserAcceptanceTests.setUp
    tearDown = fixtures.CleanWindowsBrowserAcceptanceTests.tearDown
    request_binding = fixtures.CleanWindowsBrowserAcceptanceTests.request_binding

    def payload(self):
        return json.dumps({"protocol_version": 2, "extension_version": fixtures.VERSION,
                           "companion_binding": self.request_binding()}).encode()

    def test_pipelined_request_cannot_pair_after_a_rejection(self):
        port = self.probe._server.server_port
        body = self.payload()
        def request(origin):
            return (f"POST {self.probe._pair_paths['chrome']} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\nOrigin: chrome-extension://{origin}\r\n"
                    f"User-Agent: {fixtures.CHROME_UA}\r\n"
                    'Sec-CH-UA: "Google Chrome";v="140"\r\n'
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(request(fixtures.DEV_ID) + request(fixtures.CHROME_ID))
            response = http.client.HTTPResponse(client)
            response.begin()
            self.assertEqual(response.status, 403)
            self.assertEqual(response.getheader("Connection"), "close")
            self.assertEqual(json.loads(response.read())["code"], "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
        with self.assertRaises(JobOpsError) as blocked:
            self.probe.wait(0.02)
        self.assertEqual(blocked.exception.details["observed_channels"], 0)

    def test_reused_connection_resets_the_body_consumed_marker(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.probe._server.server_port, timeout=2)
        try:
            headers = {"Origin": "chrome-extension://" + fixtures.CHROME_ID,
                       "User-Agent": fixtures.CHROME_UA,
                       "Sec-CH-UA": '"Google Chrome";v="140"', "Content-Type": "application/json"}
            connection.request("POST", self.probe._pair_paths["chrome"], self.payload(), headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            initial_socket = connection.sock
            headers["Origin"] = "chrome-extension://" + fixtures.DEV_ID
            connection.request("POST", self.probe._pair_paths["chrome"], self.payload(), headers)
            self.assertIs(connection.sock, initial_socket)
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertEqual(response.getheader("Connection"), "close")
            self.assertFalse(json.loads(response.read())["automatic_retry"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
