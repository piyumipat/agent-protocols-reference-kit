"""End-to-end test of the public wrapper over a real stdio subprocess."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import StdioServerParameters
from mcp.types import TextContent, TextResourceContents

from agent_protocols.mcp import MCPClient

PROJECT_ROOT = Path(__file__).parents[2]
SERVER_SCRIPT = PROJECT_ROOT / "examples" / "mcp" / "server.py"


async def test_stdio_tools_resources_prompts_and_shutdown() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        cwd=PROJECT_ROOT,
    )

    async with (
        asyncio.timeout(10),
        MCPClient(
            parameters,
            read_timeout_seconds=5,
        ) as client,
    ):
        assert client.protocol_version

        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["calculator"]
        calculation = await client.call_tool(
            "calculator",
            {"left": 12, "right": 7, "operation": "multiply"},
        )
        assert calculation.is_error is not True
        assert calculation.structured_content == {"result": 84.0}

        resources = await client.list_resources()
        assert [str(resource.uri) for resource in resources.resources] == [
            "example://status"
        ]
        status = await client.read_resource("example://status")
        assert isinstance(status.contents[0], TextResourceContents)
        assert "calculator tool" in status.contents[0].text

        prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts.prompts] == ["explain_result"]
        explanation = await client.get_prompt(
            "explain_result",
            {"calculation": "12 * 7", "result": "84"},
        )
        assert isinstance(explanation.messages[0].content, TextContent)
        assert (
            explanation.messages[0].content.text
            == "Explain in one sentence why 12 * 7 equals 84."
        )
