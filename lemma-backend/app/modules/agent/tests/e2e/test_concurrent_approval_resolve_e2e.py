"""One approval decided twice at the same moment: exactly one decision wins.

Approve on the phone and Deny in the browser, or two taps on one card, arrive
as two requests that both read "no decision yet" before either has written
one. The database arbitrates -- a unique row per approval, inserted with
``ON CONFLICT DO NOTHING`` -- and the request that loses has to adopt the
winner's decision rather than act on its own. Acting on its own is the costly
failure: an approved card runs the wrapped tool, so a loser that believed its
"approve" would delete the record a winning "deny" had just refused.

Sequential double-submits are covered elsewhere; nothing there makes the two
requests overlap, so the branch for losing the race was never executed.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import func, select, text
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.infrastructure.models.approval import (
    AgentApprovalDecisionModel,
)
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
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
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.test_support.e2e.waiters import eventually

# Each decision resumes the run inline, in this process. The session worker
# must not take the same queued resume -- it raced the inline path and, once
# it pooled its connections, sometimes won -- so it is held for each test.
pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

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


async def _decisions_recorded(seeded: Seeded, approval_id: str) -> int:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(func.count()).where(
                    AgentApprovalDecisionModel.conversation_id
                    == seeded.conversation_id,
                    AgentApprovalDecisionModel.approval_id == approval_id,
                )
            )
        ).scalar_one()


async def _tool_returns(seeded: Seeded, approval_id: str) -> int:
    return sum(
        1
        for message in await messages(seeded.conversation_id)
        if message.kind is MessageKind.TOOL_RETURN
        and message.tool_call_id == approval_id
    )


async def _chores_left(scenario: E2EScenario) -> int:
    listed = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    return len(listed.json()["items"])


async def _assert_one_outcome(
    scenario: E2EScenario,
    seeded: Seeded,
    paused: UUID,
    approval_id: str,
    *,
    decided: AgentRunApprovalDecision,
) -> None:
    assert await _decisions_recorded(seeded, approval_id) == 1
    assert await _tool_returns(seeded, approval_id) == 1, "the card was answered twice"
    assert await _chores_left(scenario) == (
        0 if decided is AgentRunApprovalDecision.APPROVE_ONCE else 1
    ), f"the wrapped tool did not follow the decision that won ({decided.value})"
    (first, *resumed) = await runs(seeded.conversation_id)
    assert first.id == paused
    assert len(resumed) == 1, "two resolves started two resume runs"


async def test_approve_and_deny_at_once_settle_on_one_decision(scenario) -> None:
    seeded, paused, approval_id = await _paused_on_a_delete(scenario)

    approve, deny = await asyncio.gather(
        _decide(scenario, seeded, approval_id, AgentRunApprovalDecision.APPROVE_ONCE),
        _decide(scenario, seeded, approval_id, AgentRunApprovalDecision.DENY),
    )

    assert approve.status_code == deny.status_code == status.HTTP_200_OK, (
        approve.text,
        deny.text,
    )
    won = {approve.json()["decision"], deny.json()["decision"]}
    assert len(won) == 1, f"the two requests disagree about what was decided: {won}"
    await _assert_one_outcome(
        scenario,
        seeded,
        paused,
        approval_id,
        decided=AgentRunApprovalDecision(won.pop()),
    )


async def _blocked_on_the_decision_row() -> int:
    """Backends waiting for another transaction's insert into the decision table."""
    async with async_session_maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%agent_approval_decisions%'"
                )
            )
        ).scalar_one()


async def test_requests_that_lose_the_race_adopt_the_winning_decision(
    scenario,
) -> None:
    """The losing branch, made certain rather than likely.

    A third resolver -- a platform tap, say -- has inserted DENY and not yet
    committed. Two Approve clicks read no decision, reach the insert, and wait
    on its row. When the deny commits, both inserts find the conflict, and both
    must report and act on DENY: nothing deleted, the card answered once.
    """
    seeded, paused, approval_id = await _paused_on_a_delete(scenario)
    async with create_uow_from_session_maker(async_session_maker) as tap:
        recorded = await ApprovalDecisions(tap).record_approval_decision(
            conversation_id=seeded.conversation_id,
            approval_id=approval_id,
            agent_run_id=paused,
            tool_name="pod_write_record",
            decision=AgentRunApprovalDecision.DENY,
            response={},
            resolved_by_user_id=seeded.user_id,
        )
        assert recorded is True
        clicks = [
            asyncio.create_task(
                _decide(
                    scenario,
                    seeded,
                    approval_id,
                    AgentRunApprovalDecision.APPROVE_ONCE,
                )
            )
            for _ in range(2)
        ]
        await eventually(
            label="both approve clicks waiting on the uncommitted deny",
            probe=_blocked_on_the_decision_row,
            done=lambda waiting: waiting >= 2,
            timeout_seconds=15,
        )
        await tap.commit()

    first, second = await asyncio.gather(*clicks)

    for click in (first, second):
        assert click.status_code == status.HTTP_200_OK, click.text
        assert click.json()["decision"] == AgentRunApprovalDecision.DENY.value
        assert click.json()["status"] == "reconciled"
    await _assert_one_outcome(
        scenario, seeded, paused, approval_id, decided=AgentRunApprovalDecision.DENY
    )
