"""An AGENT step hears only about its own conversation's runs, and only finished ones."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.workflow.events.agent_resume_work import (
    _STILL_WORKING,
    is_workflow_step,
)

pytestmark = pytest.mark.unit


def _fact(parent_kind: str | None, conversation_status: str = "COMPLETED"):
    return AgentRunFinished(
        run_id=uuid4(),
        conversation_id=uuid4(),
        pod_id=uuid4(),
        status="COMPLETED",
        conversation_status=conversation_status,  # type: ignore[arg-type]
        parent_kind=parent_kind,  # type: ignore[arg-type]
        origin="workflow",
    )


def test_only_a_workflow_steps_conversation_resumes_a_run() -> None:
    assert is_workflow_step(_fact("workflow"))
    assert not is_workflow_step(_fact(None))
    assert not is_workflow_step(_fact("agent"))


def test_a_conversation_still_working_is_not_an_answer() -> None:
    assert {"RUNNING", "STOP_REQUESTED", "WAITING"} == _STILL_WORKING
