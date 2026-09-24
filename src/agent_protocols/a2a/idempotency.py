"""Optional Send Message idempotency for the A2A JSON-RPC server."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Callable
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import cast

from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.request_handler import validate_request_params
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import AgentCard, Message, SendMessageRequest, Task
from a2a.utils.errors import InternalError, InvalidParamsError

ScopeResolver = Callable[[ServerCallContext], str]
_Key = tuple[str, str, str]  # Tenant, caller scope, messageId.


@dataclass(frozen=True, slots=True)
class MessageIdempotencyOptions:
    """Bounds for one server process's optional messageId cache."""

    retention_seconds: float = 3600.0
    max_entries: int = 10_000
    max_cached_events: int = 1024
    wait_timeout_seconds: float | None = 30.0
    scope_resolver: ScopeResolver | None = None

    def __post_init__(self) -> None:
        if self.retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        if self.max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if self.max_cached_events <= 0:
            raise ValueError("max_cached_events must be positive")
        if self.wait_timeout_seconds is not None and self.wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive or None")


@dataclass(frozen=True, slots=True)
class _Outcome:
    value: Message | Task | tuple[Event, ...] | None
    failure: str | None = None


@dataclass(slots=True)
class _Record:
    fingerprint: bytes
    future: asyncio.Future[_Outcome]
    expires_at: float | None = None


class InMemoryMessageIdempotencyStore:
    """Atomically reserve IDs and retain completed responses for a bounded time."""

    def __init__(
        self,
        options: MessageIdempotencyOptions,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.options = options
        self._clock = clock
        self._lock = asyncio.Lock()
        self._records: dict[_Key, _Record] = {}

    async def reserve(
        self, key: _Key, fingerprint: bytes
    ) -> tuple[asyncio.Future[_Outcome], bool]:
        """Return the existing result future or reserve the ID for first dispatch."""
        async with self._lock:
            now = self._clock()
            for expired_key, record in tuple(self._records.items()):
                if record.expires_at is not None and record.expires_at <= now:
                    del self._records[expired_key]
            current = self._records.get(key)
            if current is not None:
                if current.fingerprint != fingerprint:
                    raise InvalidParamsError(
                        message="messageId was already used with different request content"
                    )
                return current.future, False
            if len(self._records) >= self.options.max_entries:
                raise InternalError(message="messageId cache is full")
            future: asyncio.Future[_Outcome] = asyncio.get_running_loop().create_future()
            self._records[key] = _Record(fingerprint, future)
            return future, True

    async def finish(
        self, key: _Key, future: asyncio.Future[_Outcome], outcome: _Outcome
    ) -> None:
        async with self._lock:
            record = self._records[key]
            if record.future is future and not future.done():
                record.expires_at = self._clock() + self.options.retention_seconds
                future.set_result(outcome)

    async def wait(self, future: asyncio.Future[_Outcome]) -> _Outcome:
        try:
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=self.options.wait_timeout_seconds
            )
        except TimeoutError as error:
            raise InternalError(message="original messageId is still processing") from error


class IdempotentRequestHandler(DefaultRequestHandler):
    """Check messageId before SDK dispatch for Send and SendStreamingMessage."""

    def __init__(
        self,
        executor: AgentExecutor,
        task_store: TaskStore,
        agent_card: AgentCard,
        idempotency_store: InMemoryMessageIdempotencyStore,
    ) -> None:
        super().__init__(executor, task_store, agent_card)
        self.idempotency_store = idempotency_store

    def _key(self, params: SendMessageRequest, context: ServerCallContext) -> _Key:
        resolver = self.idempotency_store.options.scope_resolver
        if resolver is not None:
            caller = resolver(context)
        elif context.user.is_authenticated:
            caller = f"authenticated:{context.user.user_name}"
        else:
            caller = "anonymous"
        if not caller:
            raise InvalidParamsError(message="messageId caller scope is empty")
        return context.tenant, caller, params.message.message_id

    @staticmethod
    def _fingerprint(
        operation: str, params: SendMessageRequest, context: ServerCallContext
    ) -> bytes:
        return hashlib.sha256(
            operation.encode("ascii")
            + b"\0"
            + "\0".join(sorted(context.requested_extensions)).encode("utf-8")
            + b"\0"
            + params.SerializeToString(deterministic=True)
        ).digest()

    @validate_request_params
    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Message | Task:
        key = self._key(params, context)
        future, first = await self.idempotency_store.reserve(
            key, self._fingerprint("SendMessage", params, context)
        )
        if not first:
            outcome = await self.idempotency_store.wait(future)
            if outcome.failure is not None:
                raise InternalError(message=outcome.failure)
            return cast("Message | Task", deepcopy(outcome.value))
        try:
            result = await super().on_message_send(params, context)
            await asyncio.shield(
                self.idempotency_store.finish(key, future, _Outcome(deepcopy(result)))
            )
        except BaseException:
            await asyncio.shield(
                self.idempotency_store.finish(
                    key, future, _Outcome(None, "original messageId outcome is unknown")
                )
            )
            raise
        return cast("Message | Task", result)

    @validate_request_params
    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        key = self._key(params, context)
        future, first = await self.idempotency_store.reserve(
            key, self._fingerprint("SendStreamingMessage", params, context)
        )
        if not first:
            outcome = await self.idempotency_store.wait(future)
            if outcome.failure is not None:
                raise InternalError(message=outcome.failure)
            for event in cast("tuple[Event, ...]", outcome.value):
                yield deepcopy(event)
            return

        events: list[Event] = []
        exceeded_limit = False
        completed = False
        stream = super().on_message_send_stream(params, context)
        try:
            async for event in stream:
                if len(events) < self.idempotency_store.options.max_cached_events:
                    events.append(deepcopy(event))
                else:
                    exceeded_limit = True
                yield event
            if exceeded_limit:
                await self.idempotency_store.finish(
                    key, future, _Outcome(None, "original messageId stream exceeded replay limit")
                )
            else:
                await self.idempotency_store.finish(key, future, _Outcome(tuple(events)))
            completed = True
        finally:
            try:
                await stream.aclose()
            finally:
                if not completed:
                    await asyncio.shield(
                        self.idempotency_store.finish(
                            key,
                            future,
                            _Outcome(None, "original messageId outcome is unknown"),
                        )
                    )
