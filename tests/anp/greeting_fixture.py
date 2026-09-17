"""One-agent ANP HTTPS fixture for signed and bearer transport tests."""

from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from anp.authentication import DIDWbaAuthHeader

from agent_protocols.anp import (
    ANPClient,
    create_agent_description,
    create_anp_server,
    create_discovery_collection,
    create_e1_identity,
    create_http_authenticator,
    create_openrpc_interface,
    create_signature_verifier,
    did_document_url,
)
from examples.anp.local_https import HOSTNAME, LoopbackResolver, jwt_keys, tls_contexts


@dataclass
class LocalGreetingAgent:
    client: ANPClient
    session: aiohttp.ClientSession
    authenticator: DIDWbaAuthHeader
    unauthorized_authenticator: DIDWbaAuthHeader
    domain: str
    rpc_url: str


@asynccontextmanager
async def local_greeting_agent() -> AsyncIterator[LocalGreetingAgent]:
    """Run a greeting host with DNS-name and CA checks enabled."""
    with tempfile.TemporaryDirectory(prefix="anp-initial-path-") as temporary:
        directory = Path(temporary)
        server_tls, client_tls = tls_contexts(directory)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = int(sock.getsockname()[1])
        domain = f"{HOSTNAME}:{port}"
        origin = f"https://{domain}"
        description_url = f"{origin}/agents/greeting/ad.json"
        interface_url = f"{origin}/agents/greeting/interface.json"
        rpc_url = f"{origin}/agents/greeting/rpc"

        agent = create_e1_identity(
            HOSTNAME,
            port=port,
            path_segments=["agents", "greeting"],
            agent_description_url=description_url,
        )
        caller = create_e1_identity(
            HOSTNAME, port=port, path_segments=["agents", "caller"]
        )
        description = create_agent_description(
            url=description_url,
            name="Greeting Agent",
            did=agent.did,
            interfaces=[
                {
                    "type": "StructuredInterface",
                    "protocol": "openrpc",
                    "url": interface_url,
                }
            ],
        )
        collection = create_discovery_collection(domain, [description])
        openrpc = create_openrpc_interface(rpc_url=rpc_url)

        caller_document_path = directory / "caller-did.json"
        caller_key_path = directory / "caller-key.pem"
        caller_document_path.write_text(json.dumps(caller.document), encoding="utf-8")
        caller_key_path.write_bytes(caller.private_key_pem)
        authenticator = create_http_authenticator(caller_document_path, caller_key_path)
        agent_document_path = directory / "agent-did.json"
        agent_key_path = directory / "agent-key.pem"
        agent_document_path.write_text(json.dumps(agent.document), encoding="utf-8")
        agent_key_path.write_bytes(agent.private_key_pem)
        unauthorized_authenticator = create_http_authenticator(
            agent_document_path, agent_key_path
        )

        connector = aiohttp.TCPConnector(resolver=LoopbackResolver(), ssl=client_tls)
        async with aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=10)
        ) as session:

            async def resolve_caller_did(did: str) -> dict[str, Any]:
                url = did_document_url(did)
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError("Caller DID could not be resolved over HTTPS")
                    document = await response.json()
                if not isinstance(document, dict):
                    raise TypeError("Expected a DID Document")
                return document

            jwt_private, jwt_public = jwt_keys()
            verifier = create_signature_verifier(
                jwt_private_key=jwt_private,
                jwt_public_key=jwt_public,
                did_resolver=resolve_caller_did,
            )
            application = create_anp_server(
                agent_description=description,
                discovery_collection=collection,
                did_documents={agent.did: agent.document, caller.did: caller.document},
                openrpc_interface=openrpc,
                verifier=verifier,
                authorize_did=lambda did: did == caller.did,
                greet=lambda name, did: f"Hello, {name}." if did == caller.did else "",
            )
            runner = web.AppRunner(application)
            await runner.setup()
            site = web.SockSite(runner, sock, ssl_context=server_tls)
            try:
                await site.start()
                async with ANPClient(authenticator, session=session) as client:
                    yield LocalGreetingAgent(
                        client,
                        session,
                        authenticator,
                        unauthorized_authenticator,
                        domain,
                        rpc_url,
                    )
            finally:
                await runner.cleanup()


async def run_greeting_path() -> tuple[str, str, str]:
    """Return the discovered DID and signed/bearer greeting results."""
    async with local_greeting_agent() as local:
        agents = await local.client.discover(local.domain)
        if len(agents) != 1:
            raise RuntimeError("Expected one discovered Greeting Agent")
        first = await local.client.greet(agents[0], "Pat", force_new_signature=True)
        follow_up = await local.client.greet(agents[0], "Team")
        return agents[0].did, first, follow_up
