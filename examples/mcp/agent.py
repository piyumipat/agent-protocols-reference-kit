"""Use an LLM to discover and call a tool on the example MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

from mcp import StdioServerParameters
from mcp.types import CallToolResult
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
)

from agent_protocols.mcp import MCPClient, to_openai_tool_schemas
from agent_protocols.utils import LLMClient

SERVER_SCRIPT = Path(__file__).with_name("server.py")
USER_QUESTION = "What is 12 multiplied by 7? Use the calculator tool."


def tool_result_text(result: CallToolResult) -> str:
    """Serialize an MCP result for the next LLM message."""
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])
    messages: list[ChatCompletionMessageParam] = [
        {"role": "user", "content": USER_QUESTION}
    ]

    async with MCPClient(server) as mcp_client, LLMClient() as llm:
        available_tools = await mcp_client.list_tools()
        response = await llm.chat(
            messages,
            tools=to_openai_tool_schemas(available_tools.tools),
        )

        if not response.tool_calls:
            print(response.content or "")
            return

        messages.append(
            cast(
                ChatCompletionAssistantMessageParam,
                response.model_dump(exclude_none=True),
            )
        )

        for tool_call in response.tool_calls:
            if tool_call.type != "function":
                raise TypeError(f"Unsupported tool-call type: {tool_call.type}")

            decoded: Any = json.loads(tool_call.function.arguments)
            if not isinstance(decoded, dict):
                raise TypeError("Tool arguments must decode to a JSON object")

            result = await mcp_client.call_tool(tool_call.function.name, decoded)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result_text(result),
                }
            )

        final = await llm.chat(messages)
        print(final.content or "")


if __name__ == "__main__":
    asyncio.run(main())
