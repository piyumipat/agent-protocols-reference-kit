"""Independently runnable ANP coordinator that discovers both specialists."""

from __future__ import annotations

import asyncio

import aiohttp

from agent_protocols.anp import ANPClient, create_http_authenticator
from agent_protocols.anp.server import TextHandler
from agent_protocols.utils import LLMClient, TextGenerator
from examples.anp.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    console_action,
    host_role,
    load_identity,
)
from examples.anp.prompts import COORDINATOR_INSTRUCTION


def make_handler(
    session: aiohttp.ClientSession,
    config: DemoConfig,
    on_action: ActionSink | None,
    *,
    generator: TextGenerator | None = None,
) -> TextHandler:
    identity = load_identity(config, "coordinator")

    async def ask_specialist(role: str, question: str) -> str:
        seed_domain = config.domain(role)
        announce(on_action, f"[coordinator] Discovering {role} at {seed_domain}")
        # Each origin gets its own authenticator and client; SDK tokens are hostname-keyed.
        authenticator = create_http_authenticator(
            identity.document_path, identity.key_path
        )
        async with ANPClient(authenticator, session=session) as client:
            discovered = await client.discover(seed_domain)
            if len(discovered) != 1:
                raise RuntimeError(f"Expected one discovered {role} agent")
            announce(
                on_action, f"[coordinator] Verified {role} DID and description link"
            )
            announce(on_action, f"[coordinator] Sending signed answer call to {role}")
            reply = await client.answer(
                discovered[0], question, force_new_signature=True
            )
        announce(on_action, f"[coordinator] Received {role} reply")
        return reply

    async def answer(question: str, caller_did: str) -> str:
        del caller_did  # The shared server has already authenticated and authorized it.
        announce(on_action, "[coordinator] Authorized caller DID; asking specialists")
        facts, review = await asyncio.gather(
            ask_specialist("facts", question),
            ask_specialist("review", question),
        )
        announce(on_action, "[coordinator] Synthesizing facts and review replies")
        prompt = (
            f"User question:\n{question}\n\n"
            f"Facts specialist response:\n{facts}\n\n"
            f"Review specialist response:\n{review}"
        )
        if generator is None:
            async with LLMClient() as llm:
                result = await llm.generate_text(COORDINATOR_INSTRUCTION, prompt)
        else:
            result = await generator(COORDINATOR_INSTRUCTION, prompt)
        announce(on_action, "[coordinator] Returning final answer")
        return result

    return answer


async def main() -> None:
    config = DemoConfig()
    async with host_role(config, "coordinator", make_handler, on_action=console_action):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
