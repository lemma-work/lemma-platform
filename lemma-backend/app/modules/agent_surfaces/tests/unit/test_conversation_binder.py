"""A person has one conversation per agent in a channel, however they write.

The binder places the message, notes what was heard there, and finds the
person's conversation -- under a lock on the person in that channel, so two
first messages arriving together open one. A new thread, or a new day, is the
same place and the same person; only another agent opens another conversation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.agent.contracts import channels as agent_conversations
from app.modules.agent_surfaces.domain.entities import ResolvedSurfaceUser
from app.modules.agent_surfaces.services.channel_lines import reply_ref_of
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _conversation,
    _slack_event,
    _slack_surface,
    build_doubles,
    conversation_operations,  # noqa: F401  (autouse fixture)
    surface_conversation,
)
from app.modules.channels.contracts import SourceKind
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats


def _route(surface, *, agent_id=None) -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=surface.pod_id,
        agent_id=agent_id or surface.agent_id,
        agent_name="Surface Agent",
        agent_display_name="Surface Agent",
        conversation_kind="DM",
        route_key="dm",
    )


def _binder(surface) -> tuple[ConversationBinder, InMemoryChannelSeats]:
    doubles = build_doubles(adapter=AsyncMock(), surfaces=[surface])
    binder = ConversationBinder(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        seats=doubles.seats,
    )
    return binder, doubles.seats


def _member(user_id) -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(internal_user_id=user_id, external_user_id="U123")


async def test_a_first_message_opens_the_persons_conversation_in_their_channel():
    surface = _slack_surface(agent_id=uuid4())
    user_id = uuid4()
    opened = _conversation(surface, user_id)
    agent_conversations.open_surface_conversation.return_value = opened
    binder, seats = _binder(surface)

    bound = await binder.bind_conversation(
        surface=surface,
        parsed=_slack_event(),
        resolved_user=_member(user_id),
        route=_route(surface),
    )

    assert bound.conversation_id == opened.id
    assert bound.created_title is not None
    [source] = seats.sources.values()
    assert bound.source_id == source.id
    # A private chat is the person's place, not the address it came to.
    assert source.kind is SourceKind.PERSON
    assert source.external_user_id == "U123"
    assert source.external_channel_id is None
    # Opened in the channel the place belongs to: the member's own.
    kwargs = agent_conversations.open_surface_conversation.await_args.kwargs
    assert kwargs["channel_id"] == source.channel_id
    assert seats.channels[("DIRECT", surface.pod_id, user_id)] == source.channel_id


async def test_what_was_heard_is_kept_as_how_to_answer_the_place():
    surface = _slack_surface()
    user_id = uuid4()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, seats = _binder(surface)
    event = _slack_event()

    await binder.bind_conversation(
        surface=surface,
        parsed=event,
        resolved_user=_member(user_id),
        route=_route(surface),
    )

    [source] = seats.sources.values()
    # The place's reply target, never what was said there.
    assert source.delivery_ref == reply_ref_of(event)
    assert source.delivery_ref["message_text"] == ""
    assert source.last_inbound_at is not None


async def test_the_person_is_authorized_then_locked_then_looked_for_again():
    """Two first messages arriving together must open one conversation: the
    second waits on the lock, then finds what the first opened.

    Authorizing commits, which would end the lock, so it comes first -- and
    the look that decides to open is the one under the lock.
    """
    surface = _slack_surface()
    user_id = uuid4()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, seats = _binder(surface)
    calls: list[str] = []
    lock, look = seats.lock_person, seats.conversation_in_channel

    async def locking(**kwargs):
        calls.append("lock")
        await lock(**kwargs)

    async def looking(**kwargs):
        calls.append("look")
        return await look(**kwargs)

    seats.lock_person = locking  # type: ignore[method-assign]
    seats.conversation_in_channel = looking  # type: ignore[method-assign]
    authorize = agent_conversations.authorize_surface_conversation
    authorize.side_effect = lambda *_a, **_k: calls.append("authorize")

    await binder.bind_conversation(
        surface=surface,
        parsed=_slack_event(),
        resolved_user=_member(user_id),
        route=_route(surface),
    )

    assert calls == ["look", "authorize", "lock", "look"]
    assert seats.locked == [user_id]
    opening = agent_conversations.open_surface_conversation.await_args.kwargs
    assert opening["require_execute_grant"] is False


async def test_a_new_thread_from_the_same_person_continues_their_conversation():
    surface = _slack_surface(agent_id=uuid4())
    user_id = uuid4()
    binder, seats = _binder(surface)
    first = _slack_event()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    bound = await binder.bind_conversation(
        surface=surface,
        parsed=first,
        resolved_user=_member(user_id),
        route=_route(surface),
    )
    [source] = seats.sources.values()
    seats.seat(
        bound.conversation_id,
        channel_id=source.channel_id,
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        person=user_id,
    )
    agent_conversations.surface_conversation.return_value = surface_conversation(
        conversation_id=bound.conversation_id,
        user_id=user_id,
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
    )
    agent_conversations.open_surface_conversation.reset_mock()
    later = first.model_copy(
        update={"external_thread_id": "1700000999.000100", "message_text": "Again"}
    )

    again = await binder.bind_conversation(
        surface=surface,
        parsed=later,
        resolved_user=_member(user_id),
        route=_route(surface),
    )

    assert again.conversation_id == bound.conversation_id
    assert again.source_id == bound.source_id
    assert again.created_title is None
    agent_conversations.open_surface_conversation.assert_not_awaited()


async def test_another_agent_opens_a_conversation_of_its_own():
    surface = _slack_surface(agent_id=uuid4())
    user_id = uuid4()
    binder, seats = _binder(surface)
    event = _slack_event()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    bound = await binder.bind_conversation(
        surface=surface,
        parsed=event,
        resolved_user=_member(user_id),
        route=_route(surface),
    )
    [source] = seats.sources.values()
    seats.seat(
        bound.conversation_id,
        channel_id=source.channel_id,
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        person=user_id,
    )
    other = _conversation(surface, user_id)
    agent_conversations.open_surface_conversation.return_value = other

    elsewhere = await binder.bind_conversation(
        surface=surface,
        parsed=event,
        resolved_user=_member(user_id),
        route=_route(surface, agent_id=uuid4()),
    )

    assert elsewhere.conversation_id == other.id
    # The same place: one chat, two agents' conversations in its channel.
    assert elsewhere.source_id == bound.source_id
