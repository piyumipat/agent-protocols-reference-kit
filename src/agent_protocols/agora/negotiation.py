"""A small paper-inspired Protocol Document negotiation extension.

The Agora Working Standard deliberately leaves negotiation out of scope.  The
messages in this module are therefore a kit convention, not standard Agora fields.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from agent_protocols.agora.client import AgoraClient
from agent_protocols.agora.messages import JSONBody
from agent_protocols.agora.protocol import ProtocolDocument, ProtocolRegistry

PROPOSAL_TYPE = "agent-protocols-reference-kit/agora-proposal-v1"


@dataclass(frozen=True, slots=True)
class ProtocolProposalDecision:
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProtocolProposalResult:
    accepted: bool
    protocol_hash: str | None = None
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.accepted


EvaluationValue: TypeAlias = bool | ProtocolProposalDecision
Evaluation: TypeAlias = EvaluationValue | Awaitable[EvaluationValue]


class ProtocolProposalEvaluator(Protocol):
    """Application policy for accepting a proposed Protocol Document."""

    def __call__(self, document: ProtocolDocument, purpose: str) -> Evaluation: ...


async def handle_protocol_proposal(
    body: JSONBody,
    registry: ProtocolRegistry,
    evaluator: ProtocolProposalEvaluator,
) -> dict[str, object] | None:
    """Evaluate and register a kit proposal message, or return ``None`` otherwise."""
    if not isinstance(body, dict) or body.get("type") != PROPOSAL_TYPE:
        return None
    source = body.get("protocolDocument")
    purpose = body.get("purpose")
    if (
        not isinstance(source, str)
        or not isinstance(purpose, str)
        or not purpose.strip()
    ):
        return {"accepted": False, "reason": "Malformed protocol proposal"}
    try:
        document = ProtocolDocument.parse(source)
    except (TypeError, ValueError):
        return {"accepted": False, "reason": "Invalid Protocol Document"}

    decision = evaluator(document, purpose)
    outcome = await decision if inspect.isawaitable(decision) else decision
    if isinstance(outcome, ProtocolProposalDecision):
        accepted = outcome.accepted
        reason = outcome.reason
    else:
        accepted = outcome
        reason = None
    if not accepted:
        return {
            "accepted": False,
            "reason": reason or "Protocol proposal rejected",
        }
    registry.register(document)
    return {"accepted": True, "protocolHash": document.hash}


async def propose_protocol(
    client: AgoraClient,
    document: ProtocolDocument,
    *,
    purpose: str,
) -> ProtocolProposalResult:
    """Send the kit proposal extension through an unversioned Agora body."""
    response = await client.exchange(
        {
            "type": PROPOSAL_TYPE,
            "purpose": purpose,
            "protocolDocument": document.source,
        }
    )
    if response.status != "success" or not isinstance(response.body, dict):
        return ProtocolProposalResult(False, reason=response.error or "Invalid response")
    accepted = response.body.get("accepted") is True
    returned_hash = response.body.get("protocolHash")
    reason = response.body.get("reason")
    if accepted and returned_hash == document.hash:
        return ProtocolProposalResult(True, protocol_hash=document.hash)
    if accepted:
        return ProtocolProposalResult(
            False, reason="Receiver accepted the proposal but returned the wrong protocol hash"
        )
    return ProtocolProposalResult(
        False,
        reason=reason if isinstance(reason, str) else "Protocol proposal rejected",
    )
