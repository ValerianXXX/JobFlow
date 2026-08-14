from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .ai_runtime import (
    AI_QUALITY_CONTRACT,
    MAX_AI_OUTPUT_BYTES,
    AIAnalysisEngine,
    LocalSubprocessAIEngine,
    _candidate_filter_summary,
    _merge_candidate_filter_diagnostics,
    _run_bounded_ai_command,
    _structural_quality_summary,
    configured_ai_engine,
)
from .errors import JobOpsError
from .util import iso_utc
from .source_quality import safe_ai_failure_category


MAX_CONNECTION_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_WSL_DISCOVERY_BYTES = 64 * 1024
MAX_WSL_DISTRIBUTIONS = 16
MAX_EMBEDDED_JSON_STARTS = 32
AI_CAPABILITY_TEST_VERSION = 1
AI_CAPABILITY_TEST_TEXT = (
    "At Synthetic Evidence Lab, a Project Analyst built a 120-row review tracker in 2024 "
    "and reduced review time by 20%."
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
AGENT_CONNECTORS = {
    "hermes_proxy",
    "wsl_hermes_proxy",
    "wsl_hermes_agent",
    "openclaw",
    "wsl_openclaw",
}
SAFE_MODEL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,299}$")
SAFE_PROVIDER_REF = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
LOCAL_CONNECTORS = {
    "ollama": {
        "label": "Ollama",
        "base_url": "http://127.0.0.1:11434",
        "models_path": "/api/tags",
        "api_style": "ollama",
    },
    "lm_studio": {
        "label": "LM Studio",
        "base_url": "http://127.0.0.1:1234",
        "models_path": "/v1/models",
        "api_style": "openai",
    },
    "openai_local_8080": {
        "label": "LocalAI / llama.cpp",
        "base_url": "http://127.0.0.1:8080",
        "models_path": "/v1/models",
        "api_style": "openai",
    },
    "openai_local_8000": {
        "label": "vLLM / local OpenAI server",
        "base_url": "http://127.0.0.1:8000",
        "models_path": "/v1/models",
        "api_style": "openai",
    },
}


HTTPJSON = Callable[..., dict[str, Any]]
CommandResolver = Callable[[str], str | None]


_HERMES_METADATA_PROBE_CODE = r'''
# JOBOPS_HERMES_METADATA_V1
import contextlib
import io
import json
import logging

try:
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from hermes_cli.config import load_config
        config = load_config()
        model_config = config.get("model", {}) if isinstance(config, dict) else {}
        if isinstance(model_config, dict):
            model = str(model_config.get("default") or model_config.get("model") or "").strip()
            provider = str(model_config.get("provider") or "").strip()
        elif isinstance(model_config, str):
            model = model_config.strip()
            provider = ""
        else:
            model = ""
            provider = ""
    print(json.dumps({"ok": bool(model and provider), "model": model, "provider": provider}, separators=(",", ":")))
except BaseException:
    print('{"ok":false}')
'''.strip()


_HERMES_SAFE_ADAPTER_CODE = r'''
# JOBOPS_HERMES_SAFE_ADAPTER_V1
import contextlib
import io
import json
import logging
import os
import sys

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
tool_calls = []

def emit(value, exit_code=0):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()
    raise SystemExit(exit_code)

try:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if not raw or len(raw) > MAX_INPUT:
        emit({"ok": False, "status": "error", "toolSummary": {"calls": 0, "tools": []}}, 2)
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict):
        emit({"ok": False, "status": "error", "toolSummary": {"calls": 0, "tools": []}}, 2)

    for name in ("HERMES_KANBAN_TASK", "HERMES_YOLO_MODE", "HERMES_ACCEPT_HOOKS"):
        os.environ.pop(name, None)
    logging.disable(logging.CRITICAL)

    def block_tool(*args, **kwargs):
        tool_calls.append("blocked")
        raise RuntimeError("JobOps blocks every Hermes tool during document analysis")

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from run_agent import AIAgent

        config = load_config()
        model_config = config.get("model", {}) if isinstance(config, dict) else {}
        if isinstance(model_config, dict):
            model = str(model_config.get("default") or model_config.get("model") or "").strip()
            configured_provider = str(model_config.get("provider") or "").strip()
        elif isinstance(model_config, str):
            model = model_config.strip()
            configured_provider = ""
        else:
            model = ""
            configured_provider = ""
        if not model or not configured_provider:
            raise RuntimeError("Hermes has no configured model provider")

        runtime = resolve_runtime_provider(
            requested=configured_provider,
            target_model=model,
        )
        agent = AIAgent(
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            base_url=runtime.get("base_url"),
            api_key=runtime.get("api_key"),
            acp_command=runtime.get("acp_command"),
            acp_args=list(runtime.get("acp_args") or []),
            command=runtime.get("command"),
            args=list(runtime.get("args") or []),
            model=model,
            max_iterations=1,
            enabled_toolsets=[],
            disabled_toolsets=["all"],
            save_trajectories=False,
            verbose_logging=False,
            quiet_mode=True,
            ephemeral_system_prompt=(
                "You are the JobOps private structured-analysis engine. "
                "Return only the JSON requested by the input. Never browse, call tools, "
                "access files, or invent personal facts."
            ),
            tool_start_callback=block_tool,
            platform="jobops",
            skip_context_files=True,
            load_soul_identity=False,
            skip_memory=True,
            session_db=None,
            fallback_model=None,
            credential_pool=runtime.get("credential_pool"),
            checkpoints_enabled=False,
            pass_session_id=False,
        )
        if agent.tools:
            raise RuntimeError("Hermes exposed tools to the JobOps adapter")
        response = agent.chat(json.dumps(request, ensure_ascii=False, separators=(",", ":")))

    if tool_calls:
        emit({"ok": False, "status": "error", "toolSummary": {"calls": len(tool_calls), "tools": ["blocked"]}}, 3)
    if not isinstance(response, str) or not response.strip() or len(response.encode("utf-8")) > MAX_OUTPUT:
        emit({"ok": False, "status": "error", "toolSummary": {"calls": 0, "tools": []}}, 4)
    emit({
        "ok": True,
        "status": "ok",
        "toolSummary": {"calls": 0, "tools": []},
        "result": {"content": response},
    })
except SystemExit:
    raise
except BaseException:
    emit({"ok": False, "status": "error", "toolSummary": {"calls": len(tool_calls), "tools": []}}, 5)
'''.strip()


def _bounded_output_bytes(value: str | bytes | None, *, limit: int) -> bytes:
    if value is None:
        return b""
    raw = value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
    if len(raw) > limit:
        raise JobOpsError("AI_WSL_RESPONSE_INVALID", "WSL returned an oversized discovery response.")
    return raw


