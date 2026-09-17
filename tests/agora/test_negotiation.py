from __future__ import annotations

import httpx

from agent_protocols.agora import (
    AgoraClient,
    ProtocolDocument,
    ProtocolProposalDecision,
    ProtocolRegistry,
    create_server,
    handle_protocol_proposal,
    propose_protocol,
)
from agent_protocols.agora.messages import JSONBody

SOURCE = """name: CreatedAnswer
description: Negotiated structured question answering
multiround: false
---
The request is {"question": string}; the response is {"answer": string}.
"""


async def test_proposal_is_accepted_discovered_and_reused() -> None:
    remote_protocols = ProtocolRegistry()

    async def handler(
        body: JSONBody,
        protocol: ProtocolDocument | None,
        conversation_id: str | None,
    ) -> JSONBody:
        del conversation_id
        proposal = await handle_protocol_proposal(
            body, remote_protocols, lambda document, purpose: bool(purpose)
        )
        if proposal is not None:
            return proposal
        return {"used": protocol.hash if protocol else None}

    app = create_server(handler, protocols=remote_protocols)
    document = ProtocolDocument.parse(SOURCE)
    local_protocols = ProtocolRegistry()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http, AgoraClient(
        "http://test/agora", require_https=False, http_client=http
    ) as client:
        assert await client.find_shared_protocol(local_protocols) is None
        assert await propose_protocol(client, document, purpose="Answer questions")
        local_protocols.register(document)
        assert await client.find_shared_protocol(local_protocols) == document

        response = await client.exchange({"question": "Why?"}, protocol=document)

    assert response.body == {"used": document.hash}


async def test_rejected_proposal_is_not_registered() -> None:
    remote_protocols = ProtocolRegistry()

    async def handler(
        body: JSONBody,
        protocol: ProtocolDocument | None,
        conversation_id: str | None,
    ) -> JSONBody:
        del protocol, conversation_id
        proposal = await handle_protocol_proposal(
            body,
            remote_protocols,
            lambda document, purpose: ProtocolProposalDecision(
                False, "The response schema is unsupported"
            ),
        )
        return proposal or {"error": "Unexpected request"}

    document = ProtocolDocument.parse(SOURCE)
    app = create_server(handler, protocols=remote_protocols)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http, AgoraClient(
        "http://test/agora", require_https=False, http_client=http
    ) as client:
        result = await propose_protocol(client, document, purpose="Answer questions")

    assert not result.accepted
    assert result.reason == "The response schema is unsupported"
    assert not remote_protocols.supports(document.hash)
