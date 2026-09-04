"""A harness stream that stops without saying how the run turned out.

`drive` finalizes when it sees a terminal event. Where the stream simply ends
without one, nothing finalized and nothing raised — so the run and its
conversation were both left RUNNING with nothing that would ever move them.
`reconcile_orphaned_agent_runs` is no backstop: it waits an hour, and until now
only repaired the run. Meanwhile the schedule ledger's recovery sweep and the
workflow control adapter both decide an outcome from `conversation.status`, so
one of these wedges the schedule run and the workflow step waiting on it.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.services import run_event_pump as pump_module
from app.modules.agent.services.run_identity import RunIdentity


class _RecordingFinalizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def finish(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def _nothing() -> AsyncIterator[AgentEvent]:
    """A harness that produces no events at all and then stops."""
    return
    yield  # pragma: no cover - makes this an async generator


async def _one_token(run_id) -> AsyncIterator[AgentEvent]:
    """A harness that says something and then stops without finishing."""
    yield AgentEvent(
        type=AgentEventType.TOKEN,
        data={"kind": "text", "data": "half an ans"},
        agent_run_id=run_id,
    )


@pytest.mark.asyncio
async def test_a_stream_that_ends_without_a_terminal_event_is_failed(monkeypatch):
    """The gap itself: no terminal event, so nothing had finalized."""
    monkeypatch.setattr(pump_module, "notify_event", _ignore_event)
    finalizer = _RecordingFinalizer()
    pump = pump_module.RunEventPump(message_writer=None, finalizer=finalizer)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    outcome = pump_module.RunOutcome()

    await pump.drive(
        _one_token(run.agent_run_id),
        run=run,
        outcome=outcome,
        observer=None,
        conversation=None,
        ctx=None,
    )

    assert len(finalizer.calls) == 1
    call = finalizer.calls[0]
    # FAILED, not COMPLETED: the model never said it was done, and handing back
    # an empty result as though it were an answer is worse than saying so.
    assert call["status"] is AgentRunStatus.FAILED
    assert call["conversation_status"] is ConversationStatus.FAILED
    assert "without producing a result" in call["error"]
    # The conversation must be settled too, not just the run — leaving it
    # active is the whole failure this closes.
    assert outcome.terminal_seen is True


@pytest.mark.asyncio
async def test_an_empty_stream_is_failed_too(monkeypatch):
    """A harness that yields nothing at all is the same defect, earlier."""
    monkeypatch.setattr(pump_module, "notify_event", _ignore_event)
    finalizer = _RecordingFinalizer()
    pump = pump_module.RunEventPump(message_writer=None, finalizer=finalizer)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    outcome = pump_module.RunOutcome()

    await pump.drive(
        _nothing(),
        run=run,
        outcome=outcome,
        observer=None,
        conversation=None,
        ctx=None,
    )

    assert len(finalizer.calls) == 1
    assert finalizer.calls[0]["status"] is AgentRunStatus.FAILED


@pytest.mark.asyncio
async def test_a_stream_that_did_finish_is_not_finalized_twice(monkeypatch):
    """The guard on the guard.

    A run that already reached a terminal event has been finalized inside the
    loop, and finalizing again would publish a second completion — and, worse,
    overwrite a COMPLETED run with FAILED.
    """
    monkeypatch.setattr(pump_module, "notify_event", _ignore_event)
    finalizer = _RecordingFinalizer()
    pump = pump_module.RunEventPump(message_writer=None, finalizer=finalizer)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    outcome = pump_module.RunOutcome()
    outcome.terminal_seen = True

    await pump.drive(
        _nothing(),
        run=run,
        outcome=outcome,
        observer=None,
        conversation=None,
        ctx=None,
    )

    assert finalizer.calls == []


async def _ignore_event(*args: Any, **kwargs: Any) -> None:
    _ = args, kwargs
