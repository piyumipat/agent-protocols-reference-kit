"""Calling agent that negotiates a PD and asks two calculations in one conversation."""

from __future__ import annotations

import asyncio
import json

import httpx

from agent_protocols.agora import (
    AgoraClient,
    ProtocolDocument,
    ProtocolRegistry,
    propose_protocol,
)
from agent_protocols.utils import LLMClient, TextGenerator
from examples.agora.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    answer_from_response,
    console_action,
)

FIRST_GOAL = "Find the result of adding 2 and 2."
SECOND_GOAL = "Find the result of multiplying 7 by 6."
REQUESTER_INSTRUCTION = (
    "You are a requester agent talking to a calculator through an Agora Protocol Document. "
    "Read the calculator capability and protocol, then express the supplied goal as one concise "
    "arithmetic question. Return only the question text, such as What is 2 + 2? Do not return "
    "JSON or a field name."
)
PROTOCOL_AUTHOR_INSTRUCTION = (
    "Create a complete Agora Protocol Document from the receiving agent's capability and the "
    "requester's needs. Derive the message format, field names, field types, and semantics from "
    "that capability instead of assuming them. Return plain text only, without Markdown fences. "
    "Start with YAML metadata containing name, description, and multiround: true, then a line "
    "containing ---. Write precise declarative interaction rules and include one concrete request "
    "and response example. Do not add unsupported capabilities or require code execution."
)


async def requester_text(
    instruction: str,
    prompt: str,
    generator: TextGenerator | None,
) -> str:
    if generator is None:
        async with LLMClient() as llm:
            return await llm.generate_text(instruction, prompt, temperature=0.0)
    return await generator(instruction, prompt)


async def create_protocol_document(
    capability: str,
    generator: TextGenerator | None,
    *,
    rejected_document: ProtocolDocument | None = None,
    rejection_reason: str | None = None,
) -> ProtocolDocument:
    prompt = (
        f"Calculator capability:\n{capability}\n\n"
        "Requester need:\nAsk multiple arithmetic questions in one conversation."
    )
    if rejection_reason is not None:
        if rejected_document is None:
            raise ValueError("A rejection reason requires the rejected Protocol Document")
        prompt += (
            f"\n\nRejected Protocol Document:\n{rejected_document.source}"
            f"\nRejection reason:\n{rejection_reason}\nRevise the PD."
        )
    source = (await requester_text(PROTOCOL_AUTHOR_INSTRUCTION, prompt, generator)).strip()
    if source.startswith("```"):
        lines = source.splitlines()
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        source = "\n".join(lines).strip()
    document = ProtocolDocument.parse(source + "\n")
    if not document.multiround:
        raise ValueError("Requester LLM must create a multi-round Protocol Document")
    return document


async def formulate_question(
    goal: str,
    context: str,
    generator: TextGenerator | None,
) -> str:
    prompt = f"Calculator context:\n{context}\n\nGoal:\n{goal}"
    question = await requester_text(REQUESTER_INSTRUCTION, prompt, generator)
    question = question.strip()
    try:
        structured = json.loads(question)
    except json.JSONDecodeError:
        structured = None
    if (
        isinstance(structured, dict)
        and set(structured) == {"question"}
        and isinstance(structured["question"], str)
    ):
        question = structured["question"].strip()
    if not question:
        raise RuntimeError("Requester LLM returned an empty question")
    return question


async def ask_calculations(
    *,
    config: DemoConfig | None = None,
    on_action: ActionSink | None = None,
    http_client: httpx.AsyncClient | None = None,
    proposed_protocol: ProtocolDocument | None = None,
    generator: TextGenerator | None = None,
) -> tuple[str, str]:
    config = config or DemoConfig()
    document = proposed_protocol
    known_protocols = (
        ProtocolRegistry((document,)) if document is not None else ProtocolRegistry()
    )

    async with AgoraClient(
        config.calculator_url,
        require_https=False,
        timeout_seconds=300,
        http_client=http_client,
    ) as client:
        announce(on_action, "[requester] Discovering calculator protocols")
        shared = await client.find_shared_protocol(known_protocols)
        capability_context: str
        if shared is None:
            announce(
                on_action,
                "[requester] No shared PD; asking about capability in natural language",
            )
            capability = await client.exchange(
                "Can you answer arithmetic questions through a multi-round structured protocol?"
            )
            if not isinstance(capability.body, str):
                raise RuntimeError("Calculator returned no capability description")
            capability_context = capability.body
            announce(on_action, f"[requester] Calculator says: {capability.body}")
            attempts = 2 if proposed_protocol is None else 1
            rejection_reason: str | None = None
            for attempt in range(attempts):
                if document is None or rejection_reason is not None:
                    action = "create" if attempt == 0 else "revise"
                    announce(on_action, f"[requester] Asking LLM to {action} a PD")
                    document = await create_protocol_document(
                        capability.body,
                        generator,
                        rejected_document=document,
                        rejection_reason=rejection_reason,
                    )
                    announce(
                        on_action,
                        f"[requester] LLM-created Protocol Document:\n{document.source}",
                    )
                announce(on_action, f"[requester] Proposing PD {document.hash}")
                decision = await propose_protocol(
                    client,
                    document,
                    purpose="Ask multiple arithmetic questions in one conversation",
                )
                if decision.accepted:
                    break
                rejection_reason = decision.reason or "Proposal rejected"
                announce(
                    on_action,
                    f"[requester] Calculator rejected PD: {rejection_reason}",
                )
            else:
                raise RuntimeError(
                    f"Calculator rejected the Protocol Document: {rejection_reason}"
                )
            announce(on_action, "[requester] Calculator accepted the PD")
            known_protocols.register(document)
            shared = document
        else:
            announce(on_action, f"[requester] Reusing shared PD {shared.hash}")
            capability_context = shared.description

        announce(on_action, "[requester] Asking LLM to form the first question")
        first_question = await formulate_question(
            FIRST_GOAL,
            f"{capability_context}\n\nAccepted Protocol Document:\n{shared.source}",
            generator,
        )
        announce(on_action, f"[requester] Opening conversation with: {first_question}")
        first = await client.exchange(
            {"question": first_question}, protocol=shared, multiround=True
        )
        first_answer = answer_from_response(first)
        if first.conversation_id is None:
            raise RuntimeError("Calculator did not create a multi-round conversation")
        announce(
            on_action,
            f"[requester] Received {first_answer}; conversation {first.conversation_id}",
        )

        announce(on_action, "[requester] Asking LLM to form the follow-up question")
        second_question = await formulate_question(
            SECOND_GOAL,
            (
                f"Accepted Protocol Document:\n{shared.source}\n\n"
                f"Previous question: {first_question}\nPrevious answer: {first_answer}"
            ),
            generator,
        )
        announce(on_action, f"[requester] Following up with: {second_question}")
        second = await client.continue_conversation(
            first.conversation_id, {"question": second_question}
        )
        second_answer = answer_from_response(second)
        announce(on_action, f"[requester] Received {second_answer} in the same conversation")
        return first_answer, second_answer

async def main() -> None:
    first, second = await ask_calculations(on_action=console_action)
    print(f"First calculation answer: {first}")
    print(f"Second calculation answer: {second}")


if __name__ == "__main__":
    asyncio.run(main())
