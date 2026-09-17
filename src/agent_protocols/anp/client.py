"""Discover, authenticate, and call typed ANP OpenRPC text interfaces."""

from __future__ import annotations

import json
import uuid
from types import TracebackType
from typing import Any, Self

import aiohttp
from anp.authentication import DIDWbaAuthHeader

from agent_protocols.anp.discovery import (
    VerifiedAgent,
    _https_url,
    _origin,
    discover_agents,
)
from agent_protocols.anp.interface import (
    ANSWER_METHOD,
    GREET_METHOD,
    TextMethod,
    text_rpc_url,
)


class ANPCallError(ValueError):
    """An advertised interface or authenticated JSON-RPC call failed."""


class ANPClient:
    """Use published ANP documents and DID-WBA credentials over HTTPS.

    An optional aiohttp session can supply a private DNS resolver and CA for
    local TLS demonstrations. Normal use creates a TLS-verifying session.
    """

    def __init__(
        self,
        authenticator: DIDWbaAuthHeader,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._authenticator = authenticator
        self._session = session
        self._owns_session = session is None
        self._bound_origin: tuple[str, int | None] | None = None

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _active_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("Use ANPClient inside an async context manager")
        return self._session

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        async with self._active_session().get(
            url,
            headers={"Accept": "application/json, application/ld+json"},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ANPCallError(
                    f"Expected HTTP 200 for {url}, got {response.status}"
                )
            data = await response.json()
        if not isinstance(data, dict):
            raise ANPCallError("Expected a JSON document")
        return data

    async def discover(self, domain: str) -> list[VerifiedAgent]:
        """Fetch the well-known collection and DID-linked Agent Descriptions."""
        return await discover_agents(domain, fetch_json=self._fetch_json)

    async def _text_endpoint(self, agent: VerifiedAgent, method: TextMethod) -> str:
        interfaces = agent.description.get("interfaces")
        if not isinstance(interfaces, list):
            raise ANPCallError("Agent Description has no interfaces")
        advertised = [
            interface
            for interface in interfaces
            if isinstance(interface, dict)
            and interface.get("type") == "StructuredInterface"
            and interface.get("protocol") == "openrpc"
            and isinstance(interface.get("url"), str)
        ]
        if len(advertised) != 1:
            raise ANPCallError("Expected one advertised OpenRPC interface")
        interface_url = advertised[0]["url"]
        if not _https_url(interface_url) or _origin(interface_url) != _origin(
            agent.description_url
        ):
            raise ANPCallError("OpenRPC interface must be HTTPS on the agent origin")

        origin = _origin(agent.description_url)
        first_origin_request = self._bound_origin is None
        if self._bound_origin is not None and self._bound_origin != origin:
            raise ANPCallError("Use a separate ANP client for each server origin")
        self._bound_origin = origin

        headers = self._authenticator.get_auth_header(
            interface_url,
            force_new=first_origin_request,
            method="GET",
            headers={},
        )
        async with self._active_session().get(
            interface_url, headers=headers, allow_redirects=False
        ) as response:
            if response.status != 200:
                raise ANPCallError(
                    f"OpenRPC interface fetch failed: HTTP {response.status}"
                )
            document = await response.json()
            self._authenticator.update_token(interface_url, dict(response.headers))
        if not isinstance(document, dict):
            raise ANPCallError("Expected an OpenRPC document")
        try:
            return text_rpc_url(
                document, description_url=agent.description_url, method=method
            )
        except ValueError as exc:
            raise ANPCallError(str(exc)) from exc

    async def call_text(
        self,
        agent: VerifiedAgent,
        method: TextMethod,
        value: str,
        *,
        force_new_signature: bool = False,
    ) -> str:
        """Call an OpenRPC-described text method over authenticated JSON-RPC."""
        if not isinstance(value, str):
            raise TypeError("Text method argument must be a string")
        rpc_url = await self._text_endpoint(agent, method)
        request_id = uuid.uuid4().hex
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method.name,
                "params": {method.parameter_name: value},
                "id": request_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(
            self._authenticator.get_auth_header(
                rpc_url,
                force_new=force_new_signature,
                method="POST",
                headers=headers,
                body=body,
            )
        )
        async with self._active_session().post(
            rpc_url, data=body, headers=headers, allow_redirects=False
        ) as response:
            if response.status != 200:
                raise ANPCallError(
                    f"Authenticated RPC call failed: HTTP {response.status}"
                )
            result = await response.json()
            self._authenticator.update_token(rpc_url, dict(response.headers))
        if (
            not isinstance(result, dict)
            or result.get("jsonrpc") != "2.0"
            or result.get("id") != request_id
        ):
            raise ANPCallError("Invalid JSON-RPC response")
        if "error" in result:
            raise ANPCallError(f"JSON-RPC error: {result['error']}")
        text = result.get("result")
        if not isinstance(text, str):
            raise ANPCallError("Text method result is not a string")
        return text

    async def greet(
        self, agent: VerifiedAgent, name: str, *, force_new_signature: bool = False
    ) -> str:
        """Compatibility wrapper for the original greeting example."""
        return await self.call_text(
            agent, GREET_METHOD, name, force_new_signature=force_new_signature
        )

    async def answer(
        self, agent: VerifiedAgent, question: str, *, force_new_signature: bool = False
    ) -> str:
        """Call the three-agent example's typed answer method."""
        return await self.call_text(
            agent, ANSWER_METHOD, question, force_new_signature=force_new_signature
        )
