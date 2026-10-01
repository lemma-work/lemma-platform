"""A run started by a private note in Lemma says nothing on the chat platform.

Every other run in a channel-linked conversation streams to the platform, shows
it typing, and posts its answer there -- which is how the group sees what it
asked for. A note's run is the one exception, so the observer must not so much
as open a unit of work for it: no stream, no typing indicator, no answer, no
approval card.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageRole,
)
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)

pytestmark = pytest.mark.unit


def _refusing_observer(touched: list[str]) -> SurfaceAgentRunProgressObserver:
    def _uow_factory():
        touched.append("unit of work")
        raise AssertionError("a private note's run reached for a unit of work")

    def _egress_factory(uow):
        touched.append("egress")
        raise AssertionError("a private note's run reached for the platform")

    return SurfaceAgentRunProgressObserver(
        uow_factory=_uow_factory, egress_factory=_egress_factory
    )


async def test_a_private_notes_run_never_reaches_the_platform():
    touched: list[str] = []
    observer = _refusing_observer(touched)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )
    ctx = SimpleNamespace(agent_run_id=uuid4(), delivers_to_surface=False)

    await observer.on_run_started(conversation, ctx)
    await observer.on_event(
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_text(
                "Tell him Friday, and not the discount.", role=MessageRole.ASSISTANT
            ),
        ),
        conversation,
        ctx,
    )
    await observer.on_event(
        AgentEvent(type=AgentEventType.WAITING, data={"tool_name": "request_approval"}),
        conversation,
        ctx,
    )
    await observer.on_run_finished(conversation, ctx)

    assert touched == []
