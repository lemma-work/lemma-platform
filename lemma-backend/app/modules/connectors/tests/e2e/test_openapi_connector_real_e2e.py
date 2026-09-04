"""The `openapi` kind against a real HTTP API that checks its credentials.

This kind had no end-to-end test at all. Its unit tests build descriptors from a
spec on disk and its executor tests drive a transport double, so the two halves
were each proven and the join between them -- fetch a spec over HTTP, turn it
into operations, then call the API those operations describe -- was not.

The join is where a tenant-configured connector actually lives: the spec, the
server and the credential all come from whoever installed it, and none of them
is known when the code is written. So this runs a real server on a real socket,
discovers from its real spec, and executes against it with each credential shape
the executor claims to accept, at a server in a position to reject them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from typing import Any

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionNotFoundError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.services.execution import KindDispatcher
from app.modules.connectors.services.execution.plumbing import (
    execution_failures_translated,
)
from app.modules.connectors.services.discovery.openapi_discoverer import (
    discover_openapi,
)
from app.modules.test_support.e2e.waiters import eventually

os.environ.setdefault("CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS", "true")

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_TOKEN = "widget-api-key"
_WIDGETS = [{"id": "w1", "name": "sprocket"}, {"id": "w2", "name": "flange"}]


@pytest.fixture(scope="module", autouse=True)
def _reachable_local_server():
    """Loopback stands in for a tenant's own API; production refuses it.

    Module-scoped because discovery happens in a module-scoped fixture, which
    runs outside any function-scoped patch -- the guard re-checks its target on
    every fetch, so a function-scoped override leaves the discovery itself
    refused. Scoped to this file rather than the suite either way, so the tests
    that assert the guard *does* refuse a private target keep working.
    """
    from app.core.config import settings

    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "connector_allow_private_network_targets", True)
    yield
    patch.undo()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spec(port: int) -> dict[str, Any]:
    ok = {
        "description": "ok",
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
    return {
        "openapi": "3.0.0",
        "info": {"title": "Widgets", "version": "1.0.0"},
        "servers": [{"url": f"http://127.0.0.1:{port}"}],
        "paths": {
            "/widgets": {
                "get": {
                    "operationId": "widgets/list",
                    "summary": "List widgets",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": ok},
                },
                "post": {
                    "operationId": "widgets/create",
                    "summary": "Create a widget",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"201": ok},
                },
            },
            "/widgets/{widget_id}": {
                "get": {
                    "operationId": "widgets/get",
                    "summary": "Get one widget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": ok},
                }
            },
        },
    }


def _app(port: int):
    """Serves its own spec, and refuses anything without the bearer token.

    Echoes the token it saw back on the list route, so a test can prove *which*
    credential arrived rather than only that some request succeeded.
    """
    spec_bytes = json.dumps(_spec(port)).encode()

    async def app(scope, receive, send):
        async def reply(status: int, body: bytes) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})

        path = scope["path"]
        method = scope["method"]
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

        if path == "/openapi.json":
            await reply(200, spec_bytes)
            return

        authorization = headers.get("authorization", "")
        # RFC 7235 makes the scheme case-insensitive, and real providers honour
        # that -- GitHub's App tokens come back typed `bearer`. A test server
        # that insisted on `Bearer` would be stricter than the thing it stands
        # in for, and would fail the executor for being correct.
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or value != _TOKEN:
            await reply(401, b'{"error":"unauthorized"}')
            return

        if path == "/widgets" and method == "GET":
            await reply(
                200, json.dumps({"widgets": _WIDGETS, "seen": authorization}).encode()
            )
            return
        if path == "/widgets" and method == "POST":
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            await reply(201, json.dumps({"created": json.loads(body)}).encode())
            return
        if path == "/widgets/w1":
            await reply(200, json.dumps(_WIDGETS[0]).encode())
            return
        if path == "/widgets/forbidden":
            await reply(403, b'{"error":"forbidden"}')
            return
        await reply(404, b'{"error":"no such widget"}')

    return app


@pytest_asyncio.fixture(scope="module")
async def api():
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(
        _app(port), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    async def probe() -> None:
        if task.done():
            raise RuntimeError(f"server failed to start: {task.exception()}")
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

    await eventually(
        label=f"widget API on {port}",
        probe=probe,
        done=lambda _: True,
        retry_exceptions=(OSError,),
        timeout_seconds=10.0,
        interval_seconds=0.05,
    )
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(task, timeout=5)


@pytest_asyncio.fixture(scope="module")
async def discovered(api):
    """The operation set as an install of this connector would really get it."""
    found = await discover_openapi(
        connection_config={"spec_url": f"{api}/openapi.json"}
    )
    return {item.name: item for item in found}


def _dispatcher():
    return KindDispatcher(
        build_kind_registry(composio_gateway=AsyncMock(), package_gateway=AsyncMock())
    )


async def _run(api, discovered, name, payload, credentials):
    """Through the dispatcher, inside the translator the service wraps it in.

    The executor raises its own transport error. Turning that into a domain
    error the caller can act on happens one layer up, in
    `execution_failures_translated`, which `ConnectorOperationService` wraps the
    dispatch in -- so a test that drove the executor alone would prove the call
    worked and nothing about how a 401 or a 404 is reported.
    """
    dispatcher = _dispatcher()
    request = dispatcher.build_request(
        connector_id="openapi",
        kind=ConnectorKind.HTTP,
        operation=ResolvedOperation(name=name, execution=discovered[name].execution),
        payload=payload,
        credentials=credentials or {},
        config={"spec_url": f"{api}/openapi.json"},
    )
    with execution_failures_translated():
        return await dispatcher.execute(request)


class TestDiscoveringFromALiveSpec:
    async def test_every_operation_in_the_spec_is_discovered(self, discovered):
        assert set(discovered) == {"widgets_list", "widgets_create", "widgets_get"}

    async def test_the_descriptor_carries_the_route_it_will_call(self, discovered):
        execution = discovered["widgets_get"].execution
        assert execution["method"] == "GET"
        assert execution["path"] == "/widgets/{widget_id}"

    async def test_the_input_schema_comes_from_the_spec(self, discovered):
        properties = discovered["widgets_create"].input_schema["properties"]
        assert "body" in properties
        assert properties["body"]["properties"]["name"]["type"] == "string"

    async def test_a_path_parameter_is_required(self, discovered):
        schema = discovered["widgets_get"].input_schema
        assert "widget_id" in schema.get("required", [])


class TestApiKeyAuth:
    """A tenant that pastes a static key: the `API_KEY` scheme."""

    async def test_the_key_reaches_the_server_as_a_bearer_token(self, api, discovered):
        result = await _run(api, discovered, "widgets_list", {}, {"api_key": _TOKEN})
        assert result["seen"] == f"Bearer {_TOKEN}"
        assert [w["id"] for w in result["widgets"]] == ["w1", "w2"]

    async def test_a_wrong_key_is_reported_as_unauthorized(self, api, discovered):
        with pytest.raises(OperationExecutionUnauthorizedError):
            await _run(api, discovered, "widgets_list", {}, {"api_key": "nope"})


class TestOAuthAuth:
    """A tenant that signs in: the `OAUTH2` scheme, same wire, different source."""

    async def test_an_access_token_reaches_the_server(self, api, discovered):
        result = await _run(
            api,
            discovered,
            "widgets_list",
            {},
            {"access_token": _TOKEN, "token_type": "Bearer"},
        )
        assert result["seen"] == f"Bearer {_TOKEN}"

    async def test_a_lowercase_token_type_still_authenticates(self, api, discovered):
        """GitHub's App tokens come back as `bearer`, and HTTP auth schemes are
        case-insensitive -- so the executor must not normalise it away."""
        result = await _run(
            api,
            discovered,
            "widgets_list",
            {},
            {"access_token": _TOKEN, "token_type": "bearer"},
        )
        assert result["seen"] == f"bearer {_TOKEN}"

    async def test_an_expired_token_is_reported_as_unauthorized(self, api, discovered):
        with pytest.raises(OperationExecutionUnauthorizedError):
            await _run(api, discovered, "widgets_list", {}, {"access_token": "expired"})

    async def test_no_credential_at_all_is_unauthorized(self, api, discovered):
        with pytest.raises(OperationExecutionUnauthorizedError):
            await _run(api, discovered, "widgets_list", {}, None)


class TestTheRequestIsBuiltFromTheSpec:
    async def test_a_path_parameter_is_substituted(self, api, discovered):
        result = await _run(
            api, discovered, "widgets_get", {"widget_id": "w1"}, {"api_key": _TOKEN}
        )
        assert result["id"] == "w1"

    async def test_a_request_body_is_sent_as_json(self, api, discovered):
        result = await _run(
            api,
            discovered,
            "widgets_create",
            {"body": {"name": "cog"}},
            {"api_key": _TOKEN},
        )
        assert result["created"] == {"name": "cog"}


class TestUpstreamFailuresAreClassified:
    async def test_a_404_is_not_found_rather_than_a_generic_failure(
        self, api, discovered
    ):
        with pytest.raises(OperationExecutionNotFoundError):
            await _run(
                api,
                discovered,
                "widgets_get",
                {"widget_id": "missing"},
                {"api_key": _TOKEN},
            )

    async def test_a_403_is_access_denied(self, api, discovered):
        with pytest.raises(OperationExecutionAccessDeniedError):
            await _run(
                api,
                discovered,
                "widgets_get",
                {"widget_id": "forbidden"},
                {"api_key": _TOKEN},
            )
