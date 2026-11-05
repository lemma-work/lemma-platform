"""A surface that fails to deliver a run leaves a trace.

The observer is how a run reaches Slack, Teams or email. Its failures never
fail the run, which is right -- but they were logged at debug, which production
drops, so a person who never got their answer left nothing behind to find.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.contracts.run_observer import RunObserver
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.services.run_observer_delivery import (
    notify_event,
    notify_run_failed,
    notify_run_finished,
    notify_run_started,
)
from app.modules.agent.domain.context import ToolContext

pytestmark = pytest.mark.asyncio


class _BrokenSurface:
    """Every delivery raises, the way a bug in a platform adapter would."""

    async def on_run_started(
        self, conversation: Conversation, ctx: ToolContext
    ) -> None:
        raise RuntimeError("start blew up")

    async def on_event(
        self, event: AgentEvent, conversation: Conversation, ctx: ToolContext
    ) -> None:
        raise RuntimeError("event blew up")

    async def on_run_finished(
        self, conversation: Conversation, ctx: ToolContext
    ) -> None:
        raise RuntimeError("finish blew up")

    async def on_run_failed(self, conversation: Conversation, error: Exception) -> None:
        raise RuntimeError("failure blew up")


def _surface() -> RunObserver:
    return _BrokenSurface()


def _context(conversation: Conversation) -> ToolContext:
    return ToolContext(
        user_id=conversation.user_id,
        org_id=conversation.organization_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
    )


@pytest.fixture
def conversation() -> Conversation:
    return Conversation(pod_id=uuid4(), user_id=uuid4(), organization_id=uuid4())


async def test_a_failed_delivery_is_a_warning_with_its_traceback(
    conversation: Conversation, caplog: pytest.LogCaptureFixture
) -> None:
    run_id = uuid4()
    ctx = _context(conversation)
    event = AgentEvent(type=AgentEventType.STATUS, data={}, agent_run_id=run_id)

    with caplog.at_level("WARNING"):
        started = await notify_run_started(_surface(), conversation, ctx, run_id)
        await notify_event(_surface(), event, conversation, ctx, run_id)
        await notify_run_finished(_surface(), conversation, ctx, run_id)
        await notify_run_failed(_surface(), conversation, ValueError("x"), run_id)

    assert started is False
    for name in (
        "agent.run_observer.start_delivery.degraded",
        "agent.run_observer.event_delivery.degraded",
        "agent.run_observer.finish_delivery.degraded",
        "agent.run_observer.failure_delivery.degraded",
    ):
        assert name in caplog.text
    for message in ("start", "event", "finish", "failure"):
        assert f"{message} blew up" in caplog.text
    assert all(record.levelname == "WARNING" for record in caplog.records)
