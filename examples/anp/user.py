"""Human-facing caller for the separate ANP facts, review, and coordinator servers."""

from __future__ import annotations

import asyncio

from agent_protocols.anp import ANPClient, create_http_authenticator
from examples.anp.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    client_session,
    console_action,
    load_identity,
)

DEFAULT_QUESTION = "What should we know before adopting ANP?"


async def ask_coordinator(
    question: str,
    *,
    config: DemoConfig | None = None,
    on_action: ActionSink | None = None,
) -> str:
    config = config or DemoConfig()
    caller = load_identity(config, "caller")
    authenticator = create_http_authenticator(caller.document_path, caller.key_path)
    async with (
        client_session(config) as session,
        ANPClient(authenticator, session=session) as client,
    ):
        seed_domain = config.domain("coordinator")
        announce(on_action, f"[caller] Discovering coordinator at {seed_domain}")
        discovered = await client.discover(seed_domain)
        if len(discovered) != 1:
            raise RuntimeError("Expected one discovered coordinator agent")
        announce(on_action, "[caller] Verified coordinator DID and description link")
        announce(on_action, "[caller] Sending signed answer call to coordinator")
        answer = await client.answer(discovered[0], question, force_new_signature=True)
        announce(on_action, "[caller] Received final answer")
        return answer


async def main() -> None:
    try:
        question = input("Ask the local agent: ").strip()
    except EOFError:
        question = ""
    answer = await ask_coordinator(
        question or DEFAULT_QUESTION, on_action=console_action
    )
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
