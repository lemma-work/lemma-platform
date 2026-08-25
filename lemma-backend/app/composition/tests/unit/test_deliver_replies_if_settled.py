"""Starting the asking conversation's next turn, once every ask is answered.

The behaviour under test is a *negative* one as much as a positive one: an agent
that messaged four people must be brought back once, not four times, because
every turn replays the whole conversation. So the interesting assertions here
are the ones that check nothing happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.composition import agent_notifications
from app.modules.agent_surfaces.domain.notification import NotificationOriginKind

pytestmark = pytest.mark.unit


class _FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def _notification(
    *,
    origin_conversation_id=None,
    origin_kind=NotificationOriginKind.AGENT_RUN,
):
    return SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        origin_conversation_id=origin_conversation_id,
        origin_kind=origin_kind,
    )


@pytest.fixture
def delivery_harness(monkeypatch):
    """Stub the two things this reaches: the outstanding count, the delivery."""
    state = SimpleNamespace(outstanding=0, delivered=[], counted=[])

    class _Notifications:
        async def count_open_from_origin_conversation(self, conversation_id):
            state.counted.append(conversation_id)
            return state.outstanding

    class _Replies:
        def __init__(self, uow):
            pass

        async def deliver(self, *, conversation_id, pod_id):
            state.delivered.append(conversation_id)
            return True

    monkeypatch.setattr(
        agent_notifications,
        "_service",
        lambda uow: SimpleNamespace(notifications=_Notifications()),
    )
    monkeypatch.setattr(
        agent_notifications,
        "SessionUnitOfWorkFactory",
        lambda maker: lambda: _FakeUow(),
    )
    monkeypatch.setattr(
        "app.modules.agent.services.message_reply_service.MessageReplyService",
        _Replies,
    )
    return state


@pytest.mark.asyncio
async def test_the_last_answer_starts_the_asking_conversations_next_turn(
    delivery_harness,
):
    conversation_id = uuid4()
    delivery_harness.outstanding = 0

    delivered = await agent_notifications.deliver_replies_if_settled(
        _notification(origin_conversation_id=conversation_id)
    )

    assert delivered is True
    assert delivery_harness.counted == [conversation_id]
    assert delivery_harness.delivered == [conversation_id]


@pytest.mark.asyncio
async def test_an_answer_with_others_still_open_delivers_nothing(delivery_harness):
    """The whole point of waiting for the last one.

    Starting a turn here would replay the entire conversation so the agent could
    learn that two people still owe it an answer, and stop again.
    """
    delivery_harness.outstanding = 2

    delivered = await agent_notifications.deliver_replies_if_settled(
        _notification(origin_conversation_id=uuid4())
    )

    assert delivered is False
    assert delivery_harness.delivered == []


@pytest.mark.asyncio
async def test_a_workflow_form_answer_delivers_nothing(delivery_harness):
    """A form is owed to its workflow run, which the engine resumes itself.

    Starting a conversation turn from here would be a second resume path for an
    execution that already has one.
    """
    delivered = await agent_notifications.deliver_replies_if_settled(
        _notification(
            origin_conversation_id=uuid4(),
            origin_kind=NotificationOriginKind.WORKFLOW_FORM,
        )
    )

    assert delivered is False
    assert delivery_harness.counted == []
    assert delivery_harness.delivered == []


@pytest.mark.asyncio
async def test_an_ask_with_no_origin_conversation_delivers_nothing(delivery_harness):
    delivered = await agent_notifications.deliver_replies_if_settled(
        _notification(origin_conversation_id=None)
    )

    assert delivered is False
    assert delivery_harness.counted == []


@pytest.mark.asyncio
async def test_a_failed_delivery_is_swallowed(delivery_harness, monkeypatch):
    """The answer is already committed and the responder is owed a receipt.

    Raising here would turn a recorded answer into a tool error for the person
    who gave it, over a failure on somebody else's side of the pod.
    """

    class _Explodes:
        def __init__(self, uow):
            pass

        async def deliver(self, *, conversation_id, pod_id):
            raise RuntimeError("the run could not be started")

    monkeypatch.setattr(
        "app.modules.agent.services.message_reply_service.MessageReplyService",
        _Explodes,
    )

    delivered = await agent_notifications.deliver_replies_if_settled(
        _notification(origin_conversation_id=uuid4())
    )

    assert delivered is False


# -- the two seams that call it -------------------------------------------------


@pytest.mark.asyncio
async def test_recording_an_answer_from_a_tool_tries_the_wake(monkeypatch):
    """The agent-mediated path: the recipient's agent records the answer."""
    notification = _notification(origin_conversation_id=uuid4())
    asked: list = []

    class _Service:
        async def respond(self, **kwargs):
            assert kwargs["summary"] == "Shipped the importer."
            return notification

    monkeypatch.setattr(agent_notifications, "_service", lambda uow: _Service())
    monkeypatch.setattr(
        agent_notifications,
        "SessionUnitOfWorkFactory",
        lambda maker: lambda: _FakeUow(),
    )
    monkeypatch.setattr(
        agent_notifications,
        "deliver_replies_if_settled",
        lambda n: asked.append(n) or _done(),
    )

    await agent_notifications.record_notification_response(
        pod_id=uuid4(),
        notification_id=notification.id,
        responder_user_id=uuid4(),
        summary="Shipped the importer.",
    )

    assert asked == [notification], "the answer was recorded and went nowhere"


@pytest.mark.asyncio
async def test_answering_from_the_app_defers_the_wake_until_after_the_commit(
    monkeypatch,
):
    """The web-app path, and the ordering it depends on.

    Delivering inline would read the outstanding count in a second session that
    cannot yet see this answer, count the row it just closed, and leave the
    asker with nothing. So the endpoint registers it on the unit of work
    instead, and what is asserted here is that it registered rather than ran.
    """
    from app.modules.agent_surfaces.api.controllers import notification_controller
    from app.modules.agent_surfaces.api.schemas import NotificationRespondRequest
    from app.modules.agent_surfaces.domain.notification import NotificationEntity

    pod_id, user_id = uuid4(), uuid4()
    notification = NotificationEntity(
        pod_id=pod_id,
        recipient_user_id=user_id,
        recipient_pod_member_id=uuid4(),
        origin_kind=NotificationOriginKind.AGENT_RUN,
        origin_conversation_id=uuid4(),
        title="Standup",
        body="What did you ship yesterday?",
    )
    woken: list = []
    callbacks: list = []

    class _Uow:
        def after_commit(self, callback):
            callbacks.append(callback)

    class _Service:
        uow = _Uow()

        async def respond(self, **kwargs):
            return notification

    monkeypatch.setattr(
        notification_controller,
        "deliver_replies_if_settled",
        lambda n: woken.append(n) or _done(),
    )

    await notification_controller.respond_to_notification(
        pod_id=pod_id,
        notification_id=notification.id,
        request=NotificationRespondRequest(summary="Shipped the importer."),
        user=SimpleNamespace(id=user_id),
        ctx=None,
        service=_Service(),
    )

    assert woken == [], "the delivery ran inside the request, before the commit"
    assert len(callbacks) == 1, "nothing was registered to run after the commit"

    await callbacks[0]()
    assert woken == [notification]


async def _done() -> bool:
    return True
