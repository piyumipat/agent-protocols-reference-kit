from __future__ import annotations

import httpx
import pytest

from agent_protocols.agora import (
    AgoraClient,
    ProtocolDocument,
    ProtocolRegistry,
    create_server,
)
from agent_protocols.agora.messages import JSONBody

SOURCE = """name: Conversation
description: Structured multi-round questions
multiround: true
---
Requests and responses are JSON objects.
"""


async def echo(
    body: JSONBody,
    protocol: ProtocolDocument | None,
    conversation_id: str | None,
) -> JSONBody:
    return {
        "received": body,
        "protocol": protocol.name if protocol else None,
        "conversation": conversation_id,
    }


@pytest.fixture
def document() -> ProtocolDocument:
    return ProtocolDocument.parse(SOURCE)


@pytest.fixture
def transport(document: ProtocolDocument) -> httpx.ASGITransport:
    return httpx.ASGITransport(
        app=create_server(echo, protocols=ProtocolRegistry((document,)))
    )


async def test_discovers_and_uses_a_shared_protocol(
    document: ProtocolDocument, transport: httpx.ASGITransport
) -> None:
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http, AgoraClient(
        "http://test/agora", require_https=False, http_client=http
    ) as client:
        shared = await client.find_shared_protocol(ProtocolRegistry((document,)))
        assert shared == document

        response = await client.exchange({"question": "Why?"}, protocol=shared)

    assert response.status == "success"
    assert response.body == {
        "received": {"question": "Why?"},
        "protocol": "Conversation",
        "conversation": None,
    }


async def test_rejects_an_unknown_protocol_at_the_agora_layer(
    transport: httpx.ASGITransport,
) -> None:
    unknown = ProtocolDocument.parse(SOURCE.replace("Conversation", "Unknown"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/agora", json={"body": {}, "protocolHash": unknown.hash}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "failure", "error": "Unsupported protocol"}


async def test_continues_and_expires_a_multiround_conversation(
    document: ProtocolDocument,
) -> None:
    now = 1000.0

    def clock() -> float:
        return now

    app = create_server(
        echo,
        protocols=ProtocolRegistry((document,)),
        conversation_ttl_seconds=10,
        clock=clock,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http, AgoraClient(
        "http://test/agora", require_https=False, http_client=http
    ) as client:
        started = await client.exchange({}, protocol=document, multiround=True)
        assert started.conversation_id is not None
        continued = await client.continue_conversation(
            started.conversation_id, {"next": True}
        )
        assert continued.status == "success"
        assert continued.conversation_id == started.conversation_id

        now = 1010.0
        expired = await client.continue_conversation(started.conversation_id, {})

    assert expired.status == "failure"
    assert expired.error == "Conversation expired"


async def test_malformed_request_is_an_http_error(transport: httpx.ASGITransport) -> None:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/agora", content="not-json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
