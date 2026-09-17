"""Local A2A agent that coordinates two remote A2A agents."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

import uvicorn

from agent_protocols.a2a import (
    A2AClient,
    AgentExecutor,
    EventQueue,
    RequestContext,
    Role,
    create_agent_card,
    create_server,
    create_skill,
    get_message_text,
    new_text_message,
)
from agent_protocols.utils import LLMClient, TextGenerator
from examples.a2a.registry import AgentRegistry, registry
from examples.a2a.security import (
    FACTS_AGENT_TOKEN,
    LOCAL_AGENT_TOKEN,
    REVIEW_AGENT_TOKEN,
    bearer_middleware,
    bearer_security_requirements,
    bearer_security_schemes,
)


class ClientFactory(Protocol):
    def __call__(
        self,
        target: str,
        *,
        headers: Mapping[str, str],
        request_timeout_seconds: float | None,
    ) -> A2AClient: ...


class CoordinatorExecutor(AgentExecutor):
    """Delegate to both registered specialists and combine their responses."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        tokens: Mapping[str, str],
        client_factory: ClientFactory = A2AClient,
        generator: TextGenerator | None = None,
    ) -> None:
        self._registry = agent_registry
        self._tokens = dict(tokens)
        self._client_factory = client_factory
        self._generate = generator

    async def _generate_text(self, instruction: str, prompt: str) -> str:
        if self._generate is not None:
            return await self._generate(instruction, prompt)
        async with LLMClient() as llm:
            return await llm.generate_text(instruction, prompt)

    async def _ask(self, role: str, prompt: str) -> str:
        endpoint = self._registry.resolve(role)
        headers = {"Authorization": f"Bearer {self._tokens[role]}"}
        async with self._client_factory(
            endpoint,
            headers=headers,
            request_timeout_seconds=300.0,
        ) as client:
            async for response in client.send_message(
                new_text_message(prompt, role=Role.ROLE_USER)
            ):
                if response.HasField("message"):
                    return get_message_text(response.message)
        raise RuntimeError(f"Agent registered for {role!r} returned no Message")

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        user_request = context.get_user_input()
        facts, review = await asyncio.gather(
            self._ask("facts", user_request),
            self._ask("review", user_request),
        )
        result = await self._generate_text(
            "You are the local coordinator in a multi-agent A2A workflow. "
            "Synthesize the two specialist responses into a direct, useful answer. "
            "Distinguish evidence from uncertainty and do not claim the specialists "
            "said something they did not say.",
            f"User question:\n{user_request}\n\n"
            f"Facts specialist response:\n{facts}\n\n"
            f"Review specialist response:\n{review}",
        )
        await event_queue.enqueue_event(new_text_message(result))

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        del context, event_queue


agent_card = create_agent_card(
    "Local coordinator agent",
    "Uses an LLM to synthesize responses from the facts and review agents.",
    "http://127.0.0.1:8000/a2a",
    [
        create_skill(
            "coordinate-answer",
            "Coordinate an answer",
            "Consults two specialist A2A agents and uses an LLM to synthesize them.",
            tags=["coordination", "aggregation"],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        )
    ],
    security_schemes=bearer_security_schemes(),
    security_requirements=bearer_security_requirements(),
)
app = create_server(
    agent_card,
    CoordinatorExecutor(
        registry,
        {"facts": FACTS_AGENT_TOKEN, "review": REVIEW_AGENT_TOKEN},
    ),
    rpc_path="/a2a",
    middleware=[bearer_middleware(LOCAL_AGENT_TOKEN)],
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
