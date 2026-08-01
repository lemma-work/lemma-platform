"""Agent adapter output normalization for the workflow resume path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.composition.workflow_agent import AgentControlAdapter
from app.composition import workflow_agent


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
async def test_reserved_id_returns_existing_conversation_without_side_effects(
    monkeypatch,
):
    adapter = AgentControlAdapter(Mock(session=Mock()))
    pod_id = uuid4()
    agent = SimpleNamespace(id=uuid4(), pod_id=pod_id, name="triage")
    adapter.agent_repo = Mock(
        get=AsyncMock(return_value=agent),
        get_by_pod_and_name=AsyncMock(return_value=agent),
    )
    existing = SimpleNamespace(id=uuid4())
    create_reserved = AsyncMock(return_value=(existing, False))
    monkeypatch.setattr(workflow_agent, "create_conversation_for_id", create_reserved)
    adapter.conversation_repo = Mock(
        create_agent_run=AsyncMock(),
        append_message=AsyncMock(),
    )
    adapter._get_pod_organization_id = AsyncMock(return_value=uuid4())
    conversation_id = existing.id

    result = await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data={"ticket": 42},
        pod_id=pod_id,
        user_id=uuid4(),
        conversation_id=conversation_id,
        source="SCHEDULE",
    )

    assert result == existing.id
    invocation = create_reserved.await_args.args[1]
    assert invocation.id == conversation_id
    assert invocation.origin_type is None
    assert invocation.origin_id is None
    adapter.conversation_repo.create_agent_run.assert_not_awaited()
    adapter.conversation_repo.append_message.assert_not_awaited()
