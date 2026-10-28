"""Starting a workflow step's or a schedule's run, and reading what became of it."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.agent.application.automation import start as automation_start
from app.modules.agent.application.automation.outcome import AutomatedRunOutcome
from app.modules.agent.contracts.automation import build_agent_control_adapter
from app.modules.agent.domain.automation_prompts import step_output
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.tests.unit.work_recording import RecordingWorkSession


def _uow():
    return Mock(session=RecordingWorkSession())


def _driver(agent):
    """The workflow's port over a recording session, its agent found and its
    pod's organization and default runtime answered without a database."""
    driver = build_agent_control_adapter(_uow())
    driver.agents = Mock(
        get=AsyncMock(return_value=agent),
        get_by_pod_and_name=AsyncMock(return_value=agent),
    )
    starts = driver.starts
    starts.organization_of = AsyncMock(return_value=uuid4())
    starts.default_runtime = AsyncMock(return_value=None)
    starts.conversations = starts.runs = starts.messages = Mock(
        create_conversation=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        append_message=AsyncMock(),
        collect_events=Mock(),
    )
    return driver, starts


def _outcome(conversation, waits=()):
    outcome = AutomatedRunOutcome.build(_uow())
    outcome.conversations = Mock(get_conversation=AsyncMock(return_value=conversation))
    outcome.pauses = Mock(pending=AsyncMock(return_value=list(waits)))
    return outcome


def test_normalize_agent_output_wraps_non_dict_as_answer():
    normalize = step_output
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
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
    driver, starts = _driver(agent)
    existing = SimpleNamespace(id=uuid4())
    create_reserved = AsyncMock(return_value=(existing, False))
    monkeypatch.setattr(automation_start, "create_conversation_for_id", create_reserved)

    result = await driver.run_agent_by_id(
        agent_id=agent.id,
        input_data={"ticket": 42},
        pod_id=pod_id,
        user_id=uuid4(),
        conversation_id=existing.id,
        source="SCHEDULE",
    )

    assert result == existing.id
    invocation = create_reserved.await_args.args[1]
    assert invocation.id == existing.id
    starts.runs.create_agent_run.assert_not_awaited()
    starts.messages.append_message.assert_not_awaited()


@pytest.mark.anyio
async def test_waiting_conversation_reports_wait_when_a_wait_is_active(monkeypatch):
    """The reporting half of the wait-expiry exemption.

    ``_expire_overdue_wait`` already exempts ``wait_reason == "WAIT"``, but
    nothing ever produced that value until agent waits landed. Without this the
    exemption is dead code and an agent sleeping past
    ``workflow_wait_max_age_seconds`` has its workflow failed while it is
    perfectly healthy — a silent wrong outcome, not a visible error.
    """
    conversation_id = uuid4()
    wakes_at = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    outcome = _outcome(
        SimpleNamespace(status=ConversationStatus.WAITING, output=None),
        waits=[SimpleNamespace(wake_at=wakes_at)],
    )

    status = await outcome.status(conversation_id)

    assert status["status"] == "WAITING"
    assert status["wait_reason"] == "WAIT"
    assert status["wakes_at"] == wakes_at.isoformat()


@pytest.mark.anyio
async def test_waiting_conversation_still_reports_human_without_a_wait():
    """An agent blocked on a person is the hang the ceiling exists to catch."""
    outcome = _outcome(SimpleNamespace(status=ConversationStatus.WAITING, output=None))

    status = await outcome.status(uuid4())

    assert status["wait_reason"] == "HUMAN"
    assert status["wakes_at"] is None


@pytest.mark.anyio
async def test_a_failed_conversation_carries_the_reason_the_agent_recorded():
    """Otherwise the workflow says an agent failed and nothing about why.

    `last_run_error` is the agent's own account of the failure; dropping it
    leaves "Agent conversation FAILED" as the only thing a run records, which
    is exactly as much as knowing the status.
    """
    outcome = _outcome(
        SimpleNamespace(
            status=ConversationStatus.FAILED,
            output=None,
            last_run_error="model provider returned 401",
        )
    )

    status = await outcome.status(uuid4())

    assert status["status"] == "FAILED"
    assert status["error"] == "Agent conversation FAILED: model provider returned 401"


@pytest.mark.anyio
async def test_a_failed_conversation_without_a_reason_reads_as_it_did():
    """No reason recorded is common; do not append a dangling colon for it."""
    outcome = _outcome(
        SimpleNamespace(
            status=ConversationStatus.FAILED, output=None, last_run_error=None
        )
    )

    status = await outcome.status(uuid4())

    assert status["error"] == "Agent conversation FAILED"


@pytest.mark.anyio
async def test_the_assistant_is_started_like_any_other_agent(monkeypatch):
    """The whole mechanism, in one assertion: it is an ordinary lookup.

    Starting the pod's own assistant headlessly used to need its own method,
    because there was no row to look up and `run_agent_by_id` could only raise
    for it. Now its row's id is the pod's, so the ordinary path serves it -- and
    the conversation still reads as the assistant's, which is what selects its
    base prompt.
    """
    pod_id = uuid4()
    assistant = SimpleNamespace(
        id=pod_id, pod_id=pod_id, name="pod_default", agent_runtime=None
    )
    adapter, starts = _driver(assistant)
    created = SimpleNamespace(id=uuid4())
    starts.conversations.create_conversation.return_value = created

    result = await adapter.run_agent_by_id(
        agent_id=pod_id,
        input_data={"payload": {}},
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        instructions="Post the overnight summary.",
    )

    assert result == created.id
    conversation = starts.conversations.create_conversation.await_args.args[0]
    assert conversation.agent_id == pod_id
    # Still the assistant, and still selects the assistant's base prompt.
    assert conversation.is_pod_assistant
    # The trigger's words reach the run as conversation instructions, which the
    # prompt appends after the agent's own -- which the assistant does not have.
    assert conversation.instructions == "Post the overnight summary."
    assert starts.runs.create_agent_run.await_args.kwargs["agent_id"] == pod_id


