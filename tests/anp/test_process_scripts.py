"""The ANP setup, three servers, and user entry point run as separate processes."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from examples.anp.demo_common import DemoConfig, client_session
from examples.anp.user import DEFAULT_QUESTION

ROOT = Path(__file__).resolve().parents[2]


def _reserve_port() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock, int(sock.getsockname()[1])


async def _wait_for_collection(
    session: aiohttp.ClientSession,
    config: DemoConfig,
    role: str,
    process: asyncio.subprocess.Process,
) -> None:
    url = f"https://{config.domain(role)}/.well-known/agent-descriptions"
    for _ in range(80):
        if process.returncode is not None:
            raise RuntimeError(f"{role} process exited before serving discovery")
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return
        except (aiohttp.ClientError, OSError):
            pass
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{role} process did not publish discovery")


async def test_four_anp_scripts_answer_default_question_with_llm(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    async def completion(request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json()
        instruction = str(body["messages"][0]["content"])
        prompt = str(body["messages"][1]["content"])
        prompts.append(prompt)
        if "facts specialist" in instruction:
            content = "Facts from LLM"
        elif "review specialist" in instruction:
            content = "Review from LLM"
        else:
            assert "Facts from LLM" in prompt
            assert "Review from LLM" in prompt
            content = "Final ANP answer from LLM"
        return web.json_response(
            {
                "id": "chatcmpl-anp-process-test",
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

    sockets = {role: _reserve_port() for role in ("facts", "review", "coordinator")}
    llm_socket, llm_port = _reserve_port()
    for sock, _ in sockets.values():
        sock.close()
    ports = {role: port for role, (_, port) in sockets.items()}
    config = DemoConfig(directory=tmp_path / "demo", ports=ports)
    environment = os.environ.copy()
    environment.update(
        {
            "ANP_DEMO_DIR": str(config.directory),
            "ANP_FACTS_PORT": str(ports["facts"]),
            "ANP_REVIEW_PORT": str(ports["review"]),
            "ANP_COORDINATOR_PORT": str(ports["coordinator"]),
            "LLM_BASE_URL": f"http://127.0.0.1:{llm_port}/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
        }
    )
    llm_app = web.Application()
    llm_app.router.add_post("/v1/chat/completions", completion)
    llm_runner = web.AppRunner(llm_app)
    await llm_runner.setup()
    processes: dict[str, asyncio.subprocess.Process] = {}
    server_logs: dict[str, str] = {}
    try:
        await web.SockSite(llm_runner, llm_socket).start()
        setup = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "examples.anp.setup",
            cwd=ROOT,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        setup_output, _ = await asyncio.wait_for(setup.communicate(), 10)
        assert setup.returncode == 0, setup_output.decode()

        for role in ("facts", "review", "coordinator"):
            processes[role] = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                f"examples.anp.{role}_agent",
                cwd=ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        async with client_session(config) as session:
            for role, process in processes.items():
                await _wait_for_collection(session, config, role, process)

        user = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "examples.anp.user",
            cwd=ROOT,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        user_output, _ = await asyncio.wait_for(user.communicate(b"\n"), 15)
        assert user.returncode == 0, user_output.decode()
        assert "Final ANP answer from LLM" in user_output.decode()
        assert "[caller] Discovering coordinator" in user_output.decode()
        assert len(prompts) == 3
        assert all(DEFAULT_QUESTION in prompt for prompt in prompts)
    finally:
        for process in processes.values():
            if process.returncode is None:
                process.terminate()
        for role, process in processes.items():
            output, _ = await asyncio.wait_for(process.communicate(), 5)
            server_logs[role] = output.decode()
        await llm_runner.cleanup()
        llm_socket.close()

    assert "[facts] Authorized coordinator DID; asking LLM" in server_logs["facts"]
    assert "[review] Authorized coordinator DID; asking LLM" in server_logs["review"]
    assert (
        "[coordinator] Synthesizing facts and review replies"
        in server_logs["coordinator"]
    )
