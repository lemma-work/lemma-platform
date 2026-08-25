"""Agent adapter output normalization for the workflow resume path."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.composition.workflow_agent import AgentControlAdapter
from app.composition import workflow_agent
from app.modules.agent.domain.value_objects import ConversationStatus


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
    # `agent_runtime` is read to hand to the shared start path, before the
    # reserved-id branch can return. A real `Agent` always carries it; the fake
    # has to as well, or this asserts against a shape that never occurs.
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
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


@pytest.mark.anyio
async def test_waiting_conversation_reports_snooze_when_a_wait_is_active(monkeypatch):
    """The reporting half of the wait-expiry exemption.

    ``_expire_overdue_wait`` already exempts ``wait_reason == "SNOOZE"``, but
    nothing ever produced that value until agent snooze landed. Without this the
    exemption is dead code and an agent sleeping past
    ``workflow_wait_max_age_seconds`` has its workflow failed while it is
    perfectly healthy — a silent wrong outcome, not a visible error.
    """
    conversation_id = uuid4()
    wakes_at = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    adapter = AgentControlAdapter(Mock(session=Mock()))
    adapter.conversation_repo = Mock(
        get_conversation=AsyncMock(
            return_value=SimpleNamespace(status=ConversationStatus.WAITING, output=None)
        )
    )
    adapter.wait_repo = Mock(
        find_active_for_conversation=AsyncMock(
            return_value=SimpleNamespace(scheduled_at=wakes_at)
        )
    )

    status = await adapter.get_conversation_status(conversation_id)

    assert status["status"] == "WAITING"
    assert status["wait_reason"] == "SNOOZE"
    assert status["wakes_at"] == wakes_at.isoformat()


@pytest.mark.anyio
async def test_waiting_conversation_still_reports_human_without_a_snooze():
    """An agent blocked on a person is the hang the ceiling exists to catch."""
    adapter = AgentControlAdapter(Mock(session=Mock()))
    adapter.conversation_repo = Mock(
        get_conversation=AsyncMock(
            return_value=SimpleNamespace(status=ConversationStatus.WAITING, output=None)
        )
    )
    adapter.wait_repo = Mock(find_active_for_conversation=AsyncMock(return_value=None))

    status = await adapter.get_conversation_status(uuid4())

    assert status["wait_reason"] == "HUMAN"
    assert status["wakes_at"] is None


@pytest.mark.anyio
async def test_a_failed_conversation_carries_the_reason_the_agent_recorded():
    """Otherwise the workflow says an agent failed and nothing about why.

    `last_run_error` is the agent's own account of the failure; dropping it
    leaves "Agent conversation FAILED" as the only thing a run records, which
    is exactly as much as knowing the status.
    """
    adapter = AgentControlAdapter(Mock(session=Mock()))
    adapter.conversation_repo = Mock(
        get_conversation=AsyncMock(
            return_value=SimpleNamespace(
                status=ConversationStatus.FAILED,
                output=None,
                last_run_error="model provider returned 401",
            )
        )
    )

    status = await adapter.get_conversation_status(uuid4())

    assert status["status"] == "FAILED"
    assert status["error"] == "Agent conversation FAILED: model provider returned 401"


@pytest.mark.anyio
async def test_a_failed_conversation_without_a_reason_reads_as_it_did():
    """No reason recorded is common; do not append a dangling colon for it."""
    adapter = AgentControlAdapter(Mock(session=Mock()))
    adapter.conversation_repo = Mock(
        get_conversation=AsyncMock(
            return_value=SimpleNamespace(
                status=ConversationStatus.FAILED, output=None, last_run_error=None
            )
        )
    )

    status = await adapter.get_conversation_status(uuid4())

    assert status["error"] == "Agent conversation FAILED"


@pytest.mark.anyio
async def test_pod_default_run_creates_a_conversation_with_no_agent(monkeypatch):
    """The whole mechanism, in one assertion: `agent_id` is null.

    A conversation with no agent *is* the pod's default assistant -- the runner
    synthesises Lem from exactly that, the prompt picks its base prompt off
    `is_pod_assistant`, which is the same null check. So starting Lem headlessly
    needs no new execution path, only a conversation that names nobody.
    """
    adapter = AgentControlAdapter(Mock(session=Mock()))
    pod_id = uuid4()
    adapter.agent_repo = Mock(get=AsyncMock(), get_by_pod_and_name=AsyncMock())
    created = SimpleNamespace(id=uuid4())
    adapter.conversation_repo = Mock(
        create_conversation=AsyncMock(return_value=created),
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        append_message=AsyncMock(),
        collect_events=Mock(),
    )
    adapter._get_pod_organization_id = AsyncMock(return_value=uuid4())
    adapter._default_agent_runtime_for_pod = AsyncMock(return_value=None)

    result = await adapter.run_pod_default_agent(
        input_data={"payload": {}},
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        instructions="Post the overnight summary.",
    )

    assert result == created.id
    # No lookup happened, because there is nothing to look up.
    adapter.agent_repo.get.assert_not_awaited()
    adapter.agent_repo.get_by_pod_and_name.assert_not_awaited()
    conversation = adapter.conversation_repo.create_conversation.await_args.args[0]
    assert conversation.agent_id is None
    assert conversation.is_pod_assistant
    # The trigger's words reach the run as conversation instructions, which the
    # prompt appends after the agent instruction Lem does not have.
    assert conversation.instructions == "Post the overnight summary."
    assert (
        adapter.conversation_repo.create_agent_run.await_args.kwargs["agent_id"] is None
    )


@pytest.mark.anyio
async def test_named_agent_run_carries_the_schedule_instruction_too(monkeypatch):
    """The instruction is not a Lem special case.

    A named agent is its own standing instruction, so a schedule's sentence adds
    to it rather than replacing it -- `build_agent_instructions` layers agent
    instruction then conversation instructions, in that order.
    """
    adapter = AgentControlAdapter(Mock(session=Mock()))
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
    adapter.agent_repo = Mock(
        get=AsyncMock(return_value=agent),
        get_by_pod_and_name=AsyncMock(return_value=agent),
    )
    created = SimpleNamespace(id=uuid4())
    adapter.conversation_repo = Mock(
        create_conversation=AsyncMock(return_value=created),
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        append_message=AsyncMock(),
        collect_events=Mock(),
    )
    adapter._get_pod_organization_id = AsyncMock(return_value=uuid4())
    adapter._default_agent_runtime_for_pod = AsyncMock(return_value=None)

    await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data={"payload": {}},
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        instructions="Only the tickets raised overnight.",
    )

    conversation = adapter.conversation_repo.create_conversation.await_args.args[0]
    assert conversation.agent_id == agent.id
    assert conversation.instructions == "Only the tickets raised overnight."
