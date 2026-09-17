"""Agent Card and Part coverage for the A2A helpers."""

from a2a.helpers import get_data_parts, get_raw_parts, get_url_parts
from a2a.types.a2a_pb2 import AgentExtension, Role

from agent_protocols.a2a import (
    create_agent_card,
    create_skill,
    get_message_text,
    new_data_message,
    new_raw_message,
    new_text_message,
    new_url_message,
)


def test_agent_card_declares_v1_jsonrpc_capabilities_and_extensions() -> None:
    extension = AgentExtension(uri="urn:example:trace", required=False)
    skill = create_skill(
        "summarize",
        "Summarize",
        "Summarize supplied material.",
        tags=["writing"],
        examples=["Summarize this report"],
        input_modes=["text/plain", "application/json"],
        output_modes=["text/plain"],
    )
    card = create_agent_card(
        "Writer",
        "A writing agent.",
        "https://agent.example/rpc",
        [skill],
        streaming=True,
        tenant="research",
        extensions=[extension],
    )

    interface = card.supported_interfaces[0]
    assert interface.protocol_binding == "JSONRPC"
    assert interface.protocol_version == "1.0"
    assert interface.tenant == "research"
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is False
    assert card.capabilities.extensions[0] == extension
    assert card.skills[0].input_modes == ["text/plain", "application/json"]


def test_text_data_url_and_raw_parts_round_trip_without_fetching_url() -> None:
    text = new_text_message("hello", role=Role.ROLE_USER)
    data = new_data_message({"answer": 42}, role=Role.ROLE_USER)
    url = new_url_message(
        "https://files.example/report.pdf",
        media_type="application/pdf",
        filename="report.pdf",
        role=Role.ROLE_USER,
    )
    raw = new_raw_message(
        b"content",
        media_type="application/octet-stream",
        filename="sample.bin",
        role=Role.ROLE_USER,
    )

    assert get_message_text(text) == "hello"
    assert get_data_parts(data.parts) == [{"answer": 42.0}]
    assert get_url_parts(url.parts) == ["https://files.example/report.pdf"]
    assert get_raw_parts(raw.parts) == [b"content"]
    assert url.parts[0].filename == "report.pdf"
    assert raw.parts[0].filename == "sample.bin"
