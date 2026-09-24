"""Public A2A reference-kit API."""

from a2a.helpers import (
    get_message_text,
    new_data_message,
    new_raw_message,
    new_text_message,
    new_url_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentExtension,
    AgentSkill,
    Message,
    Part,
    Role,
    SecurityRequirement,
    SecurityScheme,
    StreamResponse,
    Task,
    TaskState,
)

from agent_protocols.a2a.cards import create_agent_card, create_skill
from agent_protocols.a2a.client import A2AClient
from agent_protocols.a2a.idempotency import MessageIdempotencyOptions
from agent_protocols.a2a.server import create_server
from agent_protocols.a2a.signing import (
    AgentCardVerificationError,
    CardKeySource,
    ResolvedCardKey,
    VerifiedAgentCard,
    sign_agent_card,
    verify_agent_card,
)

__all__ = [
    "A2AClient",
    "AgentCard",
    "AgentCardVerificationError",
    "AgentExecutor",
    "AgentExtension",
    "AgentSkill",
    "CardKeySource",
    "EventQueue",
    "InMemoryTaskStore",
    "Message",
    "MessageIdempotencyOptions",
    "Part",
    "RequestContext",
    "ResolvedCardKey",
    "Role",
    "SecurityRequirement",
    "SecurityScheme",
    "StreamResponse",
    "Task",
    "TaskState",
    "TaskUpdater",
    "VerifiedAgentCard",
    "create_agent_card",
    "create_server",
    "create_skill",
    "get_message_text",
    "new_data_message",
    "new_raw_message",
    "new_text_message",
    "new_url_message",
    "sign_agent_card",
    "verify_agent_card",
]