@pytest.mark.anyio
async def test_named_agent_run_carries_the_schedule_instruction_too(monkeypatch):
    """The instruction is not a Lem special case.

    A named agent is its own standing instruction, so a schedule's sentence adds
    to it rather than replacing it -- `build_agent_instructions` layers agent
    instruction then conversation instructions, in that order.
    """
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
    adapter, starts = _driver(agent)
    created = SimpleNamespace(id=uuid4())
    starts.conversations.create_conversation.return_value = created

    await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data={"payload": {}},
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        instructions="Only the tickets raised overnight.",
    )

    conversation = starts.conversations.create_conversation.await_args.args[0]
    assert conversation.agent_id == agent.id
    assert conversation.instructions == "Only the tickets raised overnight."


def _schedule_input(**metadata) -> dict:
    """A firing as `ScheduleStartService._build_trigger` now assembles one."""
    return {
        "payload": {},
        "metadata": {
            "schedule_id": str(uuid4()),
            "schedule_name": "weekday-triage",
            "trigger_type": "TIME",
            "fired_at": "2026-09-15T09:00:04+00:00",
            "scheduled_for": "2026-09-15T09:00:00+00:00",
            **metadata,
        },
        "llm_output": {},
    }


async def _start_scheduled_run(adapter_input: dict, instructions: str | None):
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
    adapter, starts = _driver(agent)

    await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data=adapter_input,
        pod_id=pod_id,
        user_id=uuid4(),
        source="SCHEDULE",
        instructions=instructions,
    )
    draft = starts.messages.append_message.await_args.kwargs["draft"]
    conversation = starts.conversations.create_conversation.await_args.args[0]
    return draft.text, conversation


@pytest.mark.anyio
async def test_a_time_fired_agent_is_told_what_woke_it_and_when():
    """The message a cron-started agent actually reads.

    It used to be, in full:

        Workflow input JSON:
        {"payload": {}, "metadata": {}, "llm_output": {}}

    Three empty objects under a heading naming a workflow that does not exist.
    Nothing pinned this text -- "Workflow input JSON:" appeared exactly once in
    the tree, with no test behind it -- so it stayed wrong.
    """
    text, conversation = await _start_scheduled_run(
        _schedule_input(), "Summarise yesterday's open tickets."
    )

    assert '"weekday-triage"' in text
    assert "Summarise yesterday's open tickets." in text
    assert "2026-09-15T09:00:04+00:00" in text
    # The occurrence it is *for*, which a redrive or a busy queue separates from
    # the moment it ran.
    assert "2026-09-15T09:00:00+00:00" in text
    # The empty envelope is gone -- both the heading and the `{}`s.
    assert "Workflow input JSON" not in text
    assert '"payload": {}' not in text
    assert "no event data" in text

    assert conversation.title == "Schedule: weekday-triage"
    # Still in the system prompt too: that is what survives into later turns,
    # while the message above is what the first turn is answering.
    assert conversation.instructions == "Summarise yesterday's open tickets."


@pytest.mark.anyio
async def test_a_schedules_timezone_is_named_so_utc_is_not_read_as_local():
    text, _ = await _start_scheduled_run(
        _schedule_input(timezone="Europe/Berlin"), "Post the digest."
    )
    assert "Europe/Berlin" in text


@pytest.mark.anyio
async def test_an_event_firing_still_carries_its_row():
    """A datastore firing has a body, and it must survive the new rendering."""
    firing = _schedule_input(
        trigger_type="DATASTORE",
        table_name="tickets",
        record_id=41,
        operation="INSERT",
    )
    firing["payload"] = {"id": 41, "title": "Printer on fire"}

    text, _ = await _start_scheduled_run(firing, "Triage it.")

    assert "Printer on fire" in text
    assert '"table_name": "tickets"' in text
    assert '"record_id": 41' in text
    # The facts about the firing are a sentence at the top, so they are not also
    # repeated inside the event body.
    assert '"schedule_name"' not in text
    assert '"fired_at"' not in text


@pytest.mark.anyio
async def test_a_workflow_node_still_gets_the_json_envelope():
    """The other caller is unchanged, deliberately.

    Workflow nodes bind their inputs to this shape; only a schedule firing gets
    the wake-up rendering.
    """
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="triage", agent_runtime=None
    )
    adapter, starts = _driver(agent)

    await adapter.run_agent_by_id(
        agent_id=agent.id,
        input_data={"payload": {"order_id": 7}},
        pod_id=pod_id,
        user_id=uuid4(),
        workflow_run_id=uuid4(),
    )

    draft = starts.messages.append_message.await_args.kwargs["draft"]
    assert draft.text.startswith("Workflow input JSON:")
    conversation = starts.conversations.create_conversation.await_args.args[0]
    assert conversation.title == "Workflow run: triage"
