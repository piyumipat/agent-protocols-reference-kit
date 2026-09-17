"""In-process tests for the basic MCP client and server wrappers."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent, TextResourceContents

from agent_protocols.mcp import MCPClient, create_server


def build_test_server() -> MCPServer:
    server = create_server("test-server", instructions="Basic round-trip tests.")

    @server.tool()
    async def add(left: int, right: int) -> int:
        return left + right

    @server.tool()
    async def fail() -> str:
        raise ValueError("expected test failure")

    @server.resource("test://greeting")
    async def greeting() -> str:
        return "hello from the test server"

    @server.prompt()
    async def summarize(topic: str) -> str:
        return f"Summarize {topic} in one sentence."

    return server


async def test_tools_success_and_error_results() -> None:
    async with asyncio.timeout(5), MCPClient(build_test_server()) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["add", "fail"]

        success = await client.call_tool("add", {"left": 12, "right": 7})
        assert success.is_error is not True
        assert success.structured_content == {"result": 19}

        failure = await client.call_tool("fail")
        assert failure.is_error is True
        assert failure.content
        assert isinstance(failure.content[0], TextContent)
        assert "Error executing tool fail" in failure.content[0].text


async def test_resources() -> None:
    async with asyncio.timeout(5), MCPClient(build_test_server()) as client:
        resources = await client.list_resources()
        assert [str(resource.uri) for resource in resources.resources] == [
            "test://greeting"
        ]

        result = await client.read_resource("test://greeting")
        assert len(result.contents) == 1
        assert isinstance(result.contents[0], TextResourceContents)
        assert result.contents[0].text == "hello from the test server"


async def test_prompts() -> None:
    async with asyncio.timeout(5), MCPClient(build_test_server()) as client:
        prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts.prompts] == ["summarize"]

        result = await client.get_prompt("summarize", {"topic": "MCP"})
        assert len(result.messages) == 1
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Summarize MCP in one sentence."
