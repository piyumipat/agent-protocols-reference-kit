"""Builders for standards-shaped A2A Agent Cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
)


def create_skill(
    skill_id: str,
    name: str,
    description: str,
    *,
    tags: Sequence[str] = (),
    examples: Sequence[str] = (),
    input_modes: Sequence[str] = (),
    output_modes: Sequence[str] = (),
    security_requirements: Sequence[SecurityRequirement] = (),
) -> AgentSkill:
    """Create a discoverable skill declaration for an Agent Card."""
    return AgentSkill(
        id=skill_id,
        name=name,
        description=description,
        tags=tags,
        examples=examples,
        input_modes=input_modes,
        output_modes=output_modes,
        security_requirements=security_requirements,
    )


def create_agent_card(
    name: str,
    description: str,
    url: str,
    skills: Sequence[AgentSkill],
    *,
    version: str = "0.1.0",
    tenant: str = "",
    streaming: bool = False,
    default_input_modes: Sequence[str] = ("text/plain",),
    default_output_modes: Sequence[str] = ("text/plain",),
    security_schemes: Mapping[str, SecurityScheme] | None = None,
    security_requirements: Sequence[SecurityRequirement] = (),
    extensions: Sequence[AgentExtension] = (),
) -> AgentCard:
    """Create a v1.0 JSON-RPC Agent Card.

    Extension declarations are preserved in the card, but this kit does not
    attach extension-specific behavior to them.
    """
    return AgentCard(
        name=name,
        description=description,
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                tenant=tenant,
            )
        ],
        version=version,
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=False,
            extended_agent_card=False,
            extensions=extensions,
        ),
        security_schemes=dict(security_schemes or {}),
        security_requirements=security_requirements,
        default_input_modes=default_input_modes,
        default_output_modes=default_output_modes,
        skills=skills,
    )
