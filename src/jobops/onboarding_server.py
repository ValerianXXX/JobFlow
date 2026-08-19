from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import struct
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .errors import JobOpsError
from .browser_assist import BrowserAssistManager, COMPANION_EXTENSION_VERSION, COMPANION_PROTOCOL_VERSION
from .companion_binding import sign_pair_response, validate_pair_request
from .instance_lock import local_instance_lock
from .onboarding_center import (
    MAX_LARGE_EXPORT_BYTES,
    MAX_OFFLINE_APPLICATION_BUNDLE_BYTES,
    MAX_RETAINED_SOURCE_BYTES,
    MAX_UPLOAD_BYTES,
    OnboardingCenterService,
)
from .official_discovery import MAX_SNAPSHOT_BYTES


JSON_LIMIT = 2 * 1024 * 1024
APPLICATION_BUNDLE_MANIFEST_LIMIT = 64 * 1024
REJECTED_BODY_DRAIN_LIMIT = 64 * 1024
REJECTED_BODY_DRAIN_TIMEOUT_SECONDS = 0.25


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

    def _assist_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        return origin if BrowserAssistManager.extension_origin_allowed(origin) else None

    def _assist_token_and_route(self, parsed=None) -> tuple[str, list[str]] | None:
        parsed = parsed or urlparse(self.path)
        parts = [item for item in parsed.path.split("/") if item]
        if len(parts) < 2 or parts[0] != "assist":
            return None
        return parts[1], parts[2:]

    def _intake_token_and_route(self, parsed=None) -> tuple[str, list[str]] | None:
        parsed = parsed or urlparse(self.path)
        parts = [item for item in parsed.path.split("/") if item]
        if len(parts) < 2 or parts[0] != "intake":
            return None
        return parts[1], parts[2:]

    def _assist_security_headers(self, content_type: str, origin: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")

    def _send_assist_bytes(self, status: int, data: bytes | bytearray, content_type: str, origin: str) -> None:
        self.send_response(status)
        self._assist_security_headers(content_type, origin)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_assist_json(self, status: int, value: dict[str, Any], origin: str) -> None:
        self._send_assist_bytes(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            origin,
        )

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

    def _companion_pair_body(self) -> dict[str, Any]:
        payload = self._optional_json_body()
        if (
            set(payload) != {"protocol_version", "extension_version", "companion_binding"}
            or payload.get("protocol_version") != COMPANION_PROTOCOL_VERSION
            or payload.get("extension_version") != COMPANION_EXTENSION_VERSION
            or not isinstance(payload.get("companion_binding"), dict)
        ):
            raise JobOpsError(
                "BROWSER_COMPANION_UPDATE_REQUIRED",
                "Reload the Browser Companion bundled with this JobFlow version.",
                expected_version=COMPANION_EXTENSION_VERSION,
            )
        validate_pair_request(
            payload["companion_binding"],
            local_app_data=self._companion_local_app_data(),
        )
        return payload

    def _companion_file_body(self) -> None:
        payload = self._json_body()
        if payload != {"protocol_version": COMPANION_PROTOCOL_VERSION}:
            raise JobOpsError(
                "BROWSER_COMPANION_FILE_PROTOCOL_INVALID",
                "The Browser Companion material request version is invalid.",
            )

    def _companion_local_app_data(self) -> Path:
        store = self.server.service.onboarding.store
        configured = getattr(store, "local_app_data", None)
        return Path(configured) if configured is not None else Path(store.private_root).parent.parent

    def _companion_base_url(self) -> str:
        host_value = self.headers.get("Host", "")
        try:
            parsed = urlparse(f"http://{host_value}")
            hostname = (parsed.hostname or "").casefold()
            port = parsed.port or self.server.server_port
        except ValueError as exc:
            raise JobOpsError("BROWSER_COMPANION_HOST_INVALID", "The local JobFlow host is invalid.") from exc
        if hostname not in {"127.0.0.1", "localhost"} or port != self.server.server_port:
            raise JobOpsError("BROWSER_COMPANION_HOST_INVALID", "The local JobFlow host is invalid.")
        return f"http://{hostname}:{port}"

    def _sign_companion_pair(
        self,
        *,
        assist_path: str,
        pair_request: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        signed = dict(result)
        signed["companion_binding"] = sign_pair_response(
            protocol_version=COMPANION_PROTOCOL_VERSION,
            extension_version=COMPANION_EXTENSION_VERSION,
            base_url=self._companion_base_url(),
            assist_path=assist_path,
            binding_request=pair_request["companion_binding"],
            response=result,
            local_app_data=self._companion_local_app_data(),
        )
        return signed

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

    def _discard_small_declared_body(self) -> None:
        """Best-effort drain for a rejected, already-sent loopback request.

        Windows may reset a TCP connection when the server closes it with unread
        request bytes, which can hide the JSON error response from the browser.
        Only one small, explicitly sized body is consumed, with a short timeout;
        transfer-encoded, ambiguous, oversized, or incomplete bodies still fail
        closed without an unbounded read.
        """
        if self.headers.get("Transfer-Encoding") is not None:
            return
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            return
        try:
            length = int(content_lengths[0])
        except ValueError:
            return
        if not 0 < length <= REJECTED_BODY_DRAIN_LIMIT:
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(REJECTED_BODY_DRAIN_TIMEOUT_SECONDS)
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(16 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass

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

    def _application_bundle_body(self, length: int) -> tuple[dict[str, Any], dict[str, tuple[str, bytes]]]:
        raw = self._read_exact_body(length)
        if len(raw) < 5:
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle header is incomplete.")
        manifest_length = struct.unpack(">I", raw[:4])[0]
        if not 1 <= manifest_length <= APPLICATION_BUNDLE_MANIFEST_LIMIT or 4 + manifest_length > len(raw):
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle manifest length is invalid.")
        try:
            manifest = json.loads(raw[4:4 + manifest_length].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle manifest is not valid JSON.") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle version is unsupported.")
        metadata, descriptors = manifest.get("metadata"), manifest.get("files")
        if not isinstance(metadata, dict) or not isinstance(descriptors, list) or len(descriptors) != 3:
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle manifest is incomplete.")
        offset = 4 + manifest_length
        files: dict[str, tuple[str, bytes]] = {}
        for descriptor in descriptors:
            if not isinstance(descriptor, dict) or set(descriptor) != {"key", "extension", "size"}:
                raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "A local application file descriptor is invalid.")
            key, extension, size = descriptor.get("key"), descriptor.get("extension"), descriptor.get("size")
            if key not in {"jd", "official", "form"} or key in files or not isinstance(extension, str):
                raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "A local application file key or extension is invalid.")
            if isinstance(size, bool) or not isinstance(size, int) or size < 1 or offset + size > len(raw):
                raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "A local application file length is invalid.")
            files[key] = (extension.casefold(), raw[offset:offset + size])
            offset += size
        if set(files) != {"jd", "official", "form"} or offset != len(raw):
            raise JobOpsError("APPLICATION_BUNDLE_PROTOCOL_INVALID", "The local application bundle has missing or trailing bytes.")
        return metadata, files

    def _dispatch_error(self, exc: Exception) -> None:
        # Error paths may intentionally reject a request before reading its body. Closing the
        # connection prevents unread bytes from becoming a second, ambiguous local request.
        self.close_connection = True
        if isinstance(exc, JobOpsError):
            if exc.code == "REQUEST_CONTENT_TYPE_INVALID":
                self._discard_small_declared_body()
            self._send_json(HTTPStatus.BAD_REQUEST, exc.as_dict())
        else:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "status": "BLOCKED", "code": "ONBOARDING_LOCAL_ERROR",
                "message": "The local onboarding request could not be completed.", "details": {},
            })

    def _dispatch_assist_error(self, exc: Exception, origin: str) -> None:
        self.close_connection = True
        if isinstance(exc, JobOpsError):
            self._send_assist_json(HTTPStatus.BAD_REQUEST, exc.as_dict(), origin)
        else:
            self._send_assist_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "status": "BLOCKED", "code": "BROWSER_ASSIST_LOCAL_ERROR",
                "message": "The local browser-assist request could not be completed.", "details": {},
            }, origin)

    def do_OPTIONS(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        assist = self._assist_token_and_route(parsed)
        intake = self._intake_token_and_route(parsed)
        origin = self._assist_origin()
        if (assist is None and intake is None) or origin is None or not self._valid_host():
            self.close_connection = True
            self.send_response(HTTPStatus.FORBIDDEN)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._assist_security_headers("text/plain; charset=utf-8", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "300")
        if self.headers.get("Access-Control-Request-Private-Network", "").casefold() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        assist = self._assist_token_and_route(parsed)
        if assist is not None:
            origin = self._assist_origin()
            if origin is None or not self._valid_host():
                self.close_connection = True
                self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "BROWSER_COMPANION_ORIGIN_FORBIDDEN"})
                return
            token, route_parts = assist
            if len(route_parts) != 2 or route_parts[0] != "file":
                self._send_assist_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"}, origin)
                return
            raw: bytearray | None = None
            try:
                raw, _ = self.server.service.browser_assist.take_file(
                    token, route_parts[1], extension_origin=origin,
                )
                self._send_assist_bytes(HTTPStatus.OK, raw, "application/octet-stream", origin)
            except Exception as exc:
                self._dispatch_assist_error(exc, origin)
            finally:
                if raw is not None:
                    raw[:] = b"\0" * len(raw)
            return
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
        intake = self._intake_token_and_route(parsed)
        if intake is not None:
            origin = self._assist_origin()
            if origin is None or not self._valid_host():
                self.close_connection = True
                self._discard_small_declared_body()
                self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "BROWSER_COMPANION_ORIGIN_FORBIDDEN"})
                return
            token, route_parts = intake
            try:
                if route_parts == ["pair"]:
                    pair_request = self._companion_pair_body()
                    result = self.server.service.pair_guided_intake(token, extension_origin=origin)
                    result = self._sign_companion_pair(
                        assist_path=f"/intake/{token}", pair_request=pair_request, result=result,
                    )
                elif route_parts == ["capture-job"]:
                    result = self.server.service.capture_guided_job_page(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["capture-search"]:
                    result = self.server.service.capture_guided_search_results(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["capture-form"]:
                    result = self.server.service.start_guided_application_form_preparation(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["capture-form-status"]:
                    self._optional_json_body()
                    result = self.server.service.guided_application_form_preparation_status(
                        token, extension_origin=origin,
                    )
                else:
                    self._send_assist_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"}, origin)
                    return
                self._send_assist_json(HTTPStatus.OK, result, origin)
            except Exception as exc:
                self._dispatch_assist_error(exc, origin)
            return
        assist = self._assist_token_and_route(parsed)
        if assist is not None:
            origin = self._assist_origin()
            if origin is None or not self._valid_host():
                self.close_connection = True
                self._discard_small_declared_body()
                self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "BROWSER_COMPANION_ORIGIN_FORBIDDEN"})
                return
            token, route_parts = assist
            try:
                if len(route_parts) == 2 and route_parts[0] == "file":
                    self._companion_file_body()
                    raw: bytearray | None = None
                    try:
                        raw, _ = self.server.service.browser_assist.take_file(
                            token, route_parts[1], extension_origin=origin,
                        )
                        self._send_assist_bytes(HTTPStatus.OK, raw, "application/octet-stream", origin)
                    finally:
                        if raw is not None:
                            raw[:] = b"\0" * len(raw)
                    return
                if route_parts == ["pair"]:
                    pair_request = self._companion_pair_body()
                    result = self.server.service.browser_assist.pair(token, extension_origin=origin)
                    result = self._sign_companion_pair(
                        assist_path=f"/assist/{token}", pair_request=pair_request, result=result,
                    )
                elif route_parts == ["prepare"]:
                    result = self.server.service.browser_assist.prepare(token, self._json_body(), extension_origin=origin)
                elif route_parts == ["discover-dynamic-fields"]:
                    result = self.server.service.browser_assist.discover_dynamic_fields(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["complete"]:
                    result = self.server.service.browser_assist.complete(token, self._json_body(), extension_origin=origin)
                elif route_parts == ["abort-page-apply"]:
                    result = self.server.service.browser_assist.abort_page_apply(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["authorize-navigation"]:
                    result = self.server.service.browser_assist.authorize_navigation(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["navigation-observed"]:
                    result = self.server.service.browser_assist.navigation_observed(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["resume-manual-navigation"]:
                    result = self.server.service.browser_assist.resume_manual_navigation(
                        token, self._json_body(), extension_origin=origin,
                    )
                elif route_parts == ["submit-observed"]:
                    result = self.server.service.browser_assist.submit_observed(token, self._json_body(), extension_origin=origin)
                elif route_parts == ["observe-result"]:
                    result = self.server.service.browser_assist.observe_result(token, self._json_body(), extension_origin=origin)
                elif route_parts == ["result-unavailable"]:
                    result = self.server.service.browser_assist.result_unavailable(token, self._json_body(), extension_origin=origin)
                else:
                    self._send_assist_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"}, origin)
                    return
                self._send_assist_json(HTTPStatus.OK, result, origin)
            except Exception as exc:
                self._dispatch_assist_error(exc, origin)
            return
        if not self._authorized(parsed) or not self._origin_allowed():
            self.close_connection = True
            self._discard_small_declared_body()
            self._send_json(HTTPStatus.FORBIDDEN, {"status": "BLOCKED", "code": "LOCAL_SESSION_REQUIRED"})
            return
        route = parsed.path.split("/api/", 1)[-1] if "/api/" in parsed.path else ""
        try:
            if route == "save":
                result = self.server.service.save_answers(self._json_body())
            elif route == "connect-ai":
                result = self.server.service.connect_ai(self._json_body())
            elif route == "support-diagnostics":
                result = self.server.service.support_diagnostics(self._json_body())
            elif route == "launch-update":
                result = self.server.service.launch_desktop_update(self._json_body())
            elif route == "accept-suggestion":
                result = self.server.service.accept_suggestion(str(self._json_body().get("suggestion_id", "")))
            elif route == "review":
                result = self.server.service.save_review(self._json_body())
            elif route == "queue-limit":
                result = self.server.service.set_queue_limit(self._json_body())
            elif route == "external-action-kill-switch":
                result = self.server.service.disable_external_actions(self._json_body())
            elif route == "intake-control":
                result = self.server.service.set_intake_control(self._json_body())
            elif route == "run-local-wake":
                result = self.server.service.run_local_wake(self._json_body())
            elif route == "start-browser-assist":
                result = self.server.service.start_browser_assist(self._json_body())
            elif route == "plan-application-with-ai":
                result = self.server.service.plan_application_with_ai(self._json_body())
            elif route == "start-application-with-ai":
                result = self.server.service.start_application_with_ai(self._json_body())
            elif route == "start-job-with-ai":
                result = self.server.service.start_job_with_ai(self._json_body())
            elif route == "start-guided-intake":
                result = self.server.service.start_guided_intake(self._json_body())
            elif route == "cancel-guided-intake":
                result = self.server.service.cancel_guided_intake(self._json_body())
            elif route == "select-guided-search-candidate":
                result = self.server.service.select_guided_search_candidate(self._json_body())
            elif route == "resolve-browser-assist-unknown":
                result = self.server.service.resolve_browser_assist_unknown(self._json_body())
            elif route == "pair-local-agent-assist":
                result = self.server.service.pair_local_agent_assist(self._json_body())
            elif route == "prepare-local-agent-assist":
                result = self.server.service.prepare_local_agent_assist(self._json_body())
            elif route == "discover-local-agent-dynamic-fields":
                result = self.server.service.discover_local_agent_dynamic_fields(self._json_body())
            elif route == "complete-local-agent-assist":
                result = self.server.service.complete_local_agent_assist(self._json_body())
            elif route == "take-local-agent-assist-file":
                file_request = self._json_body()
                raw: bytearray | None = None
                try:
                    raw, _ = self.server.service.take_local_agent_assist_file(
                        assist_token=str(file_request.get("assist_token", "")),
                        file_token=str(file_request.get("file_token", "")),
                    )
                    self._send_bytes(HTTPStatus.OK, raw, "application/octet-stream")
                finally:
                    if raw is not None:
                        raw[:] = b"\0" * len(raw)
                return
            elif route == "prepare-synthetic-execution":
                result = self.server.service.prepare_synthetic_execution(self._json_body())
            elif route == "complete-synthetic-execution":
                result = self.server.service.complete_synthetic_execution(self._json_body())
            elif route == "review-packet":
                result = self.server.service.review_packet(str(self._json_body().get("application_id", "")))
            elif route == "resolve-application-fields":
                result = self.server.service.resolve_application_fields(self._json_body())
            elif route == "queue-decision":
                result = self.server.service.decide_review_packet(self._json_body())
            elif route == "approve-and-start-application":
                result = self.server.service.approve_and_start_application(self._json_body())
            elif route == "approve-external-claims":
                result = self.server.service.approve_external_claims(self._json_body())
            elif route == "tailoring-manifest-proposal":
                self._optional_json_body()
                result = self.server.service.tailoring_manifest_proposal()
            elif route == "approve-tailoring-manifest":
                result = self.server.service.approve_tailoring_manifest(self._json_body())
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
            elif route == "prepare-offline-application":
                length = self._binary_body_length(
                    maximum=MAX_OFFLINE_APPLICATION_BUNDLE_BYTES + APPLICATION_BUNDLE_MANIFEST_LIMIT + 4,
                    size_code="APPLICATION_BUNDLE_SIZE_INVALID",
                    size_message="The selected local application bundle is empty or too large.",
                )
                metadata, files = self._application_bundle_body(length)
                result = self.server.service.prepare_offline_application_bundle(metadata=metadata, files=files)
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
