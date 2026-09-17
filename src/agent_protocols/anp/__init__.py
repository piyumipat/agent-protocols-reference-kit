"""Public ANP identity, discovery, and authenticated interface API."""

from anp.authentication import DIDWbaAuthHeader, DidWbaVerifier, DidWbaVerifierError

from agent_protocols.anp.client import ANPCallError, ANPClient
from agent_protocols.anp.discovery import (
    DiscoveryError,
    VerifiedAgent,
    create_agent_description,
    create_discovery_collection,
    did_document_url,
    discover_agents,
    verify_agent_association,
)
from agent_protocols.anp.identity import (
    E1Identity,
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
    verify_e1_document,
)
from agent_protocols.anp.interface import (
    ANSWER_METHOD,
    GREET_METHOD,
    TextMethod,
    create_openrpc_interface,
)
from agent_protocols.anp.server import create_anp_server
from agent_protocols.anp.verifier import ResolvingDidWbaVerifier

__all__ = [
    "ANSWER_METHOD",
    "GREET_METHOD",
    "ANPCallError",
    "ANPClient",
    "DIDWbaAuthHeader",
    "DidWbaVerifier",
    "DidWbaVerifierError",
    "DiscoveryError",
    "E1Identity",
    "ResolvingDidWbaVerifier",
    "TextMethod",
    "VerifiedAgent",
    "create_agent_description",
    "create_anp_server",
    "create_discovery_collection",
    "create_e1_identity",
    "create_http_authenticator",
    "create_openrpc_interface",
    "create_signature_verifier",
    "did_document_url",
    "discover_agents",
    "verify_agent_association",
    "verify_e1_document",
]
