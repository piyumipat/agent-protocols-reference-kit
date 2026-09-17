"""ANP 1.1 Agent Description publication and active discovery (ANP-07/08)."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp

from agent_protocols.anp.identity import verify_e1_document

JSONDocument = dict[str, Any]
JSONFetcher = Callable[[str], Awaitable[JSONDocument]]

_AD_CONTEXT = "https://agent-network-protocol.com/ad#"
_DID_PATTERN = re.compile(
    r"^did:wba:(?P<domain>[A-Za-z0-9.-]+(?:%3A[0-9]+)?):"
    r"(?P<path>[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*):"
    r"e1_[A-Za-z0-9_-]{43}$"
)


class DiscoveryError(ValueError):
    """A fetched ANP discovery, description, or DID document is inconsistent."""


@dataclass(frozen=True)
class VerifiedAgent:
    """An Agent Description linked from a verified e1 DID Document."""

    description_url: str
    description: JSONDocument
    did_document: JSONDocument

    @property
    def did(self) -> str:
        return str(self.description["did"])


def _https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.port != 0
            and parts.username is None
            and parts.password is None
            and not parts.fragment
        )
    except ValueError:
        return False


def _origin(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    return (str(parts.hostname).lower(), parts.port)


def _domain_origin(domain: str) -> str:
    url = f"https://{domain}"
    if not _https_url(url):
        raise ValueError("Expected a DNS domain, optionally with a port")
    parts = urlsplit(url)
    if not parts.hostname or parts.path or parts.query:
        raise ValueError("Expected a DNS domain, optionally with a port")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return url.rstrip("/")
    raise ValueError("ANP discovery requires a DNS domain, not an IP address")


def did_document_url(did: str) -> str:
    """Derive the HTTPS `did.json` location for an ANP-03 e1 path DID."""
    match = _DID_PATTERN.fullmatch(did)
    if not match:
        raise DiscoveryError("Expected a released ANP-03 e1 path DID")
    path = match.group("path").split(":")
    if any(segment in {".", ".."} for segment in path):
        raise DiscoveryError("DID path contains a dot segment")
    domain = unquote(match.group("domain"))
    try:
        origin = _domain_origin(domain)
    except ValueError as exc:
        raise DiscoveryError("DID domain is invalid") from exc
    return f"{origin}/{'/'.join((*path, did.rsplit(':', 1)[-1]))}/did.json"


def create_agent_description(
    *,
    url: str,
    name: str,
    did: str,
    description: str | None = None,
    interfaces: Sequence[Mapping[str, Any]] = (),
) -> JSONDocument:
    """Build an ANP-07 description for a published, e1-bound agent.

    The `auto` security location follows ANP-07's vocabulary for the ANP-03
    signature-header first request and Authorization bearer follow-up.
    """
    if not _https_url(url):
        raise ValueError("Agent Description URL must be HTTPS")
    if not name.strip():
        raise ValueError("Agent name is required")
    did_document_url(did)
    document: JSONDocument = {
        "protocolType": "ANP",
        "protocolVersion": "1.0.0",
        "type": "AgentDescription",
        "url": url,
        "name": name,
        "did": did,
        "securityDefinitions": {"didwba_sc": {"scheme": "didwba", "in": "auto"}},
        "security": "didwba_sc",
        "interfaces": [dict(interface) for interface in interfaces],
    }
    if description is not None:
        document["description"] = description
    return document


def create_discovery_collection(
    domain: str,
    descriptions: Sequence[Mapping[str, Any]],
    *,
    page_url: str | None = None,
    next_url: str | None = None,
) -> JSONDocument:
    """Build the ANP-08 JSON-LD page to publish at the well-known path."""
    origin = _domain_origin(domain)
    well_known = f"{origin}/.well-known/agent-descriptions"
    current = page_url or well_known
    if not _https_url(current) or _origin(current) != _origin(well_known):
        raise ValueError("Discovery page must be HTTPS on the requested domain")
    if next_url is not None and (
        not _https_url(next_url) or _origin(next_url) != _origin(well_known)
    ):
        raise ValueError("Next discovery page must stay on the requested domain")

    items: list[JSONDocument] = []
    for description in descriptions:
        url, name = description.get("url"), description.get("name")
        if (
            not isinstance(url, str)
            or not _https_url(url)
            or _origin(url) != _origin(well_known)
        ):
            raise ValueError(
                "Each Agent Description must be HTTPS on the discovery domain"
            )
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each Agent Description needs a name")
        items.append({"@type": "ad:AgentDescription", "name": name, "@id": url})

    page: JSONDocument = {
        "@context": {
            "@vocab": "https://schema.org/",
            "did": "https://w3id.org/did#",
            "ad": _AD_CONTEXT,
        },
        "@type": "CollectionPage",
        "url": current,
        "items": items,
    }
    if next_url is not None:
        page["next"] = next_url
    return page


def _validate_description(description: Mapping[str, Any], url: str) -> str:
    if (
        description.get("protocolType") != "ANP"
        or description.get("protocolVersion") != "1.0.0"
        or description.get("type") != "AgentDescription"
        or description.get("url") != url
        or not isinstance(description.get("name"), str)
        or not description["name"].strip()
    ):
        raise DiscoveryError(
            "Fetched Agent Description does not match ANP-07 or its URL"
        )
    did = description.get("did")
    if not isinstance(did, str):
        raise DiscoveryError("Initial discovery path requires an agent DID")
    did_document_url(did)
    definitions = description.get("securityDefinitions")
    active = description.get("security")
    if not isinstance(definitions, dict) or not isinstance(active, str):
        raise DiscoveryError("Agent Description lacks an active security definition")
    scheme = definitions.get(active)
    if not isinstance(scheme, dict) or scheme.get("scheme") != "didwba":
        raise DiscoveryError("Initial path requires active did:wba security")
    location = scheme.get("in")
    if location == "auto":
        if "name" in scheme:
            raise DiscoveryError("ANP-07 auto security must omit a header name")
    elif location == "header":
        if not isinstance(scheme.get("name"), str) or not scheme["name"]:
            raise DiscoveryError("ANP-07 header security needs a header name")
    else:
        raise DiscoveryError("Initial path requires did:wba header or auto security")
    if "interfaces" in description and not isinstance(description["interfaces"], list):
        raise DiscoveryError("Agent Description interfaces must be an array")
    return did


def verify_agent_association(
    description: Mapping[str, Any],
    description_url: str,
    did_document: Mapping[str, Any],
) -> bool:
    """Verify the e1 DID proof and its exact AgentDescription service link."""
    try:
        did = _validate_description(description, description_url)
    except DiscoveryError:
        return False
    if not verify_e1_document(did_document, did):
        return False
    services = did_document.get("service")
    return isinstance(services, list) and any(
        isinstance(service, dict)
        and service.get("type") == "AgentDescription"
        and service.get("serviceEndpoint") == description_url
        for service in services
    )


async def _fetch_https_json(url: str) -> JSONDocument:
    timeout = aiohttp.ClientTimeout(total=10)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(
            url,
            headers={"Accept": "application/json, application/ld+json"},
            ssl=True,
            allow_redirects=False,
        ) as response,
    ):
        if response.status != 200:
            raise DiscoveryError(f"Expected HTTP 200 for {url}, got {response.status}")
        if (
            response.content_type != "application/json"
            and not response.content_type.endswith("+json")
        ):
            raise DiscoveryError("Expected a JSON discovery document")
        data = json.loads(await response.text())
    if not isinstance(data, dict):
        raise DiscoveryError("Expected a JSON object")
    return data


async def discover_agents(
    domain: str, *, fetch_json: JSONFetcher | None = None
) -> list[VerifiedAgent]:
    """Follow ANP-08 pages and verify each ANP-07 agent through ANP-03.

    The default fetcher uses HTTPS with TLS verification and does not follow
    redirects. `fetch_json` lets tests or applications provide their own transport.
    """
    origin = _domain_origin(domain)
    first_page = f"{origin}/.well-known/agent-descriptions"
    fetch = fetch_json or _fetch_https_json
    pending: str | None = first_page
    visited_pages: set[str] = set()
    description_names: dict[str, str] = {}

    while pending is not None:
        if pending in visited_pages:
            raise DiscoveryError("Discovery pagination contains a cycle")
        if not _https_url(pending) or _origin(pending) != _origin(first_page):
            raise DiscoveryError("Discovery pagination left the requested domain")
        visited_pages.add(pending)
        page = await fetch(pending)
        context = page.get("@context")
        items = page.get("items")
        if (
            not isinstance(context, dict)
            or context.get("ad") != _AD_CONTEXT
            or page.get("@type") != "CollectionPage"
            or page.get("url") != pending
            or not isinstance(items, list)
        ):
            raise DiscoveryError("Fetched discovery page does not match ANP-08")
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "ad:AgentDescription":
                raise DiscoveryError("Invalid Agent Description item")
            url, name = item.get("@id"), item.get("name")
            if (
                not isinstance(url, str)
                or not _https_url(url)
                or _origin(url) != _origin(first_page)
            ):
                raise DiscoveryError("Agent Description item left the discovery domain")
            if not isinstance(name, str) or not name.strip():
                raise DiscoveryError("Agent Description item lacks a name")
            if url in description_names and description_names[url] != name:
                raise DiscoveryError("Agent Description item has conflicting names")
            description_names[url] = name
        next_page = page.get("next")
        if next_page is not None and not isinstance(next_page, str):
            raise DiscoveryError("Invalid next discovery page URL")
        pending = next_page

    verified: list[VerifiedAgent] = []
    for url, listed_name in description_names.items():
        description = await fetch(url)
        did = _validate_description(description, url)
        if description["name"] != listed_name:
            raise DiscoveryError("Agent Description name differs from discovery item")
        did_document = await fetch(did_document_url(did))
        if not verify_agent_association(description, url, did_document):
            raise DiscoveryError("DID Document does not link to this Agent Description")
        verified.append(VerifiedAgent(url, description, did_document))
    return verified
