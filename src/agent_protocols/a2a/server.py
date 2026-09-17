"""Server assembly for the A2A JSON-RPC binding."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    ServerCallContextBuilder,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types.a2a_pb2 import AgentCard
from starlette.applications import Starlette
from starlette.middleware import Middleware


def create_server(
    agent_card: AgentCard,
    executor: AgentExecutor,
    *,
    task_store: TaskStore | None = None,
    rpc_path: str = "/",
    card_path: str = "/.well-known/agent-card.json",
    context_builder: ServerCallContextBuilder | None = None,
    middleware: Sequence[Middleware] = (),
) -> Starlette:
    """Build a Starlette A2A server using the official SDK's v1 routes.

    The default store is intentionally process-local. Applications needing
    durable task state should inject their own ``TaskStore`` implementation.
    """
    store = task_store or InMemoryTaskStore()
    handler = DefaultRequestHandler(executor, store, agent_card)
    routes = [
        *create_agent_card_routes(agent_card, card_url=card_path),
        *create_jsonrpc_routes(handler, rpc_path, context_builder=context_builder),
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        del app
        yield
        await handler.aclose()

    application = Starlette(
        routes=routes,
        middleware=list(middleware),
        lifespan=lifespan,
    )
    application.state.a2a_handler = handler
    application.state.a2a_task_store = store
    return application
