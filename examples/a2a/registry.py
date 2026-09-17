"""External endpoint registry used by the three-agent example.

The registry is intentionally not an A2A component. It only tells the local
agent where discovery starts; Agent Cards remain the authoritative A2A
descriptions of the remote agents.
"""

from __future__ import annotations

from collections.abc import Mapping


class AgentRegistry:
    """Resolve an application role to an agent's discovery base URL."""

    def __init__(self, endpoints: Mapping[str, str]) -> None:
        self._endpoints = dict(endpoints)

    def resolve(self, role: str) -> str:
        try:
            return self._endpoints[role]
        except KeyError as error:
            raise LookupError(f"No agent registered for role: {role}") from error


registry = AgentRegistry(
    {
        "local": "http://127.0.0.1:8000",
        "facts": "http://127.0.0.1:8001",
        "review": "http://127.0.0.1:8002",
    }
)
