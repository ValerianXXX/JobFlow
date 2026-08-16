from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .errors import JobOpsError
from .util import has_reparse_component


BINDING_SCHEMA_VERSION = 1
BINDING_ALGORITHM = "HMAC-SHA256"
BINDING_FILENAME = "browser-companion-binding.json"
PAIR_RESPONSE_FIELDS = (
    "allowed_company_domain",
    "allowed_page_origin",
    "application_id",
    "assist_id",
    "capture_status",
    "current_step",
    "discovery_mode",
    "expires_at",
    "intake_id",
    "max_steps",
    "mode",
    "official_url",
    "provider",
    "preferred_tab_id",
    "route_kind",
    "search_query",
    "status",
)
_B64URL = re.compile(r"[A-Za-z0-9_-]+")


def binding_path(*, local_app_data: Path | None = None) -> Path:
    base = local_app_data or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "JobOps" / BINDING_FILENAME


def _decode_b64url(value: str, *, code: str) -> bytes:
    if not value or not _B64URL.fullmatch(value):
        raise JobOpsError(code, "The Browser Companion installation binding is invalid.")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise JobOpsError(code, "The Browser Companion installation binding is invalid.") from exc


def _load_binding(*, local_app_data: Path | None = None) -> tuple[str, bytes]:
    path = binding_path(local_app_data=local_app_data)
    root = path.parent
    if has_reparse_component(path, root) or not path.is_file():
        raise JobOpsError(
            "BROWSER_COMPANION_BINDING_MISSING",
            "Reinstall the Browser Companion on this Windows account before pairing.",
        )
    try:
        if path.stat().st_size > 4096:
            raise ValueError("oversized")
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise JobOpsError(
            "BROWSER_COMPANION_BINDING_INVALID",
            "Reinstall the Browser Companion because its local binding is invalid.",
        ) from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "installation_id", "secret_b64url"}:
        raise JobOpsError("BROWSER_COMPANION_BINDING_INVALID", "The Browser Companion installation binding is invalid.")
    installation_id = str(value.get("installation_id", ""))
    if value.get("schema_version") != BINDING_SCHEMA_VERSION or not re.fullmatch(r"[a-f0-9]{32}", installation_id):
        raise JobOpsError("BROWSER_COMPANION_BINDING_INVALID", "The Browser Companion installation binding is invalid.")
    secret = _decode_b64url(str(value.get("secret_b64url", "")), code="BROWSER_COMPANION_BINDING_INVALID")
    if len(secret) != 32:
        raise JobOpsError("BROWSER_COMPANION_BINDING_INVALID", "The Browser Companion installation binding is invalid.")
    return installation_id, secret


def _binding_request(value: Mapping[str, Any]) -> tuple[str, str]:
    if set(value) != {"schema_version", "algorithm", "installation_id", "challenge"}:
        raise JobOpsError("BROWSER_COMPANION_BINDING_REQUEST_INVALID", "The Browser Companion binding request is invalid.")
    installation_id = str(value.get("installation_id", ""))
    challenge = str(value.get("challenge", ""))
    if (
        value.get("schema_version") != BINDING_SCHEMA_VERSION
        or value.get("algorithm") != BINDING_ALGORITHM
        or not re.fullmatch(r"[a-f0-9]{32}", installation_id)
        or len(_decode_b64url(challenge, code="BROWSER_COMPANION_BINDING_REQUEST_INVALID")) != 32
    ):
        raise JobOpsError("BROWSER_COMPANION_BINDING_REQUEST_INVALID", "The Browser Companion binding request is invalid.")
    return installation_id, challenge


def canonical_pair_message(
    *,
    protocol_version: int,
    extension_version: str,
    base_url: str,
    assist_path: str,
    installation_id: str,
    challenge: str,
    response: Mapping[str, Any],
) -> bytes:
    values = {
        "assist_path": str(assist_path),
        "base_url": str(base_url),
        "challenge": str(challenge),
        "extension_version": str(extension_version),
        "installation_id": str(installation_id),
        "protocol_version": str(protocol_version),
    }
    for key in PAIR_RESPONSE_FIELDS:
        raw = response.get(key)
        values[f"response.{key}"] = "" if raw is None else str(raw)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def validate_pair_request(
    binding_request: Mapping[str, Any], *, local_app_data: Path | None = None,
) -> tuple[str, str]:
    requested_installation_id, challenge = _binding_request(binding_request)
    installed_id, _secret = _load_binding(local_app_data=local_app_data)
    if not hmac.compare_digest(requested_installation_id, installed_id):
        raise JobOpsError(
            "BROWSER_COMPANION_BINDING_MISMATCH",
            "Reload the Browser Companion installed for this Windows account.",
        )
    return installed_id, challenge


def sign_pair_response(
    *,
    protocol_version: int,
    extension_version: str,
    base_url: str,
    assist_path: str,
    binding_request: Mapping[str, Any],
    response: Mapping[str, Any],
    local_app_data: Path | None = None,
) -> dict[str, Any]:
    requested_installation_id, challenge = _binding_request(binding_request)
    installed_id, secret = _load_binding(local_app_data=local_app_data)
    if not hmac.compare_digest(requested_installation_id, installed_id):
        raise JobOpsError(
            "BROWSER_COMPANION_BINDING_MISMATCH",
            "Reload the Browser Companion installed for this Windows account.",
        )
    message = canonical_pair_message(
        protocol_version=protocol_version,
        extension_version=extension_version,
        base_url=base_url,
        assist_path=assist_path,
        installation_id=installed_id,
        challenge=challenge,
        response=response,
    )
    proof = base64.urlsafe_b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode("ascii").rstrip("=")
    return {
        "schema_version": BINDING_SCHEMA_VERSION,
        "algorithm": BINDING_ALGORITHM,
        "installation_id": installed_id,
        "challenge": challenge,
        "proof": proof,
    }
