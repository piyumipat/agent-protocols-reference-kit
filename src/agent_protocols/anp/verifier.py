"""ANP-03 verifier adapter with a host-supplied DID resolver."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from anp.authentication import DidWbaVerifier, DidWbaVerifierConfig, DidWbaVerifierError
from anp.authentication import did_wba_verifier as sdk_verifier

from agent_protocols.anp.identity import verify_e1_document

E1Resolver = Callable[[str], Awaitable[dict[str, Any]]]


class ResolvingDidWbaVerifier(DidWbaVerifier):  # type: ignore[misc]
    """Use SDK signature/token checks with an injected e1 DID document resolver.

    The resolver must retrieve the DID document at its method-derived HTTPS URL
    with TLS verification. The adapter validates its ID and e1 proof before use.
    """

    def __init__(self, config: DidWbaVerifierConfig, resolver: E1Resolver) -> None:
        super().__init__(config)
        self._resolver = resolver

    async def _handle_http_signature_auth(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | str | None,
        domain: str,
    ) -> dict[str, Any]:
        try:
            metadata = sdk_verifier.extract_signature_metadata(headers)
            keyid = metadata["params"].get("keyid")
            if not isinstance(keyid, str) or "#" not in keyid:
                raise ValueError("Missing Signature-Input keyid")
            did = keyid.split("#", 1)[0]
            did_document = await self._resolver(did)
        except Exception as exc:
            raise DidWbaVerifierError(
                "Failed to resolve an e1 DID from the request signature",
                status_code=401,
                headers=self._build_challenge_headers(
                    domain, "invalid_did", "Invalid DID"
                ),
            ) from exc

        if not verify_e1_document(did_document, did):
            raise DidWbaVerifierError(
                "DID binding verification failed", status_code=401
            )
        if not self._is_authentication_authorized(did_document, keyid):
            raise DidWbaVerifierError(
                "Verification method is not authorized for authentication",
                status_code=403,
            )

        valid, message, verified = sdk_verifier.verify_http_message_signature(
            did_document=did_document,
            request_method=method,
            request_url=url,
            headers=headers,
            body=body,
        )
        if not valid:
            raise DidWbaVerifierError(
                message,
                status_code=401,
                headers=self._build_challenge_headers(
                    domain, "invalid_signature", message
                ),
            )
        if not self._verify_http_signature_time_window(
            verified.get("created"), verified.get("expires")
        ):
            raise DidWbaVerifierError(
                "Invalid HTTP signature timestamp", status_code=401
            )
        nonce = verified.get("nonce")
        if self.config.require_nonce_for_http_signatures and not nonce:
            raise DidWbaVerifierError(
                "HTTP signature nonce is required", status_code=401
            )
        if nonce and not await self._is_valid_server_nonce(did, nonce):
            raise DidWbaVerifierError("Invalid or reused nonce", status_code=401)

        token = self._create_access_token({"sub": did})
        return cast(
            dict[str, Any],
            self._build_success_result(
                did=did, auth_scheme="http_signatures", access_token=token
            ),
        )
