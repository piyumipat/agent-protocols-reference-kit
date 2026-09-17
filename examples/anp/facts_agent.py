"""Independently runnable ANP facts specialist."""

from __future__ import annotations

import asyncio

import aiohttp

from agent_protocols.anp.server import TextHandler
from agent_protocols.utils import LLMClient, TextGenerator
from examples.anp.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    console_action,
    host_role,
)
from examples.anp.prompts import FACTS_INSTRUCTION


def make_handler(
    session: aiohttp.ClientSession,
    config: DemoConfig,
    on_action: ActionSink | None,
    *,
    generator: TextGenerator | None = None,
) -> TextHandler:
    del session, config

    async def answer(question: str, caller_did: str) -> str:
        del caller_did  # The shared server has already authenticated and authorized it.
        announce(on_action, "[facts] Authorized coordinator DID; asking LLM for facts")
        if generator is None:
            async with LLMClient() as llm:
                reply = await llm.generate_text(FACTS_INSTRUCTION, question)
        else:
            reply = await generator(FACTS_INSTRUCTION, question)
        announce(on_action, "[facts] Returning factual reply")
        return reply

    return answer


async def main() -> None:
    config = DemoConfig()
    async with host_role(config, "facts", make_handler, on_action=console_action):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
