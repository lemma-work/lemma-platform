"""Saying that an asking conversation is owed nothing further.

The behaviour is a *negative* one as much as a positive one: an agent that
messaged four people must announce this once, not four times, because acting on
it replays the whole conversation. So the interesting assertions here are the
ones that check nothing was announced.

This used to be `deliver_replies_if_settled` in the composition root, which both
respond paths had to remember to call -- its own docstring said so, and warned
that "a third one must too". Nobody has to remember now: the announcement is
made where the answer is recorded.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.events import NotificationSettledEvent
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
)

pytestmark = pytest.mark.unit


def _notification(
    *,
    origin_conversation_id=None,
    origin_kind=NotificationOriginKind.AGENT_RUN,
) -> NotificationEntity:
    return NotificationEntity(
        id=uuid4(),
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        recipient_pod_member_id=uuid4(),
        origin_kind=origin_kind,
        origin_conversation_id=origin_conversation_id,
        action=(
            {"run_id": str(uuid4()), "node_id": "approve"}
            if origin_kind is NotificationOriginKind.WORKFLOW_FORM
            else None
        ),
        title="Can you confirm the importer shipped?",
        body="Asking on behalf of the release.",
        status=NotificationStatus.OPEN,
    )


def _service(*, outstanding: int, notification: NotificationEntity):
    """A service whose only live parts are the count and the event sink."""
    notifications = AsyncMock()
    notifications.count_open_from_origin_conversation.return_value = outstanding
    notifications.get.return_value = notification
    notifications.update.side_effect = lambda entity: entity
    uow = AsyncMock()
    # Not an AsyncMock attribute: `collect_events` is synchronous, and an
    # awaitable stand-in for it would pass a test the real one fails.
    uow.collect_events = MagicMock()
    return NotificationService(
        uow=uow,
        notification_repository=notifications,
        surface_repository=AsyncMock(),
        conversation_link_repository=AsyncMock(),
        external_user_repository=AsyncMock(),
        ingress_service=AsyncMock(),
        pod_membership_port=AsyncMock(),
    )


def _announced(service) -> list[NotificationSettledEvent]:
    return [
        event
        for call in service.uow.collect_events.call_args_list
        for event in call.args[0]
        if isinstance(event, NotificationSettledEvent)
    ]


@pytest.mark.asyncio
async def test_the_last_answer_announces_that_the_conversation_is_settled():
    conversation_id = uuid4()
    notification = _notification(origin_conversation_id=conversation_id)
    service = _service(outstanding=0, notification=notification)

    await service._announce_if_settled(notification)

    [event] = _announced(service)
    assert event.conversation_id == conversation_id
    assert event.notification_id == notification.id
    assert event.pod_id == notification.pod_id


@pytest.mark.asyncio
async def test_an_answer_with_others_still_open_announces_nothing():
    """The whole point of waiting for the last one.

    Announcing here would replay the entire conversation so the agent could
    learn that two people still owe it an answer, and stop again.
    """
    notification = _notification(origin_conversation_id=uuid4())
    service = _service(outstanding=2, notification=notification)

    await service._announce_if_settled(notification)

    assert _announced(service) == []


@pytest.mark.asyncio
async def test_a_workflow_form_answer_announces_nothing():
    """A form is owed to its workflow run, which the engine resumes itself.

    Announcing from here would be a second resume path for an execution that
    already has one.
    """
    notification = _notification(
        origin_conversation_id=uuid4(),
        origin_kind=NotificationOriginKind.WORKFLOW_FORM,
    )
    service = _service(outstanding=0, notification=notification)

    await service._announce_if_settled(notification)

    assert _announced(service) == []
    service.notifications.count_open_from_origin_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_ask_with_no_origin_conversation_announces_nothing():
    notification = _notification(origin_conversation_id=None)
    service = _service(outstanding=0, notification=notification)

    await service._announce_if_settled(notification)

    assert _announced(service) == []
    service.notifications.count_open_from_origin_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_recording_an_answer_announces_without_the_caller_asking():
    """The property the composition-root version could not have.

    Both respond paths -- the recipient's agent and the web app -- go through
    `respond`, so neither has to remember to start the asker's next turn, and a
    third path would not have to either.
    """
    conversation_id = uuid4()
    notification = _notification(origin_conversation_id=conversation_id)
    service = _service(outstanding=0, notification=notification)

    await service.respond(
        pod_id=notification.pod_id,
        notification_id=notification.id,
        responder_user_id=notification.recipient_user_id,
        summary="Shipped the importer.",
    )

    assert [event.conversation_id for event in _announced(service)] == [
        conversation_id
    ], "the answer was recorded and nothing said the asker could resume"
