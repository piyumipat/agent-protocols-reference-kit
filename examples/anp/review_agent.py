"""Independently runnable ANP review specialist."""

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
from examples.anp.prompts import REVIEW_INSTRUCTION


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
        announce(
            on_action, "[review] Authorized coordinator DID; asking LLM for review"
        )
        if generator is None:
            async with LLMClient() as llm:
                reply = await llm.generate_text(REVIEW_INSTRUCTION, question)
        else:
            reply = await generator(REVIEW_INSTRUCTION, question)
        announce(on_action, "[review] Returning review reply")
        return reply

    return answer


async def main() -> None:
    config = DemoConfig()
    async with host_role(config, "review", make_handler, on_action=console_action):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
