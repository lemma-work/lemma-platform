"""Host execution's ``op`` over the real link: real WebSocket, Redis and database.

docs/architecture/desktop-host-execution.md §3-4. The host is this test,
speaking the link through the whole application as ``HostLink`` does for every
other Agent Host e2e suite; the caller is the production op client, publishing
on the real Redis notice channel exactly as the host provider does from any
replica. What is proved is the hop the unit tests fake: a notice published here
reaches the session holding the socket, becomes an ``op`` frame, and the
host's ``op_ok`` comes back on the reply channel.

The real ``lemma-agent-host`` binary and its exec-server under Seatbelt are
driven in ``test_host_execution_binary_e2e.py``.
"""

from __future__ import annotations

import asyncio
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
        # A control frame without the field changes run slots, not the report:
        # both live in the row's `capacity`, and neither overwrites the other.
        answer = await on.request(
            "control",
            {"capacity": {"max_runs": 2, "active_runs": 1, "available_runs": 1}},
        )
        assert answer["type"] == "control_ok", answer
        assert await host_execution_host_id(user_id) == UUID(machine["host_id"])
        listed = await authenticated_client.get("/me/runtime/agent-hosts")
        (row,) = [
            item for item in listed.json()["items"] if item["id"] == machine["host_id"]
        ]
        assert row["capacity"]["max_runs"] == 2
        assert row["capacity"]["host_execution"]["enabled"] is True
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


@pytest.mark.asyncio
async def test_the_choice_is_written_on_the_run_and_read_back(db_session, scenario):
    """The record a reclaimed run and an approved tool both read (§2)."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent.infrastructure.models import AgentRunModel
    from app.modules.agent.infrastructure.run_execution_record import (
        read_run_execution,
        record_run_execution,
    )

    await scenario.create_org_with_pod(name_prefix="HostExec")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "e2e"}
    )
    assert created.status_code in {200, 201}, created.text
    run = AgentRunModel(
        conversation_id=UUID(created.json()["id"]),
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
        run_metadata={"source": "user_message"},
    )
    db_session.add(run)
    await db_session.flush()
    uow = SqlAlchemyUnitOfWork(db_session)

    assert await read_run_execution(uow, run.id) is None
    choice = {"target": "host", "sandbox_id": str(uuid4()), "root": "/Users/o/p"}
    await record_run_execution(uow, run.id, choice)

    assert await read_run_execution(uow, run.id) == choice
    await db_session.refresh(run)
    # Written beside what was there, not over it.
    assert run.run_metadata["source"] == "user_message"


@pytest.mark.asyncio
async def test_a_host_sandbox_follows_the_conversations_latest_host_run(
    db_session, scenario
):
    """Which Mac a host sandbox is on is read off the runs, not stored (§2).

    The newest run that chose the host names it; a run that chose the VM since
    does not move the sandbox, and a later host run does.
    """
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent.infrastructure.models import AgentRunModel
    from app.modules.agent.infrastructure.run_execution_record import (
        latest_host_execution,
        record_run_execution,
    )

    await scenario.create_org_with_pod(name_prefix="HostExec")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "e2e"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    assert await latest_host_execution(uow, conversation_id) is None

    start = datetime.now(timezone.utc)
    first, second = str(uuid4()), str(uuid4())
    choices = [
        {"target": "host", "host_id": first, "sandbox_id": "s", "root": "/a"},
        {"target": "vm"},
    ]
    for offset, choice in enumerate(choices):
        run = AgentRunModel(
            conversation_id=conversation_id,
            status="COMPLETED",
            started_at=start,
            created_at=start + timedelta(seconds=offset),
            run_metadata={"source": "user_message"},
        )
        db_session.add(run)
        await db_session.flush()
        await record_run_execution(uow, run.id, choice)

    assert (await latest_host_execution(uow, conversation_id) or {})["host_id"] == first

    later = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=start,
        created_at=start + timedelta(seconds=5),
        run_metadata={"source": "user_message"},
    )
    db_session.add(later)
    await db_session.flush()
    await record_run_execution(
        uow,
        later.id,
        {"target": "host", "host_id": second, "sandbox_id": "s", "root": "/a"},
    )
    assert (await latest_host_execution(uow, conversation_id) or {})[
        "host_id"
    ] == second
