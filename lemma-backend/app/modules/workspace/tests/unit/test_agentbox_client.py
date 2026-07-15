from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from agentbox_client import AgentBoxClient
from agentbox_client.generated.manager.models import ExecCommandRequest
from agentbox_client.timeouts import (
    exec_command_http_timeout,
    write_stdin_http_timeout,
)


@pytest.mark.asyncio
async def test_ensure_sandbox_sends_minimal_payload():
    payloads: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "sandbox": {
                    "id": "user-1",
                    "ready": True,
                    "status": "RUNNING",
                },
            },
        )

    client = AgentBoxClient(base_url="https://agentbox.test", api_key="test-key")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://agentbox.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client.ensure_sandbox("user-1")
    finally:
        await client.client.aclose()

    assert response.status == "RUNNING"
    assert payloads == [{"env": {}}]


@pytest.mark.asyncio
async def test_ensure_sandbox_uses_put():
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "sandbox": {
                    "id": "user-1",
                    "ready": True,
                    "status": "RUNNING",
                },
            },
        )

    client = AgentBoxClient(base_url="https://agentbox.test", api_key="test-key")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://agentbox.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client.ensure_sandbox("user-1")
    finally:
        await client.client.aclose()

    assert response.ready is True
    assert response.id == "user-1"
    assert calls == [("PUT", "/sandboxes/user-1")]


@pytest.mark.asyncio
async def test_get_sandbox_reads_minimal_summary():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "id": "user-1",
                "ready": True,
                "status": "RUNNING",
            },
        )

    client = AgentBoxClient(base_url="https://agentbox.test", api_key="test-key")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://agentbox.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client.get_sandbox("user-1")
    finally:
        await client.client.aclose()

    assert response is not None
    assert response.id == "user-1"
    assert response.status == "RUNNING"


@pytest.mark.asyncio
async def test_suspend_sandbox_uses_non_destructive_endpoint():
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"sandbox_id": "user-1", "suspended": True},
        )

    client = AgentBoxClient(base_url="https://agentbox.test", api_key="test-key")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://agentbox.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client.suspend_sandbox("user-1")
    finally:
        await client.client.aclose()

    assert response.suspended is True
    assert calls == [("POST", "/sandboxes/user-1/suspend")]


def test_exec_command_without_yield_uses_command_timeout_plus_grace():
    assert (
        exec_command_http_timeout(
            client_timeout_seconds=120,
            command_timeout_seconds=300,
            yield_time_ms=None,
        )
        == 305
    )


def test_exec_command_with_yield_uses_short_http_timeout():
    assert (
        exec_command_http_timeout(
            client_timeout_seconds=300,
            command_timeout_seconds=300,
            yield_time_ms=1000,
        )
        == 11
    )


def test_exec_command_with_yield_keeps_minimum_http_timeout():
    assert (
        exec_command_http_timeout(
            client_timeout_seconds=300,
            command_timeout_seconds=3,
            yield_time_ms=100,
        )
        == 8
    )


def test_write_stdin_with_yield_uses_short_http_timeout():
    assert write_stdin_http_timeout(yield_time_ms=1000) == 11


def test_exec_command_request_rejects_removed_runtime_fields():
    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "shell": "/bin/sh"})

    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "login": True})
