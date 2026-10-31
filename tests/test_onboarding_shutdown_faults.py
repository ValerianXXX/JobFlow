"""Failure paths of the owned request barrier, with real loopback sockets."""
import socket
import threading
import unittest
from unittest import mock

from test_onboarding_shutdown import Service, server_fixture
from jobops.onboarding_server import OnboardingHTTPServer


class ShutdownFaultTests(unittest.TestCase):
    def test_thread_start_failure_does_not_poison_later_server_close(self):
        service = Service()
        server = OnboardingHTTPServer(("127.0.0.1", 0), service, token="synthetic-shutdown")
        client = socket.create_connection(server.server_address, timeout=3)
        request, address = server.get_request()
        try:
            with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("synthetic start failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic start failure"):
                    server.process_request(request, address)
            server.shutdown_request(request)
            server.server_close()
            self.assertEqual(service.close_count, 1)
            self.assertEqual(server.socket.fileno(), -1)
        finally:
            client.close()
            request.close()
            # On the initial failing implementation, the failed close already
            # removed its unstarted thread from the standard-library join list.
            server.server_close()

    def test_disconnect_error_still_drains_before_propagating(self):
        with server_fixture() as f:
            f["client"].sendall(b"GET /hold HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(f["request_started"].wait(3))
            original = socket.close
            failure = OSError("synthetic descriptor close report")
            def close_and_report(descriptor):
                original(descriptor)
                raise failure
            with mock.patch("jobops.onboarding_server.socket.close", side_effect=close_and_report):
                f["close_in_thread"]()
                self.assertFalse(f["closed"].wait(0.15))
                f["release"].set()
                self.assertTrue(f["closed"].wait(3))
                self.assertTrue(f["finished"].is_set())
                self.assertEqual(f["errors"], [failure])

    def test_concurrent_close_calls_share_one_completed_service_close(self):
        service = Service()
        server = OnboardingHTTPServer(("127.0.0.1", 0), service, token="synthetic-shutdown")
        entered = threading.Event()
        release = threading.Event()
        original = service.close
        def held_close():
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic close was not released")
            original()
        service.close = held_close
        errors = []
        def close():
            try:
                server.server_close()
            except BaseException as error:
                errors.append(error)
        first = threading.Thread(target=close, daemon=True)
        second = threading.Thread(target=close, daemon=True)
        try:
            first.start()
            self.assertTrue(entered.wait(3))
            second.start()
            release.set()
            first.join(3)
            second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(service.close_count, 1)
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
            service.close = original
            server.server_close()
