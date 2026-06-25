from __future__ import annotations

import httpx
import pytest

from agentbox_client.client import AgentBoxClient
from app.modules.workspace.testing.fake_agentbox import create_fake_agentbox_app


async def _client(app) -> AgentBoxClient:
    transport = httpx.ASGITransport(app=app)
    http = httpx.AsyncClient(transport=transport, base_url="http://fake-agentbox")
    return AgentBoxClient(base_url="http://fake-agentbox", api_key="k", client=http)


@pytest.mark.asyncio
async def test_fake_agentbox_satisfies_client_contract():
    """The real AgentBoxClient drives the fake end-to-end: sandbox, session,
    exec_command (real subprocess), execute_python, and file persistence."""
    client = await _client(create_fake_agentbox_app())
    try:
        summary = await client.ensure_sandbox("sb1", env={"FOO": "bar"})
        assert summary.ready and summary.status == "RUNNING"

        session = await client.create_session("sb1", "s1", cwd="/workspace")
        assert session.cwd == "/workspace"

        # Shell command really runs.
        res = await client.exec_command("sb1", "s1", cmd="echo hello-world")
        assert res.success and res.exit_code == 0
        assert "hello-world" in res.stdout

        # Session env is injected.
        env_res = await client.exec_command("sb1", "s1", cmd="echo $FOO")
        assert "bar" in env_res.stdout

        # Python really runs.
        py = await client.execute_python("sb1", "s1", code="print(6 * 7)")
        assert py.status == "completed"
        assert "42" in py.stdout

        # Files persist within the sandbox across commands.
        await client.exec_command("sb1", "s1", cmd="echo persisted > note.txt")
        cat = await client.exec_command("sb1", "s1", cmd="cat note.txt")
        assert "persisted" in cat.stdout

        # Non-zero exit is reported.
        fail = await client.exec_command("sb1", "s1", cmd="exit 3")
        assert not fail.success and fail.exit_code == 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fake_agentbox_get_and_delete_sandbox():
    client = await _client(create_fake_agentbox_app())
    try:
        assert await client.get_sandbox("missing") is None
        await client.ensure_sandbox("sb2")
        assert (await client.get_sandbox("sb2")) is not None
        deleted = await client.delete_sandbox("sb2")
        assert deleted.deleted is True
        assert await client.get_sandbox("sb2") is None
    finally:
        await client.close()
