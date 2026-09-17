# Agent Protocols Reference Kit

Reusable Python implementations of agent communication protocols, with runnable examples, tests,
and documentation that identifies the targeted specifications and current implementation limits.

This is a research-oriented reference kit. Version `0.1.0` is suitable for experiments and
prototypes; its APIs may change and should not yet be treated as a production security baseline.

## Implemented protocol integrations

| Protocol | Role in an agent system | Current coverage | Install extra | Guide |
| --- | --- | --- | --- | --- |
| Model Context Protocol (MCP) | Agent-to-tool and agent-to-context access | Tools, resources, prompts, stdio client/server flow, and an LLM tool bridge | `mcp` | [MCP](docs/mcp.md) |
| Agent2Agent Protocol (A2A) | Task-oriented communication between agents | Agent Cards, messages, tasks, HTTP client/server flow, and example bearer authentication | `a2a` | [A2A](docs/a2a.md) |
| Agent Network Protocol (ANP) | Open-network identity, discovery, and authenticated agent interfaces | `did:wba` identity, Agent Descriptions, discovery, authentication, and OpenRPC/JSON-RPC interfaces | `anp` | [ANP](docs/anp.md) |
| Agora | Protocol discovery, agreement, and conversation between agents | Protocol Documents, discovery, validation, selection, negotiation, HTTP exchange, and conversations | `agora` | [Agora](docs/agora.md) |

Coverage is intentionally explicit rather than implying complete implementation of every protocol
feature. Each guide records the authoritative sources, implemented behavior, and known limitations.

## Requirements

- Python 3.11 or newer
- `pip` or [`uv`](https://docs.astral.sh/uv/)
- Git when installing directly from the repository

## Use as a dependency

Install only the protocol integration an application needs. Pin a release tag or commit for
reproducible experiments:

```bash
pip install "agent-protocols-reference-kit[mcp] @ git+https://github.com/piyumipat/agent-protocols-reference-kit.git@<commit-or-tag>"
```

With `uv`:

```bash
uv add "agent-protocols-reference-kit[mcp] @ git+https://github.com/piyumipat/agent-protocols-reference-kit.git@<commit-or-tag>"
```

Replace `mcp` with `a2a`, `anp`, or `agora`. Use `all` to install every integration.

## Set up a development checkout

```bash
git clone https://github.com/piyumipat/agent-protocols-reference-kit.git
cd agent-protocols-reference-kit
uv sync --all-extras
```

The equivalent protocol-specific installation with `pip` is `pip install ".[mcp]"`.

## Optional LLM configuration

The protocol APIs and deterministic tests do not require an LLM. Some examples use `LLMClient`
with an OpenAI-compatible chat-completions endpoint, including local servers such as llama.cpp.

Copy the example configuration and replace its placeholders:

```bash
cp .env.example .env
```

```dotenv
LLM_BASE_URL=http://localhost:8080/v1
LLM_API_KEY=not-needed
LLM_MODEL=your-model-name
```

Shell variables override `.env`, and constructor arguments override both. No request is made until
an LLM-backed workflow runs.

## Run the examples

### MCP

Run the deterministic client/server example without an LLM:

```bash
uv run python examples/mcp/client.py
```

Applications can import the same public components:

```python
from agent_protocols.mcp import MCPClient, create_server, run_stdio
```

The LLM-backed example discovers the calculator tool and lets the model choose the tool call:

```bash
uv run python examples/mcp/agent.py
```

### A2A

The example runs a local coordinator and two specialist agents. Start each agent in a separate
terminal, then run the user process:

```bash
uv run python -m examples.a2a.facts_agent
uv run python -m examples.a2a.review_agent
uv run python -m examples.a2a.local_agent
uv run python -m examples.a2a.user
```

### ANP

Generate local demonstration identities and TLS material, start each agent in a separate terminal,
then run the user process:

```bash
uv run python -m examples.anp.setup
uv run python -m examples.anp.facts_agent
uv run python -m examples.anp.review_agent
uv run python -m examples.anp.coordinator_agent
uv run python -m examples.anp.user
```

The generated keys and identities remain under the ignored `.anp-demo/` directory.

### Agora

Start the calculator and both requesters in separate terminals:

```bash
uv run python -m examples.agora.calculator_agent
uv run python -m examples.agora.shared_requester_agent
uv run python -m examples.agora.requester_agent
```

One requester uses a pre-shared single-round Protocol Document. The other discovers the capability,
creates and negotiates a multi-round Protocol Document, and continues a conversation.

## Development checks

```bash
uv run pytest
uv run ruff check .
uv run mypy src examples tests
```

## Contributing

Issues and pull requests are welcome. Changes should preserve the documented specification boundary,
include tests for behavior changes, and update the relevant protocol guide when coverage changes.

## Maintainer

[Piyumi Pathirathna](https://github.com/piyumipat)

## License

MIT — see [`LICENSE`](LICENSE).
