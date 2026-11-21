"""A run as its read route answers it: outcome and tokens, never money."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.agent.api.run_schemas import AgentRunResponse
from app.modules.agent.api.schemas import ConversationResponse
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.runtime_choice import RuntimePurpose
from app.modules.agent.domain.value_objects import AgentRunStatus, AgentRuntimeConfig
from app.modules.agent.services.background_model import (
    AFTER_THE_RUN_SOURCES,
    USAGE_SOURCES,
)
from app.modules.usage.contracts.run_usage import RunTokenUsage

pytestmark = pytest.mark.unit


def _run() -> AgentRun:
    return AgentRun(
        id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        output_data={"answer": 42},
    )


def test_a_run_carries_its_tokens_and_no_cost():
    run = _run()
    usage = RunTokenUsage(
        input_tokens=260,
        output_tokens=55,
        cached_input_tokens=180,
        models=["model-a"],
    )

    wire = AgentRunResponse.of(run, usage).model_dump(mode="json")

    assert wire["id"] == str(run.id)
    assert wire["output"] == {"answer": 42}
    assert wire["usage"] == {
        "input_tokens": 260,
        "output_tokens": 55,
        "cached_input_tokens": 180,
        "cache_write_tokens": 0,
        "models": ["model-a"],
    }
    assert not any("cost" in key for key in wire)
    assert usage.total_tokens == 315


def test_a_run_with_nothing_recorded_says_so():
    assert AgentRunResponse.of(_run(), None).usage is None


def test_work_that_lands_after_the_run_is_not_its_answer():
    """The title and the memory pass land later; a run must read the same
    before and after they do. An image the run read is the run's own."""
    assert USAGE_SOURCES[RuntimePurpose.TITLE] in AFTER_THE_RUN_SOURCES
    assert USAGE_SOURCES[RuntimePurpose.MEMORY] in AFTER_THE_RUN_SOURCES
    assert USAGE_SOURCES[RuntimePurpose.VISION] not in AFTER_THE_RUN_SOURCES


def test_the_conversation_names_its_latest_run():
    assert "last_run_id" in ConversationResponse.model_fields
