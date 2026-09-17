"""Shared configuration and Protocol Documents for the Agora example."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent_protocols.agora import ProtocolDocument
from agent_protocols.agora.messages import AgoraResponse

ActionSink = Callable[[str], None]

SHARED_PROTOCOL_SOURCE = """name: shared-calculation-v1
description: One-time arithmetic question and answer
multiround: false
---
The request MUST be a JSON object containing exactly one string field named "question".
The response MUST be a JSON object containing exactly one string field named "answer".
"""


@dataclass(frozen=True)
class DemoConfig:
    host: str = "127.0.0.1"
    port: int = 8200

    @property
    def calculator_url(self) -> str:
        return f"http://{self.host}:{self.port}/agora"


def shared_protocol() -> ProtocolDocument:
    return ProtocolDocument.parse(SHARED_PROTOCOL_SOURCE)


def announce(on_action: ActionSink | None, message: str) -> None:
    if on_action is not None:
        on_action(message)


def answer_from_response(response: AgoraResponse) -> str:
    """Extract an example answer while preserving Agora and PD error details."""
    if response.status == "failure":
        raise RuntimeError(f"Agora request failed: {response.error}")
    if not isinstance(response.body, dict):
        raise TypeError("Calculator response body must be an object")
    protocol_error = response.body.get("error")
    if isinstance(protocol_error, str):
        # A string here is a remote application error, not a local type error.
        raise RuntimeError(  # noqa: TRY004
            f"Protocol Document request failed: {protocol_error}"
        )
    answer = response.body.get("answer")
    if not isinstance(answer, str):
        raise TypeError('Calculator response requires a string field named "answer"')
    return answer


def console_action(message: str) -> None:
    print(message, flush=True)
