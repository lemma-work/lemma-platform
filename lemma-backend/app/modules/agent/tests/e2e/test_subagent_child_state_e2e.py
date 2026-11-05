"""What a parent is told about a child: whose it is, and whether it is done.

Three things a parent read wrongly. A run id from the model was read without
asking whose run it was. A child that stopped to ask a question was reported
as finished, because its *run* had finished -- and the question, in a
conversation with no surface, was seen by nobody. And a spawn re-issued after
a retry started a second child doing the same work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.subagent_service import SubAgentError, SubAgentService
from app.modules.agent.services.wait_targets import read_target
from app.modules.agent.services.wait_wake_service import AgentWaitService
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    pausing_call,
    running_run,
    seeded_conversation,
    uow_factory,
)
from app.modules.agent.tools.context import BaseAgentContext

pytestmark = [pytest.mark.e2e]


def _service() -> SubAgentService:
    return SubAgentService(SessionUnitOfWorkFactory(async_session_maker))


def _as_parent(parent: Seeded, *, run_id: UUID | None = None) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=parent.user_id,
        pod_id=parent.pod_id,
        conversation_id=parent.conversation_id,
        workload_id=parent.agent_id,
        agent_name=parent.agent_name,
        agent_run_id=run_id,
    )


async def _child_that_asked(scenario, parent: Seeded) -> tuple[Seeded, UUID]:
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    asked = await running_run(child)
    await pausing_call(
        child, asked, tool_name="ask_user", tool_args={"questions": ["Which repo?"]}
    )
    await finish(asked, conversation_status=ConversationStatus.WAITING)
    return child, asked


async def test_a_run_from_another_conversation_is_not_readable(scenario) -> None:
    """`interact_subagent(action='await')` and `wait_for` both take a
    run id from the model."""
    parent = await seeded_conversation(scenario)
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    elsewhere = await seeded_conversation(scenario)
    foreign = await running_run(elsewhere)
    await finish(foreign)

    with pytest.raises(SubAgentError):
        await _service().await_run(
            _as_parent(parent),
            conversation_id=child.conversation_id,
            run_id=foreign,
            timeout_seconds=1,
        )
    seen = await read_target(
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(foreign),
        user_id=parent.user_id,
        pod_id=parent.pod_id,
        conversation_id=parent.conversation_id,
    )
    assert seen.reason is AgentWaitWakeReason.TARGET_GONE


async def test_a_child_that_asked_a_question_is_reported_as_needing_input(
    scenario,
) -> None:
    """Read side: every way a parent looks at the child says so."""
    parent = await seeded_conversation(scenario)
    child, asked = await _child_that_asked(scenario, parent)
    deps = _as_parent(parent)

    awaited = await _service().await_run(
        deps, conversation_id=child.conversation_id, run_id=asked, timeout_seconds=1
    )
    assert awaited["status"] == "WAITING"
    assert awaited["needs_input"] is True
    assert awaited["waiting_on"]["kind"] == "ask_user"

    status = await _service().status(deps, conversation_id=child.conversation_id)
    assert status["status"] == "WAITING" and status["needs_input"] is True
    (listed,) = await _service().list_children(deps)
    assert listed["status"] == "WAITING" and listed["needs_input"] is True

    seen = await read_target(
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(asked),
        user_id=parent.user_id,
        pod_id=parent.pod_id,
        conversation_id=parent.conversation_id,
    )
    assert seen.reason is AgentWaitWakeReason.TARGET_NEEDS_INPUT


async def test_a_waiting_parent_is_woken_to_the_question_not_a_result(scenario) -> None:
    """Relay side: the parent wakes knowing its child is waiting on an
    answer it has to arrange, rather than "the sub-agent run finished"."""
    parent = await seeded_conversation(scenario)
    child, asked = await _child_that_asked(scenario, parent)
    parent_run = await running_run(parent)
    waiting_call = await pausing_call(
        parent, parent_run, tool_name="wait_for", tool_args={"subagent_run_id": "x"}
    )
    await finish(parent_run, conversation_status=ConversationStatus.WAITING)
    now = datetime.now(timezone.utc)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await AgentConversationWaitRepository(uow).create(
            AgentConversationWaitEntity(
                conversation_id=parent.conversation_id,
                agent_run_id=parent_run,
                pod_id=parent.pod_id,
                tool_call_id=waiting_call,
                wait_type=AgentWaitType.SUBAGENT,
                external_ref=str(uuid4()),
                scheduled_at=now + timedelta(hours=1),
                spec={
                    "started_at": now.isoformat(),
                    "deadline_at": (now + timedelta(hours=1)).isoformat(),
                    "target_ref": str(asked),
                    "user_id": str(parent.user_id),
                },
            )
        )
        await uow.commit()

    # What the child's `agent.wait.resolve_child` item does when it runs.
    async with uow_factory()() as uow:
        await AgentWaitService(uow).resolve_child(
            child_run_id=asked, child_conversation_id=child.conversation_id
        )
        await uow.commit()

    (woke,) = [
        m
        for m in await messages(parent.conversation_id)
        if m.kind is MessageKind.TOOL_RETURN and m.tool_call_id == waiting_call
    ]
    assert woke.tool_result["woke_because"] == "TARGET_NEEDS_INPUT"
    assert "not finished" in woke.tool_result["message"]


async def test_a_spawn_reissued_in_one_run_gets_the_child_it_already_has(
    scenario,
) -> None:
    """The same spawn twice in one run makes one child."""
    parent = await seeded_conversation(scenario)
    parent_run = await running_run(parent)
    deps = _as_parent(parent, run_id=parent_run)

    first = await _service().spawn(deps, input_data="Summarise the README.")
    again = await _service().spawn(deps, input_data="Summarise the README.")
    other = await _service().spawn(deps, input_data="Summarise the CHANGELOG.")

    assert again.conversation_id == first.conversation_id
    assert again.run_id == first.run_id
    assert other.conversation_id != first.conversation_id
    assert len(await _service().list_children(deps)) == 2
