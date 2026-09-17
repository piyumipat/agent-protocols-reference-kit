"""Local DNS, TLS, DID, publication, and server setup for separate ANP agents."""

from __future__ import annotations

import json
import os
import socket
import ssl
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from agent_protocols.anp import (
    ANSWER_METHOD,
    E1Identity,
    create_agent_description,
    create_anp_server,
    create_discovery_collection,
    create_e1_identity,
    create_openrpc_interface,
    create_signature_verifier,
    did_document_url,
    verify_e1_document,
)
from agent_protocols.anp.server import TextHandler
from examples.anp.local_https import (
    HOSTNAME,
    LoopbackResolver,
    jwt_keys,
    tls_contexts,
)

DEFAULT_PORTS = {"coordinator": 8100, "facts": 8101, "review": 8102}
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[2] / ".anp-demo"
ActionSink = Callable[[str], None]
HandlerFactory = Callable[
    [aiohttp.ClientSession, "DemoConfig", ActionSink | None], TextHandler
]


def _default_directory() -> Path:
    configured = os.environ.get("ANP_DEMO_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_DIRECTORY


def _default_ports() -> dict[str, int]:
    return {
        role: int(os.environ.get(f"ANP_{role.upper()}_PORT", default))
        for role, default in DEFAULT_PORTS.items()
    }


@dataclass(frozen=True)
class DemoConfig:
    directory: Path = field(default_factory=_default_directory)
    ports: Mapping[str, int] = field(default_factory=_default_ports)

    def domain(self, role: str) -> str:
        return f"{HOSTNAME}:{self.ports[role]}"


@dataclass(frozen=True)
class DemoIdentity:
    document: dict[str, Any]
    document_path: Path
    key_path: Path

    @property
    def did(self) -> str:
        return str(self.document["id"])


def announce(on_action: ActionSink | None, message: str) -> None:
    if on_action is not None:
        on_action(message)


def console_action(message: str) -> None:
    print(message, flush=True)


def _identity_paths(config: DemoConfig, role: str) -> tuple[Path, Path]:
    return config.directory / f"{role}-did.json", config.directory / f"{role}-key.pem"


def _write_identity(config: DemoConfig, role: str, identity: E1Identity) -> None:
    document_path, key_path = _identity_paths(config, role)
    document_path.write_text(json.dumps(identity.document), encoding="utf-8")
    key_path.write_bytes(identity.private_key_pem)
    key_path.chmod(0o600)


def setup_demo(config: DemoConfig | None = None) -> None:
    """Generate shared local trust and all DID credentials before servers start."""
    config = config or DemoConfig()
    config.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tls_contexts(config.directory)
    (config.directory / "local-tls-key.pem").chmod(0o600)
    for role in ("facts", "review", "coordinator"):
        description_url = f"https://{config.domain(role)}/agents/{role}/ad.json"
        identity = create_e1_identity(
            HOSTNAME,
            port=config.ports[role],
            path_segments=["agents", role],
            agent_description_url=description_url,
        )
        _write_identity(config, role, identity)
    caller = create_e1_identity(
        HOSTNAME,
        port=config.ports["coordinator"],
        path_segments=["agents", "human-caller"],
    )
    _write_identity(config, "caller", caller)


def load_identity(config: DemoConfig, role: str) -> DemoIdentity:
    document_path, key_path = _identity_paths(config, role)
    if not document_path.is_file() or not key_path.is_file():
        raise RuntimeError(
            "ANP demo credentials missing; run python -m examples.anp.setup first"
        )
    document = json.loads(document_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not verify_e1_document(
        document, str(document.get("id"))
    ):
        raise ValueError(f"Invalid local DID Document for {role}")
    return DemoIdentity(document, document_path, key_path)


def client_session(config: DemoConfig) -> aiohttp.ClientSession:
    certificate = config.directory / "local-cert.pem"
    if not certificate.is_file():
        raise RuntimeError(
            "ANP demo TLS certificate missing; run python -m examples.anp.setup first"
        )
    tls = ssl.create_default_context(cafile=str(certificate))
    connector = aiohttp.TCPConnector(resolver=LoopbackResolver(), ssl=tls)
    return aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=300)
    )


def create_demo_app(
    config: DemoConfig,
    role: str,
    handler: TextHandler,
    session: aiohttp.ClientSession,
) -> web.Application:
    """Publish one role and authorize only its expected caller DID."""
    identity = load_identity(config, role)
    caller_role = "caller" if role == "coordinator" else "coordinator"
    expected_caller = load_identity(config, caller_role)
    origin = f"https://{config.domain(role)}"
    description_url = f"{origin}/agents/{role}/ad.json"
    interface_url = f"{origin}/agents/{role}/interface.json"
    rpc_url = f"{origin}/agents/{role}/rpc"
    description = create_agent_description(
        url=description_url,
        name=f"{role.capitalize()} agent",
        did=identity.did,
        description=f"{role.capitalize()} role in a three-agent ANP answer workflow.",
        interfaces=[
            {"type": "StructuredInterface", "protocol": "openrpc", "url": interface_url}
        ],
    )

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

    jwt_private, jwt_public = jwt_keys()
    verifier = create_signature_verifier(
        jwt_private_key=jwt_private,
        jwt_public_key=jwt_public,
        did_resolver=resolve_did,
    )
    hosted_documents = {identity.did: identity.document}
    if role == "coordinator":
        hosted_documents[expected_caller.did] = expected_caller.document
    return create_anp_server(
        agent_description=description,
        discovery_collection=create_discovery_collection(
            config.domain(role), [description]
        ),
        did_documents=hosted_documents,
        openrpc_interface=create_openrpc_interface(
            rpc_url=rpc_url,
            title=f"{role.capitalize()} Agent API",
            method=ANSWER_METHOD,
        ),
        verifier=verifier,
        authorize_did=lambda did: did == expected_caller.did,
        text_method=ANSWER_METHOD,
        handle_text=handler,
    )


@asynccontextmanager
async def host_role(
    config: DemoConfig,
    role: str,
    handler_factory: HandlerFactory,
    *,
    on_action: ActionSink | None = None,
    sock: socket.socket | None = None,
) -> AsyncIterator[None]:
    """Start exactly one agent server; a supplied socket supports test ports."""
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(
        str(config.directory / "local-cert.pem"),
        str(config.directory / "local-tls-key.pem"),
    )
    async with client_session(config) as session:
        handler = handler_factory(session, config, on_action)
        app = create_demo_app(config, role, handler, session)
        runner = web.AppRunner(app)
        await runner.setup()
        site: web.BaseSite
        if sock is None:
            site = web.TCPSite(
                runner, "127.0.0.1", config.ports[role], ssl_context=server_tls
            )
        else:
            site = web.SockSite(runner, sock, ssl_context=server_tls)
        try:
            await site.start()
            announce(
                on_action,
                f"[{role}] Published DID, description, discovery, and OpenRPC at https://{config.domain(role)}",
            )
            yield
        finally:
            await runner.cleanup()
