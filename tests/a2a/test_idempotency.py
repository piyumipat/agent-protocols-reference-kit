"""Transport and reservation tests for optional A2A messageId idempotency."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import httpx
import pytest
from a2a.helpers import new_task_from_user_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest
from a2a.utils.errors import InvalidParamsError
from google.protobuf.json_format import MessageToDict

from agent_protocols.a2a import (
    AgentExecutor,
    EventQueue,
    MessageIdempotencyOptions,
    RequestContext,
    TaskUpdater,
    create_agent_card,
    create_server,
    create_skill,
    new_text_message,
)
from agent_protocols.a2a.idempotency import InMemoryMessageIdempotencyStore, _Outcome


class CountingExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        await event_queue.enqueue_event(
            new_text_message("reply", context_id=context.context_id, task_id=context.task_id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        del context, event_queue


class TaskExecutor(CountingExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.calls += 1
        assert context.message is not None
        assert context.task_id is not None
        assert context.context_id is not None
        await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        await updater.complete(new_text_message("done"))


def _app(executor: CountingExecutor, options: MessageIdempotencyOptions):  # type: ignore[no-untyped-def]
    card = create_agent_card(
        "Echo", "Echo messages", "http://test/rpc",
        [create_skill("echo", "Echo", "Echo")], streaming=True,
    )
    return create_server(card, executor, rpc_path="/rpc", message_idempotency=options)


def _body(method: str = "SendMessage") -> dict[str, object]:
    message = new_text_message("request", role=Role.ROLE_USER)
    return {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "method": method,
        "params": MessageToDict(SendMessageRequest(message=message)),
    }


@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_exact_http_replay_dispatches_once(method: str) -> None:
    executor = CountingExecutor()
    executor.release.set()
    app = _app(executor, MessageIdempotencyOptions())
    body = _body(method)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/rpc", json=body, headers={"A2A-Version": "1.0"})
        replay = await client.post("/rpc", json=body, headers={"A2A-Version": "1.0"})
    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content
    assert executor.calls == 1


async def test_task_event_stream_replay_preserves_order() -> None:
    executor = TaskExecutor()
    app = _app(executor, MessageIdempotencyOptions())
    body = _body("SendStreamingMessage")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/rpc", json=body, headers={"A2A-Version": "1.0"})
        replay = await client.post("/rpc", json=body, headers={"A2A-Version": "1.0"})
    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content
    assert first.text.count("data: ") >= 3
    assert executor.calls == 1


async def test_conflicting_message_content_is_rejected() -> None:
    executor = CountingExecutor()
    executor.release.set()
    app = _app(executor, MessageIdempotencyOptions())
    body = _body()
    changed = deepcopy(body)
    changed["params"]["message"]["parts"][0]["text"] = "different"  # type: ignore[index]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/rpc", json=body, headers={"A2A-Version": "1.0"})
        conflict = await client.post("/rpc", json=changed, headers={"A2A-Version": "1.0"})
    assert first.status_code == 200
    assert conflict.json()["error"]["code"] == -32602
    assert executor.calls == 1


async def test_concurrent_duplicate_waits_for_first_result() -> None:
    executor = CountingExecutor()
    app = _app(executor, MessageIdempotencyOptions())
    body = _body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(client.post("/rpc", json=body, headers={"A2A-Version": "1.0"}))
        await asyncio.wait_for(executor.entered.wait(), 5)
        second = asyncio.create_task(client.post("/rpc", json=body, headers={"A2A-Version": "1.0"}))
        await asyncio.sleep(0)
        executor.release.set()
        responses = await asyncio.gather(first, second)
    assert responses[0].content == responses[1].content
    assert executor.calls == 1


async def test_retention_expiry_and_conflict_scope() -> None:
    now = 0.0
    store = InMemoryMessageIdempotencyStore(
        MessageIdempotencyOptions(retention_seconds=10), clock=lambda: now
    )
    key = ("tenant", "caller", "message-id")
    future, first = await store.reserve(key, b"one")
    assert first
    await store.finish(key, future, outcome=_Outcome(None))
    assert not (await store.reserve(key, b"one"))[1]
    with pytest.raises(InvalidParamsError):
        await store.reserve(key, b"two")
    assert (await store.reserve(("other-tenant", "caller", "message-id"), b"two"))[1]
    now = 11.0
    assert (await store.reserve(key, b"two"))[1]
