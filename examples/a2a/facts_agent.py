"""Remote A2A agent that contributes factual protocol context."""

from __future__ import annotations

import uvicorn

from agent_protocols.a2a import (
    AgentExecutor,
    EventQueue,
    RequestContext,
    create_agent_card,
    create_server,
    create_skill,
    new_text_message,
)
from agent_protocols.utils import LLMClient, TextGenerator
from examples.a2a.security import (
    FACTS_AGENT_TOKEN,
    bearer_middleware,
    bearer_security_requirements,
    bearer_security_schemes,
)


class FactsExecutor(AgentExecutor):
    """Use an LLM to produce evidence-oriented context for the local agent."""

    def __init__(self, generator: TextGenerator | None = None) -> None:
        self._generate = generator

    async def _generate_text(self, instruction: str, prompt: str) -> str:
        if self._generate is not None:
            return await self._generate(instruction, prompt)
        async with LLMClient() as llm:
            return await llm.generate_text(instruction, prompt)

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        prompt = context.get_user_input()
        response = await self._generate_text(
            "You are the facts specialist in a multi-agent A2A workflow. "
            "Give accurate, concise factual context. State uncertainty rather "
            "than inventing details.",
            prompt,
        )
        await event_queue.enqueue_event(
            new_text_message(response)
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        del context, event_queue


agent_card = create_agent_card(
    "Facts agent",
    "Uses an LLM to provide factual context for a requested subject.",
    "http://127.0.0.1:8001/a2a",
    [
        create_skill(
            "protocol-facts",
            "Protocol facts",
            "Uses an LLM to return factual context for a protocol question.",
            tags=["research", "facts"],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        )
    ],
    security_schemes=bearer_security_schemes(),
    security_requirements=bearer_security_requirements(),
)
app = create_server(
    agent_card,
    FactsExecutor(),
    rpc_path="/a2a",
    middleware=[bearer_middleware(FACTS_AGENT_TOKEN)],
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
