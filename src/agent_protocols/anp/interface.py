"""ANP-07 OpenRPC descriptions for typed text JSON-RPC methods."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_protocols.anp.discovery import _https_url, _origin


@dataclass(frozen=True)
class TextMethod:
    """One named string parameter and one string result in an OpenRPC method."""

    name: str
    parameter_name: str
    result_name: str
    summary: str

    def __post_init__(self) -> None:
        for value in (self.name, self.parameter_name, self.result_name):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", value):
                raise ValueError(
                    "Text method and field names must be simple identifiers"
                )
        if self.name.startswith("rpc."):
            raise ValueError("JSON-RPC reserves rpc.* method names")
        if not self.summary.strip():
            raise ValueError("Text method summary is required")


GREET_METHOD = TextMethod("greet", "name", "greeting", "Greet a named caller")
ANSWER_METHOD = TextMethod("answer", "question", "answer", "Answer a question")


def create_openrpc_interface(
    *,
    rpc_url: str,
    title: str = "Greeting Agent API",
    method: TextMethod = GREET_METHOD,
) -> dict[str, Any]:
    """Describe a typed text method using ANP-07's OpenRPC 1.3.2 format."""
    if not _https_url(rpc_url):
        raise ValueError("OpenRPC server URL must be HTTPS")
    if not title.strip():
        raise ValueError("OpenRPC title is required")
    return {
        "openrpc": "1.3.2",
        "info": {
            "title": title,
            "version": "1.0.0",
            "x-anp-protocol-type": "ANP",
            "x-anp-protocol-version": "1.0.0",
        },
        "security": [{"didwba": []}],
        "servers": [{"name": "Agent Server", "url": rpc_url}],
        "methods": [
            {
                "name": method.name,
                "summary": method.summary,
                "params": [
                    {
                        "name": method.parameter_name,
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "result": {
                    "name": method.result_name,
                    "schema": {"type": "string"},
                },
            }
        ],
        "components": {
            "securitySchemes": {
                "didwba": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "DID-WBA",
                    "description": (
                        "ANP-03 HTTP Message Signatures on initial requests; "
                        "bearer token on subsequent requests"
                    ),
                }
            }
        },
    }


def text_rpc_url(
    document: dict[str, Any], *, description_url: str, method: TextMethod
) -> str:
    """Validate a typed text method and return its advertised HTTPS RPC URL."""
    info = document.get("info")
    servers = document.get("servers")
    methods = document.get("methods")
    if (
        document.get("openrpc") != "1.3.2"
        or not isinstance(info, dict)
        or info.get("x-anp-protocol-type") != "ANP"
        or not isinstance(servers, list)
        or len(servers) != 1
        or not isinstance(servers[0], dict)
        or not isinstance(methods, list)
    ):
        raise ValueError("Invalid ANP OpenRPC interface")
    rpc_url = servers[0].get("url")
    if not isinstance(rpc_url, str) or not _https_url(rpc_url):
        raise ValueError("OpenRPC server must be HTTPS")
    if _origin(rpc_url) != _origin(description_url):
        raise ValueError(
            "Initial path requires an RPC URL on the Agent Description domain"
        )
    method_doc = next(
        (
            item
            for item in methods
            if isinstance(item, dict) and item.get("name") == method.name
        ),
        None,
    )
    if (
        not isinstance(method_doc, dict)
        or method_doc.get("params")
        != [
            {
                "name": method.parameter_name,
                "required": True,
                "schema": {"type": "string"},
            }
        ]
        or method_doc.get("result")
        != {"name": method.result_name, "schema": {"type": "string"}}
    ):
        raise ValueError(f"OpenRPC interface lacks the typed {method.name} method")
    return rpc_url


def greet_rpc_url(document: dict[str, Any], *, description_url: str) -> str:
    """Compatibility helper for the original greeting example."""
    return text_rpc_url(document, description_url=description_url, method=GREET_METHOD)
