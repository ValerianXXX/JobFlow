"""Developer-only probe of an installed Hermes MCP client, with disposable config.

Run with the Hermes Python interpreter and --hermes-root pointing at its source.
Supply only a synthetic JobFlow stdio server configuration as JSON on stdin.
No model, credentials, user's Hermes config, or external service is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-root", required=True, type=Path)
    args = parser.parse_args()
    config = json.load(sys.stdin)
    if set(config) != {"command", "args", "env"}:
        raise ValueError("Supply an explicit synthetic stdio server configuration.")
    with tempfile.TemporaryDirectory(prefix="jobflow-hermes-mcp-") as temporary:
        os.environ["HERMES_HOME"] = temporary
        sys.path.insert(0, str(args.hermes_root.resolve(strict=True)))
        from tools.mcp_tool import _connect_server

        async def probe():
            server = await _connect_server("jobflow_synthetic_probe", config)
            try:
                names = sorted(tool.name for tool in server._tools)
                expected = [
                    "get_application_summary",
                    "get_readiness",
                    "list_pending_reviews",
                ]
                assert names == expected, names
                result = await server.session.call_tool("get_readiness", {})
                data = result.structuredContent
                assert data["application_count"] == 1
                assert data["private_content_included"] is False
                pending = await server.session.call_tool(
                    "list_pending_reviews", {"limit": 20}
                )
                application_id = pending.structuredContent["applications"][0][
                    "application_id"
                ]
                summary = await server.session.call_tool(
                    "get_application_summary", {"application_id": application_id}
                )
                assert (
                    summary.structuredContent["next_action"]
                    == "VERIFY_WITH_EMPLOYER_NO_RETRY"
                )
                print(
                    json.dumps(
                        {
                            "status": "PASS",
                            "client_sdk_version": version("mcp"),
                            "protocol_version": server.initialize_result.protocolVersion,
                            "tools": names,
                            "tool_calls": 3,
                            "model_calls": 0,
                            "synthetic_only": True,
                            "user_config_changed": False,
                        }
                    )
                )
            finally:
                await server.shutdown()

        asyncio.run(asyncio.wait_for(probe(), timeout=45))


if __name__ == "__main__":
    main()
