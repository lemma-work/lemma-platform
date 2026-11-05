"""What a parent reads when a child it did not wait for finishes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.subagent_results import (
    MAX_RESULT_CHARS,
    result_message,
    result_text,
)
from app.modules.agent.domain.value_objects import AgentRunStatus

pytestmark = pytest.mark.unit


def test_an_answer_is_read_out_of_the_output() -> None:
    assert result_text({"answer": "12 open issues"}) == "12 open issues"
    assert result_text("plain") == "plain"
    assert '"count": 12' in result_text({"count": 12})


def test_a_failure_says_why() -> None:
    message = result_message(
        agent_name="triage",
        child_conversation_id=uuid4(),
        status=AgentRunStatus.FAILED,
        output=None,
        error="the model is not available",
    )
    assert "failed" in message and "the model is not available" in message


def test_a_long_result_is_cut_and_says_where_the_rest_is() -> None:
    message = result_message(
        agent_name="triage",
        child_conversation_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
        output="x" * (MAX_RESULT_CHARS * 2),
        error=None,
    )
    assert len(message) < MAX_RESULT_CHARS + 500
    assert "query_subagents" in message
