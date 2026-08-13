from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .errors import JobOpsError
from .instance_lock import local_instance_lock
from .onboarding_center import (
    MAX_LARGE_EXPORT_BYTES,
    MAX_RETAINED_SOURCE_BYTES,
    MAX_UPLOAD_BYTES,
    OnboardingCenterService,
)
from .official_discovery import MAX_SNAPSHOT_BYTES


JSON_LIMIT = 2 * 1024 * 1024


class OnboardingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: OnboardingCenterService, token: str | None = None) -> None:
        if address[0] != "127.0.0.1":
            raise JobOpsError("ONBOARDING_BIND_FORBIDDEN", "The onboarding center may bind only to 127.0.0.1.")
        self.service = service
        self.session_token = token or secrets.token_urlsafe(32)
        self.ui_root = Path(__file__).resolve().parent / "ui"
        super().__init__(address, OnboardingRequestHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/session/{self.session_token}/"

    def handle_error(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def server_close(self) -> None:
        try:
            self.service.close()
        finally:
            super().server_close()


class OnboardingRequestHandler(BaseHTTPRequestHandler):
    server: OnboardingHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _security_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def _send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers(content_type)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        self._send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].casefold()
        return host in {"127.0.0.1", "localhost"}

    def _authorized(self, parsed=None) -> bool:
        if not self._valid_host():
            return False
        parsed = parsed or urlparse(self.path)
        path_token = parsed.path.split("/")[2] if parsed.path.startswith("/session/") and len(parsed.path.split("/")) > 2 else ""
        header_token = self.headers.get("X-JobOps-Session", "")
        return secrets.compare_digest(path_token or header_token, self.server.session_token)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin in {None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}

    def _json_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise JobOpsError(
                "REQUEST_TRANSFER_ENCODING_FORBIDDEN",
                "Local JSON requests require one explicit Content-Length and do not accept transfer encodings.",
            )
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            raise JobOpsError(
                "REQUEST_LENGTH_INVALID",
                "The local JSON request must contain exactly one Content-Length header.",
            )
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise JobOpsError("REQUEST_CONTENT_TYPE_INVALID", "Local JSON requests must use application/json.")
        try:
            length = int(content_lengths[0])
        except ValueError as exc:
            raise JobOpsError("REQUEST_LENGTH_INVALID", "The local request length is invalid.") from exc
        if length < 1 or length > JSON_LIMIT:
            raise JobOpsError("REQUEST_SIZE_INVALID", "The local JSON request exceeds the safety limit.")
        try:
            value = json.loads(self._read_exact_body(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("REQUEST_JSON_INVALID", "The local request body is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("REQUEST_JSON_INVALID", "The local request body must be an object.")
        return value

    def _optional_json_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise JobOpsError(
                "REQUEST_TRANSFER_ENCODING_FORBIDDEN",
                "Local JSON requests do not accept transfer encodings.",
            )
        content_lengths = self.headers.get_all("Content-Length") or []
        if not content_lengths:
            return {}
        if len(content_lengths) != 1:
            raise JobOpsError("REQUEST_LENGTH_INVALID", "The local request must contain at most one Content-Length header.")
        try:
            if int(content_lengths[0]) == 0:
                return {}
        except ValueError as exc:
            raise JobOpsError("REQUEST_LENGTH_INVALID", "The local request length is invalid.") from exc
        return self._json_body()

    def _binary_body_length(self, *, maximum: int, size_code: str, size_message: str) -> int:
        if self.headers.get("Transfer-Encoding") is not None:
            raise JobOpsError(
                "REQUEST_TRANSFER_ENCODING_FORBIDDEN",
                "Local binary uploads require one explicit Content-Length and do not accept transfer encodings.",
            )
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            raise JobOpsError(
                "REQUEST_LENGTH_INVALID",
                "The local binary request must contain exactly one Content-Length header.",
            )
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/octet-stream":
            raise JobOpsError(
                "REQUEST_CONTENT_TYPE_INVALID",
                "Local binary uploads must use application/octet-stream.",
            )
        try:
            length = int(content_lengths[0])
        except ValueError as exc:
            raise JobOpsError("REQUEST_LENGTH_INVALID", "The local binary request length is invalid.") from exc
        if length < 1 or length > maximum:
            raise JobOpsError(size_code, size_message)
        return length

    def _read_exact_body(self, length: int) -> bytes:
        value = self.rfile.read(length)
        if len(value) != length:
            raise JobOpsError(
                "ONBOARDING_UPLOAD_INTERRUPTED",
                "The local request ended before every declared byte was received; no partial content was accepted.",
                expected_bytes=length,
                received_bytes=len(value),
            )
        return value

    def _stream_large_export(self, length: int, extension: str) -> dict[str, Any]:
        if extension.casefold() != ".zip":
            raise JobOpsError("CHATGPT_EXPORT_FORMAT_INVALID", "The streaming large-file option accepts ZIP exports only.")
        staging_root = self.server.service.onboarding.store.private_root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        required_free = length + max(512 * 1024 * 1024, length // 20)
        if shutil.disk_usage(staging_root).free < required_free:
            raise JobOpsError(
                "ONBOARDING_STAGING_SPACE_INSUFFICIENT",
                "There is not enough local temporary space to process this large export safely.",
            )
        with self.server.service.onboarding.staging_directory() as staging:
            target = staging / "chatgpt-export.zip"
            digest = hashlib.sha256()
            remaining = length
            with target.open("xb") as handle:
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                while remaining:
                    chunk = self.rfile.read(min(4 * 1024 * 1024, remaining))
                    if not chunk:
                        raise JobOpsError("ONBOARDING_UPLOAD_INTERRUPTED", "The local large-file transfer was interrupted.")
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            return self.server.service.preview_large_chatgpt_export(
                target,
                extension=extension,
                source_hash="sha256:" + digest.hexdigest(),
                upload_size=length,
            )

    def _dispatch_error(self, exc: Exception) -> None:
        # Error paths may intentionally reject a request before reading its body. Closing the
        # connection prevents unread bytes from becoming a second, ambiguous local request.
        self.close_connection = True
        if isinstance(exc, JobOpsError):
            self._send_json(HTTPStatus.BAD_REQUEST, exc.as_dict())
        else:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "status": "BLOCKED", "code": "ONBOARDING_LOCAL_ERROR",
                "message": "The local onboarding request could not be completed.", "details": {},
            })

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "READY", "binding": "127.0.0.1", "real_external_actions": 0})
            return
        if not self._authorized(parsed):
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "LOCAL_SESSION_REQUIRED"})
            return
        session_prefix = f"/session/{self.server.session_token}/"
        relative = parsed.path.removeprefix(session_prefix)
        if relative in {"", "index.html"}:
            asset, content_type = "index.html", "text/html; charset=utf-8"
        elif relative == "app.js":
            asset, content_type = "app.js", "text/javascript; charset=utf-8"
        elif relative == "styles.css":
            asset, content_type = "styles.css", "text/css; charset=utf-8"
        elif relative == "api/bootstrap":
            try:
                self._send_json(HTTPStatus.OK, self.server.service.bootstrap())
            except Exception as exc:
                self._dispatch_error(exc)
            return
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"})
            return
        path = self.server.ui_root / asset
        if not path.is_file():
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "BLOCKED", "code": "UI_ASSET_MISSING"})
            return
        self._send_bytes(HTTPStatus.OK, path.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized(parsed) or not self._origin_allowed():
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "LOCAL_SESSION_REQUIRED"})
            return
        route = parsed.path.split("/api/", 1)[-1] if "/api/" in parsed.path else ""
        try:
            if route == "save":
                result = self.server.service.save_answers(self._json_body())
            elif route == "connect-ai":
                result = self.server.service.connect_ai(self._json_body())
            elif route == "accept-suggestion":
                result = self.server.service.accept_suggestion(str(self._json_body().get("suggestion_id", "")))
            elif route == "review":
                result = self.server.service.save_review(self._json_body())
            elif route == "queue-limit":
                result = self.server.service.set_queue_limit(self._json_body())
            elif route == "review-packet":
                result = self.server.service.review_packet(str(self._json_body().get("application_id", "")))
            elif route == "queue-decision":
                result = self.server.service.decide_review_packet(self._json_body())
            elif route == "approve-external-claims":
                result = self.server.service.approve_external_claims(self._json_body())
            elif route == "claim-transform":
                result = self.server.service.transform_claims(self._json_body())
            elif route == "start-revision":
                self._optional_json_body()
                result = self.server.service.start_revision()
            elif route == "commit-source":
                payload = self._json_body()
                result = self.server.service.commit_source(str(payload.get("source_id", "")), payload.get("selections"))
            elif route == "discard-source":
                result = self.server.service.discard_source_preview(str(self._json_body().get("source_id", "")))
            elif route == "delete-source":
                payload = self._json_body()
                result = self.server.service.delete_source(
                    str(payload.get("source_id", "")),
                    user_confirmed=payload.get("user_confirmed") is True,
                )
            elif route == "reprocess-source":
                result = self.server.service.reprocess_source(str(self._json_body().get("source_id", "")))
            elif route == "discover-official-jobs":
                query = parse_qs(parsed.query)
                official_entry_url = str(query.get("official_url", [""])[0])
                company_domain = str(query.get("company_domain", [""])[0])
                source_format = str(query.get("source_format", [""])[0])
                length = self._binary_body_length(
                    maximum=MAX_SNAPSHOT_BYTES,
                    size_code="OFFICIAL_SNAPSHOT_SIZE_INVALID",
                    size_message="The local official-careers snapshot is empty or too large.",
                )
                result = self.server.service.discover_official_jobs(
                    self._read_exact_body(length),
                    official_entry_url=official_entry_url,
                    company_domain=company_domain,
                    source_format=source_format,
                )
            elif route == "complete":
                result = self.server.service.complete(user_confirmed=self._json_body().get("user_confirmed") is True)
            elif route == "import":
                query = parse_qs(parsed.query)
                source_type = str(query.get("source_type", [""])[0])
                extension = str(query.get("extension", [""])[0])
                size_limit = (
                    MAX_LARGE_EXPORT_BYTES
                    if source_type == "chatgpt_export_large"
                    else MAX_UPLOAD_BYTES
                    if source_type == "chatgpt_export"
                    else MAX_RETAINED_SOURCE_BYTES
                )
                length = self._binary_body_length(
                    maximum=size_limit,
                    size_code="ONBOARDING_SOURCE_SIZE_INVALID",
                    size_message="The onboarding upload is empty or too large.",
                )
                if source_type == "chatgpt_export_large":
                    result = self._stream_large_export(length, extension)
                else:
                    result = self.server.service.preview_source(source_type, extension, self._read_exact_body(length))
            elif route == "shutdown":
                self._optional_json_body()
                result = {"status": "CLOSING", "real_external_actions": 0}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"})
                return
            self._send_json(HTTPStatus.OK, result)
        except Exception as exc:
            self._dispatch_error(exc)


