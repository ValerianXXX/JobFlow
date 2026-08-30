from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

from .companion_binding import sign_pair_response
from .errors import JobOpsError
from .release_toolchain import (
    ReleaseToolchainError,
    _close_handle,
    _embedded_signer_identity,
    _handle_information,
    _has_absolute_reparse_component,
    _open_locked_directory,
    _open_locked_read,
    _windows_signature_valid,
)
from .util import canonical_json, iso_utc, load_json


_PROTOCOL_VERSION = 2
_MAX_PAIR_BODY_BYTES = 8 * 1024
_STRICT_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_EXTENSION_ID = re.compile(r"[a-p]{32}")
_DEVELOPMENT_EXTENSION_ID = "hhlliaaafegldkmcgmaoaelabipcaooj"
_PAIR_REQUEST_KEYS = {"protocol_version", "extension_version", "companion_binding"}
_CHANNELS = ("chrome", "edge")
_STORE_HOSTS = {
    "chrome": "chromewebstore.google.com",
    "edge": "microsoftedge.microsoft.com",
}
_STORE_PATH_PREFIXES = {
    "chrome": "/detail/",
    "edge": "/addons/detail/",
}
_BROWSER_SIGNER_COMPONENTS = {
    "chrome": frozenset({"cn=google llc", "o=google llc"}),
    "edge": frozenset({"cn=microsoft corporation", "o=microsoft corporation"}),
}
_BROWSER_EXECUTABLES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
}


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _store_extension_id(url: object, *, store: str) -> str:
    if store not in _STORE_HOSTS or not isinstance(url, str):
        raise JobOpsError(
            "CLEAN_WINDOWS_STORE_POLICY_INVALID",
            "The Browser Companion store policy is invalid.",
            store=store,
        )
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_STORE_POLICY_INVALID",
            "The Browser Companion store policy is invalid.",
            store=store,
        ) from error
    prefix = _STORE_PATH_PREFIXES[store]
    extension_id = parsed.path.removeprefix(prefix)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _STORE_HOSTS[store]
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != prefix + extension_id
        or parsed.query
        or parsed.fragment
        or _EXTENSION_ID.fullmatch(extension_id) is None
    ):
        raise JobOpsError(
            "CLEAN_WINDOWS_STORE_POLICY_INVALID",
            "The Browser Companion store policy is invalid.",
            store=store,
        )
    return extension_id


def _load_browser_policy(project: Path) -> tuple[str, dict[str, str], dict[str, str]]:
    try:
        manifest = load_json(project / "browser-companion" / "manifest.json")
        stores = load_json(project / "config" / "browser-companion-stores.json")
    except (OSError, TypeError, ValueError) as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_POLICY_MISSING",
            "The clean-Windows Browser Companion policy is unavailable.",
        ) from error
    version = manifest.get("version")
    if not isinstance(version, str) or _STRICT_SEMVER.fullmatch(version) is None:
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_POLICY_INVALID",
            "The Browser Companion version policy is invalid.",
        )
    store_urls = {
        "chrome": stores.get("chrome_web_store_url"),
        "edge": stores.get("edge_addons_url"),
    }
    extension_ids = {
        channel: _store_extension_id(store_urls[channel], store=channel)
        for channel in _CHANNELS
    }
    configured_ids = stores.get("extension_ids")
    if (
        stores.get("schema_version") != 1
        or not isinstance(configured_ids, list)
        or len(configured_ids) != 3
        or not all(isinstance(item, str) for item in configured_ids)
        or len(set(configured_ids)) != 3
        or set(configured_ids) != {*extension_ids.values(), _DEVELOPMENT_EXTENSION_ID}
        or len(set(extension_ids.values())) != 2
    ):
        raise JobOpsError(
            "CLEAN_WINDOWS_STORE_POLICY_INVALID",
            "The Browser Companion store policy is invalid.",
        )
    return version, extension_ids, {channel: str(store_urls[channel]) for channel in _CHANNELS}


def _browser_identity_matches(channel: str, user_agent: str, client_hints: str) -> bool:
    user_agent = str(user_agent)
    client_hints = str(client_hints)
    if "Windows NT" not in user_agent:
        return False
    if channel == "edge":
        return re.search(r"\bEdg/[0-9]+", user_agent) is not None
    return (
        re.search(r"\bChrome/[0-9]+", user_agent) is not None
        and re.search(r"\b(?:Edg|OPR)/[0-9]+", user_agent) is None
        and (not client_hints or "Google Chrome" in client_hints)
        and "Microsoft Edge" not in client_hints
    )


