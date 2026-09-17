"""Released ANP-03 identity and authentication behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from anp.authentication import DidWbaVerifierError
from anp.authentication import did_wba_verifier as verifier_module
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_protocols.anp import (
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
    verify_e1_document,
)


def _jwt_keys() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_e1_document_rejects_missing_proof_wrong_fingerprint_and_wrong_id() -> None:
    identity = create_e1_identity(
        "example.com",
        path_segments=["agents", "alice"],
        agent_description_url="https://example.com/agents/alice/ad.json",
    )
    assert identity.did.rsplit(":", 1)[-1].startswith("e1_")
    assert verify_e1_document(identity.document, identity.did)
    assert not verify_e1_document(identity.document, "did:wba:example.com:agents:bob:e1_wrong")

    missing_proof = {key: value for key, value in identity.document.items() if key != "proof"}
    assert not verify_e1_document(missing_proof, identity.did)

    wrong_fingerprint = dict(identity.document)
    wrong_fingerprint["id"] = identity.did.rsplit(":", 1)[0] + ":e1_wrong"
    assert not verify_e1_document(wrong_fingerprint, wrong_fingerprint["id"])


async def test_signed_first_request_and_bearer_follow_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = create_e1_identity("example.com", path_segments=["agents", "alice"])
    document_path = tmp_path / "did.json"
    private_key_path = tmp_path / "auth-key.pem"
    document_path.write_text(json.dumps(identity.document), encoding="utf-8")
    private_key_path.write_bytes(identity.private_key_pem)

    async def local_resolver(requested_did: str) -> dict[str, Any]:
        assert requested_did == identity.did
        return identity.document

    monkeypatch.setattr(verifier_module, "resolve_did_wba_document", local_resolver)

    jwt_private, jwt_public = _jwt_keys()
    verifier = create_signature_verifier(jwt_private_key=jwt_private, jwt_public_key=jwt_public)
    authenticator = create_http_authenticator(document_path, private_key_path)
    url = "https://api.example.com/agent/rpc"
    body = b'{"jsonrpc":"2.0","id":"1","method":"hello"}'
    content_headers = {"Content-Type": "application/json"}
    signature_headers = authenticator.get_auth_header(
        url, force_new=True, method="POST", headers=content_headers, body=body
    )
    assert {"Signature-Input", "Signature", "Content-Digest"} <= signature_headers.keys()

    with pytest.raises(DidWbaVerifierError):
        await verifier.verify_request("POST", url, content_headers, body)
    with pytest.raises(DidWbaVerifierError):
        await verifier.verify_request(
            "POST", url, {**content_headers, **signature_headers}, body + b" "
        )
    with pytest.raises(DidWbaVerifierError):
        await verifier.verify_request(
            "POST", url, {"Authorization": "DIDWba invalid"}, body
        )

    valid_headers = authenticator.get_auth_header(
        url, force_new=True, method="POST", headers=content_headers, body=body
    )
    first = await verifier.verify_request(
        "POST", url, {**content_headers, **valid_headers}, body
    )
    assert first["did"] == identity.did
    assert first["auth_scheme"] == "http_signatures"
    assert "Authentication-Info" in first["response_headers"]
    assert "Authorization" not in first["response_headers"]

    token = authenticator.update_token(url, first["response_headers"])
    assert token
    bearer_headers = authenticator.get_auth_header(url)
    assert bearer_headers["Authorization"].startswith("Bearer ")
    later = await verifier.verify_request("POST", url, bearer_headers, body)
    assert later["did"] == identity.did
    assert later["auth_scheme"] == "bearer"
