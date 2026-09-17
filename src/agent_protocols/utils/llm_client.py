"""Shared, protocol-independent LLM client.

Wraps an OpenAI-compatible chat-completions endpoint so every protocol example can
use the same client interface. The endpoint and model are selected through constructor
arguments or environment variables and may target a hosted service or a self-hosted
compatible server such as llama.cpp.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageParam

TextGenerator = Callable[[str, str], Awaitable[str]]
"""Callable form used when examples inject deterministic text generation."""

# Default local llama.cpp OpenAI-compatible server.
_DEFAULT_BASE_URL = "http://localhost:8080/v1"
# llama.cpp doesn't check the API key; a real OpenAI-compatible backend needs a real one.
_DEFAULT_API_KEY = "not-needed"
_DEFAULT_MODEL = "/models/Qwen3.8-27B-Q4_K_M.gguf"


class LLMClient:
    """Thin async wrapper around an OpenAI-compatible chat completions endpoint.

    Configuration precedence: explicit constructor argument, then the matching
    `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` shell environment variable,
    then the same variable from a `.env` file in the current working directory,
    then a default that points at this project's local llama.cpp backend.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # A real shell environment always wins over a local .env file. This lets
        # deployments and CI override developer-machine configuration safely.
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", _DEFAULT_BASE_URL)
        self.api_key = api_key or os.environ.get("LLM_API_KEY", _DEFAULT_API_KEY)
        self.model = model or os.environ.get("LLM_MODEL", _DEFAULT_MODEL)
        self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

    async def chat(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletionMessage:
        """Runs one chat completion call and returns the response message.

        `extra_body` reaches backend-specific request fields the OpenAI SDK doesn't
        model itself. For example, a Qwen3-family model behind llama.cpp can use
        `{"chat_template_kwargs": {"enable_thinking": False}}` to suppress its
        `reasoning_content` preamble when a caller wants clean structured output.
        """
        optional: dict[str, Any] = {}
        if tools is not None:
            optional["tools"] = tools
        if tool_choice is not None:
            optional["tool_choice"] = tool_choice
        if extra_body is not None:
            optional["extra_body"] = extra_body

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            **optional,
        )
        message: ChatCompletionMessage = response.choices[0].message
        return message

    async def generate_text(
        self,
        system_instruction: str,
        prompt: str,
        *,
        temperature: float = 0.2,
    ) -> str:
        """Generate text from a system instruction and a user prompt."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
        response = await self.chat(messages, temperature=temperature)
        if not response.content:
            raise RuntimeError("The LLM returned no text content")
        return response.content

    async def aclose(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
