"""ASGI server for the Agora Working Standard exchange."""

from __future__ import annotations

import inspect
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent_protocols.agora.messages import AgoraRequest, AgoraResponse, JSONBody
from agent_protocols.agora.protocol import ProtocolDocument, ProtocolRegistry

HandlerResult: TypeAlias = JSONBody | Awaitable[JSONBody]


class AgoraHandler(Protocol):
    """Application callback invoked after the Agora envelope is validated."""

    def __call__(
        self,
        body: JSONBody,
        protocol: ProtocolDocument | None,
        conversation_id: str | None,
    ) -> HandlerResult: ...


@dataclass(slots=True)
class _Conversation:
    protocol: ProtocolDocument | None
    expires: int


async def _invoke(
    handler: AgoraHandler,
    request: AgoraRequest,
    protocol: ProtocolDocument | None,
    conversation_id: str | None,
) -> JSONBody:
    result = handler(request.body, protocol, conversation_id)
    if inspect.isawaitable(result):
        return await result
    return result


def create_server(
    handler: AgoraHandler,
    *,
    protocols: ProtocolRegistry | None = None,
    base_path: str = "/agora",
    conversation_ttl_seconds: int = 300,
    clock: Callable[[], float] = time.time,
) -> Starlette:
    """Build an Agora server with process-local protocol and conversation state."""
    if conversation_ttl_seconds <= 0:
        raise ValueError("conversation_ttl_seconds must be positive")
    path = "/" + base_path.strip("/")
    registry = protocols or ProtocolRegistry()
    conversations: dict[str, _Conversation] = {}

    def agora_failure(error: str) -> JSONResponse:
        return JSONResponse(AgoraResponse(status="failure", error=error).to_json())

    async def read_request(request: Request) -> tuple[AgoraRequest | None, Response | None]:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/json"
        ):
            return None, JSONResponse({"error": "Content-Type must be application/json"}, 400)
        try:
            payload = await request.json()
            parsed = AgoraRequest.from_json(payload)
        except (TypeError, ValueError, UnicodeDecodeError):
            return None, JSONResponse({"error": "Malformed Agora request"}, 400)
        return parsed, None

    def resolve_protocol(request: AgoraRequest) -> tuple[ProtocolDocument | None, Response | None]:
        if request.protocol_hash is None:
            return None, None
        protocol = registry.get(request.protocol_hash)
        if protocol is None:
            return None, agora_failure("Unsupported protocol")
        return protocol, None

    async def exchange(request: Request) -> Response:
        parsed, error = await read_request(request)
        if error is not None:
            return error
        assert parsed is not None
        protocol, error = resolve_protocol(parsed)
        if error is not None:
            return error
        if parsed.multiround and protocol is not None and not protocol.multiround:
            return agora_failure("Protocol does not support multi-round conversations")

        conversation_id: str | None = None
        expires: int | None = None
        if parsed.multiround:
            conversation_id = secrets.token_urlsafe(24)
            expires = int(clock()) + conversation_ttl_seconds

        body = await _invoke(handler, parsed, protocol, conversation_id)
        if parsed.multiround:
            assert conversation_id is not None and expires is not None
            conversations[conversation_id] = _Conversation(protocol, expires)
        return JSONResponse(
            AgoraResponse(
                status="success",
                body=body,
                conversation_id=conversation_id,
                conversation_expires=expires,
            ).to_json()
        )

    async def continue_conversation(request: Request) -> Response:
        conversation_id = request.path_params["conversation_id"]
        conversation = conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "Unknown conversation"}, 404)
        if int(clock()) >= conversation.expires:
            del conversations[conversation_id]
            return agora_failure("Conversation expired")

        parsed, error = await read_request(request)
        if error is not None:
            return error
        assert parsed is not None
        payload = await request.json()
        if "protocolHash" in payload or "protocolSources" in payload or parsed.multiround:
            return JSONResponse({"error": "Conversation protocol cannot be changed"}, 400)

        body = await _invoke(handler, parsed, conversation.protocol, conversation_id)
        return JSONResponse(
            AgoraResponse(
                status="success",
                body=body,
                conversation_id=conversation_id,
                conversation_expires=conversation.expires,
            ).to_json()
        )

    async def wellknown(request: Request) -> Response:
        del request
        return JSONResponse(registry.wellknown())

    app = Starlette(
        routes=[
            Route(path, exchange, methods=["POST"]),
            Route(f"{path}/wellknown", wellknown, methods=["GET"]),
            Route(
                f"{path}/conversations/{{conversation_id}}",
                continue_conversation,
                methods=["POST"],
            ),
        ]
    )
    app.state.agora_protocols = registry
    app.state.agora_conversations = conversations
    return app
