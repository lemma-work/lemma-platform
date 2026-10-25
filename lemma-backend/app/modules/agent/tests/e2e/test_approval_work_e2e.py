"""``agent.approval.execute``: an approved tool runs at most once, and always ends.

The decision's transaction writes the item, so there is no window in which a
recorded approval has nothing that will act on it. The item takes the one-shot
execution claim before the tool runs; a claim found already taken means the
attempt that took it lost its lease, and the return says "approved, outcome
unknown" -- the tool is never run again.

Marked ``approval_worker`` so the module's autouse fixture leaves the items
queued: each test runs them itself, through the runtime's claim and execute,
or kills the process running one.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import text
from starlette import status

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work.testing import make_due
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.events.work_kinds import AGENT_APPROVAL_EXECUTE, approval_key
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    RECORD_DELETE_REFUSALS,
    Seeded,
    finish,
    messages,
    pausing_call,
    refused_before_asking,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.datastore.config import datastore_settings
from app.modules.test_support.e2e.agent_work import run_approval_work
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.approval_worker]

_TABLE = "chores"


async def _paused_on_a_delete(scenario: E2EScenario) -> tuple[Seeded, UUID, str]:
    """A run that asked to delete a chore and is waiting on the answer."""
    seeded = await seeded_conversation(scenario, toolsets=["POD", "USER_INTERACTION"])
    await create_table(scenario.owner_client, scenario.pod_id, _TABLE, enable_rls=False)
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records",
        json={"data": {"title": "the chore"}},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    delete = {
        "action": "delete",
        "table_name": _TABLE,
        "record_id": created.json()["id"],
    }
    await refused_before_asking(
        seeded,
        tool_name="pod_write_record",
        args=delete,
        permission_ids=RECORD_DELETE_REFUSALS,
    )
    paused = await running_run(seeded)
    approval_id = await pausing_call(
        seeded,
        paused,
        tool_name="request_approval",
        tool_args={
            "tool_name": "pod_write_record",
            "args": delete,
            "title": "Delete the chore",
        },
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)
    return seeded, paused, approval_id


async def _decide(
    scenario: E2EScenario,
    seeded: Seeded,
    approval_id: str,
    decision: AgentRunApprovalDecision,
) -> Response:
    return await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"
        f"/approvals/{approval_id}/decision",
        json={"decision": decision.value},
    )


async def _scalar(sql: str, **params: object) -> object:
    async with async_session_maker() as session:
        return (await session.execute(text(sql), params)).scalar_one()


async def _items(seeded: Seeded, approval_id: str, *, state: str | None = None) -> int:
    return int(
        await _scalar(  # type: ignore[arg-type]
            "SELECT count(*) FROM work_items WHERE kind = :kind AND dedup_key = :key "
            "AND (CAST(:state AS text) IS NULL OR state = :state)",
            kind=AGENT_APPROVAL_EXECUTE,
            key=approval_key(seeded.conversation_id, approval_id),
            state=state,
        )
    )


async def _returns(seeded: Seeded, approval_id: str) -> list[dict]:
    return [
        message.tool_result
        for message in await messages(seeded.conversation_id)
        if message.kind is MessageKind.TOOL_RETURN
        and message.tool_call_id == approval_id
        and isinstance(message.tool_result, dict)
    ]


async def _chores_left(scenario: E2EScenario) -> int:
    listed = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    return len(listed.json()["items"])


@pytest.mark.drives_work
async def test_a_decision_commits_with_the_item_that_carries_it_out(scenario) -> None:
    """The old gap: decision committed, enqueue lost with the process.

    Nothing runs after the decision's commit here -- the process could have
    died there -- and the item is already durable. Run later, it executes the
    approved tool once and resumes once; running the queue again does nothing.
    """
    seeded, paused, approval_id = await _paused_on_a_delete(scenario)

    decided = await _decide(
        scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE
    )

    assert decided.status_code == status.HTTP_200_OK, decided.text
    assert decided.json()["status"] == "queued"
    assert await _items(seeded, approval_id, state="queued") == 1
    assert await _chores_left(scenario) == 1, "the tool ran inside the request"

    assert await run_approval_work() == 1
    assert await run_approval_work() == 0

    assert await _chores_left(scenario) == 0
    (result,) = await _returns(seeded, approval_id)
    assert result["executed"] is True
    (first, resumed) = await runs(seeded.conversation_id)
    assert first.id == paused
    assert resumed.resumed_tool_call_id == approval_id
    assert await _items(seeded, approval_id) == 0


@pytest.mark.drives_work
async def test_racing_approvals_write_one_item_and_run_the_tool_once(scenario) -> None:
    """Two Approve taps at once: one decision row, one queued item, one run."""
    seeded, _, approval_id = await _paused_on_a_delete(scenario)

    first, second = await asyncio.gather(
        *(
            _decide(
                scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE
            )
            for _ in range(2)
        )
    )

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert {first.json()["status"], second.json()["status"]} == {"queued"}
    assert await _items(seeded, approval_id, state="queued") == 1, (
        "the loser's item did not coalesce with the winner's"
    )
    await run_approval_work()
    assert await _chores_left(scenario) == 0
    assert len(await _returns(seeded, approval_id)) == 1
    assert len(await runs(seeded.conversation_id)) == 2


async def test_a_denial_writes_its_return_inline_and_no_item(scenario) -> None:
    seeded, _, approval_id = await _paused_on_a_delete(scenario)

    denied = await _decide(scenario, seeded, approval_id, AgentRunApprovalDecision.DENY)

    assert denied.status_code == status.HTTP_200_OK, denied.text
    assert denied.json()["status"] == "resolved"
    assert await _items(seeded, approval_id) == 0
    (result,) = await _returns(seeded, approval_id)
    assert result["executed"] is False
    assert await _chores_left(scenario) == 1
    assert len(await runs(seeded.conversation_id)) == 2


_KILLED_EXECUTOR = """
import asyncio

