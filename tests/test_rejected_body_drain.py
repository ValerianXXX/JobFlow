from __future__ import annotations
import io
import unittest
from unittest.mock import Mock, patch
import _support
from jobops.errors import JobOpsError
from jobops.onboarding_server import OnboardingRequestHandler, REJECTED_BODY_DRAIN_LIMIT


class RejectedBodyDrainTests(unittest.TestCase):
    def handler(self, stream):
        handler = object.__new__(OnboardingRequestHandler)
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 7
        handler.rfile = stream
        handler.close_connection = True
        return handler

    def test_byte_cap_and_timeout_are_restored(self):
        stream = io.BytesIO(b"x" * (REJECTED_BODY_DRAIN_LIMIT + 1))
        handler = self.handler(stream)
        handler._discard_rejected_framing()
        self.assertEqual(stream.tell(), REJECTED_BODY_DRAIN_LIMIT)
        handler.connection.settimeout.assert_called_with(7)
        self.assertTrue(handler.close_connection)

    def test_single_deadline_cannot_be_extended_by_trickle_bytes(self):
        stream = Mock()
        stream.read1.return_value = b"x"
        handler = self.handler(stream)
        with patch("jobops.onboarding_server.time.monotonic", side_effect=[10, 10.1, 10.2, 10.3]):
            handler._discard_rejected_framing()
        self.assertEqual(stream.read1.call_count, 2)
        handler.connection.settimeout.assert_called_with(7)

    def test_read_error_preserves_original_structured_rejection(self):
        stream = Mock()
        stream.read1.side_effect = TimeoutError()
        handler = self.handler(stream)
        handler._send_json = Mock()
        error = JobOpsError("REQUEST_TRANSFER_ENCODING_FORBIDDEN", "Synthetic rejection")
        handler._dispatch_error(error)
        self.assertEqual(handler._send_json.call_args.args[0], 400)
        self.assertEqual(handler._send_json.call_args.args[1]["code"], error.code)
        self.assertTrue(handler.close_connection)
        handler.connection.settimeout.assert_called_with(7)


if __name__ == "__main__":
    unittest.main()
