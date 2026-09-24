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
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_protocols.a2a.idempotency import (
    IdempotentRequestHandler,
    InMemoryMessageIdempotencyStore,
    MessageIdempotencyOptions,
)
from agent_protocols.a2a.signing import signed_card_json


def create_server(
    agent_card: AgentCard,
    executor: AgentExecutor,
    *,
    task_store: TaskStore | None = None,
    rpc_path: str = "/",
    card_path: str = "/.well-known/agent-card.json",
    context_builder: ServerCallContextBuilder | None = None,
    middleware: Sequence[Middleware] = (),
    message_idempotency: MessageIdempotencyOptions | None = None,
) -> Starlette:
    """Build a Starlette A2A server using the official SDK's v1 routes.

    The default store is intentionally process-local. Applications needing
    durable task state should inject their own ``TaskStore`` implementation.
    """
    store = task_store or InMemoryTaskStore()
    idempotency_store = (
        InMemoryMessageIdempotencyStore(message_idempotency)
        if message_idempotency is not None
        else None
    )
    handler = (
        IdempotentRequestHandler(executor, store, agent_card, idempotency_store)
        if idempotency_store is not None
        else DefaultRequestHandler(executor, store, agent_card)
    )
    if agent_card.signatures:
        async def signed_card_route(request: Request) -> JSONResponse:
            del request
            return JSONResponse(signed_card_json(agent_card))

        card_routes = [Route(card_path, signed_card_route, methods=["GET"])]
    else:
        card_routes = create_agent_card_routes(agent_card, card_url=card_path)
    routes = [
        *card_routes,
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
    application.state.a2a_message_idempotency_store = idempotency_store
    return application
