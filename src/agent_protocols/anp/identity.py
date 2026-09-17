"""Released ANP 1.1 identity and HTTP authentication helpers.

AgentConnect owns DID generation, proof validation, HTTP Message Signatures, and
token verification. These helpers select its released ``e1_`` profile and turn
off compatibility behavior that differs from ANP-03.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from os import PathLike
from typing import Any

from anp.authentication import (
    DIDWbaAuthHeader,
    DidWbaVerifier,
    DidWbaVerifierConfig,
    create_did_wba_document,
)
from anp.authentication.did_wba import validate_did_document_binding


@dataclass(frozen=True)
class E1Identity:
    """A generated DID Document and its Ed25519 authentication private key."""

    document: dict[str, Any]
    private_key_pem: bytes

    @property
    def did(self) -> str:
        return str(self.document["id"])


def verify_e1_document(document: Mapping[str, Any], expected_did: str) -> bool:
    """Check the DID ID, ``e1_`` key fingerprint, and required document proof.

    This validates supplied document bytes. HTTPS DID resolution and service
    association are separate steps in an agent-discovery flow.
    """
    if document.get("id") != expected_did or not expected_did.startswith("did:wba:"):
        return False
    if not expected_did.rsplit(":", 1)[-1].startswith("e1_"):
        return False
    return bool(validate_did_document_binding(dict(document)))


def create_e1_identity(
    hostname: str,
    *,
    path_segments: Sequence[str],
    port: int | None = None,
    agent_description_url: str | None = None,
) -> E1Identity:
    """Create a path-type ``did:wba`` identity bound to an Ed25519 key.

    The caller publishes ``document`` at the HTTPS URL derived from ``did`` and
    stores ``private_key_pem`` privately. Publishing and key custody are
    deployment responsibilities, outside this helper.
    """
    if not path_segments:
        raise ValueError("An e1_ path identity requires at least one path segment")

    document, keys = create_did_wba_document(
        hostname=hostname,
        port=port,
        path_segments=list(path_segments),
        agent_description_url=agent_description_url,
        did_profile="e1",
        enable_e2ee=False,
    )
    if not verify_e1_document(document, str(document["id"])):
        raise RuntimeError("AgentConnect generated an invalid e1_ DID Document")
    return E1Identity(document=document, private_key_pem=keys["key-1"][0])


def create_http_authenticator(
    did_document_path: str | PathLike[str],
    private_key_path: str | PathLike[str],
) -> DIDWbaAuthHeader:
    """Sign first requests with RFC 9421; reuse a bearer token when issued."""
    return DIDWbaAuthHeader(
        did_document_path=str(did_document_path),
        private_key_path=str(private_key_path),
        auth_mode="http_signatures",
    )


def create_signature_verifier(
    *,
    jwt_private_key: str,
    jwt_public_key: str,
    access_token_expire_minutes: int = 60,
    did_resolver: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> DidWbaVerifier:
    """Verify signed first requests and bearer follow-ups using ANP-03 headers.

    The host application must attach the verifier to its protected HTTP route.
    Caller authorization policy remains the host application's responsibility.
    """
    if not jwt_private_key or not jwt_public_key:
        raise ValueError("JWT signing and verification keys are required")
    if access_token_expire_minutes <= 0:
        raise ValueError("Token lifetime must be positive")

    config = DidWbaVerifierConfig(
        jwt_private_key=jwt_private_key,
        jwt_public_key=jwt_public_key,
        access_token_expire_minutes=access_token_expire_minutes,
        allow_http_signatures=True,
        allow_legacy_didwba=False,
        emit_authentication_info_header=True,
        emit_legacy_authorization_header=False,
    )
    if did_resolver is None:
        return DidWbaVerifier(config)
    from agent_protocols.anp.verifier import ResolvingDidWbaVerifier

    return ResolvingDidWbaVerifier(config, did_resolver)