from app.modules.agent.events import approval_work
from app.modules.test_support.e2e.agent_work import run_approval_work


async def _hang(decided, payload, *, uow_factory):
    # The claim committed (its checkpoint returned); the tool is "running".
    print("CLAIMED", flush=True)
    await asyncio.sleep(3600)


approval_work._run_approved_tool = _hang
asyncio.run(run_approval_work())
"""


def _executor_env() -> dict[str, str]:
    backend = Path(__file__).resolve().parents[5]
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            part for part in (str(backend), os.environ.get("PYTHONPATH")) if part
        ),
        "DATABASE_URL": settings.database_url,
        "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url,
        "REDIS_URL": settings.redis_url,
        "ENVIRONMENT": "testing",
    }


@pytest.mark.drives_work
async def test_a_worker_killed_after_the_claim_never_runs_the_tool_again(
    scenario,
) -> None:
    """SIGKILL between the claim and the result: "outcome unknown", no re-run.

    A real process takes the item, commits the claim and is killed while the
    tool "runs". Its lease is then let lapse (moved to the past rather than
    waited out: the lease is 60 s). The next claim reaps it, finds the claim
    held with no return, and writes the honest return -- the delete never runs,
    so the chore is still there -- and the conversation resumes.
    """
    seeded, paused, approval_id = await _paused_on_a_delete(scenario)
    decided = await _decide(
        scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE
    )
    assert decided.status_code == status.HTTP_200_OK, decided.text

    executor = subprocess.Popen(
        [sys.executable, "-c", _KILLED_EXECUTOR],
        cwd=str(Path(__file__).resolve().parents[5]),
        env=_executor_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert executor.stdout is not None
        claimed_line = await asyncio.wait_for(
            asyncio.to_thread(_read_until, executor.stdout, "CLAIMED"), timeout=60
        )
        assert claimed_line, "the executor never reached the tool"
        executor.send_signal(signal.SIGKILL)
        executor.wait(timeout=10)
    finally:
        if executor.poll() is None:
            executor.kill()

    claimed_at = await _scalar(
        "SELECT execution_claimed_at FROM agent_approval_decisions "
        "WHERE conversation_id = :c AND approval_id = :a",
        c=seeded.conversation_id,
        a=approval_id,
    )
    assert claimed_at is not None
    assert await _items(seeded, approval_id, state="running") == 1

    killed_at = time.monotonic()
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE work_items SET lease_until = now() - interval '1 second' "
                "WHERE kind = :kind AND state = 'running'"
            ),
            {"kind": AGENT_APPROVAL_EXECUTE},
        )
        await session.commit()
    await run_approval_work()  # reaps: requeued with a short backoff
    await make_due(async_session_maker, kind=AGENT_APPROVAL_EXECUTE)
    await run_approval_work()
    settled_seconds = time.monotonic() - killed_at

    (result,) = await _returns(seeded, approval_id)
    assert result["executed"] is False
    assert "cannot re-run" in str(result["error"])
    assert await _chores_left(scenario) == 1, "the approved delete ran again"
    (first, resumed) = await runs(seeded.conversation_id)
    assert first.id == paused
    assert resumed.resumed_tool_call_id == approval_id
    assert await _items(seeded, approval_id) == 0
    print(f"APPROVAL_KILL_RESULT settled_after_lease_seconds={settled_seconds:.2f}")


def _read_until(stream, marker: str) -> str | None:
    for line in stream:
        if marker in line:
            return line
    return None


@pytest.mark.drives_work
async def test_a_resume_waits_for_every_pause_of_the_run(scenario) -> None:
    """An approval resumed while another pausing call is unanswered: no resume."""
    seeded, paused, approval_id = await _paused_on_a_delete(scenario)
    await pausing_call(
        seeded, paused, tool_name="ask_user", tool_args={"questions": []}
    )

    await _decide(scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE)
    await run_approval_work()

    assert await _chores_left(scenario) == 0
    assert len(await _returns(seeded, approval_id)) == 1
    assert len(await runs(seeded.conversation_id)) == 1, (
        "resumed with a sibling pause still open"
    )


@pytest.mark.drives_work
async def test_the_item_written_while_one_runs_waits_for_it(scenario) -> None:
    """A second item for the same approval never runs beside the first.

    The first holds the concurrency key; the second stays queued until it
    finishes, then finds the return written and only resumes (a no-op).
    """
    seeded, _, approval_id = await _paused_on_a_delete(scenario)
    await _decide(scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE)
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE work_items SET state = 'running', lease_owner = 'other', "
                "lease_until = now() + interval '1 minute' WHERE kind = :kind"
            ),
            {"kind": AGENT_APPROVAL_EXECUTE},
        )
        await session.commit()
    again = await _decide(
        scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE
    )
    assert again.json()["status"] == "queued"
    assert await _items(seeded, approval_id, state="queued") == 1

    assert await run_approval_work() == 0, "ran beside a live execution"
    await eventually(
        label="second item still queued",
        probe=lambda: _items(seeded, approval_id, state="queued"),
        done=lambda n: n == 1,
        timeout_seconds=2,
    )
