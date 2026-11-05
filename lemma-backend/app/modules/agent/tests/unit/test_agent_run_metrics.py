"""Agent run metrics: their names, their label sets, and where they are recorded.

Every instrument reads into an in-memory reader through a meter handed to the
subject, so nothing here touches the process's global meter provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.run_metrics import (
    AgentRunMetrics,
    ended_as,
    ended_by,
    model_label,
)
from app.modules.agent.services.run_phase_spans import observe_first_output
from app.modules.agent.tools.graceful_toolset import GracefulToolset, tool_kind_of
from app.modules.agent.tools.connectors.pydantic_adapter import connectors_toolset

pytestmark = pytest.mark.unit

Points = dict[str, list[tuple[dict[str, object], object]]]


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def run_metrics(reader: InMemoryMetricReader) -> AgentRunMetrics:
    return AgentRunMetrics(MeterProvider(metric_readers=[reader]).get_meter("test"))


def _points(reader: InMemoryMetricReader) -> Points:
    """Every data point by instrument name, as (attributes, point)."""
    collected: Points = {}
    data = reader.get_metrics_data()
    for resource_metrics in data.resource_metrics if data else ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                collected[metric.name] = [
                    (dict(point.attributes or {}), point)
                    for point in metric.data.data_points
                ]
    return collected


def test_a_finished_run_is_counted_timed_and_leaves_the_active_gauge(
    run_metrics: AgentRunMetrics, reader: InMemoryMetricReader
) -> None:
    run_metrics.run_started(kind="chat")
    run_metrics.run_ended(
        kind="chat", outcome="completed", model="gpt-5.4-mini", seconds=3.0
    )

    points = _points(reader)
    [(labels, count)] = points["lemma.agent.runs"]
    assert labels == {"outcome": "completed", "kind": "chat", "model": "gpt-5.4-mini"}
    assert count.value == 1
    [(labels, duration)] = points["lemma.agent.run.duration"]
    assert labels == {"kind": "chat", "outcome": "completed"}
    assert (duration.count, duration.sum) == (1, 3.0)
    [(labels, active)] = points["lemma.agent.runs.active"]
    assert labels == {"kind": "chat"}
    assert active.value == 0


def test_first_tokens_and_tool_calls_carry_only_their_bounded_labels(
    run_metrics: AgentRunMetrics, reader: InMemoryMetricReader
) -> None:
    run_metrics.first_token(0.8, model=None)
    run_metrics.tool_call(tool_kind="connector", outcome="error")

    points = _points(reader)
    [(labels, first)] = points["lemma.agent.first_token"]
    assert labels == {"model": "unknown"}
    assert first.sum == 0.8
    [(labels, calls)] = points["lemma.agent.tool_calls"]
    assert labels == {"tool_kind": "connector", "outcome": "error"}
    assert calls.value == 1


def test_a_hand_typed_model_name_is_cleaned_and_bounded() -> None:
    assert model_label("  my model\n") == "my_model"
    assert len(model_label("x" * 500)) == 96
    assert model_label("") == "unknown"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ConversationStatus.COMPLETED, "completed"),
        (ConversationStatus.WAITING, "waiting"),
        (ConversationStatus.FAILED, "failed"),
        (ConversationStatus.STOPPED, "cancelled"),
        (None, "completed"),
    ],
)
def test_a_run_that_returned_is_counted_by_its_final_status(
    status: ConversationStatus | None, expected: str
) -> None:
    assert ended_as(status) == expected


def test_a_run_that_raised_is_failed_unless_another_worker_resumes_it() -> None:
    assert ended_by(ValueError("bad")) == "failed"
    assert ended_by(asyncio.CancelledError()) == "interrupted"


class _UnreachableDatabase:
    """A unit of work whose session cannot be opened."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self) -> _UnreachableDatabase:
        return self

    async def __aenter__(self) -> None:
        raise self._exc

    async def __aexit__(self, *args: object) -> None:
        return None


def _runner(
    run_metrics: AgentRunMetrics, exc: BaseException, monkeypatch
) -> AgentRunnerService:
    conversation = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        organization_id=UUID("00000000-0000-0000-0000-000000000011"),
        pod_id=UUID("00000000-0000-0000-0000-000000000012"),
        agent_id=UUID("00000000-0000-0000-0000-000000000013"),
        metadata={"source": "SCHEDULE"},
        parent_id=None,
    )
    agent_run = SimpleNamespace(
        started_at=None,
        status=AgentRunStatus.RUNNING,
        agent_runtime=None,
        error=None,
    )

    async def loaded(*_: object, **__: object):
        return conversation, SimpleNamespace(name="a"), agent_run, []

    # Loading reads four tables through real repositories; what is under test
    # starts after it.
    service = AgentRunnerService(
        uow_factory=_UnreachableDatabase(exc),
        harness_registry=HarnessRegistry({}),
        run_metrics=run_metrics,
        load_context=loaded,
    )
    monkeypatch.setattr(
        service.turn_resume, "committed_answer", AsyncMock(return_value=None)
    )
    return service


async def _execute(service: AgentRunnerService) -> None:
    await service.execute(
        agent_run_id=UUID("00000000-0000-0000-0000-000000000020"),
        user_id=UUID("00000000-0000-0000-0000-000000000021"),
        pod_id=UUID("00000000-0000-0000-0000-000000000022"),
        agent_name="a",
    )