def _decode_wsl_distribution_output(value: str | bytes | None) -> list[str]:
    """Decode `wsl --list --quiet` without exposing or persisting distro names."""
    raw = _bounded_output_bytes(value, limit=MAX_WSL_DISCOVERY_BYTES)
    if not raw:
        return []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw.count(b"\x00") > max(2, len(raw) // 5):
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le"
    else:
        encoding = "utf-8"
    try:
        decoded = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise JobOpsError("AI_WSL_RESPONSE_INVALID", "WSL returned an unreadable distribution list.") from exc
    output: list[str] = []
    seen: set[str] = set()
    for raw_line in decoded.replace("\x00", "").splitlines():
        name = raw_line.strip()
        if (
            not name
            or len(name) > 128
            or name.startswith("-")
            or name in {".", ".."}
            or any(ord(character) < 32 for character in name)
            or any(character in "\\/" for character in name)
        ):
            continue
        folded = name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        output.append(name)
        if len(output) >= MAX_WSL_DISTRIBUTIONS:
            break
    return output


def _assert_loopback_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.username or parsed.password:
        raise JobOpsError("AI_LOOPBACK_REQUIRED", "AI auto-connection accepts only an unauthenticated local loopback endpoint.")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _loopback_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    _assert_loopback_url(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": " ".join(("Bearer", "jobops-local")),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=max(0.2, min(float(timeout), 600.0))) as response:
            raw = response.read(MAX_CONNECTION_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "The selected local AI endpoint is not available.") from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "The selected local AI endpoint is not available.") from exc
    if not raw or len(raw) > MAX_CONNECTION_RESPONSE_BYTES:
        raise JobOpsError("AI_LOCAL_RESPONSE_INVALID", "The local AI endpoint returned an empty or oversized response.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobOpsError("AI_LOCAL_RESPONSE_INVALID", "The local AI endpoint did not return valid JSON.") from exc
    if not isinstance(value, dict):
        raise JobOpsError("AI_LOCAL_RESPONSE_INVALID", "The local AI endpoint response must be a JSON object.")
    return value


def _json_from_text(value: str) -> Any:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        attempts = 0
        for index, character in enumerate(text):
            if character not in "[{":
                continue
            attempts += 1
            if attempts > MAX_EMBEDDED_JSON_STARTS:
                break
            try:
                candidate, _ = decoder.raw_decode(text[index:])
                return candidate
            except json.JSONDecodeError:
                continue
    raise JobOpsError("AI_RESPONSE_INVALID", "The connected AI did not return a JSON result.")


def _find_object(value: Any, predicate: Callable[[dict[str, Any]], bool], *, depth: int = 0) -> dict[str, Any] | None:
    if depth > 8:
        return None
    if isinstance(value, str):
        try:
            return _find_object(_json_from_text(value), predicate, depth=depth + 1)
        except JobOpsError:
            return None
    if isinstance(value, dict):
        if predicate(value):
            return value
        preferred = ("response", "output", "result", "message", "content", "text", "data", "choices")
        for key in preferred:
            if key in value:
                found = _find_object(value[key], predicate, depth=depth + 1)
                if found is not None:
                    return found
        for key, child in value.items():
            if key in preferred:
                continue
            found = _find_object(child, predicate, depth=depth + 1)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_object(child, predicate, depth=depth + 1)
            if found is not None:
                return found
    return None


def _protocol_payload(value: Any) -> dict[str, Any]:
    def matches(item: dict[str, Any]) -> bool:
        try:
            version = int(item.get("schema_version", 0) or 0)
        except (TypeError, ValueError):
            return False
        return version == 2 and isinstance(item.get("entities"), list) and isinstance(item.get("candidates"), list)

    result = _find_object(
        value,
        matches,
    )
    if result is None:
        raise JobOpsError("AI_RESPONSE_INVALID", "The connected AI response did not contain the JobOps structured result.")
    return result


def _handshake_payload(value: Any) -> dict[str, Any] | None:
    def matches(item: dict[str, Any]) -> bool:
        try:
            protocol = int(item.get("protocol", 0) or 0)
        except (TypeError, ValueError):
            return False
        return str(item.get("status", "")).upper() == "READY" and protocol == 1

    return _find_object(value, matches)


def _safe_command(executable: str, arguments: list[str]) -> list[str]:
    if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable, *arguments]
    return [executable, *arguments]


def _model_names(value: dict[str, Any], *, api_style: str) -> list[str]:
    rows = value.get("models") if api_style == "ollama" else value.get("data")
    if not isinstance(rows, list):
        return []
    output: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name") if api_style == "ollama" else row.get("id")
        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            continue
        output.append(name.strip())
    non_embedding = [item for item in output if not any(marker in item.casefold() for marker in ("embed", "whisper", "tts"))]
    return non_embedding or output


def _openclaw_safe_config_text() -> str:
    return json.dumps(
        {
            "tools": {
                "profile": "minimal",
                "deny": [
                    "group:runtime", "group:fs", "group:web", "group:memory", "group:ui",
                    "group:automation", "group:messaging", "group:nodes", "group:agents",
                    "group:media", "group:plugins",
                ],
                "codeMode": False,
                "elevated": {"enabled": False},
            },
            "update": {"checkOnStart": False},
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _write_openclaw_safe_config(path: Path) -> None:
    path.write_text(_openclaw_safe_config_text(), encoding="utf-8")


def _validated_agent_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    encoded = completed.stdout.encode("utf-8")
    if completed.returncode != 0 or not encoded or len(encoded) > MAX_AI_OUTPUT_BYTES:
        raise JobOpsError("AI_AGENT_FAILED", "The detected Agent returned no valid bounded result.")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        value = _json_from_text(completed.stdout)
    if not isinstance(value, dict):
        raise JobOpsError("AI_AGENT_FAILED", "The detected Agent returned an invalid execution envelope.")
    if value.get("ok") is False or str(value.get("status", "ok")).casefold() in {"error", "timeout"}:
        raise JobOpsError("AI_AGENT_FAILED", "The detected Agent reported an unsuccessful analysis run.")
    tool_summary = value.get("toolSummary")
    if not isinstance(tool_summary, dict):
        meta = value.get("meta")
        tool_summary = meta.get("toolSummary") if isinstance(meta, dict) else None
    calls = tool_summary.get("calls") if isinstance(tool_summary, dict) else None
    if isinstance(calls, bool) or not isinstance(calls, int):
        raise JobOpsError(
            "AI_AGENT_TOOL_AUDIT_MISSING",
            "The connected Agent did not provide a verifiable tool-call audit, so JobOps rejected the result.",
        )
    if calls != 0:
        raise JobOpsError(
            "AI_AGENT_TOOL_CALL_BLOCKED",
            "The connected Agent attempted to use a tool during analysis, so JobOps rejected the result.",
            tool_calls=calls,
        )
    return value


def _run_bounded_agent_command(
    command: list[str],
    request: dict[str, Any],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        returncode, stdout = _run_bounded_ai_command(
            command,
            request,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )
    except JobOpsError as exc:
        if exc.code == "AI_ENGINE_FAILED":
            raise JobOpsError("AI_AGENT_FAILED", "The detected Agent exceeded the bounded output limit.") from exc
        raise JobOpsError("AI_AGENT_UNAVAILABLE", "The detected Agent could not complete a private structured request.") from exc
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def _analyze_with_single_repair(
    invoke: Callable[[dict[str, Any]], Any],
    request: dict[str, Any],
    *,
    source_id: str,
    quality_diagnostics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """Validate once, ask the same AI for one replacement on protocol/content failure, then fail closed."""
    first_response: Any = {"status": "REJECTED_BEFORE_STRUCTURED_PROTOCOL"}
    source_lines = [
        item.split("\t", 1)[1] if "\t" in item else ""
        for item in request["line_numbered_document"]
    ]
    line_number_start = (
        int(str(request["line_numbered_document"][0]).split("\t", 1)[0])
        if request["line_numbered_document"] else 1
    )
    validation_error: JobOpsError | None = None
    first_diagnostics: dict[str, Any] = {}
    try:
        first_response = invoke(request)
        value = _protocol_payload(first_response)
        candidates = LocalSubprocessAIEngine._validated_candidates(
            value,
            source_id=source_id,
            source_lines=source_lines,
            line_number_start=line_number_start,
            quality_diagnostics=first_diagnostics,
        )
        if not candidates and _candidate_filter_summary(first_diagnostics)["filtered_candidate_count"]:
            raise JobOpsError(
                "AI_RESPONSE_INVALID",
                "The first AI response contained only candidates that require filtering.",
                failure_category="FILTERED_CANDIDATE_SET",
            )
        _merge_candidate_filter_diagnostics(quality_diagnostics, first_diagnostics)
        return value, candidates, False
    except JobOpsError as first_error:
        if first_error.code != "AI_RESPONSE_INVALID":
            raise
        validation_error = first_error
    assert validation_error is not None
    repair_request = LocalSubprocessAIEngine._repair_request(request, first_response, validation_error)
    repaired_diagnostics: dict[str, Any] = {}
    try:
        repaired_value = _protocol_payload(invoke(repair_request))
        repaired_candidates = LocalSubprocessAIEngine._validated_candidates(
            repaired_value,
            source_id=source_id,
            source_lines=source_lines,
            line_number_start=line_number_start,
            quality_diagnostics=repaired_diagnostics,
        )
    except JobOpsError as repaired_error:
        if repaired_error.code == "AI_RESPONSE_INVALID":
            raise LocalSubprocessAIEngine._repair_failed(repaired_error) from repaired_error
        raise
    _merge_candidate_filter_diagnostics(quality_diagnostics, repaired_diagnostics)
    return repaired_value, repaired_candidates, True


def _analyze_all_chunks(
    invoke: Callable[[dict[str, Any]], Any],
    text: str,
    *,
    source_id: str,
    source_type: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requests, coverage = LocalSubprocessAIEngine._chunk_requests(
        text, source_id=source_id, source_type=source_type,
    )
    batches: list[list[dict[str, Any]]] = []
    repairs = 0
    quality_diagnostics: dict[str, Any] = {}
    for request in requests:
        _, candidates, repaired = _analyze_with_single_repair(
            invoke, request, source_id=source_id,
            quality_diagnostics=quality_diagnostics,
        )
        batches.append(candidates)
        repairs += int(repaired)
    merged = LocalSubprocessAIEngine._merge_candidate_batches(batches)
    entity_fingerprints = {
        str((candidate.get("entity") or {}).get("entity_fingerprint"))
        for candidate in merged if candidate.get("entity")
    }
    return merged, {
        "ai_candidates": len(merged), "ai_entities": len(entity_fingerprints),
        **coverage, "ai_repair_attempted": repairs > 0,
        "ai_repair_succeeded": repairs > 0, "ai_repair_count": repairs,
        "quality_gate_version": 5,
        **_candidate_filter_summary(quality_diagnostics),
        **_structural_quality_summary(merged),
    }


def _verify_structured_capability(invoke: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Verify the full JobFlow output contract with non-private synthetic evidence."""

    request, truncated = LocalSubprocessAIEngine._request(
        AI_CAPABILITY_TEST_TEXT,
        source_id="SRC-JOBFLOW-CAPABILITY",
        source_type="project_case",
    )
    if truncated:
        raise JobOpsError("AI_STRUCTURED_CAPABILITY_FAILED", "The internal AI capability fixture was truncated.")
    request["task"] = "JOBOPS_STRUCTURED_CAPABILITY_TEST_V1"
    request["rules"] = [
        "This is a non-private capability fixture. Return its one real-world entity and at least one complete achievement Claim.",
        "The returned achievement must preserve 120, 2024, and 20% exactly and cite line 1.",
        *request["rules"],
    ]
    try:
        _, candidates, repaired = _analyze_with_single_repair(
            invoke,
            request,
            source_id="SRC-JOBFLOW-CAPABILITY",
        )
        if not candidates:
            raise JobOpsError(
                "AI_RESPONSE_INVALID",
                "The AI capability fixture returned no grounded Claim.",
                failure_category="CAPABILITY_OMISSION",
            )
        candidate = next(
            (
                item for item in candidates
                if {"120", "2024", "20"}.issubset(set(re.findall(r"\d+", str(item.get("statement", "")))))
                and item.get("entity")
            ),
            None,
        )
        if candidate is None:
            raise JobOpsError(
                "AI_RESPONSE_INVALID",
                "The AI capability fixture did not preserve its grounded entity and metrics.",
                failure_category="CAPABILITY_GROUNDING",
            )
    except JobOpsError as exc:
        raise JobOpsError(
            "AI_STRUCTURED_CAPABILITY_FAILED",
            "The detected AI answered a simple connection check but did not pass JobFlow's structured evidence test.",
            failure_category=safe_ai_failure_category(exc.code, exc.details),
            quality_contract=AI_QUALITY_CONTRACT,
            capability_test_version=AI_CAPABILITY_TEST_VERSION,
        ) from exc
    return {
        "structured_capability_status": "VERIFIED",
        "capability_test_version": AI_CAPABILITY_TEST_VERSION,
        "capability_repair_required": repaired,
        "capability_private_content_sent": False,
    }


class AgentCLIEngine(AIAnalysisEngine):
    """Ephemeral agent bridge. Private source text travels through stdin only."""

    ready = True

    def __init__(
        self,
        executable: str,
        *,
        connector_id: str,
        model: str,
        working_directory: Path,
        timeout_seconds: int = 240,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if connector_id != "openclaw" or not Path(executable).is_file():
            raise JobOpsError("AI_AGENT_CONNECTOR_INVALID", "The selected local Agent connector is unavailable.")
        if not SAFE_MODEL_REF.fullmatch(model):
            raise JobOpsError("AI_AGENT_MODEL_INVALID", "OpenClaw did not report a safe configured model identifier.")
        self.executable = executable
        self.connector_id = connector_id
        self.model = model
        self.working_directory = working_directory
        self.timeout_seconds = max(30, min(int(timeout_seconds), 600))
        self.process_runner = process_runner
        self._capability = {
            "structured_capability_status": "NOT_TESTED",
            "capability_test_version": AI_CAPABILITY_TEST_VERSION,
            "capability_repair_required": False,
            "capability_private_content_sent": False,
        }

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "READY" if self._capability["structured_capability_status"] == "VERIFIED" else "CONNECTED_UNVERIFIED",
            "mode": "AI_CORE_STRUCTURED_ANALYSIS",
            "provider": "OPENCLAW_AGENT",
            "connection_kind": "EXISTING_AGENT",
            "connection_id": self.connector_id,
            "display_name": "OpenClaw",
            "model": self.model,
            "private_transport": "EPHEMERAL_STDIN_STDOUT",
            "data_route": "FOLLOWS_AGENT_MODEL_CONFIGURATION",
            "tool_policy": "ANALYSIS_ONLY_MINIMAL",
            "tool_calls_required": 0,
            "automatic_claim_selection": False,
            "claim_output_allowed": True,
            "quality_contract": AI_QUALITY_CONTRACT,
            **self._capability,
        }

    def _invoke(self, request: dict[str, Any]) -> Any:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.working_directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="jobops-openclaw-", dir=self.working_directory) as temporary:
                temporary_root = Path(temporary)
                isolated_workspace = temporary_root / "workspace"
                isolated_workspace.mkdir()
                safe_config = temporary_root / "openclaw-jobops.json"
                _write_openclaw_safe_config(safe_config)
                command = _safe_command(
                    self.executable,
                    [
                        "agent", "exec", "--message-file", "-", "--json",
                        "--config", str(safe_config), "--cwd", str(isolated_workspace),
                        "--model", self.model,
                        "--code-mode", "direct", "--timeout", str(self.timeout_seconds),
                    ],
                )
                if self.process_runner is subprocess.run:
                    completed = _run_bounded_agent_command(
                        command,
                        request,
                        timeout_seconds=self.timeout_seconds + 10,
                        cwd=isolated_workspace,
                    )
                else:
                    completed = self.process_runner(
                        command,
                        input=json.dumps(request, ensure_ascii=False),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="strict",
                        timeout=self.timeout_seconds + 10,
                        check=False,
                        shell=False,
                        cwd=isolated_workspace,
                        creationflags=creation_flags,
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JobOpsError("AI_AGENT_UNAVAILABLE", "The detected Agent could not complete a private structured request.") from exc
        return _validated_agent_result(completed)

    def connection_test(self) -> None:
        result = self._invoke({
            "schema_version": 1,
            "task": "JOBOPS_AI_CONNECTION_TEST",
            "instruction": "Return JSON only: {\"status\":\"READY\",\"protocol\":1}. Do not call tools or access files.",
            "private_content": False,
        })
        if _handshake_payload(result) is None:
            raise JobOpsError("AI_AGENT_HANDSHAKE_FAILED", "The detected Agent did not complete the JobOps connection test.")
        self._capability = _verify_structured_capability(self._invoke)

    def analyze_document(self, text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidates, details = _analyze_all_chunks(
            self._invoke, text, source_id=source_id, source_type=source_type,
        )
        return candidates, {
            "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
            **details,
            "automatic_claim_selection": False,
            "quality_contract": AI_QUALITY_CONTRACT,
        }


class WSLAgentCLIEngine(AgentCLIEngine):
    """OpenClaw in WSL with private requests on stdin and disposable Windows-local state."""

    def __init__(
        self,
        wsl_executable: str,
        distribution: str,
        *,
        model: str,
        timeout_seconds: int = 240,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not SAFE_MODEL_REF.fullmatch(model):
            raise JobOpsError("AI_AGENT_MODEL_INVALID", "OpenClaw did not report a safe configured model identifier.")
        self.wsl_executable = wsl_executable
        self.distribution = distribution
        self.executable = "openclaw"
        self.connector_id = "wsl_openclaw"
        self.model = model
        self.timeout_seconds = max(30, min(int(timeout_seconds), 600))
        self.process_runner = process_runner
        self._capability = {
            "structured_capability_status": "NOT_TESTED",
            "capability_test_version": AI_CAPABILITY_TEST_VERSION,
            "capability_repair_required": False,
            "capability_private_content_sent": False,
        }

    def public_status(self) -> dict[str, Any]:
        status = super().public_status()
        status.update({
            "provider": "OPENCLAW_AGENT_WSL",
            "connection_id": "wsl_openclaw",
            "display_name": "OpenClaw (WSL)",
            "private_transport": "WSL_EPHEMERAL_STDIN_STDOUT",
        })
        return status

    def _invoke(self, request: dict[str, Any]) -> Any:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        safe_config = shlex.quote(_openclaw_safe_config_text())
        safe_model = shlex.quote(self.model)
        script = "\n".join((
            "set -eu",
            "jobops_agent=$(command -v openclaw) || exit 127",
            'case "$jobops_agent" in /*/openclaw) ;; *) exit 126 ;; esac',
            'umask 077',
            'jobops_tmp=$(mktemp -d "${TMPDIR:-/tmp}/jobops-openclaw.XXXXXX") || exit 125',
            'trap \'rm -rf -- "$jobops_tmp"\' EXIT HUP INT TERM',
            'mkdir "$jobops_tmp/workspace"',
            f"printf '%s' {safe_config} > \"$jobops_tmp/openclaw-jobops.json\"",
            '"$jobops_agent" agent exec --message-file - --json '
            '  --config "$jobops_tmp/openclaw-jobops.json" --cwd "$jobops_tmp/workspace" '
            f"  --model {safe_model} --code-mode direct --timeout {self.timeout_seconds}",
        ))
        try:
            command = [self.wsl_executable, "-d", self.distribution, "--exec", "sh", "-lc", script]
            if self.process_runner is subprocess.run:
                completed = _run_bounded_agent_command(
                    command,
                    request,
                    timeout_seconds=self.timeout_seconds + 10,
                )
            else:
                completed = self.process_runner(
                    command,
                    input=json.dumps(request, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=self.timeout_seconds + 10,
                    check=False,
                    shell=False,
                    creationflags=creation_flags,
                )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise JobOpsError("AI_AGENT_UNAVAILABLE", "WSL OpenClaw could not complete a private structured request.") from exc
        return _validated_agent_result(completed)


class WSLHermesCLIEngine(AgentCLIEngine):
    """Hermes in WSL, using its configured model through a zero-tool stdin adapter."""

    def __init__(
        self,
        wsl_executable: str,
        distribution: str,
        *,
        model: str,
        provider_id: str,
        timeout_seconds: int = 240,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not SAFE_MODEL_REF.fullmatch(model):
            raise JobOpsError("AI_AGENT_MODEL_INVALID", "Hermes did not report a safe configured model identifier.")
        if not SAFE_PROVIDER_REF.fullmatch(provider_id):
            raise JobOpsError("AI_AGENT_PROVIDER_INVALID", "Hermes did not report a safe configured provider identifier.")
        self.wsl_executable = wsl_executable
        self.distribution = distribution
        self.executable = "hermes"
        self.connector_id = "wsl_hermes_agent"
        self.model = model
        self.provider_id = provider_id
        self.timeout_seconds = max(30, min(int(timeout_seconds), 600))
        self.process_runner = process_runner
        self._capability = {
            "structured_capability_status": "NOT_TESTED",
            "capability_test_version": AI_CAPABILITY_TEST_VERSION,
            "capability_repair_required": False,
            "capability_private_content_sent": False,
        }

    def public_status(self) -> dict[str, Any]:
        provider_label = "OpenAI Codex" if self.provider_id == "openai-codex" else self.provider_id
        return {
            "status": "READY" if self._capability["structured_capability_status"] == "VERIFIED" else "CONNECTED_UNVERIFIED",
            "mode": "AI_CORE_STRUCTURED_ANALYSIS",
            "provider": "HERMES_AGENT_WSL",
            "connection_kind": "EXISTING_AGENT",
            "connection_id": self.connector_id,
            "display_name": f"Hermes (WSL) · {provider_label}",
            "model": self.model,
            "private_transport": "WSL_EPHEMERAL_STDIN_STDOUT",
            "data_route": "FOLLOWS_AGENT_MODEL_CONFIGURATION",
            "tool_policy": "NO_TOOLS",
            "tool_calls_required": 0,
            "automatic_claim_selection": False,
            "claim_output_allowed": True,
            "quality_contract": AI_QUALITY_CONTRACT,
            **self._capability,
        }

    def _invoke(self, request: dict[str, Any]) -> Any:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        adapter = shlex.quote(_HERMES_SAFE_ADAPTER_CODE)
        script = "\n".join((
            "set -eu",
            'jobops_python="$HOME/.hermes/hermes-agent/venv/bin/python3"',
            '[ -x "$jobops_python" ] || exit 127',
            "umask 077",
            'jobops_tmp=$(mktemp -d "${TMPDIR:-/tmp}/jobops-hermes.XXXXXX") || exit 125',
            'trap \'rm -rf -- "$jobops_tmp"\' EXIT HUP INT TERM',
            'cd "$jobops_tmp"',
            f'"$jobops_python" -I -c {adapter}',
        ))
        try:
            command = [self.wsl_executable, "-d", self.distribution, "--exec", "sh", "-lc", script]
            if self.process_runner is subprocess.run:
                completed = _run_bounded_agent_command(
                    command,
                    request,
                    timeout_seconds=self.timeout_seconds + 10,
                )
            else:
                completed = self.process_runner(
                    command,
                    input=json.dumps(request, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=self.timeout_seconds + 10,
                    check=False,
                    shell=False,
                    creationflags=creation_flags,
                )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise JobOpsError("AI_AGENT_UNAVAILABLE", "WSL Hermes could not complete a private structured request.") from exc
        return _validated_agent_result(completed)


class LoopbackModelAIEngine(AIAnalysisEngine):
    ready = True

    def __init__(
        self,
        *,
        connector_id: str,
        label: str,
        base_url: str,
        model: str,
        api_style: str,
        connection_kind: str,
        data_route: str,
        private_transport: str = "LOCAL_LOOPBACK_HTTP",
        http_json: HTTPJSON = _loopback_json,
        timeout_seconds: int = 300,
    ) -> None:
        _assert_loopback_url(base_url)
        if not model or len(model) > 300 or api_style not in {"ollama", "openai"}:
            raise JobOpsError("AI_LOCAL_MODEL_INVALID", "The detected local model configuration is invalid.")
        self.connector_id = connector_id
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_style = api_style
        self.connection_kind = connection_kind
        self.data_route = data_route
        self.private_transport = private_transport
        self.http_json = http_json
        self.timeout_seconds = max(30, min(int(timeout_seconds), 600))
        self._capability = {
            "structured_capability_status": "NOT_TESTED",
            "capability_test_version": AI_CAPABILITY_TEST_VERSION,
            "capability_repair_required": False,
            "capability_private_content_sent": False,
        }

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "READY" if self._capability["structured_capability_status"] == "VERIFIED" else "CONNECTED_UNVERIFIED",
            "mode": "AI_CORE_STRUCTURED_ANALYSIS",
            "provider": self.connector_id.upper(),
            "connection_kind": self.connection_kind,
            "connection_id": self.connector_id,
            "display_name": self.label,
            "model": self.model,
            "private_transport": self.private_transport,
            "data_route": self.data_route,
            "automatic_claim_selection": False,
            "claim_output_allowed": True,
            "quality_contract": AI_QUALITY_CONTRACT,
            **self._capability,
        }

    def _complete(self, request: dict[str, Any]) -> Any:
        prompt = json.dumps(request, ensure_ascii=False)
        system = (
            "You are the JobOps private document analysis engine. Follow every rule and output_contract in the user JSON. "
            "Return only the requested JSON object. Never call tools, browse, or add unsupported facts."
        )
        if self.api_style == "ollama":
            response = self.http_json(
                self.base_url + "/api/chat",
                method="POST",
                payload={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=self.timeout_seconds,
            )
            message = response.get("message")
            content = message.get("content") if isinstance(message, dict) else None
        else:
            response = self.http_json(
                self.base_url + "/v1/chat/completions",
                method="POST",
                payload={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "temperature": 0,
                    "stream": False,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout_seconds,
            )
            choices = response.get("choices")
            message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise JobOpsError("AI_LOCAL_RESPONSE_INVALID", "The connected local model returned no structured content.")
        return _json_from_text(content)

    def connection_test(self) -> None:
        result = self._complete({
            "schema_version": 1,
            "task": "JOBOPS_AI_CONNECTION_TEST",
            "instruction": "Return JSON only: {\"status\":\"READY\",\"protocol\":1}.",
            "private_content": False,
        })
        if _handshake_payload(result) is None:
            raise JobOpsError("AI_LOCAL_HANDSHAKE_FAILED", "The selected local model did not complete the JobOps connection test.")
        self._capability = _verify_structured_capability(self._complete)

    def analyze_document(self, text: str, *, source_id: str, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidates, details = _analyze_all_chunks(
            self._complete, text, source_id=source_id, source_type=source_type,
        )
        return candidates, {
            "analysis_mode": "AI_CORE_ENTITY_ANALYSIS",
            **details,
            "automatic_claim_selection": False,
            "quality_contract": AI_QUALITY_CONTRACT,
        }


class AIConnectionManager:
    """Detects only bounded local connectors and never reads provider credentials."""

    def __init__(
        self,
        config_path: Path,
        *,
        initial_engine: AIAnalysisEngine | None = None,
        command_resolver: CommandResolver = shutil.which,
        http_json: HTTPJSON = _loopback_json,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        process_launcher: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    ) -> None:
        self.config_path = config_path
        self.command_resolver = command_resolver
        self.http_json = http_json
        self.process_runner = process_runner
        self.process_launcher = process_launcher
        self._managed_processes: list[subprocess.Popen[Any]] = []
        self.current_engine = initial_engine or configured_ai_engine()
        if not self.current_engine.ready:
            restored = self._restore()
            if restored is not None:
                try:
                    restored.connection_test()  # type: ignore[attr-defined]
                except (AttributeError, JobOpsError):
                    restored = None
                if restored is not None:
                    self.current_engine = restored

    def public_state(self) -> dict[str, Any]:
        status = self.current_engine.public_status()
        return {
            "status": status.get("status", "NOT_CONFIGURED"),
            "structured_capability_status": status.get("structured_capability_status", "VALIDATED_ON_USE" if status.get("status") == "READY" else "NOT_TESTED"),
            "quality_contract": status.get("quality_contract"),
            "selected": {
                key: status.get(key)
                for key in ("connection_id", "connection_kind", "display_name", "model", "data_route")
                if status.get(key) is not None
            },
            "options": [
                {
                    "mode": "agent", "auto_detect": True, "environments": ["WINDOWS", "WSL"],
                    "examples": ["Hermes", "OpenClaw"],
                },
                {
                    "mode": "local_model", "auto_detect": True, "environments": ["WINDOWS", "WSL"],
                    "examples": ["Ollama", "LM Studio", "LocalAI", "llama.cpp", "vLLM"],
                },
                {"mode": "custom_adapter", "auto_detect": False, "status": "API_RESERVED"},
            ],
            "wsl_auto_detect": True,
            "credentials_read": 0,
            "credentials_stored": 0,
            "real_application_external_actions": 0,
        }

    def _read_preference(self) -> dict[str, Any] | None:
        try:
            raw = self.config_path.read_bytes()
        except OSError:
            return None
        if not raw or len(raw) > 16_384:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or int(value.get("schema_version", 0)) != 1:
            return None
        return value

    def _persist(self, *, mode: str, connector_id: str, model: str | None) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": 1,
            "mode": mode,
            "connector_id": connector_id,
            "model": model,
            "saved_at": iso_utc(),
            "contains_credentials": False,
            "contains_executable_paths": False,
        }
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.config_path)

    def _wsl_executable(self) -> str | None:
        return self.command_resolver("wsl.exe") or self.command_resolver("wsl")

    def _wsl_distributions(self, executable: str) -> list[str]:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = self.process_runner(
                [executable, "--list", "--quiet"],
                capture_output=True,
                text=False,
                timeout=12,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        try:
            return _decode_wsl_distribution_output(completed.stdout)
        except JobOpsError:
            return []

    def _run_wsl(
        self,
        executable: str,
        distribution: str,
        arguments: list[str],
        *,
        timeout: float,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        return self.process_runner(
            [executable, "-d", distribution, "--exec", *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
            check=False,
            shell=False,
            cwd=self.config_path.parent,
            creationflags=creation_flags,
        )

    def _wsl_command_path(self, executable: str, distribution: str, command_name: str) -> str | None:
        if command_name not in {"curl", "hermes", "openclaw"}:
            return None
        try:
            completed = self._run_wsl(
                executable,
                distribution,
                ["sh", "-lc", f"command -v {command_name}"],
                timeout=12,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 4_096:
            return None
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            return None
        path = lines[0]
        parts = path.split("/")
        if (
            not path.startswith("/")
            or path.endswith("/")
            or len(path) > 4_096
            or parts[-1] != command_name
            or ".." in parts
            or any(ord(character) < 32 for character in path)
        ):
            return None
        return path

    def _wsl_has_command(self, executable: str, distribution: str, command_name: str) -> bool:
        return self._wsl_command_path(executable, distribution, command_name) is not None

    def _wsl_http_json(self, executable: str, distribution: str) -> HTTPJSON:
        """Build a WSL-local loopback transport; private JSON is sent only through stdin."""
        curl_ready = self._wsl_has_command(executable, distribution, "curl")

        def request(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, Any] | None = None,
            timeout: float = 2.0,
        ) -> dict[str, Any]:
            _assert_loopback_url(url)
            if not curl_ready:
                raise JobOpsError("AI_WSL_LOCAL_BRIDGE_MISSING", "The safe WSL loopback bridge requires curl.")
            normalized_method = method.upper()
            if normalized_method not in {"GET", "POST"}:
                raise JobOpsError("AI_WSL_REQUEST_INVALID", "The WSL AI bridge rejected an unsupported HTTP method.")
            bounded_timeout = max(1.0, min(float(timeout), 600.0))
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else None
            if body is not None and len(body.encode("utf-8")) > MAX_CONNECTION_RESPONSE_BYTES:
                raise JobOpsError("AI_WSL_REQUEST_INVALID", "The WSL AI request exceeded the private transport limit.")
            arguments = [
                "--silent", "--show-error", "--fail", "--max-time", str(int(bounded_timeout) + 1),
                "--max-filesize", str(MAX_CONNECTION_RESPONSE_BYTES),
                "--request", normalized_method,
                "--header", "Accept: application/json",
                "--header", "Authorization: " + " ".join(("Bearer", "jobops-local")),
            ]
            if body is not None:
                arguments.extend(["--header", "Content-Type: application/json", "--data-binary", "@-"])
            arguments.append(url)
            public_arguments = " ".join(shlex.quote(argument) for argument in arguments)
            script = "\n".join((
                "set -eu",
                "jobops_curl=$(command -v curl) || exit 127",
                'case "$jobops_curl" in /*/curl) ;; *) exit 126 ;; esac',
                f'exec "$jobops_curl" {public_arguments}',
            ))
            try:
                completed = self._run_wsl(
                    executable,
                    distribution,
                    ["sh", "-lc", script],
                    timeout=bounded_timeout + 5.0,
                    input_text=body,
                )
            except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
                raise JobOpsError("AI_WSL_LOCAL_ENDPOINT_UNAVAILABLE", "The WSL-local AI endpoint is not available.") from exc
            encoded = completed.stdout.encode("utf-8")
            if completed.returncode != 0:
                raise JobOpsError("AI_WSL_LOCAL_ENDPOINT_UNAVAILABLE", "The WSL-local AI endpoint is not available.")
            if not encoded or len(encoded) > MAX_CONNECTION_RESPONSE_BYTES:
                raise JobOpsError("AI_WSL_LOCAL_RESPONSE_INVALID", "The WSL-local AI endpoint returned an empty or oversized response.")
            try:
                value = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise JobOpsError("AI_WSL_LOCAL_RESPONSE_INVALID", "The WSL-local AI endpoint did not return valid JSON.") from exc
            if not isinstance(value, dict):
                raise JobOpsError("AI_WSL_LOCAL_RESPONSE_INVALID", "The WSL-local AI endpoint response must be a JSON object.")
            return value

        return request

    def _wsl_local_engine(
        self,
        connector_id: str,
        model: str,
        *,
        http_json: HTTPJSON,
    ) -> LoopbackModelAIEngine:
        if connector_id == "hermes_proxy":
            return LoopbackModelAIEngine(
                connector_id="wsl_hermes_proxy",
                label="Hermes managed model (WSL)",
                base_url="http://127.0.0.1:8645",
                model=model,
                api_style="openai",
                connection_kind="EXISTING_AGENT",
                data_route="HERMES_MANAGED_PROVIDER_VIA_WSL",
                private_transport="WSL_STDIN_LOOPBACK_HTTP",
                http_json=http_json,
            )
        details = LOCAL_CONNECTORS.get(connector_id)
        if details is None:
            raise JobOpsError("AI_CONNECTOR_UNKNOWN", "The WSL AI connector is not supported.")
        return LoopbackModelAIEngine(
            connector_id=f"wsl_{connector_id}",
            label=f"{details['label']} (WSL)",
            base_url=str(details["base_url"]),
            model=model,
            api_style=str(details["api_style"]),
            connection_kind="LOCAL_MODEL",
            data_route="WSL_LOCAL_MACHINE_ONLY",
            private_transport="WSL_STDIN_LOOPBACK_HTTP",
            http_json=http_json,
        )

    def _local_engine(self, connector_id: str, model: str, *, connection_kind: str = "LOCAL_MODEL") -> LoopbackModelAIEngine:
        if connector_id == "hermes_proxy":
            return LoopbackModelAIEngine(
                connector_id=connector_id,
                label="Hermes managed model",
                base_url="http://127.0.0.1:8645",
                model=model,
                api_style="openai",
                connection_kind="EXISTING_AGENT",
                data_route="HERMES_MANAGED_PROVIDER",
                http_json=self.http_json,
            )
        details = LOCAL_CONNECTORS.get(connector_id)
        if details is None:
            raise JobOpsError("AI_CONNECTOR_UNKNOWN", "The saved AI connector is not supported.")
        return LoopbackModelAIEngine(
            connector_id=connector_id,
            label=str(details["label"]),
            base_url=str(details["base_url"]),
            model=model,
            api_style=str(details["api_style"]),
            connection_kind=connection_kind,
            data_route="LOCAL_MACHINE_ONLY",
            http_json=self.http_json,
        )

    def _models(self, connector_id: str) -> list[str]:
        return self._models_with_http(connector_id, self.http_json)

    @staticmethod
    def _models_with_http(connector_id: str, http_json: HTTPJSON) -> list[str]:
        if connector_id == "hermes_proxy":
            value = http_json("http://127.0.0.1:8645/v1/models", timeout=1.0)
            return _model_names(value, api_style="openai")
        details = LOCAL_CONNECTORS[connector_id]
        value = http_json(str(details["base_url"]) + str(details["models_path"]), timeout=1.25)
        return _model_names(value, api_style=str(details["api_style"]))

    def _restore_wsl(self, connector_id: str, model: str) -> LoopbackModelAIEngine | None:
        executable = self._wsl_executable()
        if not executable or not connector_id.startswith("wsl_"):
            return None
        base_connector = connector_id.removeprefix("wsl_")
        if base_connector != "hermes_proxy" and base_connector not in LOCAL_CONNECTORS:
            return None
        for distribution in self._wsl_distributions(executable):
            if not self._wsl_has_command(executable, distribution, "curl"):
                continue
            transport = self._wsl_http_json(executable, distribution)
            try:
                models = self._models_with_http(base_connector, transport)
            except JobOpsError:
                continue
            chosen = model if model in models else (models[0] if models else "")
            if chosen:
                return self._wsl_local_engine(base_connector, chosen, http_json=transport)
        return None

    def _restore(self) -> AIAnalysisEngine | None:
        preference = self._read_preference()
        if preference is None:
            return None
        connector_id = str(preference.get("connector_id", ""))
        model = str(preference.get("model", ""))
        if connector_id == "openclaw":
            executable = self.command_resolver("openclaw")
            if executable:
                try:
                    configured_model = self._openclaw_model(executable)
                except JobOpsError:
                    return None
                return AgentCLIEngine(
                    executable,
                    connector_id="openclaw",
                    model=configured_model,
                    working_directory=self.config_path.parent,
                    process_runner=self.process_runner,
                )
            return None
        if connector_id == "wsl_hermes_agent":
            executable = self._wsl_executable()
            if not executable:
                return None
            for distribution in self._wsl_distributions(executable):
                if not self._wsl_has_command(executable, distribution, "hermes"):
                    continue
                configured = self._wsl_hermes_configuration(executable, distribution)
                if configured is None:
                    continue
                configured_model, configured_provider = configured
                return WSLHermesCLIEngine(
                    executable,
                    distribution,
                    model=configured_model,
                    provider_id=configured_provider,
                    process_runner=self.process_runner,
                )
            return None
        if connector_id == "wsl_openclaw":
            executable = self._wsl_executable()
            if not executable:
                return None
            for distribution in self._wsl_distributions(executable):
                if not self._wsl_has_command(executable, distribution, "openclaw"):
                    continue
                try:
                    configured_model = self._wsl_openclaw_model(executable, distribution)
                except JobOpsError:
                    continue
                return WSLAgentCLIEngine(
                    executable,
                    distribution,
                    model=configured_model,
                    process_runner=self.process_runner,
                )
            return None
        if connector_id in LOCAL_CONNECTORS or connector_id == "hermes_proxy":
            try:
                models = self._models(connector_id)
            except JobOpsError:
                return None
            chosen = model if model in models else (models[0] if models else "")
            return self._local_engine(connector_id, chosen) if chosen else None
        if connector_id.startswith("wsl_"):
            return self._restore_wsl(connector_id, model)
        return None

    def _start_hermes_proxy(self, executable: str) -> None:
        command = _safe_command(executable, ["proxy", "start", "--host", "127.0.0.1", "--port", "8645"])
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            process = self.process_launcher(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.config_path.parent,
                shell=False,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise JobOpsError("AI_AGENT_UNAVAILABLE", "Hermes was detected but its private model bridge could not start.") from exc
        self._managed_processes.append(process)

    def _start_wsl_hermes_proxy(self, executable: str, distribution: str) -> subprocess.Popen[Any]:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._wsl_has_command(executable, distribution, "hermes"):
            raise JobOpsError("AI_WSL_PROXY_START_FAILED", "The WSL Hermes command is no longer available.")
        script = "\n".join((
            "set -eu",
            "jobops_hermes=$(command -v hermes) || exit 127",
            'case "$jobops_hermes" in /*/hermes) ;; *) exit 126 ;; esac',
            'exec "$jobops_hermes" proxy start --host 127.0.0.1 --port 8645',
        ))
        try:
            process = self.process_launcher(
                [
                    executable, "-d", distribution, "--exec", "sh", "-lc", script,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.config_path.parent,
                shell=False,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise JobOpsError(
                "AI_WSL_PROXY_START_FAILED",
                "Hermes was found in WSL, but its loopback-only model bridge could not start.",
            ) from exc
        self._managed_processes.append(process)
        return process

    def _wsl_hermes_status(self, executable: str, distribution: str) -> str:
        if not self._wsl_has_command(executable, distribution, "hermes"):
            return ""
        script = "\n".join((
            "set -eu",
            "jobops_hermes=$(command -v hermes) || exit 127",
            'case "$jobops_hermes" in /*/hermes) ;; *) exit 126 ;; esac',
            'exec "$jobops_hermes" proxy status',
        ))
        try:
            completed = self._run_wsl(
                executable,
                distribution,
                ["sh", "-lc", script],
                timeout=20,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return ""
        combined = f"{completed.stdout}\n{completed.stderr}"
        if len(combined.encode("utf-8")) > MAX_WSL_DISCOVERY_BYTES:
            return ""
        return combined.casefold()

    def _wsl_hermes_configuration(self, executable: str, distribution: str) -> tuple[str, str] | None:
        """Read only Hermes's public model/provider selection through its own isolated runtime."""
        probe = shlex.quote(_HERMES_METADATA_PROBE_CODE)
        script = "\n".join((
            "set -eu",
            'jobops_python="$HOME/.hermes/hermes-agent/venv/bin/python3"',
            '[ -x "$jobops_python" ] || exit 127',
            f'"$jobops_python" -I -c {probe}',
        ))
        try:
            completed = self._run_wsl(
                executable,
                distribution,
                ["sh", "-lc", script],
                timeout=20,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return None
        encoded = completed.stdout.encode("utf-8")
        if completed.returncode != 0 or not encoded or len(encoded) > 4_096:
            return None
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or value.get("ok") is not True:
            return None
        model = value.get("model")
        provider = value.get("provider")
        if not isinstance(model, str) or not SAFE_MODEL_REF.fullmatch(model):
            return None
        if not isinstance(provider, str):
            return None
        provider_id = re.sub(r"[^a-z0-9]+", "-", provider.casefold()).strip("-")
        if not SAFE_PROVIDER_REF.fullmatch(provider_id):
            return None
        return model, provider_id

    @staticmethod
    def _wsl_hermes_needs_model(status_text: str) -> bool:
        return any(
            marker in status_text
            for marker in (
                "no llm provider configured",
                "no provider configured",
                "select a provider",
                "run hermes model",
                "authentication required",
            )
        )

    def _connect_wsl_hermes(self) -> AIAnalysisEngine | None:
        executable = self._wsl_executable()
        if not executable:
            return None
        auth_required = False
        bridge_failed = False
        bridge_missing = False
        for distribution in self._wsl_distributions(executable):
            if not self._wsl_has_command(executable, distribution, "hermes"):
                continue
            transport = (
                self._wsl_http_json(executable, distribution)
                if self._wsl_has_command(executable, distribution, "curl")
                else None
            )
            if transport is not None:
                try:
                    running_models = self._models_with_http("hermes_proxy", transport)
                except JobOpsError:
                    running_models = []
                if running_models:
                    engine = self._wsl_local_engine("hermes_proxy", running_models[0], http_json=transport)
                    engine.connection_test()
                    return engine
            configured = self._wsl_hermes_configuration(executable, distribution)
            if configured is not None:
                configured_model, configured_provider = configured
                engine = WSLHermesCLIEngine(
                    executable,
                    distribution,
                    model=configured_model,
                    provider_id=configured_provider,
                    process_runner=self.process_runner,
                )
                try:
                    engine.connection_test()
                except JobOpsError as exc:
                    if exc.code in {"AI_AGENT_TOOL_AUDIT_MISSING", "AI_AGENT_TOOL_CALL_BLOCKED"}:
                        raise
                    auth_required = True
                    continue
                return engine
            status_text = self._wsl_hermes_status(executable, distribution)
            if self._wsl_hermes_needs_model(status_text):
                auth_required = True
                continue
            process = self._start_wsl_hermes_proxy(executable, distribution)
            for _ in range(24):
                if process.poll() is not None:
                    break
                time.sleep(0.25)
                try:
                    forwarded_models = self._models("hermes_proxy")
                except JobOpsError:
                    forwarded_models = []
                if forwarded_models:
                    engine = LoopbackModelAIEngine(
                        connector_id="wsl_hermes_proxy",
                        label="Hermes managed model (WSL)",
                        base_url="http://127.0.0.1:8645",
                        model=forwarded_models[0],
                        api_style="openai",
                        connection_kind="EXISTING_AGENT",
                        data_route="HERMES_MANAGED_PROVIDER_VIA_WSL",
                        private_transport="WINDOWS_TO_WSL_LOOPBACK_HTTP",
                        http_json=self.http_json,
                    )
                    engine.connection_test()
                    return engine
                if transport is not None:
                    try:
                        direct_models = self._models_with_http("hermes_proxy", transport)
                    except JobOpsError:
                        direct_models = []
                    if direct_models:
                        engine = self._wsl_local_engine("hermes_proxy", direct_models[0], http_json=transport)
                        engine.connection_test()
                        return engine
            bridge_failed = True
            bridge_missing = bridge_missing or transport is None
            if self._wsl_hermes_needs_model(self._wsl_hermes_status(executable, distribution)):
                auth_required = True
        if auth_required:
            raise JobOpsError(
                "AI_WSL_HERMES_AUTH_REQUIRED",
                "Hermes was found in WSL, but no ready model provider is selected or signed in.",
            )
        if bridge_failed and bridge_missing:
            raise JobOpsError(
                "AI_WSL_LOCAL_BRIDGE_MISSING",
                "Hermes was found in WSL, but curl is required because Windows loopback forwarding is unavailable.",
            )
        if bridge_failed:
            raise JobOpsError(
                "AI_WSL_PROXY_START_FAILED",
                "Hermes was found in WSL, but its loopback-only model bridge did not become ready.",
            )
        return None

    def _connect_hermes(self) -> LoopbackModelAIEngine | None:
        try:
            models = self._models("hermes_proxy")
        except JobOpsError:
            executable = self.command_resolver("hermes")
            if not executable:
                return None
            self._start_hermes_proxy(executable)
            models = []
            for _ in range(16):
                if self._managed_processes[-1].poll() is not None:
                    break
                time.sleep(0.25)
                try:
                    models = self._models("hermes_proxy")
                    break
                except JobOpsError:
                    continue
        if not models:
            return None
        engine = self._local_engine("hermes_proxy", models[0])
        engine.connection_test()
        return engine

    def _connect_openclaw(self) -> AgentCLIEngine | None:
        executable = self.command_resolver("openclaw")
        if not executable:
            return None
        configured_model = self._openclaw_model(executable)
        engine = AgentCLIEngine(
            executable,
            connector_id="openclaw",
            model=configured_model,
            working_directory=self.config_path.parent,
            process_runner=self.process_runner,
        )
        engine.connection_test()
        return engine

    def _connect_wsl_openclaw(self) -> WSLAgentCLIEngine | None:
        executable = self._wsl_executable()
        if not executable:
            return None
        failures: list[JobOpsError] = []
        for distribution in self._wsl_distributions(executable):
            if not self._wsl_has_command(executable, distribution, "openclaw"):
                continue
            try:
                configured_model = self._wsl_openclaw_model(executable, distribution)
                engine = WSLAgentCLIEngine(
                    executable,
                    distribution,
                    model=configured_model,
                    process_runner=self.process_runner,
                )
                engine.connection_test()
                return engine
            except JobOpsError as exc:
                failures.append(exc)
        if failures:
            raise failures[0]
        return None

    def _wsl_openclaw_model(self, executable: str, distribution: str) -> str:
        script = "\n".join((
            "set -eu",
            "jobops_agent=$(command -v openclaw) || exit 127",
            'case "$jobops_agent" in /*/openclaw) ;; *) exit 126 ;; esac',
            'exec "$jobops_agent" models status --json',
        ))
        try:
            completed = self._run_wsl(
                executable,
                distribution,
                ["sh", "-lc", script],
                timeout=20,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "WSL OpenClaw could not report its configured model.") from exc
        encoded = completed.stdout.encode("utf-8")
        if completed.returncode != 0 or not encoded or len(encoded) > MAX_CONNECTION_RESPONSE_BYTES:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "WSL OpenClaw did not report a bounded configured model.")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "WSL OpenClaw model status was not valid JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "WSL OpenClaw model status was not a JSON object.")
        model = value.get("resolvedDefault") or value.get("defaultModel")
        if not isinstance(model, str) or not SAFE_MODEL_REF.fullmatch(model):
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "WSL OpenClaw has no safe configured default model.")
        return model

    def _openclaw_model(self, executable: str) -> str:
        """Read only OpenClaw's public resolved model id; discard all auth/path metadata."""
        command = _safe_command(executable, ["models", "status", "--json"])
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = self.process_runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=20,
                check=False,
                shell=False,
                cwd=self.config_path.parent,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "OpenClaw could not report its configured model.") from exc
        encoded = completed.stdout.encode("utf-8")
        if completed.returncode != 0 or not encoded or len(encoded) > MAX_CONNECTION_RESPONSE_BYTES:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "OpenClaw did not report a bounded configured model.")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "OpenClaw model status was not valid JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "OpenClaw model status was not a JSON object.")
        model = value.get("resolvedDefault") or value.get("defaultModel")
        if not isinstance(model, str) or not SAFE_MODEL_REF.fullmatch(model):
            raise JobOpsError("AI_AGENT_MODEL_UNAVAILABLE", "OpenClaw has no safe configured default model.")
        return model

    def _connect_agent(self) -> AIAnalysisEngine | None:
        failures: list[JobOpsError] = []
        for connector in (
            self._connect_hermes,
            self._connect_wsl_hermes,
            self._connect_openclaw,
            self._connect_wsl_openclaw,
        ):
            try:
                engine = connector()
            except JobOpsError as exc:
                failures.append(exc)
                continue
            if engine is not None:
                return engine
        if failures:
            security_failure = next(
                (
                    failure
                    for failure in failures
                    if failure.code in {"AI_AGENT_TOOL_AUDIT_MISSING", "AI_AGENT_TOOL_CALL_BLOCKED"}
                ),
                None,
            )
            capability_failure = next(
                (failure for failure in failures if failure.code == "AI_STRUCTURED_CAPABILITY_FAILED"),
                None,
            )
            raise security_failure or capability_failure or failures[0]
        return None

    def _connect_wsl_local_model(self) -> LoopbackModelAIEngine | None:
        executable = self._wsl_executable()
        if not executable:
            return None
        found_service_without_curl = False
        capability_failures: list[JobOpsError] = []
        for distribution in self._wsl_distributions(executable):
            if not self._wsl_has_command(executable, distribution, "curl"):
                found_service_without_curl = True
                continue
            transport = self._wsl_http_json(executable, distribution)
            for connector_id in LOCAL_CONNECTORS:
                try:
                    models = self._models_with_http(connector_id, transport)
                except JobOpsError:
                    continue
                if not models:
                    continue
                engine = self._wsl_local_engine(connector_id, models[0], http_json=transport)
                try:
                    engine.connection_test()
                except JobOpsError as exc:
                    if exc.code == "AI_STRUCTURED_CAPABILITY_FAILED":
                        capability_failures.append(exc)
                    continue
                return engine
        if capability_failures:
            raise capability_failures[0]
        if found_service_without_curl:
            raise JobOpsError(
                "AI_WSL_LOCAL_BRIDGE_MISSING",
                "WSL was found, but its safe loopback bridge is unavailable because curl is not installed there.",
            )
        return None

    def _connect_local_model(self) -> LoopbackModelAIEngine | None:
        capability_failures: list[JobOpsError] = []
        for connector_id in LOCAL_CONNECTORS:
            try:
                models = self._models(connector_id)
            except JobOpsError:
                continue
            if models:
                engine = self._local_engine(connector_id, models[0])
                try:
                    engine.connection_test()
                except JobOpsError as exc:
                    if exc.code == "AI_STRUCTURED_CAPABILITY_FAILED":
                        capability_failures.append(exc)
                    continue
                return engine
        try:
            wsl_engine = self._connect_wsl_local_model()
        except JobOpsError as exc:
            if exc.code == "AI_STRUCTURED_CAPABILITY_FAILED":
                capability_failures.append(exc)
            elif not capability_failures:
                raise
        else:
            if wsl_engine is not None:
                return wsl_engine
        if capability_failures:
            raise capability_failures[0]
        return None

    def connect(self, mode: str) -> AIAnalysisEngine:
        if mode not in {"agent", "local_model", "auto"}:
            raise JobOpsError("AI_CONNECTION_MODE_INVALID", "Select an existing Agent or a prepared local model.")
        engine: AIAnalysisEngine | None = None
        if mode in {"agent", "auto"}:
            try:
                engine = self._connect_agent()
            except JobOpsError:
                if mode == "agent":
                    raise
        if engine is None and mode in {"local_model", "auto"}:
            engine = self._connect_local_model()
        if engine is None:
            raise JobOpsError(
                "AI_CONNECTION_NOT_FOUND",
                "No ready supported AI was found on Windows or WSL. Start Hermes/OpenClaw or a local Ollama/LM Studio/OpenAI-compatible model server, then retry.",
                mode=mode,
            )
        self.current_engine = engine
        status = engine.public_status()
        connector_id = str(status.get("connection_id", "custom_adapter"))
        persisted_mode = "agent" if connector_id in AGENT_CONNECTORS else "local_model"
        model = status.get("model")
        self._persist(mode=persisted_mode, connector_id=connector_id, model=str(model) if model else None)
        return engine

    def close(self) -> None:
        for process in self._managed_processes:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except (OSError, subprocess.SubprocessError):
                    try:
                        process.kill()
                    except OSError:
                        pass
        self._managed_processes.clear()
