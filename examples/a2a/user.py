"""Human-facing entry point for the three-agent A2A example."""

from __future__ import annotations

import asyncio

from agent_protocols.a2a import A2AClient, Role, get_message_text, new_text_message
from examples.a2a.registry import registry
from examples.a2a.security import LOCAL_AGENT_TOKEN


async def main() -> None:
    user_request = input("Ask the local agent: ").strip()
    if not user_request:
        user_request = "What should we know before adopting A2A?"

    # The user-facing program uses A2A to reach the local agent.
    async with A2AClient(
        registry.resolve("local"),
        headers={"Authorization": f"Bearer {LOCAL_AGENT_TOKEN}"},
        request_timeout_seconds=300.0,
    ) as client:
        async for response in client.send_message(
            new_text_message(user_request, role=Role.ROLE_USER)
        ):
            if response.HasField("message"):
                print(get_message_text(response.message))


if __name__ == "__main__":
    asyncio.run(main())
