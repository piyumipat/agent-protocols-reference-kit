"""Helpers for creating and running a basic MCP server."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def create_server(
    name: str,
    *,
    instructions: str | None = None,
    version: str = "0.1.0",
) -> MCPServer:
    """Create an official SDK server ready for tool, resource, and prompt registration."""
    return MCPServer(name=name, instructions=instructions, version=version)


async def run_stdio(server: MCPServer) -> None:
    """Serve MCP over stdin/stdout until the client disconnects."""
    await server.run_stdio_async()
