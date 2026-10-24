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
from app.modules.agent.domain.pod_asks import ASK_KEY
from app.modules.agent.domain.run_origin import conversation_origin, run_origin
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.services.run_finalizer import (
    completed_event,
    counted_tool_calls,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.usage.contracts import AgentRunUsage

pytestmark = pytest.mark.unit


def _origin(metadata: dict[str, object] | None, parent_id=None) -> Origin | None:
    return conversation_origin(
        metadata=metadata, parent_id=parent_id, conversation_id=uuid4()
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
    metadata = {"source": "SCHEDULE", "started_by": "SCHEDULE"}
    if schedule_type is not None:
        metadata["schedule_type"] = schedule_type
    assert _origin(metadata) == Origin(kind)


def test_a_surface_conversation_names_its_platform() -> None:
    origin = _origin(
        {"source": "agent_surfaces", "surface_platform": "SLACK", "surface_id": "s"}
    )
    assert origin == Origin(OriginKind.SURFACE, platform="slack")


def test_a_workflow_step_is_the_workflow() -> None:
    origin = _origin({"source": "WORKFLOW_RUN", "workflow_run_id": str(uuid4())})
    assert origin == Origin(OriginKind.WORKFLOW)


@pytest.mark.parametrize(
    ("kind", "metadata", "parent_id"),
    [
        ("chat", {}, None),
        ("subagent", {}, uuid4()),
        ("pod_ask", {ASK_KEY: {"from": "x"}}, None),
    ],
)
def test_a_conversation_that_cannot_say_which_client_answers_none(
    kind, metadata, parent_id
) -> None:
    """A person's chat came through some client the row does not record. No
    origin is a gap; a guessed one is a wrong number."""
    started = run_origin(metadata=metadata, parent_id=parent_id, conversation_id=1)
    assert started.origin == kind
    assert _origin(metadata, parent_id) is None


def _usage(**fields) -> AgentRunUsage:
    return AgentRunUsage(model_name="m", **fields)


def test_tool_calls_are_counted_only_where_the_runtime_counts_them() -> None:
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    assert counted_tool_calls(run, _usage(tool_call_count=0)) == 0
    assert counted_tool_calls(run, _usage(tool_call_count=4)) == 4
    assert counted_tool_calls(run, None) is None
    # An Agent Host reports tokens and nothing about tools: its zero is the
    # field's default, not a count.
    hosted = run.with_runtime_profile({"harness_id": str(uuid4())})
    assert counted_tool_calls(hosted, _usage(tool_call_count=0)) is None


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
    conversation = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        agent_id=None,
        parent_id=None,
        metadata={"source": "SCHEDULE", "schedule_type": "DATASTORE"},
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
    conversation = SimpleNamespace(
        id=uuid4(),
        organization_id=None,
        pod_id=uuid4(),
        agent_id=None,
        parent_id=None,
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
