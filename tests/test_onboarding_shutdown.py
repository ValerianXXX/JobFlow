"""Synthetic loopback shutdown contracts; no model, profile, employer or mailbox."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import socket
import tempfile
import threading
import unittest

import _support
from jobops.onboarding_server import OnboardingHTTPServer, OnboardingRequestHandler


class Service:
    def __init__(self):
        self.close_called = threading.Event()
        self.close_count = 0
        self.error = None

    def close(self):
        self.close_count += 1
        self.close_called.set()
        if self.error:
            raise self.error


@contextmanager
def server_fixture():
    with tempfile.TemporaryDirectory(prefix="jobflow-shutdown-contract-") as directory:
        root = Path(directory)
        entered = threading.Event()
        request_started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        second = threading.Event()
        body_started = threading.Event()

        class Handler(OnboardingRequestHandler):
            def handle(self):
                entered.set()
                try:
                    super().handle()
                finally:
                    finished.set()

            def do_GET(self):
                request_started.set()
                if self.path == "/hold":
                    if not release.wait(5):
                        raise AssertionError("Synthetic request was not released")
                if self.path == "/second":
                    second.set()
                (root / "last-write.txt").write_text(self.path, encoding="utf8")
                self._send_bytes(200, b"synthetic", "text/plain")

            def do_POST(self):
                body_started.set()
                try:
                    raw = self._read_exact_body(int(self.headers["Content-Length"]))
                except Exception:
                    self.close_connection = True
                    return
                (root / "upload.txt").write_bytes(raw)

        service = Service()
        server = OnboardingHTTPServer(("127.0.0.1", 0), service, token="synthetic-shutdown")
        server.RequestHandlerClass = Handler
        loop = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        loop.start()
        client = socket.create_connection(server.server_address, timeout=3)
        if not entered.wait(3):
            raise AssertionError("Synthetic loopback connection was not accepted")
        errors = []
        closed = threading.Event()
        closer = None

        def close_in_thread():
            nonlocal closer
            server.shutdown()
            def close():
                try:
                    server.server_close()
                except BaseException as error:
                    errors.append(error)
                finally:
                    closed.set()
            closer = threading.Thread(target=close, daemon=True)
            closer.start()
            if not service.close_called.wait(3):
                raise AssertionError("Server did not start closing its service")

        try:
            yield locals()
        finally:
            release.set()
            client.close()
            server.shutdown()
            if closer:
                closer.join(5)
            loop.join(3)
            if not finished.wait(3):
                raise AssertionError("An owned request survived fixture teardown")
            if closer and closer.is_alive():
                raise AssertionError("The owned close thread survived fixture teardown")
            service.error = None
            server.server_close()


class OnboardingShutdownTests(unittest.TestCase):
    def test_close_waits_for_an_accepted_mutation_before_returning(self):
        with server_fixture() as f:
            f["client"].sendall(b"GET /hold HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(f["request_started"].wait(3))
            f["close_in_thread"]()
            self.assertFalse(f["closed"].wait(0.15), "Close returned while an accepted write was still active")
            f["release"].set()
            self.assertTrue(f["closed"].wait(3))
            self.assertTrue(f["finished"].is_set())
            self.assertEqual((f["root"] / "last-write.txt").read_text(), "/hold")
            self.assertEqual(f["errors"], [])

    def test_idle_keep_alive_connection_is_stopped_before_close_returns(self):
        with server_fixture() as f:
            f["close_in_thread"]()
            self.assertTrue(f["closed"].wait(3))
            self.assertTrue(f["finished"].is_set(), "Idle request still owns the temporary workspace")
            self.assertEqual(f["errors"], [])

    def test_buffered_second_request_is_not_admitted_after_close_starts(self):
        with server_fixture() as f:
            f["client"].sendall(b"GET /hold HTTP/1.1\r\nHost: localhost\r\n\r\nGET /second HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(f["request_started"].wait(3))
            f["close_in_thread"]()
            f["release"].set()
            self.assertTrue(f["closed"].wait(3))
            self.assertFalse(f["second"].wait(0.15), "Buffered request wrote after shutdown began")
            self.assertEqual((f["root"] / "last-write.txt").read_text(), "/hold")

    def test_service_close_error_does_not_skip_request_drain(self):
        with server_fixture() as f:
            failure = RuntimeError("synthetic close failure")
            f["service"].error = failure
            f["client"].sendall(b"GET /hold HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(f["request_started"].wait(3))
            f["close_in_thread"]()
            self.assertFalse(f["closed"].wait(0.15))
            f["release"].set()
            self.assertTrue(f["closed"].wait(3))
            self.assertTrue(f["finished"].is_set())
            self.assertEqual(f["errors"], [failure])

    def test_unfinished_request_body_is_not_committed_during_shutdown(self):
        with server_fixture() as f:
            f["client"].sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\npartial")
            self.assertTrue(f["body_started"].wait(3))
            f["close_in_thread"]()
            self.assertTrue(f["closed"].wait(3))
            self.assertTrue(f["finished"].is_set())
            self.assertFalse((f["root"] / "upload.txt").exists())

    def test_successful_close_is_idempotent(self):
        service = Service()
        server = OnboardingHTTPServer(("127.0.0.1", 0), service, token="synthetic-shutdown")
        server.server_close()
        server.server_close()
        self.assertEqual(service.close_count, 1)

    def test_unused_listener_closes_without_starting_the_serve_loop(self):
        service = Service()
        server = OnboardingHTTPServer(("127.0.0.1", 0), service, token="synthetic-shutdown")
        server.server_close()
        self.assertEqual(server.socket.fileno(), -1)
        self.assertEqual(service.close_count, 1)


if __name__ == "__main__":
    unittest.main()
