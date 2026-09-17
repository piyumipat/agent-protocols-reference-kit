"""Real local HTTPS publication, discovery, and authenticated JSON-RPC calls."""

from __future__ import annotations

import json

from tests.anp.greeting_fixture import local_greeting_agent, run_greeting_path


async def test_greeting_signed_then_bearer_over_local_https() -> None:
    did, signed, bearer = await run_greeting_path()
    assert did.startswith("did:wba:agents.example.test%3A")
    assert ":e1_" in did
    assert signed == "Hello, Pat."
    assert bearer == "Hello, Team."


async def test_rpc_rejects_missing_and_tampered_auth_and_handles_batch() -> None:
    async with local_greeting_agent() as local:
        agents = await local.client.discover(local.domain)
        assert len(agents) == 1
        interface_url = agents[0].description["interfaces"][0]["url"]
        async with local.session.get(interface_url) as response:
            assert response.status == 401

        body = json.dumps(
            {"jsonrpc": "2.0", "method": "greet", "params": {"name": "Pat"}, "id": 1},
            separators=(",", ":"),
        ).encode()
        async with local.session.post(
            local.rpc_url, data=body, headers={"Content-Type": "application/json"}
        ) as response:
            assert response.status == 401

        other_headers = {"Content-Type": "application/json"}
        other_headers.update(
            local.unauthorized_authenticator.get_auth_header(
                local.rpc_url,
                force_new=True,
                method="POST",
                headers=other_headers,
                body=body,
            )
        )
        async with local.session.post(
            local.rpc_url, data=body, headers=other_headers
        ) as response:
            assert response.status == 403

        signed_headers = {"Content-Type": "application/json"}
        signed_headers.update(
            local.authenticator.get_auth_header(
                local.rpc_url,
                force_new=True,
                method="POST",
                headers=signed_headers,
                body=body,
            )
        )
        async with local.session.post(
            local.rpc_url, data=body + b" ", headers=signed_headers
        ) as response:
            assert response.status == 401

        batch = json.dumps(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "greet",
                    "params": {"name": "Pat"},
                    "id": 1,
                },
                {"jsonrpc": "2.0", "method": "greet", "params": {"name": "Team"}},
                {"jsonrpc": "2.0", "method": "missing", "id": 2},
            ],
            separators=(",", ":"),
        ).encode()
        batch_headers = {"Content-Type": "application/json"}
        batch_headers.update(
            local.authenticator.get_auth_header(
                local.rpc_url,
                force_new=True,
                method="POST",
                headers=batch_headers,
                body=batch,
            )
        )
        async with local.session.post(
            local.rpc_url, data=batch, headers=batch_headers
        ) as response:
            assert response.status == 200
            result = await response.json()
            assert "Authentication-Info" in response.headers
            assert "Authorization" not in response.headers
        assert result == [
            {"jsonrpc": "2.0", "result": "Hello, Pat.", "id": 1},
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": 2,
            },
        ]
