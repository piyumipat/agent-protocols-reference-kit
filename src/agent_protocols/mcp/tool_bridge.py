"""Convert MCP tool definitions for OpenAI-compatible LLM clients."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.types import Tool


def to_openai_tool_schema(tool: Tool) -> dict[str, Any]:
    """Convert one MCP tool into a chat-completions function-tool definition."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def to_openai_tool_schemas(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Convert tools without changing their order."""
    return [to_openai_tool_schema(tool) for tool in tools]
