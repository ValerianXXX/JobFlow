from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  # Adds the project src directory to sys.path.

from jobops.ai_connections import (
    AIConnectionManager,
    _analyze_all_chunks,
    _analyze_with_single_repair,
    _assert_loopback_url,
    _decode_wsl_distribution_output,
    _run_bounded_agent_command,
)
from jobops.ai_runtime import AIAnalysisEngine, LocalSubprocessAIEngine
from jobops.errors import JobOpsError


class AIConnectionTests(unittest.TestCase):
    def test_agent_bridge_stops_unbounded_output_during_generation(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*(6*1024*1024)); sys.stdout.flush()",
        ]
        with self.assertRaises(JobOpsError) as blocked:
            _run_bounded_agent_command(
                command,
                {"private_content": "synthetic"},
                timeout_seconds=30,
            )
        self.assertEqual(blocked.exception.code, "AI_AGENT_FAILED")

    def test_complete_analysis_chunks_large_text_and_merges_cross_chunk_duplicates(self) -> None:
        line = "Built a synthetic project workflow."
        source_text = "\n".join(line for _ in range(14_000))
        requests: list[dict[str, object]] = []

        def valid_response(request: dict[str, object]) -> dict[str, object]:
            requests.append(request)
            numbered = request["line_numbered_document"]
            assert isinstance(numbered, list) and numbered
            line_number = int(str(numbered[0]).split("\t", 1)[0])
            return {
                "schema_version": 2,
                "entities": [{
                    "entity_key": "synthetic-project", "entity_type": "project",
                    "organization": "Synthetic", "role": "Project",
                    "start_date": "", "end_date": "",
                    "line_start": line_number, "line_end": line_number,
                }],
                "candidates": [{
                    "statement": line, "category": "project", "claim_kind": "achievement",
                    "entity_key": "synthetic-project", "confidence": "HIGH",
                    "line_start": line_number, "line_end": line_number,
                    "reason": "Explicit source statement.",
                }],
            }

        candidates, summary = _analyze_all_chunks(
            valid_response,
            source_text,
            source_id="SRC-LARGE-SYNTHETIC",
            source_type="project_case",
        )

        self.assertGreaterEqual(len(requests), 2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(summary["ai_chunks"], len(requests))
        self.assertEqual(summary["ai_input_characters"], len(source_text))
        self.assertEqual(summary["ai_covered_characters"], len(source_text))
        self.assertFalse(summary["ai_input_truncated"])
        starts = [int(str(item["line_numbered_document"][0]).split("\t", 1)[0]) for item in requests]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(starts), len(set(starts)))

    def test_complete_analysis_discards_every_chunk_when_one_chunk_fails_validation(self) -> None:
        line = "Built a synthetic project workflow."
        source_text = "\n".join(line for _ in range(14_000))
        calls = 0

        def second_chunk_never_repairs(request: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            chunk = request.get("chunk", {})
            chunk_index = int(chunk.get("index", 1)) if isinstance(chunk, dict) else 1
            numbered = request["line_numbered_document"]
            assert isinstance(numbered, list) and numbered
            line_number = int(str(numbered[0]).split("\t", 1)[0])
            statement = line if chunk_index == 1 else "Unsupported result of 999 percent"
            return {
                "schema_version": 2,
                "entities": [{
                    "entity_key": "synthetic-project", "entity_type": "project",
                    "organization": "Synthetic", "role": "Project",
                    "start_date": "", "end_date": "",
                    "line_start": line_number, "line_end": line_number,
                }],
                "candidates": [{
                    "statement": statement, "category": "project", "claim_kind": "achievement",
                    "entity_key": "synthetic-project", "confidence": "HIGH",
                    "line_start": line_number, "line_end": line_number,
                    "reason": "Synthetic validation case.",
                }],
            }

        with self.assertRaises(JobOpsError) as caught:
            _analyze_all_chunks(
                second_chunk_never_repairs,
                source_text,
                source_id="SRC-LARGE-FAIL",
                source_type="project_case",
            )
        self.assertEqual(caught.exception.code, "AI_RESPONSE_REPAIR_FAILED")
        self.assertGreaterEqual(calls, 3)

    def test_ai_repair_fails_closed_when_replacement_remains_ungrounded(self) -> None:
        private_value = "Synthetic grounded project statement."
        request, _ = LocalSubprocessAIEngine._request(
            private_value,
            source_id="SRC-REPAIR-FAIL",
            source_type="project_case",
        )
        calls: list[dict[str, object]] = []

        def invalid_response(payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            return {
                "schema_version": 2,
                "entities": [{
                    "entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic",
                    "role": "Project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 1,
                }],
                "candidates": [{
                    "statement": "Unsupported result of 999 percent", "category": "project",
                    "claim_kind": "achievement", "entity_key": "synthetic", "confidence": "HIGH",
                    "line_start": 1, "line_end": 1, "reason": "Synthetic invalid result.",
                }],
            }

        with self.assertRaises(JobOpsError) as caught:
            _analyze_with_single_repair(invalid_response, request, source_id="SRC-REPAIR-FAIL")
        self.assertEqual(caught.exception.code, "AI_RESPONSE_REPAIR_FAILED")
        self.assertEqual(
            [str(item.get("task")) for item in calls],
            ["JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2", "JOBOPS_REPAIR_PRIVATE_DOCUMENT_UNDERSTANDING_V2"],
        )

    def test_wsl_distribution_discovery_decodes_utf16_and_rejects_unsafe_names(self) -> None:
        encoded = "Ubuntu\r\nUbuntu\r\n--exec\r\n../unsafe\r\nDebian Test\r\n".encode("utf-16")
        self.assertEqual(_decode_wsl_distribution_output(encoded), ["Ubuntu", "Debian Test"])

    def test_only_loopback_ai_endpoints_are_accepted(self) -> None:
        for allowed in ("http://127.0.0.1:11434", "http://localhost:1234/v1", "http://[::1]:8080"):
            _assert_loopback_url(allowed)
        for blocked in ("https://example.test/v1", "http://192.168.1.2:8080", "http://user:pass@127.0.0.1:8080"):
            with self.assertRaises(JobOpsError) as caught:
                _assert_loopback_url(blocked)
            self.assertEqual(caught.exception.code, "AI_LOOPBACK_REQUIRED")

    def test_local_model_is_detected_and_preference_contains_no_credentials_or_paths(self) -> None:
        calls: list[str] = []

        def fake_http(url: str, **_: object) -> dict[str, object]:
            calls.append(url)
            if url == "http://127.0.0.1:11434/api/tags":
                return {"models": [{"name": "synthetic-local-model"}]}
            if url == "http://127.0.0.1:11434/api/chat":
                return {"message": {"content": '{"status":"READY","protocol":1}'}}
            raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")

        with tempfile.TemporaryDirectory(prefix="jobops-ai-test-") as directory:
            root = Path(directory)
            config = root / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda _: None,
                http_json=fake_http,
            )
            engine = manager.connect("local_model")
            status = engine.public_status()
            self.assertEqual(status["connection_id"], "ollama")
            self.assertEqual(status["model"], "synthetic-local-model")
            self.assertEqual(status["data_route"], "LOCAL_MACHINE_ONLY")
            self.assertEqual(calls, ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/chat"])
            saved = config.read_text(encoding="utf-8")
            self.assertNotIn(str(root), saved)
            self.assertNotIn("api_key", saved.casefold())
            self.assertNotIn("token", saved.casefold())
            self.assertFalse(json.loads(saved)["contains_credentials"])

    def test_openclaw_uses_ephemeral_stdin_and_never_places_private_text_in_arguments(self) -> None:
        invocations: list[tuple[list[str], str]] = []
        metadata_commands: list[list[str]] = []
        working_directories: list[Path] = []
        safe_configs: list[dict[str, object]] = []
        safe_config_paths: list[Path] = []

        def fake_http(_: str, **__: object) -> dict[str, object]:
            raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if "models" in command and "status" in command:
                metadata_commands.append(list(command))
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps({"resolvedDefault": "synthetic/provider-model"}), stderr="",
                )
            body = str(kwargs["input"])
            invocations.append((list(command), body))
            working_directories.append(Path(str(kwargs["cwd"])))
            config_path = Path(command[command.index("--config") + 1])
            safe_config_paths.append(config_path)
            safe_configs.append(json.loads(config_path.read_text(encoding="utf-8")))
            request = json.loads(body)
            if request.get("task") == "JOBOPS_AI_CONNECTION_TEST":
                output = {"toolSummary": {"calls": 0, "tools": []}, "result": {"content": '{"status":"READY","protocol":1}'}}
            else:
                payload = {
                    "schema_version": 2,
                    "entities": [{
                        "entity_key": "synthetic-project", "entity_type": "project",
                        "organization": "Synthetic", "role": "Project", "start_date": "", "end_date": "",
                        "line_start": 1, "line_end": 1,
                    }],
                    "candidates": [{
                        "statement": "Built a synthetic private project.", "category": "project",
                        "claim_kind": "achievement", "entity_key": "synthetic-project", "confidence": "HIGH",
                        "line_start": 1, "line_end": 1, "reason": "Explicit source statement.",
                    }],
                }
                output = {"toolSummary": {"calls": 0, "tools": []}, "result": {"message": {"content": json.dumps(payload)}}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(output), stderr="")

        with tempfile.TemporaryDirectory(prefix="jobops-agent-test-") as directory:
            root = Path(directory)
            executable = root / "openclaw.exe"
            executable.write_bytes(b"synthetic")
            manager = AIConnectionManager(
                root / "JobOps" / "ai-connection.json",
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: str(executable) if name == "openclaw" else None,
                http_json=fake_http,
                process_runner=fake_runner,
            )
            engine = manager.connect("agent")
            candidates, summary = engine.analyze_document(
                "Built a synthetic private project.", source_id="SRC-SYNTHETIC", source_type="project_case",
            )
            self.assertEqual(engine.public_status()["connection_id"], "openclaw")
            self.assertEqual(engine.public_status()["model"], "synthetic/provider-model")
            self.assertEqual(summary["analysis_mode"], "AI_CORE_ENTITY_ANALYSIS")
            self.assertEqual(len(candidates), 1)
            private_value = "Built a synthetic private project."
            self.assertTrue(any(private_value in body for _, body in invocations))
            self.assertTrue(all(private_value not in " ".join(command) for command, _ in invocations))
            self.assertTrue(all("--message-file" in command and "-" in command for command, _ in invocations))
            self.assertTrue(all("--config" in command and "--code-mode" in command for command, _ in invocations))
            self.assertTrue(all("--model" in command and "synthetic/provider-model" in command for command, _ in invocations))
            self.assertTrue(all(path != root and path.name == "workspace" for path in working_directories))
            self.assertTrue(all(config["tools"]["profile"] == "minimal" for config in safe_configs))
            self.assertTrue(all("group:plugins" in config["tools"]["deny"] for config in safe_configs))
            self.assertTrue(all(not path.exists() for path in safe_config_paths))
            self.assertEqual(len(metadata_commands), 1)
            self.assertNotIn("--probe", metadata_commands[0])

    def test_openclaw_rejects_any_tool_call(self) -> None:
        def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "models" in command and "status" in command:
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps({"resolvedDefault": "synthetic/provider-model"}), stderr="",
                )
            output = {
                "ok": True,
                "status": "ok",
                "toolSummary": {"calls": 1, "tools": ["read"]},
                "final": '{"status":"READY","protocol":1}',
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(output), stderr="")

        with tempfile.TemporaryDirectory(prefix="jobops-agent-tool-test-") as directory:
            root = Path(directory)
            executable = root / "openclaw.exe"
            executable.write_bytes(b"synthetic")
            manager = AIConnectionManager(
                root / "JobOps" / "ai-connection.json",
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: str(executable) if name == "openclaw" else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
            )
            with self.assertRaises(JobOpsError) as caught:
                manager.connect("agent")
            self.assertEqual(caught.exception.code, "AI_AGENT_TOOL_CALL_BLOCKED")

    def test_openclaw_rejects_unsafe_reported_model_without_running_analysis(self) -> None:
        commands: list[list[str]] = []

        def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"resolvedDefault": "--unsafe-model"}), stderr="",
            )

        with tempfile.TemporaryDirectory(prefix="jobops-agent-model-test-") as directory:
            root = Path(directory)
            executable = root / "openclaw.exe"
            executable.write_bytes(b"synthetic")
            manager = AIConnectionManager(
                root / "JobOps" / "ai-connection.json",
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: str(executable) if name == "openclaw" else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
            )
            with self.assertRaises(JobOpsError) as caught:
                manager.connect("agent")
            self.assertEqual(caught.exception.code, "AI_AGENT_MODEL_UNAVAILABLE")
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][-3:], ["models", "status", "--json"])

    def test_wsl_hermes_uses_active_codex_provider_with_zero_tools_and_private_stdin(self) -> None:
        private_value = "Built a synthetic private career project for Hermes."
        adapter_calls: list[tuple[list[str], str]] = []
        adapter_scripts: list[str] = []

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
            if command[-2:] == ["--list", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, stdout="Ubuntu\r\n".encode("utf-16"), stderr=b"")
            script = command[-1] if command and isinstance(command[-1], str) else ""
            if "command -v hermes" in script:
                return subprocess.CompletedProcess(command, 0, stdout="/home/test/.local/bin/hermes\n", stderr="")
            if "command -v curl" in script:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
            if "JOBOPS_HERMES_METADATA_V1" in script:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"ok": True, "model": "gpt-5.6-sol", "provider": "OpenAI Codex"}),
                    stderr="",
                )
            if "JOBOPS_HERMES_SAFE_ADAPTER_V1" in script:
                body = str(kwargs["input"])
                adapter_calls.append((list(command), body))
                adapter_scripts.append(script)
                request = json.loads(body)
                if request.get("task") == "JOBOPS_AI_CONNECTION_TEST":
                    content = '{"status":"READY","protocol":1}'
                else:
                    statement = (
                        private_value
                        if request.get("task") == "JOBOPS_REPAIR_PRIVATE_DOCUMENT_UNDERSTANDING_V2"
                        else private_value.rstrip(".")
                    )
                    content = json.dumps({
                        "schema_version": 2,
                        "entities": [{
                            "entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic",
                            "role": "Project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 1,
                        }],
                        "candidates": [{
                            "statement": statement, "category": "project", "claim_kind": "achievement",
                            "entity_key": "synthetic", "confidence": "HIGH", "line_start": 1, "line_end": 1,
                            "reason": "Explicit source statement.",
                        }],
                    })
                output = {
                    "ok": True,
                    "status": "ok",
                    "toolSummary": {"calls": 0, "tools": []},
                    "result": {"content": content},
                }
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(output), stderr="")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory(prefix="jobops-wsl-hermes-direct-") as directory:
            config = Path(directory) / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
            )
            engine = manager.connect("agent")
            candidates, summary = engine.analyze_document(
                private_value,
                source_id="SRC-WSL-HERMES",
                source_type="project_case",
            )
            status = engine.public_status()
            self.assertEqual(status["connection_id"], "wsl_hermes_agent")
            self.assertEqual(status["model"], "gpt-5.6-sol")
            self.assertIn("OpenAI Codex", status["display_name"])
            self.assertEqual(status["tool_policy"], "NO_TOOLS")
            self.assertEqual(candidates[0]["statement"], private_value)
            self.assertTrue(summary["ai_repair_attempted"])
            analysis_requests = [json.loads(body) for _, body in adapter_calls if json.loads(body).get("schema_version") == 2]
            self.assertEqual(
                [item["task"] for item in analysis_requests],
                ["JOBOPS_PRIVATE_DOCUMENT_UNDERSTANDING_V2", "JOBOPS_REPAIR_PRIVATE_DOCUMENT_UNDERSTANDING_V2"],
            )
            self.assertIn(private_value.rstrip("."), json.dumps(analysis_requests[1], ensure_ascii=False))
            self.assertTrue(any(private_value in body for _, body in adapter_calls))
            self.assertTrue(all(private_value not in " ".join(command) for command, _ in adapter_calls))
            self.assertTrue(all("enabled_toolsets=[]" in script for script in adapter_scripts))
            self.assertTrue(all('disabled_toolsets=["all"]' in script for script in adapter_scripts))
            self.assertTrue(all("mktemp -d" in script and "rm -rf" in script for script in adapter_scripts))
            self.assertTrue(all("/home/test" not in " ".join(command) for command, _ in adapter_calls))
            saved = config.read_text(encoding="utf-8")
            self.assertNotIn("Ubuntu", saved)
            self.assertNotIn("wsl.exe", saved)
            self.assertNotIn(private_value, saved)
            self.assertFalse(json.loads(saved)["contains_credentials"])

    def test_wsl_hermes_without_a_ready_provider_returns_actionable_error(self) -> None:
        commands: list[list[str]] = []
        launch_attempts: list[list[str]] = []

        def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[object]:
            commands.append(list(command))
            if command[-2:] == ["--list", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, stdout="Ubuntu\r\n".encode("utf-16"), stderr=b"")
            if "JOBOPS_HERMES_METADATA_V1" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout='{"ok":false}', stderr="")
            if "proxy status" in command[-1]:
                return subprocess.CompletedProcess(
                    command, 0, stdout="No LLM provider configured. Run hermes model to select a provider.", stderr="",
                )
            if "command -v hermes" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="/home/test/.local/bin/hermes\n", stderr="")
            if "command -v curl" in command[-1]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
            if "command -v openclaw" in command[-1]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory(prefix="jobops-wsl-hermes-auth-") as directory:
            config = Path(directory) / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
                process_launcher=lambda command, **_kwargs: launch_attempts.append(list(command)),
            )
            with self.assertRaises(JobOpsError) as caught:
                manager.connect("agent")
            self.assertEqual(caught.exception.code, "AI_WSL_HERMES_AUTH_REQUIRED")
            self.assertEqual(launch_attempts, [])
            self.assertFalse(config.exists())
            self.assertTrue(any("proxy status" in " ".join(command) for command in commands))

    def test_wsl_hermes_starts_loopback_only_proxy_and_persists_no_distro(self) -> None:
        launched = False
        launched_commands: list[list[str]] = []

        class FakeProcess:
            def poll(self) -> None:
                return None

        def fake_http(url: str, **_: object) -> dict[str, object]:
            if not launched:
                raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
            if url == "http://127.0.0.1:8645/v1/models":
                return {"data": [{"id": "synthetic/hermes-model"}]}
            if url == "http://127.0.0.1:8645/v1/chat/completions":
                return {"choices": [{"message": {"content": '{"status":"READY","protocol":1}'}}]}
            raise JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")

        def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[object]:
            if command[-2:] == ["--list", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, stdout="Ubuntu\r\n".encode("utf-16"), stderr=b"")
            if "JOBOPS_HERMES_METADATA_V1" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout='{"ok":false}', stderr="")
            if "/v1/models" in command[-1]:
                return subprocess.CompletedProcess(command, 22, stdout="", stderr="not running")
            if "proxy status" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="Provider ready", stderr="")
            if "command -v hermes" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="/home/test/.local/bin/hermes\n", stderr="")
            if "command -v curl" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="/usr/bin/curl\n", stderr="")
            raise AssertionError(command)

        def fake_launcher(command: list[str], **_: object) -> FakeProcess:
            nonlocal launched
            launched = True
            launched_commands.append(list(command))
            return FakeProcess()

        with tempfile.TemporaryDirectory(prefix="jobops-wsl-hermes-ready-") as directory:
            config = Path(directory) / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else None,
                http_json=fake_http,
                process_runner=fake_runner,
                process_launcher=fake_launcher,
            )
            engine = manager.connect("agent")
            status = engine.public_status()
            self.assertEqual(status["connection_id"], "wsl_hermes_proxy")
            self.assertEqual(status["private_transport"], "WINDOWS_TO_WSL_LOOPBACK_HTTP")
            self.assertEqual(len(launched_commands), 1)
            self.assertIn("127.0.0.1", " ".join(launched_commands[0]))
            self.assertNotIn("0.0.0.0", " ".join(launched_commands[0]))
            self.assertIn("--exec", launched_commands[0])
            self.assertNotIn("/home/test", " ".join(launched_commands[0]))
            saved = config.read_text(encoding="utf-8")
            self.assertNotIn("Ubuntu", saved)
            self.assertNotIn("wsl.exe", saved)
            self.assertFalse(json.loads(saved)["contains_credentials"])

    def test_wsl_local_model_uses_stdin_bridge_for_private_content(self) -> None:
        curl_calls: list[tuple[list[str], str | None]] = []
        private_value = "Built a synthetic private career project."

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
            if command[-2:] == ["--list", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, stdout="Ubuntu\r\n".encode("utf-16"), stderr=b"")
            if "/api/tags" in command[-1] or "/api/chat" in command[-1]:
                body = kwargs.get("input")
                curl_calls.append((list(command), str(body) if body is not None else None))
                if "/api/tags" in command[-1]:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=json.dumps({"models": [{"name": "synthetic-wsl-model"}]}), stderr="",
                    )
                if "/api/chat" in command[-1]:
                    request = json.loads(str(body))
                    user_content = request["messages"][1]["content"]
                    if private_value in user_content:
                        content = json.dumps({
                            "schema_version": 2,
                            "entities": [{
                                "entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic",
                                "role": "Project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 1,
                            }],
                            "candidates": [{
                                "statement": private_value, "category": "project", "claim_kind": "achievement",
                                "entity_key": "synthetic", "confidence": "HIGH", "line_start": 1, "line_end": 1,
                                "reason": "Explicit source statement.",
                            }],
                        })
                    else:
                        content = '{"status":"READY","protocol":1}'
                    return subprocess.CompletedProcess(
                        command, 0, stdout=json.dumps({"message": {"content": content}}), stderr="",
                    )
            if "command -v curl" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="/usr/bin/curl\n", stderr="")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory(prefix="jobops-wsl-local-") as directory:
            config = Path(directory) / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
            )
            engine = manager.connect("local_model")
            candidates, _ = engine.analyze_document(private_value, source_id="SRC-WSL", source_type="project_case")
            self.assertEqual(engine.public_status()["connection_id"], "wsl_ollama")
            self.assertEqual(engine.public_status()["data_route"], "WSL_LOCAL_MACHINE_ONLY")
            self.assertEqual(candidates[0]["statement"], private_value)
            self.assertTrue(any(private_value in (body or "") for _, body in curl_calls))
            self.assertTrue(all(private_value not in " ".join(command) for command, _ in curl_calls))
            self.assertTrue(all("--exec" in command for command, _ in curl_calls))
            self.assertTrue(all("/home/test" not in " ".join(command) for command, _ in curl_calls))
            saved = config.read_text(encoding="utf-8")
            self.assertNotIn("Ubuntu", saved)
            self.assertNotIn("wsl.exe", saved)
            self.assertNotIn(private_value, saved)

    def test_wsl_openclaw_uses_isolated_workspace_and_private_stdin(self) -> None:
        private_value = "Delivered a synthetic WSL project."
        agent_calls: list[tuple[list[str], str]] = []
        agent_scripts: list[str] = []

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
            if command[-2:] == ["--list", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, stdout="Ubuntu\r\n".encode("utf-16"), stderr=b"")
            if "models status --json" in command[-1]:
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps({"resolvedDefault": "synthetic/wsl-agent-model"}), stderr="",
                )
            if "agent exec" in command[-1]:
                agent_scripts.append(command[-1])
                body = str(kwargs["input"])
                agent_calls.append((list(command), body))
                request = json.loads(body)
                if request.get("task") == "JOBOPS_AI_CONNECTION_TEST":
                    result = {"toolSummary": {"calls": 0}, "result": {"content": '{"status":"READY","protocol":1}'}}
                else:
                    payload = {
                        "schema_version": 2,
                        "entities": [{
                            "entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic",
                            "role": "Project", "start_date": "", "end_date": "", "line_start": 1, "line_end": 1,
                        }],
                        "candidates": [{
                            "statement": private_value, "category": "project", "claim_kind": "achievement",
                            "entity_key": "synthetic", "confidence": "HIGH", "line_start": 1, "line_end": 1,
                            "reason": "Explicit source statement.",
                        }],
                    }
                    result = {"toolSummary": {"calls": 0}, "result": {"content": json.dumps(payload)}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr="")
            if "command -v hermes" in command[-1]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
            if "command -v openclaw" in command[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="/home/test/.local/bin/openclaw\n", stderr="")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory(prefix="jobops-wsl-openclaw-") as directory:
            config = Path(directory) / "JobOps" / "ai-connection.json"
            manager = AIConnectionManager(
                config,
                initial_engine=AIAnalysisEngine(),
                command_resolver=lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else None,
                http_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    JobOpsError("AI_LOCAL_ENDPOINT_UNAVAILABLE", "not running")
                ),
                process_runner=fake_runner,
            )
            engine = manager.connect("agent")
            candidates, _ = engine.analyze_document(private_value, source_id="SRC-WSL-AGENT", source_type="project_case")
            status = engine.public_status()
            self.assertEqual(status["connection_id"], "wsl_openclaw")
            self.assertEqual(status["private_transport"], "WSL_EPHEMERAL_STDIN_STDOUT")
            self.assertEqual(candidates[0]["statement"], private_value)
            self.assertTrue(any(private_value in body for _, body in agent_calls))
            self.assertTrue(all(private_value not in " ".join(command) for command, _ in agent_calls))
            self.assertTrue(all("--exec" in command for command, _ in agent_calls))
            self.assertTrue(all("/home/test" not in " ".join(command) for command, _ in agent_calls))
            self.assertTrue(all("--message-file -" in script for script in agent_scripts))
            self.assertTrue(all("mktemp -d" in script and "rm -rf" in script for script in agent_scripts))
            self.assertTrue(all('"profile":"minimal"' in script for script in agent_scripts))
            self.assertTrue(all("group:web" in script for script in agent_scripts))
            saved = config.read_text(encoding="utf-8")
            self.assertNotIn("Ubuntu", saved)
            self.assertNotIn("/home/test", saved)
            self.assertNotIn("wsl.exe", saved)
            self.assertNotIn(private_value, saved)


if __name__ == "__main__":
    unittest.main()
