"""Transport-level tests for A2A discovery, messages, and tasks."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.types.a2a_pb2 import Role, TaskState

from agent_protocols.a2a import (
    A2AClient,
    AgentExecutor,
    EventQueue,
    RequestContext,
    StreamResponse,
    TaskUpdater,
    create_agent_card,
    create_server,
    create_skill,
    get_message_text,
    new_text_message,
)


class DirectReplyExecutor(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        await event_queue.enqueue_event(
            new_text_message(
                f"Echo: {context.get_user_input()}",
                context_id=context.context_id,
                task_id=context.task_id,
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        del context, event_queue


class ArtifactExecutor(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.message is not None
        assert context.task_id is not None
        assert context.context_id is not None
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        await updater.add_artifact(
            [new_text_part("first ")],
            artifact_id="answer",
            name="answer",
            append=False,
            last_chunk=False,
        )
        await updater.add_artifact(
            [new_text_part("second")],
            artifact_id="answer",
            append=True,
            last_chunk=True,
        )
        await updater.complete(
            new_text_message(
                "done", context_id=context.context_id, task_id=context.task_id
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class PausingExecutor(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.message is not None
        assert context.task_id is not None
        assert context.context_id is not None
        await event_queue.enqueue_event(new_task_from_user_message(context.message))
        await TaskUpdater(
            event_queue, context.task_id, context.context_id
        ).requires_input(new_text_message("Please clarify"))

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class CaptureExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.tenant: str | None = None
        self.return_immediately = False

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        self.tenant = context.tenant
        assert context.configuration is not None
        self.return_immediately = context.configuration.return_immediately
        await event_queue.enqueue_event(new_text_message("captured"))

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        del context, event_queue


class StateExecutor(AgentExecutor):
    def __init__(self, state: TaskState) -> None:
        self.state = state

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.message is not None
        assert context.task_id is not None
        assert context.context_id is not None
        await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if self.state == TaskState.TASK_STATE_FAILED:
            await updater.failed()
        elif self.state == TaskState.TASK_STATE_REJECTED:
            await updater.reject()
        else:
            await updater.requires_auth()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        del context, event_queue


def build_app(executor: AgentExecutor, *, streaming: bool = True):  # type: ignore[no-untyped-def]
    card = create_agent_card(
        "Test agent",
        "Exercises the reference implementation.",
        "http://test/rpc",
        [create_skill("echo", "Echo", "Echo input")],
        streaming=streaming,
    )
    return create_server(card, executor, rpc_path="/rpc")


async def test_well_known_discovery_and_direct_message_response() -> None:
    app = build_app(DirectReplyExecutor())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        card_response = await http.get("/.well-known/agent-card.json")
        assert card_response.status_code == 200
        assert card_response.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"

        async with A2AClient("http://test", http_client=http) as client:
            responses = [
                response
                async for response in client.send_message(
                    new_text_message("hello", role=Role.ROLE_USER)
                )
            ]

    assert len(responses) == 1
    assert responses[0].WhichOneof("payload") == "message"
    assert get_message_text(responses[0].message) == "Echo: hello"


async def test_streaming_task_artifact_order_storage_and_listing() -> None:
    app = build_app(ArtifactExecutor())
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        A2AClient("http://test", http_client=http) as client,
    ):
        responses = [
            response
            async for response in client.send_message(
                new_text_message("work", role=Role.ROLE_USER)
            )
        ]
        task_id = responses[0].task.id
        task = await client.get_task(task_id, history_length=10)
        listed = await client.list_tasks(
            context_id=task.context_id, history_length=10
        )

    payloads = [response.WhichOneof("payload") for response in responses]
    assert payloads == [
        "task",
        "status_update",
        "artifact_update",
        "artifact_update",
        "status_update",
    ]
    assert responses[2].artifact_update.append is False
    assert responses[3].artifact_update.append is True
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert [part.text for part in task.artifacts[0].parts] == ["first ", "second"]
    assert len(task.history) == 1
    assert listed.total_size == 1
    assert listed.tasks[0].id == task_id


async def test_context_continues_across_two_tasks() -> None:
    app = build_app(ArtifactExecutor())
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        A2AClient("http://test", http_client=http) as client,
    ):
        first_events = [
            event
            async for event in client.send_message(
                new_text_message("first", role=Role.ROLE_USER)
            )
        ]
        first = first_events[0].task
        second_events = [
            event
            async for event in client.send_message(
                new_text_message(
                    "second", role=Role.ROLE_USER, context_id=first.context_id
                )
            )
        ]
        second = second_events[0].task
        listed = await client.list_tasks(context_id=first.context_id)

    assert first.id != second.id
    assert first.context_id == second.context_id
    assert listed.total_size == 2


async def test_input_required_task_can_be_cancelled() -> None:
    app = build_app(PausingExecutor())
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        A2AClient("http://test", http_client=http) as client,
    ):
        events = [
            event
            async for event in client.send_message(
                new_text_message("pause", role=Role.ROLE_USER)
            )
        ]
        task_id = events[0].task.id
        assert events[-1].status_update.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        async def collect_subscription() -> list[StreamResponse]:
            return [event async for event in client.subscribe(task_id)]

        subscription = asyncio.create_task(collect_subscription())
        await asyncio.sleep(0)
        cancelled = await client.cancel_task(task_id)
        async with asyncio.timeout(2):
            reconnected = await subscription

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert reconnected[-1].status_update.status.state == TaskState.TASK_STATE_CANCELED


async def test_version_header_tenant_and_return_immediately_propagate() -> None:
    executor = CaptureExecutor()
    card = create_agent_card(
        "Tenant agent",
        "Captures request configuration.",
        "http://test/rpc",
        [create_skill("capture", "Capture", "Capture request details")],
        tenant="tenant-a",
    )
    requests: list[httpx.Request] = []

    async def capture(request: httpx.Request) -> None:
        requests.append(request)

    transport = httpx.ASGITransport(app=create_server(card, executor, rpc_path="/rpc"))
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            event_hooks={"request": [capture]},
        ) as http,
        A2AClient("http://test", http_client=http) as client,
    ):
        _ = [
            event
            async for event in client.send_message(
                new_text_message("capture", role=Role.ROLE_USER),
                return_immediately=True,
            )
        ]

    rpc_request = next(request for request in requests if request.method == "POST")
    assert rpc_request.headers["A2A-Version"] == "1.0"
    assert executor.tenant == "tenant-a"
    assert executor.return_immediately is True


@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ],
)
async def test_remaining_task_lifecycle_states(state: TaskState) -> None:
    app = build_app(StateExecutor(state))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        A2AClient("http://test", http_client=http) as client,
    ):
        events = [
            event
            async for event in client.send_message(
                new_text_message("state", role=Role.ROLE_USER)
            )
        ]

    assert events[0].task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert events[-1].status_update.status.state == state
