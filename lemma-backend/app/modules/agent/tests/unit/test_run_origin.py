"""What started a conversation's turns, read off the row the same way at start and at finish."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.turns import turn_class_for
from app.modules.agent.domain.run_origin import run_origin

pytestmark = pytest.mark.unit


def _origin(metadata=None, parent_id=None):
    return run_origin(metadata=metadata, parent_id=parent_id, conversation_id=uuid4())


def test_a_plain_chat() -> None:
    origin = _origin({})
    assert (origin.origin, origin.parent_kind, origin.has_reply_target) == (
        "chat",
        None,
        False,
    )


def test_a_workflow_step_is_waited_on_by_its_conversation() -> None:
    conversation_id = uuid4()
    origin = run_origin(
        metadata={"source": "WORKFLOW_RUN", "workflow_run_id": str(uuid4())},
        parent_id=None,
        conversation_id=conversation_id,
    )
    assert origin.origin == "workflow"
    assert origin.parent_kind == "workflow"
    assert origin.parent_ref == str(conversation_id)
    assert turn_class_for(origin.origin) is WorkClass.AUTOMATION


def test_a_schedule_conversation_runs_unattended() -> None:
    origin = _origin({"source": "SCHEDULE", "started_by": "SCHEDULE"})
    assert origin.origin == "schedule"
    assert origin.parent_kind is None
    assert turn_class_for(origin.origin) is WorkClass.AUTOMATION


def test_a_sub_agent_reports_to_its_parent() -> None:
    parent = uuid4()
    origin = _origin({"is_sub_agent": True, "reports_to_parent": True}, parent)
    assert (origin.origin, origin.parent_kind, origin.parent_ref) == (
        "subagent",
        "agent",
        str(parent),
    )
    assert origin.settles_subagents


def test_a_parent_with_children_out_settles_their_results() -> None:
    assert _origin({"subagents_out": True}).settles_subagents
    assert not _origin({}).settles_subagents


def test_a_pod_ask_on_either_side() -> None:
    asked = _origin({"ask": {"from_pod_id": str(uuid4())}})
    assert (asked.origin, asked.parent_kind, asked.settles_pod_asks) == (
        "pod_ask",
        "pod_ask",
        True,
    )
    asker = _origin({"pod_asks_out": True})
    assert asker.origin == "chat" and asker.settles_pod_asks


def test_a_surface_conversation_has_somewhere_to_reply() -> None:
    origin = _origin({"surface_platform": "SLACK", "surface_id": str(uuid4())})
    assert origin.origin == "surface"
    assert origin.has_reply_target
    assert turn_class_for(origin.origin) is WorkClass.INTERACTIVE
