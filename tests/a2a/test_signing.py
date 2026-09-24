"""Signed Agent Cards over the actual well-known discovery route."""

from __future__ import annotations

import asyncio
import base64
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import jcs  # type: ignore[import-untyped]
import jwt
import pytest
import uvicorn
from a2a.types.a2a_pb2 import HTTPAuthSecurityScheme, SecurityScheme
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_protocols.a2a import (
    A2AClient,
    AgentCard,
    AgentCardVerificationError,
    AgentExecutor,
    EventQueue,
    RequestContext,
    ResolvedCardKey,
    create_agent_card,
    create_server,
    create_skill,
    sign_agent_card,
    verify_agent_card,
)
from agent_protocols.a2a.signing import load_agent_card_json, signed_card_json


class _NoopExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, queue: EventQueue) -> None:
        del context, queue

    async def cancel(self, context: RequestContext, queue: EventQueue) -> None:
        del context, queue


def _keys() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _card(url: str = "http://test/rpc") -> AgentCard:
    card = create_agent_card(
        "Example agent",
        "A signed example",
        url,
        [create_skill("echo", "Echo", "Echo input", tags=["echo"])],
    )
    card.icon_url = ""  # Explicit optional default must remain in the signed payload.
    return card


@asynccontextmanager
async def _live_card_endpoint() -> AsyncIterator[tuple[str, dict[str, Any]]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    wire: dict[str, Any] = {}

    async def get_card(request: Request) -> JSONResponse:
        del request
        return JSONResponse(wire)

    app = Starlette(routes=[Route("/.well-known/agent-card.json", get_card)])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", log_level="critical", lifespan="off")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("A2A discovery test server did not start")
        yield base_url, wire
    finally:
        server.should_exit = True
        await server_task
        listener.close()


async def test_signed_card_is_verified_before_client_uses_discovered_interface() -> None:
    private, public = _keys()
    signed = sign_agent_card(_card(), private, kid="key-1", jku="https://example.com/jwks")
    app = create_server(signed, _NoopExecutor(), rpc_path="/rpc")
    observed: list[tuple[str, str | None]] = []

    def key_source(kid: str, jku: str | None) -> ResolvedCardKey:
        observed.append((kid, jku))
        return ResolvedCardKey(public, kid, "test-key-store")

    verifier = partial(verify_agent_card, key_source=key_source)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/.well-known/agent-card.json")
        wire = response.json()
        assert wire["iconUrl"] == ""
        assert "signatures" in wire
        assert verifier(wire).key_id == "key-1"
        async with A2AClient("http://test", http_client=http, card_verifier=verifier) as client:
            assert client.verified_card is not None
            assert client.verified_card.key_source == "test-key-store"
    assert observed == [
        ("key-1", "https://example.com/jwks"),
        ("key-1", "https://example.com/jwks"),
    ]


def test_rejects_tampering_missing_signature_and_unusable_keys() -> None:
    private, public = _keys()
    signed = sign_agent_card(_card(), private, kid="key-1")
    key_source = lambda kid, jku: ResolvedCardKey(public, kid, "local")
    assert verify_agent_card(signed, key_source=key_source).key_id == "key-1"
    tampered = deepcopy(signed)
    tampered.name = "Different agent"
    with pytest.raises(AgentCardVerificationError):
        verify_agent_card(tampered, key_source=key_source)
    with pytest.raises(AgentCardVerificationError):
        verify_agent_card(_card(), key_source=key_source)
    for unusable in (
        ResolvedCardKey(public, "key-1", "local", revoked=True),
        ResolvedCardKey(
            public, "key-1", "local", expires_at=datetime.now(UTC) - timedelta(seconds=1)
        ),
    ):
        def unusable_key_source(
            kid: str, jku: str | None, key: ResolvedCardKey = unusable
        ) -> ResolvedCardKey:
            del kid, jku
            return key

        with pytest.raises(AgentCardVerificationError):
            verify_agent_card(signed, key_source=unusable_key_source)


def test_rotation_uses_another_valid_signature_and_rejects_duplicate_json_keys() -> None:
    private, public = _keys()
    signed = sign_agent_card(_card(), private, kid="retired")
    rotated = sign_agent_card(signed, private, kid="current")
    result = verify_agent_card(
        rotated,
        key_source=lambda kid, jku: ResolvedCardKey(
            public, kid, "local", revoked=kid == "retired"
        ),
    )
    assert result.key_id == "current"
    with pytest.raises(ValueError, match="duplicate JSON property"):
        load_agent_card_json(b'{"name":"first","name":"second"}')


def test_signature_matches_jcs_jws_and_nested_default_rules() -> None:
    private, public = _keys()
    card = _card()
    card.description = ""
    card.security_schemes["bearer"].CopyFrom(
        SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer"))
    )
    signed = sign_agent_card(card, private, kid="key-1")
    wire = signed_card_json(signed)
    assert wire["description"] == ""
    assert wire["iconUrl"] == ""
    assert wire["capabilities"]["streaming"] is False
    signature = wire["signatures"][0]
    payload = jcs.canonicalize({key: value for key, value in wire.items() if key != "signatures"})
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    compact = f"{signature['protected']}.{encoded_payload}.{signature['signature']}"
    assert jwt.api_jws.PyJWS().decode_complete(
        compact, public, algorithms=["ES256"]
    )["payload"] == payload

    # A non-optional nested field set to its protobuf default is omitted from
    # the canonical content even if received explicitly in JSON.
    wire["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["description"] = ""
    assert verify_agent_card(
        wire, key_source=lambda kid, jku: ResolvedCardKey(public, kid, "local")
    ).key_id == "key-1"


async def test_live_http_discovery_accepts_valid_and_rejects_bad_cards() -> None:
    private, public = _keys()
    _, other_public = _keys()
    async with _live_card_endpoint() as (base_url, wire):
        signed = sign_agent_card(_card(f"{base_url}/rpc"), private, kid="key-1")
        valid = signed_card_json(signed)
        wire.update(valid)
        key_mode = "valid"

        def key_source(kid: str, jku: str | None) -> ResolvedCardKey | None:
            del jku
            if key_mode == "missing":
                return None
            return ResolvedCardKey(
                other_public if key_mode == "wrong" else public,
                kid,
                "test-key-store",
                revoked=key_mode == "revoked",
                expires_at=(
                    datetime.now(UTC) - timedelta(seconds=1)
                    if key_mode == "expired"
                    else None
                ),
            )

        verifier = partial(verify_agent_card, key_source=key_source)
        async with A2AClient(base_url, card_verifier=verifier) as client:
            assert client.verified_card is not None
            assert client.verified_card.key_id == "key-1"

        for variant in ("unsigned", "tampered", "missing", "wrong", "expired", "revoked"):
            key_mode = variant
            wire.clear()
            wire.update(deepcopy(valid))
            if variant == "unsigned":
                wire.pop("signatures")
            elif variant == "tampered":
                wire["name"] = "Modified agent"
            with pytest.raises(AgentCardVerificationError):
                async with A2AClient(base_url, card_verifier=verifier):
                    pytest.fail(f"{variant} card was accepted")
