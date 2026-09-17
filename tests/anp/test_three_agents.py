"""A2A-comparable ANP workflow with real discovery and authenticated HTTPS calls."""

from __future__ import annotations

import pytest

from agent_protocols.anp import ANPCallError
from tests.anp.local_fixture import local_three_agents


async def test_three_agents_discover_delegate_and_authorize_distinct_dids() -> None:
    question = "What should we know before adopting ANP?"
    generated: list[tuple[str, str]] = []

    async def facts(instruction: str, prompt: str) -> str:
        generated.append(("facts", prompt))
        assert "facts specialist" in instruction
        return "Facts specialist: ANP publishes DIDs and interfaces."

    async def review(instruction: str, prompt: str) -> str:
        generated.append(("review", prompt))
        assert "review specialist" in instruction
        return "Review specialist: verify the service link and authorization."

    async def coordinator(instruction: str, prompt: str) -> str:
        generated.append(("coordinator", prompt))
        assert "local coordinator" in instruction
        assert question in prompt
        assert "Facts specialist: ANP publishes DIDs and interfaces." in prompt
        assert "Review specialist: verify the service link and authorization." in prompt
        return "ANP advertises interfaces; check identity and authorization."

    async with local_three_agents(
        facts_generator=facts,
        review_generator=review,
        coordinator_generator=coordinator,
    ) as fixture:
        discovered = await fixture.client.discover(fixture.domains["coordinator"])
        assert len(discovered) == 1
        assert discovered[0].did == fixture.identities["coordinator"]
        answer = await fixture.client.answer(
            discovered[0], question, force_new_signature=True
        )
        assert answer == "ANP advertises interfaces; check identity and authorization."
        assert sorted(fixture.events) == sorted(
            [
                ("coordinator", fixture.identities["caller"]),
                ("facts", fixture.identities["coordinator"]),
                ("review", fixture.identities["coordinator"]),
            ]
        )
        assert sorted(role for role, _ in generated) == [
            "coordinator",
            "facts",
            "review",
        ]
        assert {
            role: prompt for role, prompt in generated if role != "coordinator"
        } == {
            "facts": question,
            "review": question,
        }

        facts_agents = await fixture.client.discover(fixture.domains["facts"])
        with pytest.raises(ANPCallError, match="separate ANP client"):
            await fixture.client.answer(facts_agents[0], question)


async def test_three_agent_specialist_rejects_unsigned_call() -> None:
    async def fixed(_: str, prompt: str) -> str:
        return prompt

    async with local_three_agents(
        facts_generator=fixed,
        review_generator=fixed,
        coordinator_generator=fixed,
    ) as fixture:
        facts_rpc = f"https://{fixture.domains['facts']}/agents/facts/rpc"
        async with fixture.session.post(
            facts_rpc,
            json={
                "jsonrpc": "2.0",
                "method": "answer",
                "params": {"question": "hello"},
                "id": 1,
            },
        ) as response:
            assert response.status == 401
