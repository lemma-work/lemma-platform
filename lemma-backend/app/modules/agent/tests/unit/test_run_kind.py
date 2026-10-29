"""A run's kind is set where it starts; who waits on it is the conversation's."""

from __future__ import annotations

from typing import get_args
from uuid import uuid4

import pytest

from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.run_facts import AgentRunOrigin
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import AskChain, AskMode, PodAsk
from app.modules.agent.domain.run_kind import POLICIES, RunKind
from app.modules.agent.domain.run_waiters import run_waiters
from app.modules.agent.domain.value_objects import ConversationOrigin
from app.modules.agent.services.turn_staging import turn_class_for

pytestmark = pytest.mark.unit


def _conversation(**fields: object) -> Conversation:
    return Conversation(user_id=uuid4(), pod_id=uuid4(), **fields)


def _ask() -> PodAsk:
    asker = uuid4()
    return PodAsk(
        from_pod_id=asker,
        from_pod_name="Sales",
        from_conversation_id=uuid4(),
        for_user_id=None,
        mode=AskMode.LINK,
        chain=AskChain.start(asker),
    )


def test_every_kind_has_a_policy_and_a_name_the_fact_accepts() -> None:
    assert set(POLICIES) == set(RunKind)
    assert set(get_args(AgentRunOrigin)) == {kind.value for kind in RunKind}


@pytest.mark.parametrize("kind", [RunKind.WORKFLOW, RunKind.SCHEDULE, RunKind.SUBAGENT])
def test_unattended_kinds_never_queue_a_person(kind: RunKind) -> None:
    assert turn_class_for(kind) is WorkClass.AUTOMATION


@pytest.mark.parametrize(
    "kind",
    [
        RunKind.CHAT,
        RunKind.SURFACE,
        RunKind.CHANNEL,
        RunKind.CONTACT,
        RunKind.POD_ASK,
        RunKind.CALL,
    ],
)
def test_somebody_waits_on_the_rest(kind: RunKind) -> None:
    assert turn_class_for(kind) is WorkClass.INTERACTIVE


def test_a_memory_pass_follows_only_a_members_own_turns() -> None:
    kept = {kind for kind in RunKind if kind.policy.memory_pass}
    assert kept == {RunKind.CHAT, RunKind.SURFACE, RunKind.CHANNEL}


def test_a_person_continuing_an_automation_is_a_persons_turn() -> None:
    assert RunKind.SCHEDULE.continuing(by_a_person=True) is RunKind.CHAT
    assert RunKind.WORKFLOW.continuing(by_a_person=True) is RunKind.CHAT
    assert RunKind.SCHEDULE.continuing() is RunKind.SCHEDULE
    assert RunKind.SUBAGENT.continuing(by_a_person=True) is RunKind.SUBAGENT


def test_a_platform_thread_that_answers_outsiders_is_a_contacts() -> None:
    assert RunKind.SURFACE.for_audience(answers_outsiders=True) is RunKind.CONTACT
    assert RunKind.SURFACE.for_audience(answers_outsiders=False) is RunKind.SURFACE
    assert RunKind.CHAT.for_audience(answers_outsiders=True) is RunKind.CHAT


def test_a_plain_chat_has_nobody_waiting() -> None:
    waiters = run_waiters(_conversation())
    assert waiters.parent_kind is None


def test_a_workflow_step_is_waited_on_by_its_conversation() -> None:
    conversation = _conversation(
        origin=ConversationOrigin.WORKFLOW_STEP, workflow_run_id=uuid4()
    )
    waiters = run_waiters(conversation)
    assert waiters.parent_kind == "workflow"
    assert waiters.parent_ref == str(conversation.id)


def test_a_sub_agent_reports_to_its_parent() -> None:
    parent = uuid4()
    waiters = run_waiters(
        _conversation(
            origin=ConversationOrigin.SUBAGENT, parent_id=parent, reports_to_parent=True
        )
    )
    assert (waiters.parent_kind, waiters.parent_ref) == ("agent", str(parent))
    assert waiters.settles_subagents


def test_a_conversation_pinned_under_a_project_waits_on_nobody() -> None:
    """A parent is not what makes a sub-agent: its origin is."""
    waiters = run_waiters(_conversation(parent_id=uuid4()))
    assert waiters.parent_kind is None
    assert not waiters.settles_subagents


def test_a_conversation_that_delegated_settles_what_comes_back() -> None:
    delegated = run_waiters(_conversation(delegations_out=True))
    assert delegated.settles_subagents and delegated.settles_pod_asks
    plain = run_waiters(_conversation())
    assert not plain.settles_subagents and not plain.settles_pod_asks


def test_a_pod_ask_on_the_answering_side() -> None:
    asked = run_waiters(_conversation(origin=ConversationOrigin.POD_ASK, ask=_ask()))
    assert (asked.parent_kind, asked.settles_pod_asks) == ("pod_ask", True)
