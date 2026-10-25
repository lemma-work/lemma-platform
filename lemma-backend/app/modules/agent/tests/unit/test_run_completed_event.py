"""What a finished run's ``agent.run.completed`` event says about how it arrived.

A turn runs as a work item, and a work item starts with no origin bound -- so
the event, which captures the origin bound where it is built, used to carry
none for every run a worker finished. Product analytics read that as "nobody
knows how this work arrived", which made a surface's answers uncountable
(``surface.message_answered`` is pinned to the SURFACE origin) and kept every
scheduled agent run out of activation. The conversation records what started
it; the run carries that to the event.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.origin import Origin, OriginKind, origin_scope
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import AskChain, AskMode, PodAsk
from app.modules.agent.domain.run_origin import conversation_origin, run_origin
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    ConversationOrigin,
    MessageDraft,
)
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_finalizer import (
    completed_event,
    counted_tool_calls,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.usage.contracts import AgentRunUsage

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


@pytest.mark.parametrize(
    ("schedule_type", "kind"),
    [
        ("TIME", OriginKind.SCHEDULE),
        ("DATASTORE", OriginKind.DATA_TRIGGER),
        ("WEBHOOK", OriginKind.CONNECTOR),
        (None, OriginKind.SCHEDULE),
    ],
)
def test_a_scheduled_conversation_names_what_fired_it(schedule_type, kind) -> None:
    metadata = {"schedule_type": schedule_type} if schedule_type is not None else {}
    conversation = _conversation(origin=ConversationOrigin.SCHEDULE, metadata=metadata)
    assert conversation_origin(conversation) == Origin(kind)


def test_a_surface_conversation_names_its_platform() -> None:
    conversation = _conversation(
        origin=ConversationOrigin.SURFACE,
        metadata={"surface_platform": "SLACK", "surface_id": "s"},
    )
    assert conversation_origin(conversation) == Origin(
        OriginKind.SURFACE, platform="slack"
    )


def test_a_workflow_step_is_the_workflow() -> None:
    conversation = _conversation(
        origin=ConversationOrigin.WORKFLOW_STEP, workflow_run_id=uuid4()
    )
    assert conversation_origin(conversation) == Origin(OriginKind.WORKFLOW)


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("chat", {}),
        ("subagent", {"origin": ConversationOrigin.SUBAGENT, "parent_id": uuid4()}),
        ("pod_ask", {"origin": ConversationOrigin.POD_ASK, "ask": _ask()}),
    ],
)
def test_a_conversation_that_cannot_say_which_client_answers_none(kind, fields) -> None:
    """A person's chat came through some client the row does not record. No
    origin is a gap; a guessed one is a wrong number."""
    conversation = _conversation(**fields)
    assert run_origin(conversation).origin == kind
    assert conversation_origin(conversation) is None


def _usage(**fields) -> AgentRunUsage:
    return AgentRunUsage(model_name="m", **fields)


def test_tool_calls_are_counted_only_where_the_runtime_counts_them() -> None:
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    assert counted_tool_calls(run, _usage(tool_call_count=0)) == 0
    assert counted_tool_calls(run, _usage(tool_call_count=4)) == 4
    assert counted_tool_calls(run, None) is None
    # The in-process harness reports no usage; the calls it streamed count.
    assert counted_tool_calls(run, None, seen=3) == 3
    # An Agent Host reports tokens and nothing about tools: its zero is the
    # field's default, not a count.
    hosted = run.with_runtime_profile({"harness_id": str(uuid4())})
    assert counted_tool_calls(hosted, _usage(tool_call_count=0)) is None


def test_the_pump_counts_the_tool_calls_a_run_writes() -> None:
    """What `seen` is: the in-process harness's only count of its tool calls."""
    writer = SimpleNamespace(
        output_data_from_event=lambda event: None,
        final_status_from_event=lambda event: (None, None),
    )
    pump = RunEventPump(message_writer=writer, finalizer=None)
    outcome = RunOutcome()
    run_id = uuid4()
    for draft in (
        MessageDraft.of_text("Looking."),
        MessageDraft.of_tool_call(
            tool_name="pod_query", tool_call_id="a", tool_args={}
        ),
        MessageDraft.of_tool_call(
            tool_name="web_search", tool_call_id="b", tool_args={}
        ),
    ):
        pump._absorb_message(
            AgentEvent(type=AgentEventType.MESSAGE, data=draft, agent_run_id=run_id),
            outcome,
        )

    assert outcome.tool_calls_seen == 2


def test_the_completed_event_carries_the_runs_origin_and_usage() -> None:
    run = RunIdentity(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        origin=Origin(OriginKind.SURFACE, platform="slack"),
    )

    event = completed_event(
        run,
        status=AgentRunStatus.COMPLETED,
        data=None,
        usage=_usage(input_tokens=1_200, output_tokens=300, tool_call_count=2),
    )

    assert (event.origin, event.origin_platform) == ("SURFACE", "slack")
    assert (event.token_count, event.tool_call_count) == (1_500, 2)
    # And with no usage reported, no counts rather than zeros.
    bare = completed_event(run, status=AgentRunStatus.FAILED, data=None, usage=None)
    assert (bare.token_count, bare.tool_call_count) == (None, None)


def test_a_turn_run_by_a_worker_takes_the_origin_its_conversation_records() -> None:
    conversation = _conversation(
        organization_id=uuid4(),
        origin=ConversationOrigin.SCHEDULE,
        metadata={"schedule_type": "DATASTORE"},
    )
    agent_run = SimpleNamespace(started_at=None)

    agent_run_id = uuid4()
    run = RunIdentity.of_turn(
        conversation,
        agent_run,
        agent_run_id=agent_run_id,
        user_id=uuid4(),
        usage_source="agent_run",
    )

    assert run.origin == Origin(OriginKind.DATA_TRIGGER)
    assert (run.agent_run_id, run.pod_id) == (agent_run_id, conversation.pod_id)


def test_a_turn_run_inside_a_request_keeps_the_requests_origin() -> None:
    conversation = _conversation(
        origin=ConversationOrigin.SURFACE,
        metadata={"surface_platform": "SLACK", "surface_id": "s"},
    )
    agent_run = SimpleNamespace(started_at=None)

    with origin_scope(Origin(OriginKind.WEB)):
        run = RunIdentity.of_turn(
            conversation,
            agent_run,
            agent_run_id=uuid4(),
            user_id=uuid4(),
            usage_source="agent_run",
        )

    assert run.origin == Origin(OriginKind.WEB)
