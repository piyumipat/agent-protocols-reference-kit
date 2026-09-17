"""End-to-end coverage for the authenticated three-agent example."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from a2a.client.errors import A2AClientError
from starlette.applications import Starlette

from agent_protocols.a2a import (
    A2AClient,
    Role,
    create_server,
    get_message_text,
    new_text_message,
)
from examples.a2a.facts_agent import FactsExecutor
from examples.a2a.facts_agent import agent_card as facts_card
from examples.a2a.local_agent import CoordinatorExecutor
from examples.a2a.local_agent import agent_card as local_card
from examples.a2a.registry import AgentRegistry
from examples.a2a.review_agent import ReviewExecutor
from examples.a2a.review_agent import agent_card as review_card
from examples.a2a.security import bearer_middleware

LOCAL_TOKEN = "local-test-token"
FACTS_TOKEN = "facts-test-token"
REVIEW_TOKEN = "review-test-token"


async def deterministic_llm(system_instruction: str, prompt: str) -> str:
    """Offline stand-in that verifies which agent invokes which LLM prompt."""
    if "facts specialist" in system_instruction:
        return f"Facts model response for: {prompt}"
    if "review specialist" in system_instruction:
        return f"Review model response for: {prompt}"
    assert "Facts model response" in prompt
    assert "Review model response" in prompt
    return "Coordinator model synthesis"


class InProcessClientFactory:
    def __init__(self, apps: Mapping[str, Starlette]) -> None:
        self._apps = dict(apps)

    def __call__(
        self,
        target: str,
        *,
        headers: Mapping[str, str],
        request_timeout_seconds: float | None,
    ) -> A2AClient:
        del request_timeout_seconds
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._apps[target]),
            base_url=target,
            headers=headers,
        )
        return A2AClient(target, http_client=http)


def protected_remote_apps() -> dict[str, Starlette]:
    return {
        "http://facts.test": create_server(
            facts_card,
            FactsExecutor(deterministic_llm),
            rpc_path="/a2a",
            middleware=[bearer_middleware(FACTS_TOKEN)],
        ),
        "http://review.test": create_server(
            review_card,
            ReviewExecutor(deterministic_llm),
            rpc_path="/a2a",
            middleware=[bearer_middleware(REVIEW_TOKEN)],
        ),
    }


async def test_user_local_and_two_remote_agents_communicate_over_a2a() -> None:
    remote_apps = protected_remote_apps()
    registry = AgentRegistry(
        {"facts": "http://facts.test", "review": "http://review.test"}
    )
    coordinator = CoordinatorExecutor(
        registry,
        {"facts": FACTS_TOKEN, "review": REVIEW_TOKEN},
        client_factory=InProcessClientFactory(remote_apps),
        generator=deterministic_llm,
    )
    local_app = create_server(
        local_card,
        coordinator,
        rpc_path="/a2a",
        middleware=[bearer_middleware(LOCAL_TOKEN)],
    )

    # All three agents expose public cards that declare bearer authentication.
    for app, base_url in [
        (local_app, "http://local.test"),
        (remote_apps["http://facts.test"], "http://facts.test"),
        (remote_apps["http://review.test"], "http://review.test"),
    ]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base_url
        ) as http:
            card = (await http.get("/.well-known/agent-card.json")).json()
            assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]

    unauthenticated_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=local_app), base_url="http://local.test"
    )
    async with A2AClient(
        "http://local.test", http_client=unauthenticated_http
    ) as client:
        with pytest.raises(A2AClientError):
            _ = [
                event
                async for event in client.send_message(
                    new_text_message("Explain A2A", role=Role.ROLE_USER)
                )
            ]

    authenticated_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=local_app),
        base_url="http://local.test",
        headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
    )
    async with A2AClient(
        "http://local.test", http_client=authenticated_http
    ) as client:
        responses = [
            event
            async for event in client.send_message(
                new_text_message("Explain A2A", role=Role.ROLE_USER)
            )
        ]

    result = get_message_text(responses[0].message)
    assert result == "Coordinator model synthesis"
