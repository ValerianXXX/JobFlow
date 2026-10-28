from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from _support import PROJECT, project_temp
import test_onboarding_center as support
from test_workbench import seed_application
from jobops.mcp_server import ReadOnlyQueries


class MCPTests(unittest.TestCase):
    def test_database_connection_is_read_only_and_output_is_redacted(self):
        with project_temp() as root:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database)
            queries = ReadOnlyQueries(service.project)
            with queries.connection() as c:
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute("DELETE FROM applications")
            result = queries.readiness()
            self.assertEqual(result["external_result_needs_verification_count"], 1)
            self.assertNotIn("Example Synthetic", json.dumps(result))
            self.assertNotIn("secure-ref", json.dumps(result))

    def test_real_stdio_sdk_modern_and_legacy_clients(self):
        async def exercise(project, mode):
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "jobops.mcp_server", "--project", str(project)],
                env={
                    **os.environ,
                    "PYTHONPATH": str(PROJECT / "src"),
                    "PYTHONUTF8": "1",
                },
            )
            async with Client(params, mode=mode, read_timeout_seconds=15) as client:
                self.assertEqual(
                    client.protocol_version,
                    "2026-07-28" if mode == "auto" else "2025-11-25",
                )
                listed = await client.list_tools()
                tools = listed.tools if hasattr(listed, "tools") else listed
                self.assertEqual(
                    {tool.name for tool in tools},
                    {
                        "get_readiness",
                        "list_pending_reviews",
                        "get_application_summary",
                    },
                )
                for tool in tools:
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertFalse(tool.annotations.open_world_hint)
                ready = await client.call_tool("get_readiness", {})
                self.assertIsNotNone(ready.structured_content, ready.model_dump())
                self.assertEqual(ready.structured_content["application_count"], 1)
                pending = await client.call_tool("list_pending_reviews", {"limit": 20})
                self.assertEqual(
                    pending.structured_content["applications"][0]["application_id"],
                    "APP-AAAAAAAAAAAA",
                )
                summary = await client.call_tool(
                    "get_application_summary", {"application_id": "APP-AAAAAAAAAAAA"}
                )
                self.assertEqual(
                    summary.structured_content["next_action"],
                    "VERIFY_WITH_EMPLOYER_NO_RETRY",
                )
                invalid = await client.call_tool(
                    "get_application_summary", {"application_id": "../../secret"}
                )
                self.assertEqual(
                    invalid.structured_content["code"], "MCP_ARGUMENT_INVALID"
                )
                absent = await client.call_tool("submit_application", {})
                self.assertTrue(absent.is_error)
                self.assertNotIn(
                    "Example Synthetic", json.dumps(summary.structured_content)
                )
                self.assertNotIn("secure-ref", json.dumps(summary.structured_content))

        with project_temp() as root:
            service, _, _, _, _ = support.OnboardingCenterTests().make_service(root)
            seed_application(service.database, "APP-AAAAAAAAAAAA")
            for mode in ("auto", "legacy"):
                with self.subTest(mode=mode):
                    anyio.run(exercise, service.project, mode)
