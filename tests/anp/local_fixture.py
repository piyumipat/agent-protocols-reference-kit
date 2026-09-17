"""In-process ANP HTTPS fixture for repeatable three-agent integration tests."""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from anp.authentication import DIDWbaAuthHeader

from agent_protocols.anp import (
    ANSWER_METHOD,
    ANPClient,
    E1Identity,
    create_agent_description,
    create_anp_server,
    create_discovery_collection,
    create_e1_identity,
    create_http_authenticator,
    create_openrpc_interface,
    create_signature_verifier,
    did_document_url,
)
from agent_protocols.anp.server import TextHandler
from agent_protocols.utils import LLMClient, TextGenerator
from examples.anp.local_https import (
    HOSTNAME,
    LoopbackResolver,
    jwt_keys,
    tls_contexts,
)
from examples.anp.prompts import (
    COORDINATOR_INSTRUCTION,
    FACTS_INSTRUCTION,
    REVIEW_INSTRUCTION,
)


def _announce(on_action: Callable[[str], None] | None, message: str) -> None:
    if on_action is not None:
        on_action(message)


async def _generate(
    generator: TextGenerator | None, instruction: str, prompt: str
) -> str:
    if generator is not None:
        return await generator(instruction, prompt)
    async with LLMClient() as llm:
        return await llm.generate_text(instruction, prompt)


@dataclass(frozen=True)
class PublishedAgent:
    role: str
    domain: str
    identity: E1Identity
    description: dict[str, Any]
    collection: dict[str, Any]
    openrpc: dict[str, Any]
    rpc_url: str


def _publish(role: str, sock: socket.socket) -> PublishedAgent:
    port = int(sock.getsockname()[1])
    domain = f"{HOSTNAME}:{port}"
    origin = f"https://{domain}"
    description_url = f"{origin}/agents/{role}/ad.json"
    interface_url = f"{origin}/agents/{role}/interface.json"
    rpc_url = f"{origin}/agents/{role}/rpc"
    identity = create_e1_identity(
        HOSTNAME,
        port=port,
        path_segments=["agents", role],
        agent_description_url=description_url,
    )
    description = create_agent_description(
        url=description_url,
        name=f"{role.capitalize()} agent",
        did=identity.did,
        description=f"{role.capitalize()} role in a three-agent ANP answer workflow.",
        interfaces=[
            {
                "type": "StructuredInterface",
                "protocol": "openrpc",
                "url": interface_url,
            }
        ],
    )
    return PublishedAgent(
        role=role,
        domain=domain,
        identity=identity,
        description=description,
        collection=create_discovery_collection(domain, [description]),
        openrpc=create_openrpc_interface(
            rpc_url=rpc_url,
            title=f"{role.capitalize()} Agent API",
            method=ANSWER_METHOD,
        ),
        rpc_url=rpc_url,
    )


def _authenticator(
    identity: E1Identity, role: str, directory: Path
) -> DIDWbaAuthHeader:
    document_path = directory / f"{role}-did.json"
    key_path = directory / f"{role}-auth-key.pem"
    document_path.write_text(json.dumps(identity.document), encoding="utf-8")
    key_path.write_bytes(identity.private_key_pem)
    return create_http_authenticator(document_path, key_path)


@dataclass
class ThreeAgentFixture:
    client: ANPClient
    session: aiohttp.ClientSession
    domains: Mapping[str, str]
    identities: Mapping[str, str]
    events: list[tuple[str, str]]


