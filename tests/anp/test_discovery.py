"""Released ANP-07/08 publication and DID-backed active discovery."""

from __future__ import annotations

from typing import Any

import pytest

from agent_protocols.anp import (
    DiscoveryError,
    create_agent_description,
    create_discovery_collection,
    create_e1_identity,
    did_document_url,
    discover_agents,
    verify_agent_association,
)


def _published_agent(name: str, slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://example.test/agents/{slug}/ad.json"
    identity = create_e1_identity(
        "example.test", path_segments=["agents", slug], agent_description_url=url
    )
    description = create_agent_description(url=url, name=name, did=identity.did)
    return description, identity.document


async def test_paginated_discovery_verifies_did_service_and_proof() -> None:
    alice, alice_did = _published_agent("Alice", "alice")
    bob, bob_did = _published_agent("Bob", "bob")
    bob["securityDefinitions"][bob["security"]] = {
        "scheme": "didwba",
        "in": "header",
        "name": "Authorization",
    }
    first = "https://example.test/.well-known/agent-descriptions"
    second = f"{first}?page=2"
    documents = {
        first: create_discovery_collection("example.test", [alice], next_url=second),
        second: create_discovery_collection("example.test", [bob], page_url=second),
        alice["url"]: alice,
        bob["url"]: bob,
        did_document_url(alice["did"]): alice_did,
        did_document_url(bob["did"]): bob_did,
    }
    fetched: list[str] = []

    async def fetch(url: str) -> dict[str, Any]:
        fetched.append(url)
        return documents[url]

    agents = await discover_agents("example.test", fetch_json=fetch)
    assert [agent.did for agent in agents] == [alice["did"], bob["did"]]
    assert fetched == [
        first,
        second,
        alice["url"],
        did_document_url(alice["did"]),
        bob["url"],
        did_document_url(bob["did"]),
    ]
    assert alice["type"] == "AgentDescription"
    assert alice["securityDefinitions"][alice["security"]] == {
        "scheme": "didwba",
        "in": "auto",
    }


async def test_discovery_rejects_unassociated_or_tampered_agent() -> None:
    alice, did_document = _published_agent("Alice", "alice")
    first = "https://example.test/.well-known/agent-descriptions"
    documents: dict[str, dict[str, Any]] = {}

    def publish(description: dict[str, Any], document: dict[str, Any]) -> None:
        documents.clear()
        documents.update(
            {
                first: create_discovery_collection("example.test", [description]),
                description["url"]: description,
                did_document_url(description["did"]): document,
            }
        )

    async def fetch(url: str) -> dict[str, Any]:
        return documents[url]

    no_service_identity = create_e1_identity(
        "example.test", path_segments=["agents", "alice"]
    )
    missing_service_ad = create_agent_description(
        url=alice["url"], name="Alice", did=no_service_identity.did
    )
    publish(missing_service_ad, no_service_identity.document)
    with pytest.raises(DiscoveryError, match="link"):
        await discover_agents("example.test", fetch_json=fetch)
    assert not verify_agent_association(
        missing_service_ad, alice["url"], no_service_identity.document
    )

    wrong_service_identity = create_e1_identity(
        "example.test",
        path_segments=["agents", "alice"],
        agent_description_url="https://example.test/agents/other/ad.json",
    )
    wrong_service_ad = create_agent_description(
        url=alice["url"], name="Alice", did=wrong_service_identity.did
    )
    publish(wrong_service_ad, wrong_service_identity.document)
    with pytest.raises(DiscoveryError, match="link"):
        await discover_agents("example.test", fetch_json=fetch)

    altered_proof = {
        **did_document,
        "proof": {**did_document["proof"], "proofValue": "zinvalid"},
    }
    publish(alice, altered_proof)
    with pytest.raises(DiscoveryError, match="link"):
        await discover_agents("example.test", fetch_json=fetch)

    product_shaped = {**alice, "type": "Product"}
    publish(product_shaped, did_document)
    with pytest.raises(DiscoveryError, match="ANP-07"):
        await discover_agents("example.test", fetch_json=fetch)


async def test_discovery_rejects_untrusted_collection_links() -> None:
    alice, _ = _published_agent("Alice", "alice")
    first = "https://example.test/.well-known/agent-descriptions"
    page = create_discovery_collection("example.test", [alice])

    async def fetch(_: str) -> dict[str, Any]:
        return page

    page["next"] = first
    with pytest.raises(DiscoveryError, match="cycle"):
        await discover_agents("example.test", fetch_json=fetch)

    page["next"] = "https://unrelated.test/.well-known/agent-descriptions"
    with pytest.raises(DiscoveryError, match="requested domain"):
        await discover_agents("example.test", fetch_json=fetch)

    page.pop("next")
    page["items"][0]["@id"] = "http://example.test/agents/alice/ad.json"
    with pytest.raises(DiscoveryError, match="discovery domain"):
        await discover_agents("example.test", fetch_json=fetch)
