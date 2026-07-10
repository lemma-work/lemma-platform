"""Agent adapter output normalization for the workflow resume path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.workflow.infrastructure.adapters.agent_adapter import (
    AgentControlAdapter,
)


def test_normalize_agent_output_wraps_non_dict_as_answer():
    normalize = AgentControlAdapter._normalize_agent_output
    # Structured output (agent has an output_schema) passes through.
    assert normalize({"answer": "x", "score": 1}) == {"answer": "x", "score": 1}
    # No output_schema -> bare string -> {"answer": text}.
    assert normalize("All done.") == {"answer": "All done."}
    # Non-string non-dict still becomes a dict so the resume never crashes.
    assert normalize(["a", "b"]) == {"answer": ["a", "b"]}
    # Empty / missing -> empty dict.
    assert normalize(None) == {}
    assert normalize("") == {}


@pytest.mark.anyio
async def test_schedule_origin_returns_existing_conversation_without_side_effects():
    adapter = AgentControlAdapter(Mock(session=Mock()))
    pod_id = uuid4()
    agent = SimpleNamespace(id=uuid4(), pod_id=pod_id, name="triage")
    adapter.agent_repo = Mock(
        get=AsyncMock(return_value=agent),
        get_by_pod_and_name=AsyncMock(return_value=agent),
    )
    existing = SimpleNamespace(id=uuid4())
    adapter.conversation_repo = Mock(
        create_conversation_once=AsyncMock(return_value=(existing, False)),
        create_agent_run=AsyncMock(),
        append_message=AsyncMock(),
    )
    adapter._get_pod_organization_id = AsyncMock(return_value=uuid4())
    origin_id = uuid4()

    result = await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data={"ticket": 42},
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        origin_type="SCHEDULE_RUN",
        origin_id=origin_id,
    )

    assert result == existing.id
    invocation = adapter.conversation_repo.create_conversation_once.await_args.args[0]
    assert invocation.origin_type == "SCHEDULE_RUN"
    assert invocation.origin_id == origin_id
    adapter.conversation_repo.create_agent_run.assert_not_awaited()
    adapter.conversation_repo.append_message.assert_not_awaited()
