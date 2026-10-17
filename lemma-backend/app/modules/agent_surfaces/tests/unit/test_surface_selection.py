"""Unit tests for deterministic surface selection (the multi-pod / shared-bot
disambiguation): pod membership → user default (authoritative) → continuity →
tiebreak."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter
from app.modules.channels.contracts import Source, SourceKind
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = pytest.mark.asyncio


def _surface(pod_id, surface_id) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=surface_id,
        pod_id=pod_id,
        agent_id=pod_id,
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
    )


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="chat-1",
        external_thread_id="chat-1",
        sender_external_user_id="tg-user-1",
        message_text="hi",
        is_dm=True,
    )


def _private_place(surface_id, *, ago: timedelta = timedelta()) -> Source:
    """``tg-user-1``'s private chat on a surface, last written to ``ago``."""
    return Source(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=uuid4(),
        surface_id=surface_id,
        platform="TELEGRAM",
        kind=SourceKind.PRIVATE,
        external_user_id="tg-user-1",
        last_inbound_at=datetime.now(timezone.utc) - ago,
    )


def _service(
    *,
    continuity_id,
    member_pod_ids,
    default_surface_id,
    default_surface_pod_id=None,
    person_continuity_id=None,
):
    """A selector over doubles.

    `default_surface_pod_id` is what the *database* says about the saved
    default, which is a separate fact from whether that surface is among this
    delivery's candidates: None means the surface is gone or no longer live, and
    a pod id means it still stands in that pod. Deciding staleness from the
    candidate list instead is the bug these doubles exist to keep out.
    """
    # The person's private chats: the one ``continuity_id`` names, written to
    # just now, and ``person_continuity_id``'s, written to a while ago.
    seats = InMemoryChannelSeats()
    if continuity_id is not None:
        seats.add(_private_place(continuity_id))
    if person_continuity_id is not None:
        seats.add(_private_place(person_continuity_id, ago=timedelta(days=2)))
    membership = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=list(member_pod_ids)),
        get_user_default_surface_id=AsyncMock(return_value=default_surface_id),
        clear_user_default_surface_id=AsyncMock(return_value=None),
    )
    saved_default = (
        _surface(default_surface_pod_id, default_surface_id)
        if default_surface_pod_id is not None
        else None
    )
    surfaces = SimpleNamespace(
        list_active_for_routing=AsyncMock(
            return_value=[saved_default] if saved_default is not None else []
        )
    )
    # Selection is `SurfaceRouter`'s, and the router is the subject here. It
    # used to be reached through an eight-mixin ingress service built with
    # `uow_factory=lambda: None` -- a half-object, for a method that never
    # touches a session.
    service = SurfaceRouter(
        uow=SimpleNamespace(session=None),
        surface_repository=surfaces,
        seats=seats,
        pod_membership_port=membership,
        identity_service=SimpleNamespace(),
        credential_resolver=SimpleNamespace(),
    )
    # Expose the membership double for assertions.
    service._test_membership = membership  # type: ignore[attr-defined]
    return service


async def test_continuity_reuses_prior_surface_over_ordering():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    # An existing conversation for this chat lives on surface B; the sender is a
    # member of both pods. B must win even though A is first in the list.
    service = _service(
        continuity_id=surf_b.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_user_default_wins_when_multiple_member_pods():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=surf_b.id,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_deterministic_tiebreak_when_no_default():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    # First candidate wins (list is ordered by created_at, id upstream).
    assert chosen is surf_a


async def test_single_member_candidate_selected():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_b},  # only member of pod B
        default_surface_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_none_when_sender_not_a_member():
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids=set(),
        default_surface_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is None


async def test_none_when_unresolved_user_and_no_continuity():
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a},
        default_surface_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a],
        resolved_user=ResolvedSurfaceUser(internal_user_id=None),
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is None


async def test_valid_default_wins_over_continuity():
    """The user set a default; an older conversation lives on a different pod.
    The valid default is authoritative and must win (issue 3)."""
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    # Continuity says A, but the user's default is B and they're a member of both.
    service = _service(
        continuity_id=surf_a.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=surf_b.id,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b
    service._test_membership.clear_user_default_surface_id.assert_not_awaited()


async def test_stale_default_is_cleared_and_falls_back_to_continuity():
    """A default pointing at a pod the user left is stale: it must be cleared and
    routing falls through to continuity (not silently honored)."""
    pod_a, pod_b, pod_left = uuid4(), uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    stale_surface_id = uuid4()  # a surface the user is no longer a member of
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=surf_a.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=stale_surface_id,
        default_surface_pod_id=pod_left,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_a  # continuity, since the stale default is ignored
    service._test_membership.clear_user_default_surface_id.assert_awaited_once()


async def test_a_default_on_a_surface_that_is_gone_is_cleared():
    """The other half of stale: the surface itself no longer routes."""
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a},
        default_surface_id=uuid4(),
        default_surface_pod_id=None,
    )
    chosen = await service.select_surface(
        candidates=[surf_a],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_a
    service._test_membership.clear_user_default_surface_id.assert_awaited_once()


async def test_a_default_another_bot_serves_is_not_treated_as_stale():
    """The candidate list is this delivery's, and a default is not about it.

    `candidates` is narrowed by which receiver took delivery and by whether
    system credentials are required, so a person whose default sits on a second
    bot on the same platform has a default that is absent from most deliveries.
    Reading that as stale deleted their saved choice on somebody else's message.
    """
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    other_bots_surface_id = uuid4()
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a},
        default_surface_id=other_bots_surface_id,
        # Live, and in a pod the user is still in -- it simply is not one of the
        # candidates this particular receiver delivered.
        default_surface_pod_id=pod_a,
    )
    chosen = await service.select_surface(
        candidates=[surf_a],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_a  # routed here, because this is where it arrived
    service._test_membership.clear_user_default_surface_id.assert_not_awaited()


async def test_continuity_is_the_freshest_thread_among_the_candidates():
    """A place on a surface that is no longer a candidate must not mask one.

    A shared-bot sender whose most recent chat is on a surface that has since
    gone -- switched off, or served by a bot that did not deliver this event --
    must still be answered where their real ongoing conversation is, on a
    candidate, rather than by whichever pod the tiebreak sorts first. So the
    lookup is narrowed to the candidates, not filtered after.
    """
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())

    service = _service(
        # The freshest chat is on a surface this delivery has no candidate for.
        continuity_id=uuid4(),
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
        person_continuity_id=surf_b.id,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(), external_user_id="tg-user-1"
        ),
        parsed=_event(),
        platform=SurfacePlatform.TELEGRAM.value,
    )

    assert chosen is surf_b


