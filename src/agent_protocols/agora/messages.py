"""Validation for the Agora Working Standard's JSON envelope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

JSONBody: TypeAlias = str | dict[str, object]


def _protocol_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError("protocolHash must be null or a 40-character hexadecimal string")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(
            "protocolHash must be null or a 40-character hexadecimal string"
        ) from error
    return value.lower()


@dataclass(frozen=True, slots=True)
class AgoraRequest:
    body: JSONBody
    protocol_hash: str | None = None
    protocol_sources: tuple[str, ...] = ()
    multiround: bool = False

    @classmethod
    def from_json(cls, payload: object) -> AgoraRequest:
        if not isinstance(payload, dict):
            raise TypeError("request must be a JSON object")
        if "body" not in payload:
            raise ValueError("request requires body")
        body = payload["body"]
        if not isinstance(body, (str, dict)):
            raise TypeError("body must be a string or object")

        raw_sources = payload.get("protocolSources", [])
        if not isinstance(raw_sources, list) or not all(
            isinstance(source, str) for source in raw_sources
        ):
            raise TypeError("protocolSources must be an array of strings")
        multiround = payload.get("multiround", False)
        if not isinstance(multiround, bool):
            raise TypeError("multiround must be a boolean")

        return cls(
            body=body,
            protocol_hash=_protocol_hash(payload.get("protocolHash")),
            protocol_sources=tuple(raw_sources),
            multiround=multiround,
        )

    def to_json(self, *, include_protocol: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {"body": self.body}
        if include_protocol and self.protocol_hash is not None:
            payload["protocolHash"] = self.protocol_hash
        if include_protocol and self.protocol_sources:
            payload["protocolSources"] = list(self.protocol_sources)
        if self.multiround:
            payload["multiround"] = True
        return payload


@dataclass(frozen=True, slots=True)
class AgoraResponse:
    status: str
    body: JSONBody | None = None
    error: str | None = None
    conversation_id: str | None = None
    conversation_expires: int | None = None

    @classmethod
    def from_json(cls, payload: object) -> AgoraResponse:
        if not isinstance(payload, dict):
            raise TypeError("response must be a JSON object")
        status = payload.get("status")
        if status not in {"success", "failure"}:
            raise ValueError("response status must be success or failure")
        body = payload.get("body")
        error = payload.get("error")
        if status == "success":
            if error is not None or not isinstance(body, (str, dict)):
                raise ValueError("success response requires body and must not contain error")
        elif not isinstance(error, str) or body is not None:
            raise ValueError("failure response requires error and must not contain body")

        conversation_id = payload.get("conversationId")
        conversation_expires = payload.get("conversationExpires")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise ValueError("conversationId must be a string")
        if conversation_expires is not None and not isinstance(conversation_expires, int):
            raise ValueError("conversationExpires must be an integer")
        return cls(
            status=status,
            body=body,
            error=error,
            conversation_id=conversation_id,
            conversation_expires=conversation_expires,
        )

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.body is not None:
            payload["body"] = self.body
        if self.error is not None:
            payload["error"] = self.error
        if self.conversation_id is not None:
            payload["conversationId"] = self.conversation_id
        if self.conversation_expires is not None:
            payload["conversationExpires"] = self.conversation_expires
        return payload
