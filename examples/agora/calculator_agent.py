"""LLM-backed receiving agent for the Agora negotiation example."""

from __future__ import annotations

import uvicorn
from starlette.applications import Starlette

from agent_protocols.agora import (
    ProtocolDocument,
    ProtocolProposalDecision,
    ProtocolRegistry,
    create_server,
    handle_protocol_proposal,
)
from agent_protocols.agora.messages import JSONBody
from agent_protocols.agora.server import AgoraHandler
from agent_protocols.utils import LLMClient, TextGenerator
from examples.agora.demo_common import (
    ActionSink,
    DemoConfig,
    announce,
    console_action,
    shared_protocol,
)

PROTOCOL_REVIEW_INSTRUCTION = (
    "You are an agent deciding whether to accept a proposed Agora Protocol Document. "
    "Accept a single- or multi-round declarative calculation protocol containing a string "
    "question request and a string answer response, with no executable code. A rule that prohibits "
    "code execution is safe and must not be interpreted as requiring it. Reply with exactly ACCEPT, "
    "or REJECT followed by a colon and a brief reason."
)
CAPABILITY_INSTRUCTION = (
    "You are a calculator agent describing your communication capabilities. Explain concisely "
    "that you answer arithmetic questions and accept single- or multi-round JSON protocols with "
    "one string question field and one string answer field. Answer only the capability inquiry; "
    "do not perform a calculation or invent other capabilities."
)
CALCULATOR_INSTRUCTION = (
    "You are a calculator agent. Answer the arithmetic question accurately and concisely. "
    "Return only the answer, without explanation."
)


async def generate(
    instruction: str,
    prompt: str,
    generator: TextGenerator | None,
) -> str:
    if generator is not None:
        return await generator(instruction, prompt)
    async with LLMClient() as llm:
        return await llm.generate_text(instruction, prompt, temperature=0.0)


def make_handler(
    protocols: ProtocolRegistry,
    on_action: ActionSink | None,
    *,
    generator: TextGenerator | None = None,
) -> AgoraHandler:
    def calculation_incompatibility(document: ProtocolDocument) -> str | None:
        specification = f"{document.description}\n{document.specification}".lower()
        required_terms = ("json", "question", "answer", "arithmetic")
        missing = [term for term in required_terms if term not in specification]
        if missing:
            return "Protocol must describe: " + ", ".join(missing)
        return None

    def supports_calculation_shape(document: ProtocolDocument) -> bool:
        return calculation_incompatibility(document) is None

    async def evaluate(
        document: ProtocolDocument, purpose: str
    ) -> ProtocolProposalDecision:
        announce(on_action, "[calculator] Received a Protocol Document proposal")
        incompatibility = calculation_incompatibility(document)
        if incompatibility is not None:
            announce(on_action, f"[calculator] Rejected PD: {incompatibility}")
            return ProtocolProposalDecision(False, incompatibility)
        announce(on_action, "[calculator] Asking LLM to review the proposal")
        decision = await generate(
            PROTOCOL_REVIEW_INSTRUCTION,
            f"Purpose: {purpose}\n\nProtocol Document:\n{document.source}",
            generator,
        )
        normalized = decision.strip()
        accepted = normalized.upper() == "ACCEPT"
        reason = None
        if not accepted:
            prefix, separator, detail = normalized.partition(":")
            reason = (
                detail.strip()
                if separator and prefix.strip().upper() == "REJECT" and detail.strip()
                else "Calculator LLM rejected the proposed protocol"
            )
        announce(
            on_action,
            f"[calculator] {'Accepted and cached' if accepted else 'Rejected'} PD {document.hash}",
        )
        return ProtocolProposalDecision(accepted, reason)

    async def handler(
        body: JSONBody,
        protocol: ProtocolDocument | None,
        conversation_id: str | None,
    ) -> JSONBody:
        proposal = await handle_protocol_proposal(body, protocols, evaluate)
        if proposal is not None:
            return proposal
        if protocol is None:
            if isinstance(body, str):
                announce(on_action, "[calculator] Asking LLM to answer capability inquiry")
                capability = await generate(CAPABILITY_INSTRUCTION, body, generator)
                capability = capability.strip()
                if not capability:
                    return {"error": "Calculator LLM returned no capability description"}
                announce(on_action, f"[calculator] Capability response: {capability}")
                return capability
            return {"error": "A supported Protocol Document is required"}
        if not isinstance(body, dict) or set(body) != {"question"} or not isinstance(
            body.get("question"), str
        ):
            return {"error": 'Request must contain exactly one string field, "question"'}
        if not supports_calculation_shape(protocol):
            return {"error": "Protocol Document is incompatible with this calculator"}
        mode = (
            f"conversation {conversation_id}"
            if conversation_id is not None
            else "single-round exchange"
        )
        announce(on_action, f"[calculator] Using registered PD {protocol.hash}")
        announce(on_action, f"[calculator] Handling {mode}")
        announce(on_action, f"[calculator] Asking LLM: {body['question']}")
        answer = await generate(CALCULATOR_INSTRUCTION, str(body["question"]), generator)
        return {"answer": answer.strip()}

    return handler


def calculator_app(
    *,
    on_action: ActionSink | None = None,
    generator: TextGenerator | None = None,
) -> Starlette:
    protocols = ProtocolRegistry((shared_protocol(),))
    return create_server(
        make_handler(protocols, on_action, generator=generator),
        protocols=protocols,
    )


def main() -> None:
    config = DemoConfig()
    console_action(f"[calculator] Serving Agora at {config.calculator_url}")
    uvicorn.run(
        calculator_app(on_action=console_action), host=config.host, port=config.port
    )


if __name__ == "__main__":
    main()