def create_server(service: OnboardingCenterService, *, port: int = 0, token: str | None = None) -> OnboardingHTTPServer:
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise JobOpsError("ONBOARDING_PORT_INVALID", "The local onboarding port must be between 0 and 65535.")
    return OnboardingHTTPServer(("127.0.0.1", port), service, token=token)


def _run_server_unlocked(service: OnboardingCenterService, *, port: int = 0, open_browser: bool = True) -> dict[str, Any]:
    # run_server holds the single-instance lock here, so residue cannot belong to
    # another live onboarding process. Cleanup happens before a listening socket exists.
    service.onboarding.clear_staging_residue()
    server = create_server(service, port=port)
    safe = {
        "status": "ONBOARDING_CENTER_READY", "url": server.url,
        "binding": "127.0.0.1", "supported_locales": ["zh", "en"],
        "private_values_emitted": 0, "real_external_actions": 0,
        "next_safe_action": "complete the local onboarding form; close with Ctrl+C",
    }
    print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)
    if open_browser:
        webbrowser.open(server.url, new=2)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {**safe, "status": "ONBOARDING_CENTER_CLOSED"}


def run_server(service: OnboardingCenterService, *, port: int = 0, open_browser: bool = True) -> dict[str, Any]:
    with local_instance_lock():
        return _run_server_unlocked(service, port=port, open_browser=open_browser)
