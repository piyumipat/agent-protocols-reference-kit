"""Small async client wrapper for the basic MCP operations."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, TypeAlias

from mcp import StdioServerParameters
from mcp.client import Client, Transport
from mcp.server import Server
from mcp.server.mcpserver import MCPServer
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

ConnectionTarget: TypeAlias = (
    StdioServerParameters | MCPServer | Server[Any] | Transport | str
)
"""A local process, in-process server, custom transport, or remote MCP URL."""


class MCPClient:
    """Manage an MCP connection and expose its basic server primitives.

    The official SDK owns transport setup, protocol discovery/version handling,
    JSON-RPC dispatch, and shutdown. ``read_timeout_seconds`` bounds requests so
    an unavailable or unresponsive peer does not block forever.
    """

    def __init__(
        self,
        target: ConnectionTarget,
        *,
        read_timeout_seconds: float | None = 30.0,
        **client_options: Any,
    ) -> None:
        self._client = Client(
            target,
            read_timeout_seconds=read_timeout_seconds,
            **client_options,
        )

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    @property
    def protocol_version(self) -> str:
        """Return the protocol revision selected by the SDK."""
        return self._client.protocol_version

    async def list_tools(self) -> ListToolsResult:
        return await self._client.list_tools()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        return await self._client.call_tool(name, arguments)

    async def list_resources(self) -> ListResourcesResult:
        return await self._client.list_resources()

    async def read_resource(self, uri: str) -> ReadResourceResult:
        return await self._client.read_resource(uri)

    async def list_prompts(self) -> ListPromptsResult:
        return await self._client.list_prompts()

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        return await self._client.get_prompt(name, arguments)
