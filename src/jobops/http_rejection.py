"""Bounded disposal of rejected loopback bytes; never parse or retain a body."""
from __future__ import annotations

import time
from email.message import Message
from socket import socket
from typing import BinaryIO


REJECTED_BODY_DRAIN_LIMIT = 64 * 1024
REJECTED_BODY_DRAIN_TIMEOUT_SECONDS = 0.25


def small_declared_body_length(headers: Message) -> int | None:
    """Return one small declared length, not a framing decision for acceptance."""
    if headers.get("Transfer-Encoding") is not None:
        return None
    lengths = headers.get_all("Content-Length") or []
    if len(lengths) != 1:
        return None
    try:
        length = int(lengths[0])
    except ValueError:
        return None
    return length if 0 < length <= REJECTED_BODY_DRAIN_LIMIT else None


def discard_rejected_prefix(
    stream: BinaryIO,
    connection: socket,
    *,
    byte_limit: int,
    deadline: float,
) -> None:
    """Consume at most a small prefix before closing an already-rejected request.

    A single monotonic deadline covers every read. Buffered ``read`` can perform
    multiple socket reads whose individual timeouts restart, so use ``read1``.
    The caller must close the connection: an ambiguous length or transfer coding
    is never interpreted here, and remaining bytes must not become a request.
    Best effort only; a large/late body can still cause a transport reset.
    """
    previous_timeout = connection.gettimeout()
    remaining = min(max(byte_limit, 0), REJECTED_BODY_DRAIN_LIMIT)
    try:
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            connection.settimeout(timeout)
            chunk = stream.read1(min(16 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    except OSError:
        pass
    finally:
        try:
            connection.settimeout(previous_timeout)
        except OSError:
            pass
