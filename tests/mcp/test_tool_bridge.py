from __future__ import annotations

from mcp.types import Tool

from agent_protocols.mcp import to_openai_tool_schema, to_openai_tool_schemas


def make_tool(name: str, description: str | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )


def test_converts_tool_schema() -> None:
    schema = to_openai_tool_schema(make_tool("lookup", "Look up a value"))

    assert schema == {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a value",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }


def test_converts_missing_description_to_empty_string() -> None:
    schema = to_openai_tool_schema(make_tool("lookup"))

    assert schema["function"]["description"] == ""


def test_preserves_tool_order() -> None:
    schemas = to_openai_tool_schemas(
        [make_tool("first"), make_tool("second"), make_tool("third")]
    )

    assert [schema["function"]["name"] for schema in schemas] == [
        "first",
        "second",
        "third",
    ]
