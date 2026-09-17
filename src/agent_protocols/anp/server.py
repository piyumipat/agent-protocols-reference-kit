"""ANP-07/08 publication and ANP-03-protected JSON-RPC HTTP server."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web
from anp.authentication import DidWbaVerifier, DidWbaVerifierError

from agent_protocols.anp.discovery import (
    _https_url,
    _origin,
    did_document_url,
    verify_agent_association,
)
from agent_protocols.anp.identity import verify_e1_document
from agent_protocols.anp.interface import GREET_METHOD, TextMethod, text_rpc_url

TextHandler = Callable[[str, str], str | Awaitable[str]]
GreetHandler = TextHandler
AuthorizeDID = Callable[[str], bool]


def _rpc_error(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }


async def _dispatch(
    request: Any,
    *,
    caller_did: str,
    text_method: TextMethod,
    handle_text: TextHandler,
) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return _rpc_error(-32600, "Invalid Request")
    request_id = request.get("id")
    if (
        request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
        or (
            "id" in request
            and (
                isinstance(request_id, bool)
                or not isinstance(request_id, (str, int, float, type(None)))
            )
        )
        or ("params" in request and not isinstance(request["params"], (dict, list)))
    ):
        return _rpc_error(-32600, "Invalid Request")
    notification = "id" not in request
    if request["method"] != text_method.name:
        return (
            None if notification else _rpc_error(-32601, "Method not found", request_id)
        )
    params = request.get("params")
    if isinstance(params, dict) and set(params) == {text_method.parameter_name}:
        value = params[text_method.parameter_name]
    elif isinstance(params, list) and len(params) == 1:
        value = params[0]
    else:
        value = None
    if not isinstance(value, str):
        return (
            None if notification else _rpc_error(-32602, "Invalid params", request_id)
        )
    try:
        result_text = handle_text(value, caller_did)
        if inspect.isawaitable(result_text):
            result_text = await result_text
        if not isinstance(result_text, str):
            raise TypeError("Text method handler must return a string")
    except Exception:  # noqa: BLE001 - convert host handler failures to JSON-RPC Internal error
        return (
            None if notification else _rpc_error(-32603, "Internal error", request_id)
        )
    return (
        None
        if notification
        else {"jsonrpc": "2.0", "result": result_text, "id": request_id}
    )


def create_anp_server(
    *,
    agent_description: Mapping[str, Any],
    discovery_collection: Mapping[str, Any],
    did_documents: Mapping[str, Mapping[str, Any]],
    openrpc_interface: Mapping[str, Any],
    verifier: DidWbaVerifier,
    authorize_did: AuthorizeDID,
    greet: GreetHandler | None = None,
    text_method: TextMethod | None = None,
    handle_text: TextHandler | None = None,
) -> web.Application:
    """Serve published ANP documents and one authenticated typed text method.

    `did_documents` maps DID strings to documents hosted by this server. The
    verifier resolves caller DIDs independently; `authorize_did` is mandatory.
    """
    if greet is not None and (text_method is not None or handle_text is not None):
        raise ValueError("Choose greet or a custom text method and handler")
    if greet is not None:
        selected_method, selected_handler = GREET_METHOD, greet
    elif text_method is not None and handle_text is not None:
        selected_method, selected_handler = text_method, handle_text
    else:
        raise ValueError("A typed text method and handler are required")
    description_url = agent_description.get("url")
    collection_url = discovery_collection.get("url")
    if not isinstance(description_url, str) or not isinstance(collection_url, str):
        raise TypeError("Published ANP documents need HTTPS URLs")
    if not _https_url(description_url) or not _https_url(collection_url):
        raise ValueError("Published ANP documents need HTTPS URLs")
    if _origin(description_url) != _origin(collection_url):
        raise ValueError("Published ANP documents must share an origin")
    if urlsplit(collection_url).path != "/.well-known/agent-descriptions":
        raise ValueError("Discovery collection must use the ANP-08 well-known path")

    agent_did = agent_description.get("did")
    if not isinstance(agent_did, str) or agent_did not in did_documents:
        raise ValueError("The agent's DID Document must be published")
    if not verify_agent_association(
        agent_description, description_url, did_documents[agent_did]
    ):
        raise ValueError("Agent DID Document does not link to its description")
    items = discovery_collection.get("items")
    if not isinstance(items, list) or not any(
        isinstance(item, dict) and item.get("@id") == description_url for item in items
    ):
        raise ValueError("Discovery collection must list the Agent Description")

    interfaces = agent_description.get("interfaces")
    if not isinstance(interfaces, list):
        raise TypeError("Agent Description must advertise the OpenRPC interface")
    listed = [
        item
        for item in interfaces
        if isinstance(item, dict)
        and item.get("type") == "StructuredInterface"
        and item.get("protocol") == "openrpc"
        and isinstance(item.get("url"), str)
    ]
    if len(listed) != 1:
        raise ValueError("Exactly one OpenRPC interface must be advertised")
    interface_url = listed[0]["url"]
    if not _https_url(interface_url) or _origin(interface_url) != _origin(
        description_url
    ):
        raise ValueError("OpenRPC interface must be HTTPS on the agent origin")
    rpc_url = text_rpc_url(
        dict(openrpc_interface),
        description_url=description_url,
        method=selected_method,
    )

    static_documents: dict[str, tuple[Mapping[str, Any], str]] = {
        urlsplit(collection_url).path: (discovery_collection, "application/ld+json"),
        urlsplit(description_url).path: (agent_description, "application/json"),
        urlsplit(interface_url).path: (openrpc_interface, "application/json"),
    }
    for did, document in did_documents.items():
        if not verify_e1_document(document, did):
            raise ValueError("Invalid published e1 DID Document")
        url = did_document_url(did)
        if _origin(url) != _origin(description_url):
            raise ValueError("Published DID Document must share the agent origin")
        path = urlsplit(url).path
        if path in static_documents:
            raise ValueError("ANP publication paths overlap")
        static_documents[path] = (document, "application/json")
    if urlsplit(rpc_url).path in static_documents:
        raise ValueError("RPC path overlaps a published document")

    application = web.Application()

    async def get_document(request: web.Request) -> web.Response:
        document, content_type = static_documents[request.path]
        if request.path == urlsplit(interface_url).path:
            try:
                auth = await verifier.verify_request(
                    "GET", str(request.url), dict(request.headers), b""
                )
            except DidWbaVerifierError as exc:
                return web.json_response(
                    {"error": "Authentication failed"},
                    status=exc.status_code,
                    headers=exc.headers,
                )
            if not authorize_did(str(auth["did"])):
                return web.json_response({"error": "Caller not authorized"}, status=403)
            return web.json_response(
                dict(document),
                content_type=content_type,
                headers=auth.get("response_headers", {}),
            )
        return web.json_response(dict(document), content_type=content_type)

    for path in static_documents:
        application.router.add_get(path, get_document)

    async def post_rpc(request: web.Request) -> web.Response:
        body = await request.read()
        try:
            auth = await verifier.verify_request(
                "POST", str(request.url), dict(request.headers), body
            )
        except DidWbaVerifierError as exc:
            return web.json_response(
                {"error": "Authentication failed"},
                status=exc.status_code,
                headers=exc.headers,
            )
        caller_did = str(auth["did"])
        if not authorize_did(caller_did):
            return web.json_response({"error": "Caller not authorized"}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "Expected application/json"}, status=415)
        response_headers = auth.get("response_headers", {})
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return web.json_response(
                _rpc_error(-32700, "Parse error"), headers=response_headers
            )
        if isinstance(payload, list):
            if not payload:
                return web.json_response(
                    _rpc_error(-32600, "Invalid Request"), headers=response_headers
                )
            results = [
                await _dispatch(
                    item,
                    caller_did=caller_did,
                    text_method=selected_method,
                    handle_text=selected_handler,
                )
                for item in payload
            ]
            visible = [result for result in results if result is not None]
            if not visible:
                return web.Response(status=204, headers=response_headers)
            return web.json_response(visible, headers=response_headers)
        result = await _dispatch(
            payload,
            caller_did=caller_did,
            text_method=selected_method,
            handle_text=selected_handler,
        )
        if result is None:
            return web.Response(status=204, headers=response_headers)
        return web.json_response(result, headers=response_headers)

    application.router.add_post(urlsplit(rpc_url).path, post_rpc)
    return application
