"""A notification answered by choosing, and the asker told however it closes.

A schedule's question holds an event until its person picks an option, so two
things are promised here. A reply must pick one of the options -- anything else
leaves the question open rather than closing it on an answer nobody can route.
And every way it closes is announced to the schedule: answered, expired or
cancelled, once each.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    NotificationTransitionError,
)
from app.modules.agent_surfaces.domain.events import NotificationClosedEvent
from app.modules.agent_surfaces.domain.notification import (
    CHOICE_ACTION,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
    chosen_option,
    choice_options,
)
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
)

pytestmark = pytest.mark.unit

CHOICES = {
    "type": CHOICE_ACTION,
    "run_id": str(uuid4()),
    "options": [
        {"key": "urgent", "label": "A customer is waiting.", "route": "act"},
        {"key": "spam", "label": "Promotions.", "route": "ignore"},
    ],
}


def _question(**overrides: object) -> NotificationEntity:
    values: dict[str, object] = {
        "pod_id": uuid4(),
        "recipient_user_id": uuid4(),
        "recipient_pod_member_id": uuid4(),
        "origin_kind": NotificationOriginKind.SCHEDULE,
        "origin_id": uuid4(),
        "title": "support-inbox: What should Kit do with this email?",
        "body": "Answer with one of: urgent, spam.",
        "action": CHOICES,
    }
    values.update(overrides)
    return NotificationEntity.model_validate(values)


def test_a_choice_is_read_off_the_action_and_nothing_else_is_one():
    assert choice_options(CHOICES) == {
        "urgent": "A customer is waiting.",
        "spam": "Promotions.",
    }
    assert choice_options({"type": "WORKFLOW_FORM", "options": []}) == {}
    assert choice_options({"type": CHOICE_ACTION, "options": "urgent"}) == {}
    assert choice_options(None) == {}


def test_a_reply_picks_by_its_answer_or_by_saying_the_key_or_label():
    options = choice_options(CHOICES)

    assert chosen_option(options, summary="whatever", data={"answer": "spam"}) == (
        "spam"
    )
    assert chosen_option(options, summary="Urgent.", data=None) == "urgent"
    assert chosen_option(options, summary="promotions", data=None) == "spam"
    # An explicit answer is not overruled by the summary's wording.
    assert chosen_option(options, summary="urgent", data={"answer": "maybe"}) is None
    assert chosen_option(options, summary="not sure, later", data=None) is None


def test_answering_records_the_option_chosen():
    question = _question()

    question.respond(summary="That one's urgent", data={"answer": "urgent"})

    assert question.status is NotificationStatus.RESPONDED
    assert question.response_data == {"answer": "urgent"}


def test_a_reply_that_picks_nothing_leaves_the_question_open():
    question = _question()

    with pytest.raises(AgentSurfaceValidationError, match="urgent, spam"):
        question.respond(summary="I'll look later", data=None)

    assert question.status is NotificationStatus.OPEN
    assert question.response_data is None


def test_a_schedule_question_must_offer_its_choices():
    with pytest.raises(AgentSurfaceValidationError, match="offer its choices"):
        _question(action=None)


def _service(notification: NotificationEntity) -> NotificationService:
    notifications = AsyncMock()
    notifications.get.return_value = notification
    notifications.update.side_effect = lambda entity: entity
    notifications.list_open_for_origin.return_value = [notification]
    notifications.list_past_due.return_value = [notification]
    uow = AsyncMock()
    # `collect_events` is synchronous; an awaitable stand-in would pass a test
    # the real one fails.
    uow.collect_events = MagicMock()
    return NotificationService(
        uow=uow,
        notification_repository=notifications,
        surface_repository=AsyncMock(),
        conversation_link_repository=AsyncMock(),
        external_user_repository=AsyncMock(),
        egress=AsyncMock(),
        pod_membership_port=AsyncMock(),
        outside_origin_reader=_no_outside_origin,
    )


async def _no_outside_origin(_conversation_id):
    """No conversation here answers people outside the pod."""
    return


def _closed(service: NotificationService) -> list[NotificationClosedEvent]:
    return [
        event
        for call in service.uow.collect_events.call_args_list
        for event in call.args[0]
        if isinstance(event, NotificationClosedEvent)
    ]


async def test_an_answer_is_announced_with_the_choice_and_who_made_it():
    question = _question()
    service = _service(question)

    await service.respond(
        pod_id=question.pod_id,
        notification_id=question.id,
        responder_user_id=question.recipient_user_id,
        summary="urgent",
    )

    [closed] = _closed(service)
    assert closed.notification_id == question.id
    assert closed.origin_kind is NotificationOriginKind.SCHEDULE
    assert closed.origin_id == question.origin_id
    assert closed.status is NotificationStatus.RESPONDED
    assert closed.answer == "urgent"
    assert closed.responder_user_id == question.recipient_user_id
    assert closed.action == CHOICES
    # Nobody said the person chose it: an answer is their agent's by default.
    assert closed.owner_confirmed is False


@pytest.mark.parametrize("owner_confirmed", [True, False])
async def test_the_announcement_says_whether_the_person_chose_the_answer(
    owner_confirmed: bool,
):
    """Chosen in the app or approved word for word, versus given by their agent."""
    question = _question()
    service = _service(question)

    await service.respond(
        pod_id=question.pod_id,
        notification_id=question.id,
        responder_user_id=question.recipient_user_id,
        summary="urgent",
        owner_confirmed=owner_confirmed,
    )

    [closed] = _closed(service)
    assert closed.owner_confirmed is owner_confirmed


async def test_a_second_answer_is_refused_and_announces_nothing_more():
    question = _question()
    service = _service(question)
    answer = {
        "pod_id": question.pod_id,
        "notification_id": question.id,
        "responder_user_id": question.recipient_user_id,
        "summary": "urgent",
    }

    await service.respond(**answer)
    with pytest.raises(NotificationTransitionError):
        await service.respond(**answer)

    assert len(_closed(service)) == 1


async def test_an_expired_question_is_announced_unanswered():
    question = _question()
    question.expires_at = question.created_at
    service = _service(question)

    assert await service.expire_past_due() == 1

    [closed] = _closed(service)
    assert closed.status is NotificationStatus.EXPIRED
    assert closed.answer is None
    assert closed.responder_user_id is None


async def test_a_cancelled_question_is_announced_unanswered():
    question = _question()
    service = _service(question)

    await service.cancel_for_origin(
        origin_kind=NotificationOriginKind.SCHEDULE, origin_id=question.origin_id
    )

    [closed] = _closed(service)
    assert closed.status is NotificationStatus.CANCELLED


async def test_an_agents_ask_closing_is_not_announced_this_way():
    """Its asker is woken by `NotificationSettledEvent`; this one is a schedule's."""
    ask = _question(origin_kind=NotificationOriginKind.AGENT_RUN, action=None)
    service = _service(ask)

    await service.respond(
        pod_id=ask.pod_id,
        notification_id=ask.id,
        responder_user_id=ask.recipient_user_id,
        summary="Shipped it.",
    )

    assert _closed(service) == []


async def test_a_notification_kept_in_the_inbox_only_is_not_rate_limited():
    """The limit bounds what is pushed to a phone; an inbox row pushes nothing."""
    notifications = AsyncMock()
    notifications.create.side_effect = lambda entity: entity
    limiter = AsyncMock()
    membership = AsyncMock()
    membership.get_pod_member_id.return_value = uuid4()
    service = NotificationService(
        uow=AsyncMock(),
        notification_repository=notifications,
        surface_repository=AsyncMock(),
        conversation_link_repository=AsyncMock(),
        external_user_repository=AsyncMock(),
        egress=AsyncMock(),
        pod_membership_port=membership,
        outside_origin_reader=_no_outside_origin,
        rate_limiter=limiter,
    )

    created = await service.notify(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        title="support-inbox: What should Kit do?",
        body="Answer with one of: urgent, spam.",
        origin_kind=NotificationOriginKind.SCHEDULE,
        action=CHOICES,
        deliver=False,
    )

    assert created.offers_choice
    limiter.check.assert_not_awaited()
