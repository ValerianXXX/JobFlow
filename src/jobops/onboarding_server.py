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
from .onboarding_center import MAX_LARGE_EXPORT_BYTES, MAX_UPLOAD_BYTES, OnboardingCenterService


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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise JobOpsError("REQUEST_LENGTH_INVALID", "The local request length is invalid.") from exc
        if length < 1 or length > JSON_LIMIT:
            raise JobOpsError("REQUEST_SIZE_INVALID", "The local JSON request exceeds the safety limit.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("REQUEST_JSON_INVALID", "The local request body is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("REQUEST_JSON_INVALID", "The local request body must be an object.")
        return value

    def _optional_json_body(self) -> dict[str, Any]:
        if self.headers.get("Content-Length") in {None, "", "0"}:
            return {}
        return self._json_body()

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
            elif route == "complete":
                result = self.server.service.complete(user_confirmed=self._json_body().get("user_confirmed") is True)
            elif route == "import":
                query = parse_qs(parsed.query)
                source_type = str(query.get("source_type", [""])[0])
                extension = str(query.get("extension", [""])[0])
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise JobOpsError("REQUEST_LENGTH_INVALID", "The local upload length is invalid.") from exc
                size_limit = MAX_LARGE_EXPORT_BYTES if source_type == "chatgpt_export_large" else MAX_UPLOAD_BYTES
                if length < 1 or length > size_limit:
                    raise JobOpsError("ONBOARDING_SOURCE_SIZE_INVALID", "The onboarding upload is empty or too large.")
                if source_type == "chatgpt_export_large":
                    result = self._stream_large_export(length, extension)
                else:
                    result = self.server.service.preview_source(source_type, extension, self.rfile.read(length))
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


def run_server(service: OnboardingCenterService, *, port: int = 0, open_browser: bool = True) -> dict[str, Any]:
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
