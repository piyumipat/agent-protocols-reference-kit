"""Small async client wrapper for A2A messages and tasks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from types import TracebackType
from typing import Self

import httpx
from a2a.client import Client, ClientConfig, create_client
from a2a.client.interceptors import ClientCallInterceptor
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskState,
)


class A2AClient:
    """Discover an agent and use the official SDK's JSON-RPC transport.

    URL targets are resolved through ``/.well-known/agent-card.json`` by
    default. Passing an ``AgentCard`` skips network discovery. The SDK handles
    interface selection and propagates the selected interface's tenant and the
    ``A2A-Version`` header.
    """

    def __init__(
        self,
        target: str | AgentCard,
        *,
        streaming: bool = True,
        polling: bool = False,
        accepted_output_modes: Sequence[str] = (),
        headers: Mapping[str, str] | None = None,
        request_timeout_seconds: float | None = 30.0,
        http_client: httpx.AsyncClient | None = None,
        card_path: str | None = None,
        interceptors: Sequence[ClientCallInterceptor] = (),
    ) -> None:
        self._target = target
        self._streaming = streaming
        self._polling = polling
        self._accepted_output_modes = list(accepted_output_modes)
        self._headers = dict(headers or {})
        self._request_timeout_seconds = request_timeout_seconds
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._card_path = card_path
        self._interceptors = list(interceptors)
        self._client: Client | None = None

    async def __aenter__(self) -> Self:
        if self._client is not None:
            raise RuntimeError("A2AClient is already connected")
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self._request_timeout_seconds,
            )
        elif self._headers:
            self._http_client.headers.update(self._headers)
        config = ClientConfig(
            streaming=self._streaming,
            polling=self._polling,
            httpx_client=self._http_client,
            supported_protocol_bindings=["JSONRPC"],
            accepted_output_modes=self._accepted_output_modes,
        )
        self._client = await create_client(
            self._target,
            client_config=config,
            interceptors=self._interceptors,
            relative_card_path=self._card_path,
            resolver_http_kwargs={"headers": self._headers},
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def client(self) -> Client:
        """Return the connected official SDK client."""
        if self._client is None:
            raise RuntimeError("Use A2AClient as an async context manager")
        return self._client

    async def send_message(
        self,
        message: Message,
        *,
        return_immediately: bool = False,
        history_length: int = 0,
        accepted_output_modes: Sequence[str] = (),
    ) -> AsyncIterator[StreamResponse]:
        """Send a message and yield its message/task/update response stream."""
        request = SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(
                accepted_output_modes=accepted_output_modes,
                history_length=history_length,
                return_immediately=return_immediately,
            ),
        )
        async for response in self.client.send_message(request):
            yield response

    async def get_task(self, task_id: str, *, history_length: int = 0) -> Task:
        return await self.client.get_task(
            GetTaskRequest(id=task_id, history_length=history_length)
        )

    async def list_tasks(
        self,
        *,
        context_id: str = "",
        status: TaskState | str | None = None,
        page_size: int = 50,
        page_token: str = "",
        history_length: int = 0,
        include_artifacts: bool = True,
    ) -> ListTasksResponse:
        return await self.client.list_tasks(
            ListTasksRequest(
                context_id=context_id,
                status=status,
                page_size=page_size,
                page_token=page_token,
                history_length=history_length,
                include_artifacts=include_artifacts,
            )
        )

    async def cancel_task(self, task_id: str) -> Task:
        return await self.client.cancel_task(CancelTaskRequest(id=task_id))

    async def subscribe(self, task_id: str) -> AsyncIterator[StreamResponse]:
        """Subscribe or reconnect to updates for an existing task."""
        async for response in self.client.subscribe(SubscribeToTaskRequest(id=task_id)):
            yield response
