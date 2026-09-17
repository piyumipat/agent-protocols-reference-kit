"""Requester that already shares a single-round PD with the calculator."""

from __future__ import annotations

import asyncio

import httpx

from agent_protocols.agora import AgoraClient, ProtocolRegistry
from examples.agora.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    answer_from_response,
    console_action,
    shared_protocol,
)

QUESTION = "What is 10 - 3?"


async def ask_once(
    *,
    config: DemoConfig | None = None,
    on_action: ActionSink | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    config = config or DemoConfig()
    document = shared_protocol()
    known_protocols = ProtocolRegistry((document,))
    async with AgoraClient(
        config.calculator_url,
        require_https=False,
        http_client=http_client,
    ) as client:
        announce(on_action, "[shared-requester] Discovering calculator protocols")
        shared = await client.find_shared_protocol(known_protocols)
        if shared is None:
            raise RuntimeError("Expected a pre-shared Protocol Document")
        announce(on_action, f"[shared-requester] Found shared PD {shared.hash}")
        announce(on_action, f"[shared-requester] Making one-time call: {QUESTION}")
        response = await client.exchange({"question": QUESTION}, protocol=shared)
    if response.conversation_id is not None:
        raise RuntimeError("One-time call unexpectedly created a conversation")
    answer = answer_from_response(response)
    announce(on_action, f"[shared-requester] Received one-time answer {answer}")
    return answer


async def main() -> None:
    answer = await ask_once(on_action=console_action)
    print(f"{QUESTION} {answer}")


if __name__ == "__main__":
    asyncio.run(main())
