from __future__ import annotations

import httpx

from agent_protocols.agora import AgoraClient, ProtocolDocument, propose_protocol
from examples.agora.calculator_agent import (
    CALCULATOR_INSTRUCTION,
    CAPABILITY_INSTRUCTION,
    PROTOCOL_REVIEW_INSTRUCTION,
    calculator_app,
)
from examples.agora.demo_common import DemoConfig
from examples.agora.requester_agent import (
    PROTOCOL_AUTHOR_INSTRUCTION,
    REQUESTER_INSTRUCTION,
    ask_calculations,
    formulate_question,
)
from examples.agora.shared_requester_agent import ask_once


async def generator(instruction: str, prompt: str) -> str:
    if instruction == CAPABILITY_INSTRUCTION:
        assert "arithmetic questions" in prompt
        return (
            "I answer arithmetic questions and support single- or multi-round JSON "
            "protocols with question and answer string fields."
        )
    if instruction == PROTOCOL_AUTHOR_INSTRUCTION:
        assert "I answer arithmetic questions" in prompt
        return """name: llm-authored-calculation
description: LLM-authored multi-round arithmetic exchange
multiround: true
---
Send a JSON request with one string named "question" for an arithmetic calculation.
Example request: {"question": "What is 2 + 2?"}
Return a JSON response with one string named "answer".
Example response: {"answer": "4"}
"""
    if instruction == REQUESTER_INSTRUCTION:
        return "What is 2 + 2?" if "adding 2 and 2" in prompt else "What is 7 * 6?"
    if instruction == PROTOCOL_REVIEW_INSTRUCTION:
        return "ACCEPT"
    if instruction == CALCULATOR_INSTRUCTION:
        if "10 - 3" in prompt:
            return "7"
        return "4" if "2 + 2" in prompt else "42"
    raise AssertionError("Unexpected instruction")


async def test_requester_normalizes_a_json_wrapped_question() -> None:
    async def wrapped_question_generator(instruction: str, prompt: str) -> str:
        assert instruction == REQUESTER_INSTRUCTION
        assert "adding 2 and 2" in prompt
        return '{"question": "What is 2 + 2?"}'

    question = await formulate_question(
        "Find the result of adding 2 and 2.",
        "A calculator protocol",
        wrapped_question_generator,
    )

    assert question == "What is 2 + 2?"


async def test_shared_single_round_then_negotiated_multiround_conversation() -> None:
    actions: list[str] = []
    calculator = calculator_app(on_action=actions.append, generator=generator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=calculator),
        base_url="http://calculator.test",
    ) as http:
        one_time = await ask_once(
            config=DemoConfig(host="calculator.test", port=80),
            on_action=actions.append,
            http_client=http,
        )
        answers = await ask_calculations(
            config=DemoConfig(host="calculator.test", port=80),
            on_action=actions.append,
            http_client=http,
            generator=generator,
        )

    assert one_time == "7"
    assert answers == ("4", "42")
    assert any("Found shared PD" in action for action in actions)
    assert any("Handling single-round exchange" in action for action in actions)
    assert any("No shared PD" in action for action in actions)
    assert any("Accepted and cached" in action for action in actions)
    assert any("Opening conversation" in action for action in actions)
    assert any("Following up" in action for action in actions)
    assert any("same conversation" in action for action in actions)


async def test_calculator_accepts_a_compatible_differently_worded_pd() -> None:
    proposed = ProtocolDocument.parse(
        """name: requester-arithmetic
description: A caller-defined calculation exchange
multiround: true
---
Use a JSON request with a string named "question" for an arithmetic problem.
Return a JSON response with the result in a string named "answer".
"""
    )
    app = calculator_app(generator=generator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://calculator.test"
    ) as http:
        answers = await ask_calculations(
            config=DemoConfig(host="calculator.test", port=80),
            http_client=http,
            proposed_protocol=proposed,
            generator=generator,
        )

    assert answers == ("4", "42")


async def test_calculator_can_negotiate_a_single_round_pd() -> None:
    proposed = ProtocolDocument.parse(
        """name: one-calculation
description: A single arithmetic exchange
multiround: false
---
Send a JSON object with a string "question" and receive a JSON object with a string "answer".
"""
    )
    app = calculator_app(generator=generator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://calculator.test"
    ) as http, AgoraClient(
        "http://calculator.test/agora", require_https=False, http_client=http
    ) as client:
        accepted = await propose_protocol(
            client, proposed, purpose="Ask one arithmetic question"
        )
        response = await client.exchange(
            {"question": "What is 10 - 3?"}, protocol=proposed
        )

    assert accepted
    assert response.body == {"answer": "7"}
    assert response.conversation_id is None


async def test_calculator_explains_an_incompatible_proposal() -> None:
    proposed = ProtocolDocument.parse(
        """name: incomplete-calculation
description: An arithmetic request
multiround: false
---
Send a JSON object with a string named "question".
"""
    )
    app = calculator_app(generator=generator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://calculator.test"
    ) as http, AgoraClient(
        "http://calculator.test/agora", require_https=False, http_client=http
    ) as client:
        result = await propose_protocol(
            client, proposed, purpose="Ask one arithmetic question"
        )

    assert not result.accepted
    assert result.reason == "Protocol must describe: answer"


async def test_requester_llm_revises_a_rejected_pd() -> None:
    author_calls = 0

    async def revising_generator(instruction: str, prompt: str) -> str:
        nonlocal author_calls
        if instruction != PROTOCOL_AUTHOR_INSTRUCTION:
            return await generator(instruction, prompt)
        author_calls += 1
        if author_calls == 1:
            return """name: incomplete
description: Multi-round arithmetic requests
multiround: true
---
Send a JSON object with a string named "question".
"""
        assert "name: incomplete" in prompt
        assert "Protocol must describe: answer" in prompt
        return await generator(instruction, prompt)

    app = calculator_app(generator=revising_generator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://calculator.test"
    ) as http:
        answers = await ask_calculations(
            config=DemoConfig(host="calculator.test", port=80),
            http_client=http,
            generator=revising_generator,
        )

    assert author_calls == 2
    assert answers == ("4", "42")
