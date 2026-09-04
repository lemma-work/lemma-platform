"""Starting the asking conversation's next turn when its asks are all answered.

`message_user` does not pause the asker -- it sends and the turn ends -- so
without this an answer sits on its row and nothing ever reads it.

The failure mode is the reason this is an event. It used to be a direct call
from `agent_surfaces` through the composition root, wrapped in a bare
`except Exception` that logged and returned False: an answer whose asker could
not be restarted was lost, with a warning as the only record. On the stream a
failure is redelivered, so the last test here asserts that the handler *raises*.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.events.notification_settled import on_notification_settled
from app.modules.agent_surfaces.contracts import NotificationSettledEvent

pytestmark = pytest.mark.unit


class _Uow:
    def __init__(self):
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.committed = True


class _PassThroughInbox:
    """Runs the work, the way the real inbox does for an unseen event."""

    async def process(self, _key, _event, work):
        await work()


def _settled_event() -> dict:
    return NotificationSettledEvent(
        pod_id=uuid4(), conversation_id=uuid4(), notification_id=uuid4()
    ).model_dump(mode="json")


class _Delivery:
    """A stand-in for the handler's reply delivery, and what it was asked to do."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def factory(self, uow):
        del uow

        async def _deliver(*, conversation_id, pod_id):
            self.calls.append((conversation_id, pod_id))
            return True

        return _deliver


@pytest.fixture
def delivery():
    """Injected, not patched: the handler takes its delivery as a dependency."""
    return _Delivery()


@pytest.mark.asyncio
async def test_a_settled_conversation_gets_its_next_turn(delivery):
    event = _settled_event()
    uow = _Uow()

    await on_notification_settled(
        event,
        fs_logger=SimpleNamespace(),
        uow_factory=lambda: uow,
        inbox=_PassThroughInbox(),
        deliver_replies=delivery.factory,
    )

    assert delivery.calls == [
        (
            NotificationSettledEvent.model_validate(event).conversation_id,
            NotificationSettledEvent.model_validate(event).pod_id,
        )
    ]
    assert uow.committed, "the delivery was not committed"


@pytest.mark.asyncio
async def test_another_event_on_the_same_stream_is_ignored(delivery):
    """Surfaces publishes several kinds of event to one stream."""
    await on_notification_settled(
        {"event_type": "surface.connected", "surface_id": str(uuid4())},
        fs_logger=SimpleNamespace(),
        uow_factory=lambda: _Uow(),
        inbox=_PassThroughInbox(),
        deliver_replies=delivery.factory,
    )

    assert delivery.calls == []


@pytest.mark.asyncio
async def test_a_failed_delivery_is_raised_so_the_event_is_redelivered():
    """The behaviour change this event was made for.

    The call this replaced swallowed the failure, so an answer whose asker could
    not be restarted was simply lost. Letting it out is what puts the event back
    on the stream.
    """

    def _explodes(uow):
        del uow

        async def _deliver(*, conversation_id, pod_id):
            raise RuntimeError("the run could not be started")

        return _deliver

    uow = _Uow()

    with pytest.raises(RuntimeError, match="could not be started"):
        await on_notification_settled(
            _settled_event(),
            fs_logger=SimpleNamespace(),
            uow_factory=lambda: uow,
            inbox=_PassThroughInbox(),
            deliver_replies=_explodes,
        )

    assert not uow.committed


@pytest.mark.asyncio
async def test_an_event_the_inbox_has_already_seen_delivers_nothing(delivery):
    """Replaying a turn twice is expensive and visible to whoever is watching."""

    class _AlreadySeen:
        async def process(self, _key, _event, work):
            del work

    await on_notification_settled(
        _settled_event(),
        fs_logger=SimpleNamespace(),
        uow_factory=lambda: _Uow(),
        inbox=_AlreadySeen(),
        deliver_replies=delivery.factory,
    )

    assert delivery.calls == []


def test_the_handler_is_typed_for_the_event_it_parses():
    """A guard on the shape, not the wiring: the payload must round-trip."""
    parsed = NotificationSettledEvent.model_validate(_settled_event())
    assert parsed.event_type == "notification.settled"
    assert parsed.stream_name() == "surface_events"
