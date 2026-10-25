"""A sub-agent's result reaches a parent that did not wait for it, once.

The parent spawns a child and ends its turn without waiting -- fire and forget.
When the child finishes, its result arrives in the parent as a new message, and
the completion arriving again (an event redelivered, the parent's own finish
looking for owed results) delivers nothing more. A parent that did collect the
result itself is not told it a second time.

No worker and no model: runs are started with the enqueue suppressed and
finished by hand, which is all the delivery rules look at.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.subagent_results import RESULT_SOURCE
from app.modules.agent.domain.value_objects import AgentRunStatus, MessageRole
from app.modules.agent.services.subagent_result_delivery import SubagentResultDelivery
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.services.subagent_service import SubAgentService
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    running_run,
    runs,
    seeded_conversation,
    uow_factory,
)
from app.modules.agent.tools.context import BaseAgentContext

pytestmark = [pytest.mark.e2e]


def _service() -> SubAgentService:
    return SubAgentService(SessionUnitOfWorkFactory(async_session_maker))


def _as_parent(parent: Seeded, run_id: UUID) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=parent.user_id,
        pod_id=parent.pod_id,
        conversation_id=parent.conversation_id,
        workload_id=parent.agent_id,
        agent_name=parent.agent_name,
        agent_run_id=run_id,
    )


async def _spawn(parent: Seeded, parent_run: UUID, task: str) -> tuple[UUID, UUID]:
    with suppress_agent_run_enqueue():
        handle = await _service().spawn(_as_parent(parent, parent_run), input_data=task)
    assert handle.run_id is not None
    return handle.conversation_id, handle.run_id


async def _child_answers(run_id: UUID, answer: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Runs(uow).finish_agent_run(
            agent_run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            output_data={"answer": answer},
        )
        await uow.commit()


async def _settle(conversation_id: UUID, run_id: UUID) -> None:
    with suppress_agent_run_enqueue():
        # What the run's finalize writes for it: its `agent.subagent_result`
        # item (each follow-up runs in its own transaction).
        del run_id
        await SubagentResultDelivery(uow_factory()).settle(
            conversation_id=conversation_id
        )


async def _results(parent: Seeded) -> list[str]:
    return [
        message.text or ""
        for message in await messages(parent.conversation_id)
        if message.role == MessageRole.USER
        and (message.metadata or {}).get("source") == RESULT_SOURCE
    ]


async def test_a_parent_that_did_not_wait_gets_the_result_once(scenario) -> None:
    parent = await seeded_conversation(scenario)
    parent_run = await running_run(parent)
    child_id, child_run = await _spawn(parent, parent_run, "Count the open issues.")

    # Fire and forget: the parent's turn ends without waiting on the child.
    await finish(parent_run)
    await _settle(parent.conversation_id, parent_run)
    assert await _results(parent) == []

    await _child_answers(child_run, "There are 12 open issues.")
    await _settle(child_id, child_run)
    # The same completion again, and the parent's next finish looking for owed
    # results: neither delivers a second copy.
    await _settle(child_id, child_run)

    (delivered,) = await _results(parent)
    assert "There are 12 open issues." in delivered
    # It is a new turn for the parent, which was idle.
    parent_runs = await runs(parent.conversation_id)
    assert len(parent_runs) == 2
    await finish(parent_runs[-1].id)
    await _settle(parent.conversation_id, parent_runs[-1].id)
    assert len(await _results(parent)) == 1


async def test_a_result_waits_for_the_run_that_may_still_await_it(scenario) -> None:
    """While the parent run that spawned the child is going it may await the
    child itself, so the result is not pushed into it; that run's own finish
    delivers what it left uncollected."""
    parent = await seeded_conversation(scenario)
    parent_run = await running_run(parent)
    child_id, child_run = await _spawn(parent, parent_run, "Summarise the README.")

    await _child_answers(child_run, "It explains setup.")
    await _settle(child_id, child_run)
    assert await _results(parent) == []

    await finish(parent_run)
    await _settle(parent.conversation_id, parent_run)
    (delivered,) = await _results(parent)
    assert "It explains setup." in delivered


async def test_a_result_the_parent_awaited_is_not_delivered_again(scenario) -> None:
    parent = await seeded_conversation(scenario)
    parent_run = await running_run(parent)
    child_id, child_run = await _spawn(parent, parent_run, "Check the build.")
    await _child_answers(child_run, "The build is green.")

    awaited = await _service().await_run(
        _as_parent(parent, parent_run),
        conversation_id=child_id,
        run_id=child_run,
        timeout_seconds=1,
    )
    assert awaited["status"] == AgentRunStatus.COMPLETED.value

    await finish(parent_run)
    await _settle(child_id, child_run)
    await _settle(parent.conversation_id, parent_run)
    assert await _results(parent) == []
