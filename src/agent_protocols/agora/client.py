"""Async client for Agora JSON exchanges and protocol discovery."""

from __future__ import annotations

from types import TracebackType
from typing import Self
from urllib.parse import urlparse

import httpx

from agent_protocols.agora.messages import AgoraRequest, AgoraResponse, JSONBody
from agent_protocols.agora.protocol import ProtocolDocument, ProtocolRegistry


class AgoraClient:
    """Call one Agora base URL and inspect its supported Protocol Documents."""

    def __init__(
        self,
        base_url: str,
        *,
        require_https: bool = True,
        timeout_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if require_https and parsed.scheme != "https":
            raise ValueError("Agora requires HTTPS; disable only for controlled local tests")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._owns_http_client = http_client is None

    async def __aenter__(self) -> Self:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            raise RuntimeError("Use AgoraClient as an async context manager")
        return self._http_client

    async def discover(self) -> dict[str, tuple[str, ...]]:
        response = await self.http.get(f"{self._base_url}/wellknown")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("wellknown response must be a JSON object")
        discovered: dict[str, tuple[str, ...]] = {}
        for protocol_hash, raw_sources in payload.items():
            if not isinstance(protocol_hash, str) or not isinstance(raw_sources, list):
                raise TypeError("invalid wellknown response")
            if not raw_sources or not all(isinstance(source, str) for source in raw_sources):
                raise TypeError("each wellknown protocol requires non-empty string sources")
            discovered[protocol_hash] = tuple(raw_sources)
        return discovered

    async def find_shared_protocol(
        self, local_protocols: ProtocolRegistry
    ) -> ProtocolDocument | None:
        remote = await self.discover()
        for protocol_hash in remote:
            local = local_protocols.get(protocol_hash)
            if local is not None:
                return local
        return None

    async def exchange(
        self,
        body: JSONBody,
        *,
        protocol: ProtocolDocument | None = None,
        provide_source: bool = False,
        multiround: bool = False,
    ) -> AgoraResponse:
        request = AgoraRequest(
            body=body,
            protocol_hash=protocol.hash if protocol is not None else None,
            protocol_sources=(protocol.source,) if protocol is not None and provide_source else (),
            multiround=multiround,
        )
        response = await self.http.post(self._base_url, json=request.to_json())
        response.raise_for_status()
        return AgoraResponse.from_json(response.json())

    async def continue_conversation(
        self, conversation_id: str, body: JSONBody
    ) -> AgoraResponse:
        request = AgoraRequest(body=body)
        response = await self.http.post(
            f"{self._base_url}/conversations/{conversation_id}",
            json=request.to_json(include_protocol=False),
        )
        response.raise_for_status()
        return AgoraResponse.from_json(response.json())