async def test_a_dm_whose_delivery_address_changed_stays_on_the_surface_it_was_on():
    """A private chat is the person's, not the address it came to.

    On WhatsApp the address embeds the number the message arrived on, so a
    reassigned number is a new address; keyed by it, the tiebreak would answer
    from whichever pod sorts first -- not the one holding the conversation the
    person is still looking at.
    """
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
        person_continuity_id=surf_b.id,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(), external_user_id="tg-user-1"
        ),
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_the_saved_default_still_outranks_a_persons_dm_continuity():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=surf_a.id,
        person_continuity_id=surf_b.id,
    )
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(), external_user_id="tg-user-1"
        ),
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_a


async def test_a_group_message_has_no_person_level_continuity():
    """A group is somebody else's room; "the same person" does not pick it."""
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
        person_continuity_id=surf_b.id,
    )
    group = _event().model_copy(update={"is_dm": False})
    chosen = await service.select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(), external_user_id="tg-user-1"
        ),
        parsed=group,
        platform="TELEGRAM",
    )
    assert chosen is surf_a


# --- the saved default, asked by the personal route ---------------------------
#
# A verified personal route answers a DM before selection runs, so the saved
# default -- which selection alone consulted -- never reached it. The probe below
# is how the route asks the same question selection would answer, on the same
# predicate, so the two cannot disagree about who a message belongs to.


async def test_a_default_this_delivery_could_be_routed_to_is_reported():
    pod = uuid4()
    chosen_surface = _surface(pod, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod},
        default_surface_id=chosen_surface.id,
        default_surface_pod_id=pod,
    )
    found = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=None
    )
    assert found is not None and found.id == chosen_surface.id


async def test_a_default_on_its_own_credentials_is_not_deliverable_to_the_shared_bot():
    """Selection narrows a shared-bot event to surfaces on the system's credentials.

    A saved default that runs on an account's own bot is not somewhere that event
    can be answered, so the probe cannot report it either -- or the personal route
    would step aside for a default selection then declines to pick.
    """
    pod = uuid4()
    own_bot_default = _surface(pod, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod},
        default_surface_id=own_bot_default.id,
        default_surface_pod_id=pod,
    )

    async def only_where_the_narrowing_allows(platform, **kwargs):
        return [] if kwargs.get("system_credentials_only") else [own_bot_default]

    service.surface_repository.list_active_for_routing.side_effect = (
        only_where_the_narrowing_allows
    )

    on_shared_bot = await service.deliverable_default(
        user_id=uuid4(),
        parsed=_event(),
        receiver_surface_ids=None,
        system_credentials_only=True,
    )
    on_own_bot = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=None
    )

    assert on_shared_bot is None
    assert on_own_bot is not None


async def test_no_saved_default_reports_none():
    service = _service(
        continuity_id=None, member_pod_ids={uuid4()}, default_surface_id=None
    )
    found = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=None
    )
    assert found is None


async def test_a_default_in_a_pod_the_user_left_is_not_deliverable_and_not_cleared():
    """A probe reads. Clearing is selection's, which owns the one write here."""
    left_pod = uuid4()
    surface = _surface(left_pod, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={uuid4()},
        default_surface_id=surface.id,
        default_surface_pod_id=left_pod,
    )
    found = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=None
    )
    assert found is None
    service._test_membership.clear_user_default_surface_id.assert_not_awaited()


async def test_a_default_on_a_surface_that_is_gone_is_not_deliverable():
    pod = uuid4()
    service = _service(
        continuity_id=None,
        member_pod_ids={pod},
        default_surface_id=uuid4(),
        default_surface_pod_id=None,
    )
    found = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=None
    )
    assert found is None


async def test_a_default_another_bot_serves_is_not_deliverable_on_this_one():
    """Two installations can share a workspace; only the receiver's own count."""
    pod = uuid4()
    surface = _surface(pod, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod},
        default_surface_id=surface.id,
        default_surface_pod_id=pod,
    )
    found = await service.deliverable_default(
        user_id=uuid4(), parsed=_event(), receiver_surface_ids=[uuid4()]
    )
    assert found is None
    service._test_membership.clear_user_default_surface_id.assert_not_awaited()
