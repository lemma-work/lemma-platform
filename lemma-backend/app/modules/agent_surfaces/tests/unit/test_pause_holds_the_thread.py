"""A run paused on the person is never left behind by what comes next.

The approval card and the typed answer both reach only the conversation that
paused. A notification sent later, or a reply a day later, used to be able to
start a fresh conversation -- the card's buttons then did nothing, and
"approve" went to an agent with nothing to approve. A person now has one
conversation with an agent, so both land where the pause is.

Driven through the same journey harness as the notification tests, over the
real `NotificationService`, `NotificationEgress` and `ConversationBinder`.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.tests.unit.test_notification_journey import (
    AgentSide,
    World,
    _now,
    _telegram_surface,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def world(monkeypatch) -> World:
    agent_id = uuid4()
    surface = _telegram_surface(agent_id)
    agent_side = AgentSide(pod_id=surface.pod_id)
    agent_side.agents["Surface Agent"] = agent_id
    return World(monkeypatch, surface=surface, agent_side=agent_side)


def _paused(monkeypatch, conversation_id: UUID) -> None:
    """``conversation_id`` holds an approval nobody has answered yet."""

    async def pending(_uow, wanted: UUID):
        if wanted != conversation_id:
            return None
        return SimpleNamespace(
            tool_call_id="call-approve-1",
            kind="request_approval",
            tool_args={"title": "Delete the draft"},
            agent_run_id=uuid4(),
            is_approval=True,
        )

    monkeypatch.setattr(
        "app.modules.agent.contracts.conversations_for_surfaces.pending_interaction",
        pending,
    )


async def test_a_notification_lands_in_the_conversation_waiting_on_an_approval(
    world, monkeypatch
):
    chat = world.had_chatted(hours_ago=1)
    world.agent_side.conversations[chat.id].updated_at = _now() - timedelta(minutes=31)
    _paused(monkeypatch, chat.id)

    notification = await world.ask(title="Standup")

    assert notification.delivery_conversation_id == chat.id


async def test_a_reply_a_day_later_still_reaches_the_paused_conversation(
    world, monkeypatch
):
    chat = world.had_chatted(hours_ago=30)
    _paused(monkeypatch, chat.id)

    bound, created_title = await world.reply("approve")

    assert bound.conversation_id == chat.id
    assert created_title is None
