"""Authentication utilities for the three-agent example.

These static tokens are intentionally limited to a local demonstration. Real
deployments should validate short-lived credentials and apply authorization
policy for each operation.
"""

from __future__ import annotations

import os

from a2a.types.a2a_pb2 import HTTPAuthSecurityScheme, StringList
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from agent_protocols.a2a import SecurityRequirement, SecurityScheme

LOCAL_AGENT_TOKEN = os.getenv("A2A_LOCAL_AGENT_TOKEN", "local-agent-demo-token")
FACTS_AGENT_TOKEN = os.getenv("A2A_FACTS_AGENT_TOKEN", "facts-agent-demo-token")
REVIEW_AGENT_TOKEN = os.getenv("A2A_REVIEW_AGENT_TOKEN", "review-agent-demo-token")


class BearerAuthBackend(AuthenticationBackend):
    """Allow public card discovery and require a token for A2A RPCs."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def authenticate(self, conn):  # type: ignore[no-untyped-def]
        if conn.url.path == "/.well-known/agent-card.json":
            return None
        if conn.headers.get("authorization") != f"Bearer {self._token}":
            raise AuthenticationError("missing or invalid bearer token")
        return AuthCredentials(["authenticated"]), SimpleUser("a2a-caller")


def bearer_middleware(token: str) -> Middleware:
    """Create middleware that enforces the example bearer token."""
    return Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(token))


def bearer_security_schemes() -> dict[str, SecurityScheme]:
    """Create the bearer scheme advertised by each Agent Card."""
    return {
        "bearer": SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                description="Bearer token required for A2A requests",
                scheme="bearer",
            )
        )
    }


def bearer_security_requirements() -> list[SecurityRequirement]:
    """Require the card's bearer scheme without OAuth scopes."""
    return [SecurityRequirement(schemes={"bearer": StringList()})]
