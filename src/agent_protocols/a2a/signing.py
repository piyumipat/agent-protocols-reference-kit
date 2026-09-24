"""A2A v1 Agent Card JCS/JWS signing and verification."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import jcs  # type: ignore[import-untyped]
import jwt
from a2a.types.a2a_pb2 import AgentCard, AgentCardSignature
from google.api import field_behavior_pb2  # type: ignore[import-untyped]
from google.protobuf.descriptor import (  # type: ignore[import-untyped]
    Descriptor,
)
from google.protobuf.json_format import (  # type: ignore[import-untyped]
    MessageToDict,
    ParseDict,
)
from jwt.exceptions import PyJWTError

ASYMMETRIC_JWS_ALGORITHMS = (
    "ES256", "ES384", "ES512", "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512", "EdDSA",
)


class AgentCardVerificationError(ValueError):
    """An Agent Card has no usable A2A signature."""


@dataclass(frozen=True, slots=True)
class ResolvedCardKey:
    """A key returned by a caller's key source, with its current status."""

    key: Any
    key_id: str
    source: str
    expires_at: datetime | None = None
    revoked: bool = False


CardKeySource = Callable[[str, str | None], ResolvedCardKey | None]


@dataclass(frozen=True, slots=True)
class VerifiedAgentCard:
    """Cryptographically verified card and the key used for verification."""

    card: AgentCard
    key_id: str
    key_source: str
    algorithm: str


def sign_agent_card(
    card: AgentCard,
    private_key: Any,
    *,
    kid: str,
    algorithm: str = "ES256",
    jku: str | None = None,
) -> AgentCard:
    """Return a copy with one additional standard AgentCardSignature."""
    if not kid:
        raise ValueError("kid is required")
    if algorithm not in ASYMMETRIC_JWS_ALGORITHMS:
        raise ValueError("unsupported asymmetric JWS algorithm")
    signed = AgentCard()
    signed.CopyFrom(card)
    protected: dict[str, str] = {"alg": algorithm, "typ": "JOSE", "kid": kid}
    if jku is not None:
        protected["jku"] = jku
    compact = jwt.api_jws.PyJWS().encode(
        _canonical_payload(_card_to_dict(signed)),
        private_key,
        algorithm=algorithm,
        headers=protected,
    )
    encoded_header, _, encoded_signature = compact.split(".")
    signed.signatures.append(
        AgentCardSignature(protected=encoded_header, signature=encoded_signature)
    )
    return signed


def verify_agent_card(
    card: AgentCard | Mapping[str, Any],
    *,
    key_source: CardKeySource,
    algorithms: tuple[str, ...] = ASYMMETRIC_JWS_ALGORITHMS,
    now: datetime | None = None,
) -> VerifiedAgentCard:
    """Verify at least one signature against a supplied key source.

    ``key_source`` receives the protected ``kid`` and optional ``jku``. It must
    enforce its own trust and key-retrieval policy; a ``jku`` is never fetched
    implicitly from an untrusted card.
    """
    document = _card_to_dict(card) if isinstance(card, AgentCard) else deepcopy(dict(card))
    if not isinstance(document.get("signatures"), list) or not document["signatures"]:
        raise AgentCardVerificationError("Agent Card has no signatures")
    try:
        parsed = ParseDict(document, AgentCard(), ignore_unknown_fields=True)
        payload = _canonical_payload(document)
    except (ValueError, TypeError) as error:
        raise AgentCardVerificationError("invalid Agent Card JSON") from error
    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise ValueError("now must be timezone aware")
    encoded_payload = _b64url(payload)
    for signature in document["signatures"]:
        if not isinstance(signature, dict):
            continue
        try:
            protected = load_agent_card_json(_unb64url(signature["protected"]))
            kid, alg = protected.get("kid"), protected.get("alg")
            jku = protected.get("jku")
            if not isinstance(kid, str) or not kid:
                continue
            if not isinstance(alg, str) or alg not in algorithms or alg not in ASYMMETRIC_JWS_ALGORITHMS:
                continue
            if jku is not None and not isinstance(jku, str):
                continue
            if protected.get("b64") is False or protected.get("crit"):
                continue
            unprotected = signature.get("header", {})
            if not isinstance(unprotected, dict) or protected.keys() & unprotected.keys():
                continue
            resolved = key_source(kid, jku)
            if (
                resolved is None
                or resolved.key_id != kid
                or not resolved.source
                or resolved.revoked
            ):
                continue
            if resolved.expires_at is not None and (
                resolved.expires_at.tzinfo is None or resolved.expires_at <= checked_at
            ):
                continue
            compact = f"{signature['protected']}.{encoded_payload}.{signature['signature']}"
            jwt.api_jws.PyJWS().decode_complete(
                compact, resolved.key, algorithms=list(algorithms)
            )
            return VerifiedAgentCard(parsed, kid, resolved.source, alg)
        except (KeyError, TypeError, ValueError, PyJWTError):
            continue
    raise AgentCardVerificationError("no valid Agent Card signature")


