"""Remote A2A agent that contributes a critical review."""

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
    REVIEW_AGENT_TOKEN,
    bearer_middleware,
    bearer_security_requirements,
    bearer_security_schemes,
)


class ReviewExecutor(AgentExecutor):
    """Use an LLM to identify critical considerations for the local agent."""

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
            "You are the review specialist in a multi-agent A2A workflow. "
            "Identify risks, trade-offs, assumptions, and questions worth "
            "checking. Be concise and do not fabricate evidence.",
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
    "Review agent",
    "Uses an LLM to critically review a subject and identify considerations.",
    "http://127.0.0.1:8002/a2a",
    [
        create_skill(
            "critical-review",
            "Critical review",
            "Uses an LLM to return concise critical considerations for a question.",
            tags=["review", "risk"],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        )
    ],
    security_schemes=bearer_security_schemes(),
    security_requirements=bearer_security_requirements(),
)
app = create_server(
    agent_card,
    ReviewExecutor(),
    rpc_path="/a2a",
    middleware=[bearer_middleware(REVIEW_AGENT_TOKEN)],
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