@pytest.mark.asyncio
async def test_a_run_that_fails_is_counted_failed_under_its_origin(
    run_metrics: AgentRunMetrics, reader: InMemoryMetricReader, monkeypatch
) -> None:
    service = _runner(run_metrics, RuntimeError("database refused"), monkeypatch)

    await _execute(service)

    points = _points(reader)
    [(labels, count)] = points["lemma.agent.runs"]
    # The runtime never resolved, so the run has no model -- it is still
    # counted, against `unknown`.
    assert labels == {"outcome": "failed", "kind": "schedule", "model": "unknown"}
    assert count.value == 1
    [(labels, duration)] = points["lemma.agent.run.duration"]
    assert labels == {"kind": "schedule", "outcome": "failed"}
    assert duration.count == 1
    [(_, active)] = points["lemma.agent.runs.active"]
    assert active.value == 0


@pytest.mark.asyncio
async def test_a_run_its_worker_abandons_is_counted_interrupted_and_still_raises(
    run_metrics: AgentRunMetrics, reader: InMemoryMetricReader, monkeypatch
) -> None:
    service = _runner(run_metrics, asyncio.CancelledError(), monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await _execute(service)

    [(labels, _)] = _points(reader)["lemma.agent.runs"]
    assert labels["outcome"] == "interrupted"
    [(_, active)] = _points(reader)["lemma.agent.runs.active"]
    assert active.value == 0


async def _events(*types: AgentEventType) -> AsyncIterator[AgentEvent]:
    for event_type in types:
        yield AgentEvent(type=event_type, data="x")


@pytest.mark.asyncio
async def test_the_first_token_is_reported_once_and_a_silent_run_reports_none() -> None:
    seen: list[float] = []
    async for _ in observe_first_output(
        _events(AgentEventType.TOKEN, AgentEventType.TOKEN, AgentEventType.MESSAGE),
        on_first_token=seen.append,
    ):
        pass
    assert len(seen) == 1 and seen[0] >= 0

    silent: list[float] = []
    async for _ in observe_first_output(
        _events(AgentEventType.MESSAGE), on_first_token=silent.append
    ):
        pass
    assert silent == []


class _AnsweringToolset:
    def __init__(self, result: object) -> None:
        self._result = result

    async def call_tool(self, name, tool_args, ctx, tool):
        return self._result


@pytest.mark.asyncio
async def test_each_tool_call_is_counted_with_how_it_ended(
    run_metrics: AgentRunMetrics, reader: InMemoryMetricReader
) -> None:
    succeeding = GracefulToolset(_AnsweringToolset({"ok": 1}), run_metrics=run_metrics)
    failing = GracefulToolset(
        _AnsweringToolset({"success": False, "error": "no"}), run_metrics=run_metrics
    )

    await succeeding.call_tool("function_send_invoice", {}, None, None)
    await failing.call_tool("agent_researcher", {}, None, None)

    calls = {
        (labels["tool_kind"], labels["outcome"]): point.value
        for labels, point in _points(reader)["lemma.agent.tool_calls"]
    }
    assert calls == {("function", "ok"): 1, ("agent", "error"): 1}


def test_tool_kinds_follow_the_toolset_and_the_dynamic_prefixes() -> None:
    assert tool_kind_of("run_connector_operation", connectors_toolset) == "connector"
    assert tool_kind_of("function_x", FunctionToolset()) == "function"
    assert tool_kind_of("agent_x", FunctionToolset()) == "agent"
    assert tool_kind_of("ask_user", FunctionToolset()) == "builtin"


class _RecordingFinalizer:
    def __init__(self) -> None:
        self.finished: list[dict[str, object]] = []

    async def finish(self, **kwargs: object) -> None:
        self.finished.append(kwargs)


async def _said_nothing(agent_run_id: UUID) -> None:
    """No words on the record: what a completed run with no output finds."""
    del agent_run_id


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (AgentEventType.ERROR, "failed"),
        (AgentEventType.REJECTED, "failed"),
        (AgentEventType.WAITING, "waiting"),
        (AgentEventType.STOPPED, "cancelled"),
        (AgentEventType.COMPLETED, "completed"),
    ],
)
@pytest.mark.asyncio
async def test_a_terminal_event_is_counted_as_the_outcome_it_finalized(
    event_type: AgentEventType, expected: str
) -> None:
    """The pump finalizes these without a final status of their own; the
    metric reads it, and an unset one used to count every one as completed."""
    from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
    from app.modules.agent.services.run_identity import RunIdentity

    finalizer = _RecordingFinalizer()
    outcome = RunOutcome()
    run = RunIdentity(
        conversation_id=UUID("00000000-0000-0000-0000-000000000010"),
        agent_run_id=UUID("00000000-0000-0000-0000-000000000020"),
        organization_id=None,
        pod_id=UUID("00000000-0000-0000-0000-000000000012"),
        user_id=UUID("00000000-0000-0000-0000-000000000021"),
        agent_id=None,
        started_at=None,
    )

    terminal = await RunEventPump(
        SimpleNamespace(last_words=_said_nothing), finalizer
    ).handle(event=AgentEvent(type=event_type, data={}), run=run, outcome=outcome)

    assert terminal and len(finalizer.finished) == 1
    assert ended_as(outcome.final_status) == expected
