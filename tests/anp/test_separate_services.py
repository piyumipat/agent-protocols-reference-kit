"""The separate ANP agent apps use discovery, DID auth, and the shared LLM."""

from __future__ import annotations

import socket
from contextlib import AsyncExitStack, ExitStack
from pathlib import Path
from typing import Any

from aiohttp import web
from pytest import MonkeyPatch

from examples.anp import coordinator_agent, facts_agent, review_agent
from examples.anp.demo_common import DemoConfig, host_role, setup_demo
from examples.anp.user import DEFAULT_QUESTION, ask_coordinator


def _socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


async def test_separate_agent_apps_answer_through_anp_and_llm(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    async def completion(request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json()
        instruction = str(body["messages"][0]["content"])
        prompt = str(body["messages"][1]["content"])
        calls.append((instruction, prompt))
        if "facts specialist" in instruction:
            content = "Facts: agents publish identities and interfaces."
        elif "review specialist" in instruction:
            content = "Review: verify identity and authorization."
        else:
            assert "Facts: agents publish identities and interfaces." in prompt
            assert "Review: verify identity and authorization." in prompt
            content = "ANP publishes interfaces; verify identity and authorization."
        return web.json_response(
            {
                "id": "chatcmpl-anp-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    llm_app = web.Application()
    llm_app.router.add_post("/v1/chat/completions", completion)
    llm_runner = web.AppRunner(llm_app)
    await llm_runner.setup()
    with ExitStack() as stack:
        sockets = {role: _socket() for role in ("facts", "review", "coordinator")}
        llm_socket = _socket()
        for sock in (*sockets.values(), llm_socket):
            stack.callback(sock.close)
        ports = {role: int(sock.getsockname()[1]) for role, sock in sockets.items()}
        config = DemoConfig(directory=tmp_path / "demo", ports=ports)
        setup_demo(config)
        await web.SockSite(llm_runner, llm_socket).start()
        monkeypatch.setenv(
            "LLM_BASE_URL", f"http://127.0.0.1:{llm_socket.getsockname()[1]}/v1"
        )
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        try:
            async with AsyncExitStack() as servers:
                await servers.enter_async_context(
                    host_role(
                        config, "facts", facts_agent.make_handler, sock=sockets["facts"]
                    )
                )
                await servers.enter_async_context(
                    host_role(
                        config,
                        "review",
                        review_agent.make_handler,
                        sock=sockets["review"],
                    )
                )
                await servers.enter_async_context(
                    host_role(
                        config,
                        "coordinator",
                        coordinator_agent.make_handler,
                        sock=sockets["coordinator"],
                    )
                )
                answer = await ask_coordinator(DEFAULT_QUESTION, config=config)
                assert (
                    answer
                    == "ANP publishes interfaces; verify identity and authorization."
                )
        finally:
            await llm_runner.cleanup()

    assert len(calls) == 3
    assert sorted(
        "facts"
        if "facts specialist" in instruction
        else "review"
        if "review specialist" in instruction
        else "coordinator"
        for instruction, _ in calls
    ) == ["coordinator", "facts", "review"]
    assert all(DEFAULT_QUESTION in prompt for _, prompt in calls)
