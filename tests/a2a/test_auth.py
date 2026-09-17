"""Authentication enforcement example coverage."""

from __future__ import annotations

import httpx
import pytest
from a2a.client.errors import A2AClientError
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from agent_protocols.a2a import (
    A2AClient,
    Role,
    create_agent_card,
    create_server,
    create_skill,
    new_text_message,
)

from .test_server_client import DirectReplyExecutor

TOKEN = "test-token"


class BearerBackend(AuthenticationBackend):
    async def authenticate(self, conn):  # type: ignore[no-untyped-def]
        if conn.url.path == "/.well-known/agent-card.json":
            return None
        if conn.headers.get("authorization") != f"Bearer {TOKEN}":
            raise AuthenticationError("invalid bearer token")
        return AuthCredentials(["authenticated"]), SimpleUser("test-client")


def protected_app() -> Starlette:
    card = create_agent_card(
        "Protected agent",
        "Requires a bearer token for RPC calls.",
        "http://test/rpc",
        [create_skill("echo", "Echo", "Echo input")],
    )
    return create_server(
        card,
        DirectReplyExecutor(),
        rpc_path="/rpc",
        middleware=[Middleware(AuthenticationMiddleware, backend=BearerBackend())],
    )


async def test_rejects_unauthenticated_and_accepts_authenticated_request() -> None:
    transport = httpx.ASGITransport(app=protected_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        # Discovery is public.
        assert (await http.get("/.well-known/agent-card.json")).status_code == 200

        async with A2AClient("http://test", http_client=http) as client:
            with pytest.raises(A2AClientError):
                _ = [
                    response
                    async for response in client.send_message(
                        new_text_message("denied", role=Role.ROLE_USER)
                    )
                ]

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http, A2AClient("http://test", http_client=http) as client:
        responses = [
            response
            async for response in client.send_message(
                new_text_message("allowed", role=Role.ROLE_USER)
            )
        ]

    assert responses[0].message.parts[0].text == "Echo: allowed"
