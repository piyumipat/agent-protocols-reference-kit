from hashlib import sha1

import pytest

from agent_protocols.agora import ProtocolDocument, ProtocolRegistry

SOURCE = """name: Answer
description: Structured question answering
multiround: false
---
Requests contain {\"question\": string}.
"""


def test_protocol_document_preserves_and_hashes_the_entire_source() -> None:
    document = ProtocolDocument.parse(SOURCE)

    assert document.name == "Answer"
    assert document.description == "Structured question answering"
    assert document.multiround is False
    assert document.hash == sha1(
        SOURCE.encode(), usedforsecurity=False
    ).hexdigest()
    assert ProtocolRegistry((document,)).wellknown() == {
        document.hash: [SOURCE]
    }


@pytest.mark.parametrize(
    "source",
    [
        "name: Missing separator\ndescription: no\nmultiround: false\n",
        "name: Missing description\nmultiround: false\n---\nspec\n",
        "name: Wrong flag\ndescription: test\nmultiround: later\n---\nspec\n",
        "name: Empty spec\ndescription: test\nmultiround: false\n---\n",
        "name: [invalid\ndescription: test\nmultiround: false\n---\nspec\n",
    ],
)
def test_protocol_document_rejects_missing_required_parts(source: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        ProtocolDocument.parse(source)
