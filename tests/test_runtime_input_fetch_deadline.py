from __future__ import annotations

import unittest
from unittest.mock import patch

from jobops.runtime_inputs import RuntimeInputError, _default_fetch, _fetch_exact
from jobops.util import sha256_bytes


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)


class _Raw:
    def __init__(self, socket: _Socket) -> None:
        self._sock = socket


class _FilePointer:
    def __init__(self, socket: _Socket) -> None:
        self.raw = _Raw(socket)


class _Response:
    status = 200

    def __init__(self, url: str, body: bytes, declared_length: int) -> None:
        self._url = url
        self._body = body
        self.headers = {"Content-Length": str(declared_length)}
        self.socket = _Socket()
        self.fp: _FilePointer | None = _FilePointer(self.socket)
        self.read_calls = 0

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        self.fp = None

    def geturl(self) -> str:
        return self._url

    def isclosed(self) -> bool:
        return self.fp is None

    def read(self, amount: int) -> bytes:
        self.read_calls += 1
        chunk = self._body[:amount]
        self._body = self._body[len(chunk) :]
        if not self._body:
            self.fp = None
        return chunk


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def open(self, request: object, timeout: int) -> _Response:
        del request, timeout
        return self.response


class RuntimeInputFetchDeadlineTests(unittest.TestCase):
    def test_declared_length_stops_before_detached_socket_is_reconfigured(self) -> None:
        url = "https://www.python.org/fixture.bin"
        response = _Response(url, b"test", 4)
        with patch(
            "jobops.runtime_inputs.urllib.request.build_opener",
            return_value=_Opener(response),
        ):
            result = _default_fetch(url, 4)
        self.assertEqual(result.body, b"test")
        self.assertEqual(response.read_calls, 1)
        self.assertEqual(len(response.socket.timeouts), 1)

    def test_detached_truncated_response_still_fails_exact_length_gate(self) -> None:
        url = "https://www.python.org/fixture.bin"
        response = _Response(url, b"te", 4)
        with patch(
            "jobops.runtime_inputs.urllib.request.build_opener",
            return_value=_Opener(response),
        ):
            with self.assertRaises(RuntimeInputError) as blocked:
                _fetch_exact(
                    _default_fetch,
                    url,
                    host="www.python.org",
                    expected_bytes=4,
                    expected_sha256=sha256_bytes(b"test"),
                    filename="fixture.bin",
                )
        self.assertEqual(str(blocked.exception), "RUNTIME_INPUT_LENGTH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
