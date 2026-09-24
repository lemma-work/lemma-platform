"""Host execution's ``op`` over the real link: real WebSocket, Redis and database.

docs/architecture/desktop-host-execution.md §3-4. The host is this test,
speaking the link through the whole application as ``HostLink`` does for every
other Agent Host e2e suite; the caller is the production op client, publishing
on the real Redis notice channel exactly as the host provider does from any
replica. What is proved is the hop the unit tests fake: a notice published here
reaches the session holding the socket, becomes an ``op`` frame, and the
host's ``op_ok`` comes back on the reply channel.

The half that needs the Rust exec-server -- the real ``lemma-agent-host``
binary running ``exec_command`` for an owner's run on the Mac, and a
non-owner's run landing in the VM -- is
``test_the_real_binary_runs_an_owners_command_on_the_host`` below. It skips
until the binary on this branch has an ``exec-server`` subcommand; the Rust
half lands on a parallel branch.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.contracts.host_execution import (
    HOST_OFFLINE,
    AgentHostOpClient,
    AgentHostOpError,
    host_execution_host_id,
)
from app.modules.agent.tests.e2e.agent_host_helpers import (
    app_of,
    connected_host,
    pair,
)

pytestmark = pytest.mark.e2e

_ON = {"enabled": True, "platform": "macos", "available": True}


def _deadline(seconds: float = 20) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _next_op(link) -> dict:
    while True:
        frame = await link.next_push(timeout=10)
        if frame["type"] == "op":
            return frame


@pytest.mark.asyncio
async def test_an_op_crosses_redis_to_the_hosts_socket_and_back(
    authenticated_client, async_client
):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    link = await connected_host(app_of(async_client), machine, host_execution=_ON)
    try:
        workspace = uuid4()
        pending = asyncio.ensure_future(
            AgentHostOpClient().request(
                host_id=UUID(machine["host_id"]),
                workspace=workspace,
                method="process.list",
                params={},
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        assert frame["id"].startswith("s")
        assert frame["body"]["workspace"] == str(workspace)
        assert frame["body"]["method"] == "process.list"

        link.reply("op_ok", frame["id"], {"result": {"processes": []}})
        assert await asyncio.wait_for(pending, timeout=10) == {"processes": []}
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_a_host_refusal_reaches_the_caller_with_its_kind(
    authenticated_client, async_client
):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    link = await connected_host(app_of(async_client), machine, host_execution=_ON)
    try:
        pending = asyncio.ensure_future(
            AgentHostOpClient().request(
                host_id=UUID(machine["host_id"]),
                workspace=uuid4(),
                method="file.read",
                params={"path": "/Users/me/.ssh/id_rsa", "offset": 0, "length": 1},
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        link.reply(
            "error",
            frame["id"],
            {
                "code": "OP_FAILED",
                "message": "outside the workspace",
                "retryable": False,
                "detail": {"kind": "outside_workspace"},
            },
        )
        with pytest.raises(AgentHostOpError) as raised:
            await asyncio.wait_for(pending, timeout=10)
        assert raised.value.kind == "outside_workspace"
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_a_host_with_no_live_link_is_offline_within_the_pickup_window():
    started = asyncio.get_running_loop().time()
    with pytest.raises(AgentHostOpError) as raised:
        await AgentHostOpClient().request(
            host_id=uuid4(),
            workspace=uuid4(),
            method="process.list",
            params={},
            deadline_at=_deadline(),
        )
    assert raised.value.kind == HOST_OFFLINE
    assert "This Mac is not connected" in raised.value.message
    assert asyncio.get_running_loop().time() - started < 10


@pytest.mark.asyncio
async def test_hello_capabilities_decide_whether_the_host_can_take_commands(
    authenticated_client, async_client
):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    user_id = UUID(machine["user_id"])

    off = await connected_host(
        app_of(async_client),
        machine,
        host_execution={"enabled": False, "platform": "macos", "available": True},
    )
    try:
        assert await host_execution_host_id(user_id) is None
    finally:
        await off.aclose()

    on = await connected_host(app_of(async_client), machine, host_execution=_ON)
    try:
        assert await host_execution_host_id(user_id) == UUID(machine["host_id"])
        # Turned off mid-connection: the heartbeat carries it.
        answer = await on.request(
            "control",
            {
                "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
                "host_execution": {
                    "enabled": False,
                    "platform": "macos",
                    "available": True,
                },
            },
        )
        assert answer["type"] == "control_ok", answer
        assert await host_execution_host_id(user_id) is None
    finally:
        await on.aclose()


def _binary_with_exec_server() -> str | None:
    binary = os.environ.get("LEMMA_AGENT_HOST_BIN") or shutil.which("lemma-agent-host")
    if not binary:
        return None
    probe = subprocess.run(
        [binary, "exec-server", "--help"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return binary if probe.returncode == 0 else None


@pytest.mark.asyncio
async def test_the_real_binary_runs_an_owners_command_on_the_host():
    if _binary_with_exec_server() is None:
        pytest.skip(
            "lemma-agent-host on this branch has no `exec-server`; the Rust half "
            "of host execution lands separately (desktop-host-execution.md §8)"
        )
    pytest.skip(
        "wire the real binary through pairing and a Desktop-owner run once the "
        "exec-server lands; see test_agent_host_process_e2e.py for the harness"
    )
