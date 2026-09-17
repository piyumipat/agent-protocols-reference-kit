from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from openai.types.chat import ChatCompletionMessage
from pytest import MonkeyPatch

from agent_protocols.utils import LLMClient


async def test_loads_dotenv_from_current_working_directory(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "LLM_BASE_URL=http://dotenv.example/v1\n"
        "LLM_API_KEY=dotenv-key\n"
        "LLM_MODEL=dotenv-model\n"
    )
    monkeypatch.chdir(tmp_path)

    async with LLMClient() as client:
        assert client.base_url == "http://dotenv.example/v1"
        assert client.api_key == "dotenv-key"
        assert client.model == "dotenv-model"


async def test_shell_environment_overrides_dotenv(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "LLM_BASE_URL=http://dotenv.example/v1\n"
        "LLM_API_KEY=dotenv-key\n"
        "LLM_MODEL=dotenv-model\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "http://environment.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")
    monkeypatch.setenv("LLM_MODEL", "environment-model")

    async with LLMClient() as client:
        assert client.base_url == "http://environment.example/v1"
        assert client.api_key == "environment-key"
        assert client.model == "environment-model"


async def test_generate_text_builds_messages_and_extracts_content(
    monkeypatch: MonkeyPatch,
) -> None:
    client = LLMClient(
        base_url="http://llm.test/v1", api_key="test-key", model="test-model"
    )
    chat = AsyncMock(
        return_value=ChatCompletionMessage(role="assistant", content="generated text")
    )
    monkeypatch.setattr(client, "chat", chat)

    result = await client.generate_text("Be concise.", "Explain A2A.", temperature=0.1)
    await client.aclose()

    assert result == "generated text"
    chat.assert_awaited_once_with(
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Explain A2A."},
        ],
        temperature=0.1,
    )