def _registered_browser_path(executable_name: str) -> Path:
    if os.name != "nt":
        raise JobOpsError(
            "CLEAN_WINDOWS_PLATFORM_REQUIRED",
            "Clean-Windows browser acceptance must run on Windows.",
        )
    try:
        import winreg
    except ImportError as error:  # pragma: no cover - Windows supplies winreg
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_REGISTRY_UNAVAILABLE",
            "The Windows browser registry is unavailable.",
        ) from error
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}"
    views = tuple(dict.fromkeys((0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)))
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    raw, _kind = winreg.QueryValueEx(key, None)
                candidate = Path(str(raw).strip().strip('"'))
                if (
                    candidate.is_absolute()
                    and candidate.is_file()
                    and candidate.name.casefold() == executable_name.casefold()
                ):
                    return candidate.resolve(strict=True)
            except (FileNotFoundError, OSError, ValueError):
                continue
    raise JobOpsError(
        "CLEAN_WINDOWS_BROWSER_MISSING",
        "Install both Google Chrome and Microsoft Edge before clean-Windows acceptance.",
        browser=executable_name,
    )


def _browser_signer_matches(channel: str, subject: str) -> bool:
    required = _BROWSER_SIGNER_COMPONENTS.get(channel)
    if required is None or not isinstance(subject, str):
        return False
    components = {item.strip().casefold() for item in subject.split(",") if item.strip()}
    return required.issubset(components)


@contextmanager
def _locked_authenticated_browser(path: Path, *, channel: str) -> Iterator[Path]:
    """Authenticate and path-lock a browser until CreateProcess has consumed it."""

    expected_name = _BROWSER_EXECUTABLES.get(channel)
    if expected_name is None:
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_CHANNEL_INVALID",
            "The clean-Windows browser channel is invalid.",
        )
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_IDENTITY_UNSAFE",
            "A registered clean-Windows browser has an unsafe file identity.",
            browser=channel,
        ) from error
    if (
        not resolved.is_file()
        or resolved.name.casefold() != expected_name.casefold()
        or _has_absolute_reparse_component(resolved)
    ):
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_IDENTITY_UNSAFE",
            "A registered clean-Windows browser has an unsafe file identity.",
            browser=channel,
        )

    ancestor_handles: list[int] = []
    file_handle = -1
    try:
        for ancestor in reversed(resolved.parents):
            ancestor_handles.append(_open_locked_directory(ancestor))
        file_handle = _open_locked_read(resolved)
        identity = _handle_information(file_handle)
        if not _windows_signature_valid(resolved):
            raise JobOpsError(
                "CLEAN_WINDOWS_BROWSER_SIGNATURE_INVALID",
                "A registered clean-Windows browser does not have a valid Windows signature.",
                browser=channel,
            )
        subject, _thumbprint = _embedded_signer_identity(resolved)
        if not _browser_signer_matches(channel, subject):
            raise JobOpsError(
                "CLEAN_WINDOWS_BROWSER_PUBLISHER_INVALID",
                "A registered clean-Windows browser is not signed by the required publisher.",
                browser=channel,
            )
        yield resolved
        if _handle_information(file_handle) != identity:
            raise JobOpsError(
                "CLEAN_WINDOWS_BROWSER_IDENTITY_CHANGED",
                "A registered clean-Windows browser changed while it was being opened.",
                browser=channel,
            )
    except ReleaseToolchainError as error:
        raise JobOpsError(
            "CLEAN_WINDOWS_BROWSER_IDENTITY_UNSAFE",
            "A registered clean-Windows browser has an unsafe file identity.",
            browser=channel,
        ) from error
    finally:
        if file_handle >= 0:
            _close_handle(file_handle)
        for handle in reversed(ancestor_handles):
            _close_handle(handle)


class _ProbeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    probe: "BrowserAcceptanceProbe"


