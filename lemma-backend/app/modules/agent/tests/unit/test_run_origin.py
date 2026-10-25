"""What started a conversation's turns, read off the row the same way at start and at finish."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.turns import turn_class_for
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import AskChain, AskMode, PodAsk
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.domain.value_objects import ConversationOrigin

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


def test_a_plain_chat() -> None:
    origin = run_origin(_conversation())
    assert (origin.origin, origin.parent_kind, origin.has_reply_target) == (
        "chat",
        None,
        False,
    )


def test_a_workflow_step_is_waited_on_by_its_conversation() -> None:
    conversation = _conversation(
        origin=ConversationOrigin.WORKFLOW_STEP, workflow_run_id=uuid4()
    )
    origin = run_origin(conversation)
    assert origin.origin == "workflow"
    assert origin.parent_kind == "workflow"
    assert origin.parent_ref == str(conversation.id)
    assert turn_class_for(origin.origin) is WorkClass.AUTOMATION


def test_a_schedule_conversation_runs_unattended() -> None:
    origin = run_origin(_conversation(origin=ConversationOrigin.SCHEDULE))
    assert origin.origin == "schedule"
    assert origin.parent_kind is None
    assert turn_class_for(origin.origin) is WorkClass.AUTOMATION


def test_a_sub_agent_reports_to_its_parent() -> None:
    parent = uuid4()
    origin = run_origin(
        _conversation(
            origin=ConversationOrigin.SUBAGENT, parent_id=parent, reports_to_parent=True
        )
    )
    assert (origin.origin, origin.parent_kind, origin.parent_ref) == (
        "subagent",
        "agent",
        str(parent),
    )
    assert origin.settles_subagents


def test_a_conversation_pinned_under_a_project_is_a_chat() -> None:
    """A parent is not what makes a sub-agent: its origin is."""
    origin = run_origin(_conversation(parent_id=uuid4()))
    assert origin.origin == "chat"
    assert not origin.settles_subagents


def test_a_conversation_that_delegated_settles_what_comes_back() -> None:
    delegated = run_origin(_conversation(delegations_out=True))
    assert delegated.settles_subagents and delegated.settles_pod_asks
    plain = run_origin(_conversation())
    assert not plain.settles_subagents and not plain.settles_pod_asks


def test_a_pod_ask_on_the_answering_side() -> None:
    asked = run_origin(_conversation(origin=ConversationOrigin.POD_ASK, ask=_ask()))
    assert (asked.origin, asked.parent_kind, asked.settles_pod_asks) == (
        "pod_ask",
        "pod_ask",
        True,
    )


def test_a_surface_conversation_has_somewhere_to_reply() -> None:
    origin = run_origin(
        _conversation(
            origin=ConversationOrigin.SURFACE,
            metadata={"surface_platform": "SLACK", "surface_id": str(uuid4())},
        )
    )
    assert origin.origin == "surface"
    assert origin.has_reply_target
    assert turn_class_for(origin.origin) is WorkClass.INTERACTIVE


def test_metadata_a_client_wrote_says_nothing_about_the_origin() -> None:
    """The keys that used to stand for an origin are tags now."""
    origin = run_origin(
        _conversation(
            metadata={
                "source": "SCHEDULE",
                "is_sub_agent": True,
                "workflow_run_id": str(uuid4()),
            }
        )
    )
    assert origin.origin == "chat"