@asynccontextmanager
async def local_three_agents(
    *,
    facts_generator: TextGenerator | None = None,
    review_generator: TextGenerator | None = None,
    coordinator_generator: TextGenerator | None = None,
    on_action: Callable[[str], None] | None = None,
) -> AsyncIterator[ThreeAgentFixture]:
    """Host three ANP agents and a caller on DNS-named local HTTPS."""
    with (
        tempfile.TemporaryDirectory(prefix="anp-three-agents-") as temporary,
        ExitStack() as stack,
    ):
        directory = Path(temporary)
        server_tls, client_tls = tls_contexts(directory)
        sockets: dict[str, socket.socket] = {}
        for role in ("facts", "review", "coordinator"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            stack.callback(sock.close)
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            sockets[role] = sock

        agents = {role: _publish(role, sock) for role, sock in sockets.items()}
        coordinator = agents["coordinator"]
        caller = create_e1_identity(
            HOSTNAME,
            port=int(sockets["coordinator"].getsockname()[1]),
            path_segments=["agents", "human-caller"],
        )
        caller_auth = _authenticator(caller, "human-caller", directory)
        specialist_auth = {
            "facts": _authenticator(
                coordinator.identity, "coordinator-to-facts", directory
            ),
            "review": _authenticator(
                coordinator.identity, "coordinator-to-review", directory
            ),
        }
        events: list[tuple[str, str]] = []

        connector = aiohttp.TCPConnector(resolver=LoopbackResolver(), ssl=client_tls)
        async with aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=300)
        ) as session:

            async def resolve_did(did: str) -> dict[str, Any]:
                async with session.get(
                    did_document_url(did), allow_redirects=False
                ) as response:
                    if response.status != 200:
                        raise ValueError("Caller DID could not be resolved over HTTPS")
                    document = await response.json()
                if not isinstance(document, dict):
                    raise TypeError("Expected a DID Document")
                return document

            async def specialist_answer(
                role: str, question: str, caller_did: str
            ) -> str:
                events.append((role, caller_did))
                _announce(
                    on_action, f"[{role}] Authorized coordinator DID; generating reply"
                )
                generator = facts_generator if role == "facts" else review_generator
                instruction = (
                    FACTS_INSTRUCTION if role == "facts" else REVIEW_INSTRUCTION
                )
                reply = await _generate(generator, instruction, question)
                _announce(on_action, f"[{role}] Returning reply")
                return reply

            async def ask_specialist(role: str, question: str) -> str:
                seed_domain = agents[role].domain
                _announce(
                    on_action, f"[coordinator] Discovering {role} at {seed_domain}"
                )
                async with ANPClient(specialist_auth[role], session=session) as client:
                    discovered = await client.discover(seed_domain)
                    if len(discovered) != 1:
                        raise RuntimeError(f"Expected one discovered {role} agent")
                    _announce(
                        on_action,
                        f"[coordinator] Verified {role} DID and description link",
                    )
                    _announce(
                        on_action, f"[coordinator] Sending signed answer call to {role}"
                    )
                    reply = await client.answer(
                        discovered[0], question, force_new_signature=True
                    )
                    _announce(on_action, f"[coordinator] Received {role} reply")
                    return reply

            async def coordinator_answer(question: str, caller_did: str) -> str:
                events.append(("coordinator", caller_did))
                _announce(
                    on_action, "[coordinator] Authorized caller DID; asking specialists"
                )
                facts, review = await asyncio.gather(
                    ask_specialist("facts", question),
                    ask_specialist("review", question),
                )
                _announce(
                    on_action, "[coordinator] Synthesizing facts and review replies"
                )
                answer = await _generate(
                    coordinator_generator,
                    COORDINATOR_INSTRUCTION,
                    f"User question:\n{question}\n\n"
                    f"Facts specialist response:\n{facts}\n\n"
                    f"Review specialist response:\n{review}",
                )
                _announce(on_action, "[coordinator] Returning final answer")
                return answer

            runners: list[web.AppRunner] = []
            try:
                for role in ("facts", "review", "coordinator"):
                    published = agents[role]
                    jwt_private, jwt_public = jwt_keys()
                    verifier = create_signature_verifier(
                        jwt_private_key=jwt_private,
                        jwt_public_key=jwt_public,
                        did_resolver=resolve_did,
                    )
                    if role == "coordinator":
                        authorize = lambda did: did == caller.did
                        selected_handler: TextHandler = coordinator_answer
                        did_documents = {
                            coordinator.identity.did: coordinator.identity.document,
                            caller.did: caller.document,
                        }
                    else:
                        authorize = lambda did: did == coordinator.identity.did

                        async def specialist_handler(
                            question: str, did: str, specialist_role: str = role
                        ) -> str:
                            return await specialist_answer(
                                specialist_role, question, did
                            )

                        selected_handler = specialist_handler
                        did_documents = {
                            published.identity.did: published.identity.document
                        }
                    app = create_anp_server(
                        agent_description=published.description,
                        discovery_collection=published.collection,
                        did_documents=did_documents,
                        openrpc_interface=published.openrpc,
                        verifier=verifier,
                        authorize_did=authorize,
                        text_method=ANSWER_METHOD,
                        handle_text=selected_handler,
                    )
                    runner = web.AppRunner(app)
                    await runner.setup()
                    runners.append(runner)
                    await web.SockSite(
                        runner, sockets[role], ssl_context=server_tls
                    ).start()
                    _announce(
                        on_action,
                        f"[{role}] Published DID, description, discovery, and OpenRPC at https://{published.domain}",
                    )

                async with ANPClient(caller_auth, session=session) as user_client:
                    yield ThreeAgentFixture(
                        user_client,
                        session,
                        {role: agent.domain for role, agent in agents.items()},
                        {
                            **{
                                role: agent.identity.did
                                for role, agent in agents.items()
                            },
                            "caller": caller.did,
                        },
                        events,
                    )
            finally:
                for runner in reversed(runners):
                    await runner.cleanup()