class _ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def probe(self) -> "BrowserAcceptanceProbe":
        return self.server.probe  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _valid_host(self) -> bool:
        return self.headers.get("Host", "").casefold() == self.probe.host_header.casefold()

    def _security_headers(self, content_type: str, *, origin: str | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin" if origin else "same-origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        origin: str | None = None,
    ) -> None:
        self.send_response(status)
        self._security_headers(content_type, origin=origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(
        self,
        status: HTTPStatus,
        value: Mapping[str, Any],
        *,
        origin: str | None = None,
    ) -> None:
        self._send_bytes(
            status,
            canonical_json(dict(value)),
            "application/json; charset=utf-8",
            origin=origin,
        )

    def _blocked(self, status: HTTPStatus, code: str, *, origin: str | None = None) -> None:
        self.close_connection = True
        self._send_json(
            status,
            {"status": "BLOCKED", "code": code, "automatic_retry": False},
            origin=origin,
        )

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._blocked(HTTPStatus.FORBIDDEN, "CLEAN_WINDOWS_PROBE_HOST_FORBIDDEN")
            return
        channel = self.probe.channel_for_page_path(self.path)
        if channel is None:
            self._blocked(HTTPStatus.NOT_FOUND, "CLEAN_WINDOWS_PROBE_NOT_FOUND")
            return
        body, nonce = self.probe.page(channel)
        self.send_response(HTTPStatus.OK)
        self._security_headers("text/html; charset=utf-8")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
            "img-src 'none'; connect-src 'none'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        channel = self.probe.channel_for_pair_path(self.path)
        origin = self.headers.get("Origin", "")
        if (
            not self._valid_host()
            or channel is None
            or not self.probe.origin_matches(channel, origin)
        ):
            self._blocked(HTTPStatus.FORBIDDEN, "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers("text/plain; charset=utf-8", origin=origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "60")
        if self.headers.get("Access-Control-Request-Private-Network", "").casefold() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        channel = self.probe.channel_for_pair_path(self.path)
        origin = self.headers.get("Origin", "")
        if (
            not self._valid_host()
            or channel is None
            or not self.probe.origin_matches(channel, origin)
        ):
            self._blocked(HTTPStatus.FORBIDDEN, "CLEAN_WINDOWS_PROBE_ORIGIN_FORBIDDEN")
            return
        if not _browser_identity_matches(
            channel,
            self.headers.get("User-Agent", ""),
            self.headers.get("Sec-CH-UA", ""),
        ):
            self._blocked(
                HTTPStatus.FORBIDDEN,
                "CLEAN_WINDOWS_BROWSER_IDENTITY_MISMATCH",
                origin=origin,
            )
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._blocked(HTTPStatus.BAD_REQUEST, "CLEAN_WINDOWS_PAIR_BODY_INVALID", origin=origin)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 2 or length > _MAX_PAIR_BODY_BYTES:
            self._blocked(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > _MAX_PAIR_BODY_BYTES else HTTPStatus.BAD_REQUEST,
                "CLEAN_WINDOWS_PAIR_BODY_INVALID",
                origin=origin,
            )
            return
        if self.headers.get_content_type().casefold() != "application/json":
            self._blocked(HTTPStatus.BAD_REQUEST, "CLEAN_WINDOWS_PAIR_CONTENT_TYPE_INVALID", origin=origin)
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey):
            self._blocked(HTTPStatus.BAD_REQUEST, "CLEAN_WINDOWS_PAIR_BODY_INVALID", origin=origin)
            return
        if not isinstance(payload, dict) or set(payload) != _PAIR_REQUEST_KEYS:
            self._blocked(HTTPStatus.BAD_REQUEST, "CLEAN_WINDOWS_PAIR_SCHEMA_INVALID", origin=origin)
            return
        if payload.get("protocol_version") != _PROTOCOL_VERSION:
            self._blocked(HTTPStatus.CONFLICT, "CLEAN_WINDOWS_PROTOCOL_VERSION_MISMATCH", origin=origin)
            return
        if payload.get("extension_version") != self.probe.version:
            self._blocked(HTTPStatus.CONFLICT, "CLEAN_WINDOWS_EXTENSION_VERSION_MISMATCH", origin=origin)
            return
        if not isinstance(payload.get("companion_binding"), dict):
            self._blocked(HTTPStatus.BAD_REQUEST, "CLEAN_WINDOWS_NATIVE_BINDING_INVALID", origin=origin)
            return
        try:
            result = self.probe.accept_pair(channel, payload)
        except JobOpsError as error:
            self._blocked(HTTPStatus.FORBIDDEN, error.code, origin=origin)
            return
        self._send_json(HTTPStatus.OK, result, origin=origin)


class BrowserAcceptanceProbe:
    """Observe exact Chrome and Edge store builds without trusting checkboxes.

    A PASS requires each registered browser to open its own unguessable local
    route, the corresponding store extension origin to report the exact source
    version, and that extension to complete the installed native HMAC binding.
    The returned observation contains no path, extension origin, token, secret,
    or private applicant value and has no authority outside the enclosing
    clean-Windows acceptance run.
    """

    def __init__(self, project: Path, *, local_app_data: Path | None = None) -> None:
        self.project = Path(os.path.abspath(project))
        self.version, self.extension_ids, self.store_urls = _load_browser_policy(self.project)
        self.local_app_data = local_app_data
        self._tokens = {channel: secrets.token_urlsafe(48) for channel in _CHANNELS}
        self._page_paths = {channel: f"/clean-windows/{self._tokens[channel]}" for channel in _CHANNELS}
        self._pair_paths = {channel: f"/intake/{self._tokens[channel]}/pair" for channel in _CHANNELS}
        self._condition = threading.Condition()
        self._observed: set[str] = set()
        self._server: _ProbeHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._launched_processes: list[subprocess.Popen[Any]] = []

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise JobOpsError("CLEAN_WINDOWS_PROBE_NOT_STARTED", "Start browser acceptance before using it.")
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def host_header(self) -> str:
        return self.base_url.removeprefix("http://")

    def page_url(self, channel: str) -> str:
        if channel not in _CHANNELS:
            raise ValueError(channel)
        return self.base_url + self._page_paths[channel]

    def channel_for_page_path(self, path: str) -> str | None:
        return next((channel for channel, expected in self._page_paths.items() if path == expected), None)

    def channel_for_pair_path(self, path: str) -> str | None:
        return next((channel for channel, expected in self._pair_paths.items() if path == expected), None)

    def origin_matches(self, channel: str, origin: str) -> bool:
        return secrets.compare_digest(origin, f"chrome-extension://{self.extension_ids[channel]}")

    def page(self, channel: str) -> tuple[bytes, str]:
        nonce = secrets.token_urlsafe(24)
        pairing = {
            "protocol_version": _PROTOCOL_VERSION,
            "base_url": self.base_url,
            "assist_path": self._pair_paths[channel].removesuffix("/pair"),
        }
        extension_id = self.extension_ids[channel]
        store_url = html.escape(self.store_urls[channel], quote=True)
        browser_name = "Google Chrome" if channel == "chrome" else "Microsoft Edge"
        script = f"""
const extensionId = {json.dumps(extension_id)};
const pairing = Object.freeze({json.dumps(pairing, separators=(',', ':'))});
const status = document.getElementById('status');
let bridgeSent = false;
function show(value) {{ status.textContent = value; }}
function directPair() {{
  if (!globalThis.chrome?.runtime?.sendMessage) return;
  try {{
    chrome.runtime.sendMessage(extensionId, {{type: 'JOBFLOW_PAIR', pairing}}, (result) => {{
      if (chrome.runtime.lastError) {{ show('Extension not detected yet / 尚未检测到扩展'); return; }}
      if (result?.status === 'BLOCKED') show('Pairing blocked; repair and retry / 配对被阻止，请修复后重试');
      else show('Browser channel observed / 已观测到浏览器通道');
    }});
  }} catch (_error) {{ show('Extension not detected yet / 尚未检测到扩展'); }}
}}
window.addEventListener('message', (event) => {{
  if (event.source !== window || event.origin !== location.origin || !event.data) return;
  if (event.data.type === 'JOBFLOW_COMPANION_READY' && !bridgeSent) {{
    bridgeSent = true;
    window.postMessage({{type: 'JOBFLOW_PAIR_REQUEST', protocol_version: 2, pairing}}, location.origin);
  }}
  if (event.data.type === 'JOBFLOW_PAIR_RESULT') show(
    event.data.result?.status === 'BLOCKED'
      ? 'Pairing blocked; repair and retry / 配对被阻止，请修复后重试'
      : 'Browser channel observed / 已观测到浏览器通道'
  );
}});
directPair();
setTimeout(directPair, 1500);
"""
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JobFlow clean Windows acceptance</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;max-width:760px;margin:7vh auto;padding:28px;color:#132029}}.card{{border:2px solid #2f6f5e;padding:26px}}a{{color:#215f50}}#status{{font-weight:700}}</style></head>
<body><main class="card"><h1>{browser_name} acceptance</h1>
<p>This local page verifies the exact public Browser Companion build. It does not open or read a recruiting site.</p>
<p>此本地页面仅验证公开商店扩展，不会打开或读取招聘网站。</p>
<p id="status">Checking Browser Companion / 正在检查浏览器伴侣</p>
<p>If the extension is not installed, <a href="{store_url}" rel="noreferrer">install it from the official store</a>, return here, and refresh.</p>
<script nonce="{nonce}">{script}</script></main></body></html>"""
        return body.encode("utf-8"), nonce

    def start(self) -> "BrowserAcceptanceProbe":
        if self._server is not None:
            raise JobOpsError("CLEAN_WINDOWS_PROBE_ALREADY_STARTED", "Browser acceptance is already running.")
        server = _ProbeHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        server.probe = self
        thread = threading.Thread(target=server.serve_forever, name="jobflow-clean-windows-probe", daemon=True)
        self._server = server
        self._thread = thread
        thread.start()
        return self

    def open_browsers(
        self,
        *,
        launcher: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        resolver: Callable[[str], Path] = _registered_browser_path,
    ) -> tuple[str, str]:
        if self._server is None:
            raise JobOpsError("CLEAN_WINDOWS_PROBE_NOT_STARTED", "Start browser acceptance before opening browsers.")
        for channel, executable in _BROWSER_EXECUTABLES.items():
            with _locked_authenticated_browser(resolver(executable), channel=channel) as browser:
                try:
                    process = launcher(
                        [str(browser), "--new-window", self.page_url(channel)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        close_fds=True,
                    )
                except OSError as error:
                    raise JobOpsError(
                        "CLEAN_WINDOWS_BROWSER_LAUNCH_FAILED",
                        "A required clean-Windows browser could not be opened.",
                        browser=channel,
                    ) from error
                self._launched_processes.append(process)
        return _CHANNELS

    def accept_pair(self, channel: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if channel not in _CHANNELS:
            raise JobOpsError("CLEAN_WINDOWS_BROWSER_CHANNEL_INVALID", "The browser channel is invalid.")
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        result: dict[str, Any] = {
            "status": "GUIDED_INTAKE_PAIRED",
            "mode": "JOB_CAPTURE",
            "capture_status": "REVIEW_PACKET_READY",
            "intake_id": "GIN-CLEAN-WINDOWS-ACCEPTANCE",
            "allowed_company_domain": "example.invalid",
            "discovery_mode": "DIRECT_OFFICIAL_URL",
            "official_url": "https://example.invalid/jobflow-clean-windows-acceptance",
            "search_query": "",
            "current_step": 1,
            "max_steps": 1,
            "preferred_tab_id": None,
            "expires_at": iso_utc(expires),
        }
        result["companion_binding"] = sign_pair_response(
            protocol_version=_PROTOCOL_VERSION,
            extension_version=self.version,
            base_url=self.base_url,
            assist_path=self._pair_paths[channel].removesuffix("/pair"),
            binding_request=payload["companion_binding"],
            response=result,
            local_app_data=self.local_app_data,
        )
        with self._condition:
            self._observed.add(channel)
            self._condition.notify_all()
        return result

    def wait(self, timeout_seconds: float) -> dict[str, Any]:
        if self._server is None:
            raise JobOpsError("CLEAN_WINDOWS_PROBE_NOT_STARTED", "Start browser acceptance before waiting.")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0 or timeout_seconds > 900:
            raise JobOpsError(
                "CLEAN_WINDOWS_PROBE_TIMEOUT_INVALID",
                "Browser acceptance timeout must be between 1 and 900 seconds.",
            )
        with self._condition:
            completed = self._condition.wait_for(
                lambda: self._observed == set(_CHANNELS),
                timeout=float(timeout_seconds),
            )
            observed = set(self._observed)
        if not completed:
            raise JobOpsError(
                "CLEAN_WINDOWS_BROWSER_ACCEPTANCE_TIMEOUT",
                "Both exact store extensions must pair from their registered browsers before acceptance can pass.",
                observed_channels=len(observed),
                required_channels=len(_CHANNELS),
                automatic_retry=False,
            )
        return {
            "schema_version": 1,
            "format": "JOBFLOW_CLEAN_WINDOWS_BROWSER_OBSERVATION_V1",
            "status": "PASS",
            "browser_companion": {
                "version": self.version,
                "chrome_store_install_observed": True,
                "edge_store_install_observed": True,
                "chrome_pairing_observed": True,
                "edge_pairing_observed": True,
                "native_binding_proof_observed": True,
            },
            "safety": {
                "external_actions": 0,
                "real_job_site_visits": 0,
                "final_submit_attempts": 0,
                "secret_material_in_observation": False,
            },
        }

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> "BrowserAcceptanceProbe":
        return self.start()

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()
