"""Exercise the example server's basic MCP primitives without an LLM."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import StdioServerParameters

from agent_protocols.mcp import MCPClient

SERVER_SCRIPT = Path(__file__).with_name("server.py")


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])

    async with MCPClient(server) as client:
        tools = await client.list_tools()
        print("tools:", [tool.name for tool in tools.tools])

        calculation = await client.call_tool(
            "calculator",
            {"left": 12, "right": 7, "operation": "multiply"},
        )
        print("tool result:", calculation.structured_content)

        resources = await client.list_resources()
        print("resources:", [str(resource.uri) for resource in resources.resources])
        server_status = await client.read_resource("example://status")
        print("resource result:", server_status.contents[0])

        prompts = await client.list_prompts()
        print("prompts:", [prompt.name for prompt in prompts.prompts])
        explanation = await client.get_prompt(
            "explain_result",
            {"calculation": "12 * 7", "result": "84"},
        )
        print("prompt result:", explanation.messages[0])


if __name__ == "__main__":
    asyncio.run(main())