def signed_card_json(card: AgentCard) -> dict[str, Any]:
    """Render a signed card without adding unsigned compatibility properties."""
    document = _card_to_dict(card)
    payload = _normalized_card(document)
    payload["signatures"] = document.get("signatures", [])
    return payload


def load_agent_card_json(data: bytes) -> dict[str, Any]:
    """Parse wire JSON without collapsing duplicate property names."""
    parsed = json.loads(
        data,
        object_pairs_hook=_unique_object,
        parse_constant=lambda value: _invalid_json_constant(value),
    )
    if not isinstance(parsed, dict):
        raise TypeError("Agent Card JSON must be an object")
    return parsed


def _card_to_dict(card: AgentCard) -> dict[str, Any]:
    return cast(dict[str, Any], MessageToDict(card, always_print_fields_with_no_presence=True))


def _canonical_payload(document: dict[str, Any]) -> bytes:
    return cast(bytes, jcs.canonicalize(_normalized_card(document)))


def _normalized_card(document: dict[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in document.items() if key != "signatures"}
    return _normalize_message(content, AgentCard.DESCRIPTOR)


def _normalize_message(document: dict[str, Any], descriptor: Descriptor) -> dict[str, Any]:
    fields = {field.json_name: field for field in descriptor.fields}
    # A2A asks receivers to ignore fields unknown to their model. Keep them in
    # the signed payload so that ignoring them does not erase tampering evidence.
    result: dict[str, Any] = {
        name: value for name, value in document.items() if name not in fields
    }
    for name, field in fields.items():
        required = field_behavior_pb2.REQUIRED in field.GetOptions().Extensions[
            field_behavior_pb2.field_behavior
        ]
        if name not in document:
            if required:
                raise ValueError(f"missing required Agent Card field: {name}")
            continue
        value = document[name]
        if field.is_repeated:
            if required and not value:
                raise ValueError(f"required Agent Card array is empty: {name}")
            if not value and not required:
                continue
            if field.message_type is not None:
                if field.message_type.GetOptions().map_entry:
                    value_field = field.message_type.fields_by_name["value"]
                    value_type = value_field.message_type
                    if value_type is not None and value_type.full_name not in {
                        "google.protobuf.Struct", "google.protobuf.Value"
                    }:
                        value = {
                            key: _normalize_message(item, value_type)
                            for key, item in value.items()
                        }
                else:
                    value = [_normalize_message(item, field.message_type) for item in value]
        elif field.message_type is not None:
            if field.message_type.full_name not in {
                "google.protobuf.Struct", "google.protobuf.Value"
            }:
                value = _normalize_message(value, field.message_type)
        elif not required and not field.has_presence and value == field.default_value:
            continue
        result[name] = value
    return result


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(data: str) -> bytes:
    if not isinstance(data, str):
        raise TypeError("invalid base64url")
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
