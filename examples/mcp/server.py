"""Example MCP server exposing a tool, resource, and prompt over stdio."""

from __future__ import annotations

import asyncio
from typing import Literal

from agent_protocols.mcp import create_server, run_stdio

server = create_server("example-server", instructions="Basic MCP primitive examples.")


@server.tool()
async def calculator(
    left: float,
    right: float,
    operation: Literal["add", "subtract", "multiply", "divide"],
) -> float:
    """Apply one basic arithmetic operation to two numbers."""
    if operation == "add":
        return left + right
    if operation == "subtract":
        return left - right
    if operation == "multiply":
        return left * right
    if right == 0:
        raise ValueError("Cannot divide by zero")
    return left / right


@server.resource("example://status")
async def status() -> str:
    """Describe the capabilities exposed by this example server."""
    return "example-server: calculator tool, status resource, explain_result prompt"


@server.prompt()
async def explain_result(calculation: str, result: str) -> str:
    """Create a request to explain a calculation result."""
    return f"Explain in one sentence why {calculation} equals {result}."


if __name__ == "__main__":
    asyncio.run(run_stdio(server))
