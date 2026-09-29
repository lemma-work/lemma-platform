"""A private chat is one conversation to the person, wherever it is delivered.

The link key names a delivery address -- the surface, the channel, the thread id
-- and on WhatsApp that address embeds the number the message arrived on. So a
number reassigned within the pool, or a different surface of the same pod taking
over the chat, produced a key nobody had written and a fresh conversation with
none of the history the person was still looking at on their phone. These tests
pin the other reading: for a DM the address is a delivery detail, and the
conversation belongs to the person, the pod and the agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _conversation,
    build_doubles,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio

WA_ID = "15550100"


@pytest.fixture(autouse=True)
def _restore_reset_window():
    original = surface_settings.surface_dm_conversation_reset_after_hours
    yield
    surface_settings.surface_dm_conversation_reset_after_hours = original


def _surface(*, pod_id: UUID, agent_id: UUID, number: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        agent_id=agent_id,
        name="whatsapp",
        surface_type=SurfacePlatform.WHATSAPP,
        credential_mode=SurfaceCredentialMode.SYSTEM,
        surface_identity_id=number,
        config=SurfaceConfig(),
        is_active=True,
    )


def _dm_on(number: str, *, is_dm: bool = True) -> ParsedInboundSurfaceEvent:
    """The person's message, arriving on ``number`` -- the WhatsApp thread shape."""
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=number,
        external_thread_id=f"{WA_ID}@{number}",
        external_message_id=f"wamid-{uuid4().hex}",
        sender_external_user_id=WA_ID,
        message_text="hello again",
        is_dm=is_dm,
        reply_target={"phone_number_id": number},
    )


def _route(surface: AgentSurfaceEntity, *, kind: str = "DM") -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        agent_name="Assistant",
        agent_display_name="Assistant",
        conversation_kind=kind,
        route_key="dm",
    )


def _earlier_link(
    surface: AgentSurfaceEntity, *, number: str, hours_ago: float = 1
) -> AgentSurfaceConversationLink:
    """The link the person's conversation has under the address it used before."""
    return AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="WHATSAPP",
        external_channel_id=number,
        external_thread_id=f"{WA_ID}@{number}",
        external_user_id=WA_ID,
        routed_agent_id=surface.agent_id,
        conversation_kind="DM",
        route_key="dm",
        last_event={},
        last_inbound_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


def _binder(
    *surfaces: AgentSurfaceEntity,
    earlier: AgentSurfaceConversationLink | None,
    owner: SimpleNamespace | None = None,
):
    doubles = build_doubles(adapter=AsyncMock(), surfaces=list(surfaces))
    if owner is not None:
        # After `build_doubles`, which installs its own answer for this lookup.
        agent_conversations.surface_conversation.return_value = owner
    links = doubles.conversation_link_repository
    # No link under the address this message arrived on: that is the whole case.
    links.get_by_external_thread.return_value = None
    links.find_latest_dm_link_for_person.return_value = earlier
    # A bare AsyncMock answers truthy, which the binder would read as "this
    # conversation is still owed a notification's reply" and never let it go cold.
    links.conversation_holds_notification.return_value = False
    links.rebind_thread_address.side_effect = lambda **kwargs: earlier.model_copy(
        update={
            "surface_id": kwargs["surface_id"],
            "external_channel_id": kwargs["external_channel_id"],
            "external_thread_id": kwargs["external_thread_id"],
        }
    )
    links.update_last_event.side_effect = lambda **kwargs: None
    binder = ConversationBinder(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=links,
    )
    return binder, links


def _owned_by(
    earlier: AgentSurfaceConversationLink,
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
) -> SimpleNamespace:
    """The conversation behind a link, as the published lookup returns it."""
    return SimpleNamespace(
        id=earlier.conversation_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_id=agent_id,
        title=None,
        updated_at=datetime.now(timezone.utc),
    )


def _person(user_id: UUID) -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(internal_user_id=user_id, external_user_id=WA_ID)


async def test_a_number_reassignment_keeps_the_conversation():
    pod_id, agent_id, user_id = uuid4(), uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    earlier = _earlier_link(surface, number="pn-old")
    owner = _owned_by(earlier, user_id=user_id, pod_id=pod_id, agent_id=agent_id)
    binder, links = _binder(surface, earlier=earlier, owner=owner)

    link, created_title = await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(surface),
    )

    assert link.conversation_id == earlier.conversation_id
    assert created_title is None
    agent_conversations.open_surface_conversation.assert_not_awaited()
    links.create.assert_not_awaited()
    links.rebind_thread_address.assert_awaited_once_with(
        link_id=earlier.id,
        surface_id=surface.id,
        external_channel_id="pn-new",
        external_thread_id=f"{WA_ID}@pn-new",
    )


async def test_a_different_surface_of_the_same_pod_taking_over_keeps_it_too():
    pod_id, agent_id, user_id = uuid4(), uuid4(), uuid4()
    old_surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-old")
    new_surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    earlier = _earlier_link(old_surface, number="pn-old")
    owner = _owned_by(earlier, user_id=user_id, pod_id=pod_id, agent_id=agent_id)
    binder, links = _binder(old_surface, new_surface, earlier=earlier, owner=owner)

    link, created_title = await binder.bind_conversation(
        surface=new_surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(new_surface),
    )

    assert link.conversation_id == earlier.conversation_id
    assert created_title is None
    # Looked for among this pod's surfaces, which is what keeps the read on the
    # per-surface index and keeps another pod's conversation out of reach.
    searched = links.find_latest_dm_link_for_person.await_args.kwargs["surface_ids"]
    assert set(searched) == {old_surface.id, new_surface.id}


async def test_moving_onto_a_personal_route_keeps_the_conversation_in_that_pod():
    """The route names the pod, and the installation only carries the message.

    A personal route is answered by the person's own pod through an installation
    that belongs to somebody else's, so the surface that received the message is
    not in the pod the conversation lives in. The pod searched is the route's.
    """
    personal_pod, company_pod, user_id = uuid4(), uuid4(), uuid4()
    installation = _surface(pod_id=company_pod, agent_id=uuid4(), number=None)
    ordinary = _surface(pod_id=personal_pod, agent_id=personal_pod, number=None)
    earlier = _earlier_link(ordinary, number="pn-old")
    owner = _owned_by(
        earlier, user_id=user_id, pod_id=personal_pod, agent_id=personal_pod
    )
    binder, links = _binder(installation, ordinary, earlier=earlier, owner=owner)
    route = ResolvedSurfaceRoute(
        pod_id=personal_pod,
        agent_id=personal_pod,
        agent_name="pod_default",
        agent_display_name="Assistant",
        conversation_kind="DM",
        route_key="personal:route",
    )

    link, created_title = await binder.bind_conversation(
        surface=installation,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=route,
    )

    assert link.conversation_id == earlier.conversation_id
    assert created_title is None
    searched = links.find_latest_dm_link_for_person.await_args.kwargs["surface_ids"]
    assert set(searched) == {installation.id, ordinary.id}


async def test_another_pods_conversation_is_never_adopted():
    """Same person, same platform, different pod: a different conversation."""
    agent_id, user_id = uuid4(), uuid4()
    here = _surface(pod_id=uuid4(), agent_id=agent_id, number="pn-new")
    elsewhere_pod = uuid4()
    earlier = _earlier_link(here, number="pn-old")
    owner = _owned_by(earlier, user_id=user_id, pod_id=elsewhere_pod, agent_id=agent_id)
    agent_conversations.open_surface_conversation.return_value = _conversation(
        here, user_id
    )
    binder, links = _binder(here, earlier=earlier, owner=owner)

    _, created_title = await binder.bind_conversation(
        surface=here,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(here),
    )

    assert created_title is not None
    links.rebind_thread_address.assert_not_awaited()
    agent_conversations.open_surface_conversation.assert_awaited_once()


async def test_somebody_elses_conversation_is_never_adopted():
    """One number is not one person: the owner has to be who is writing."""
    pod_id, agent_id = uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    earlier = _earlier_link(surface, number="pn-old")
    owner = _owned_by(earlier, user_id=uuid4(), pod_id=pod_id, agent_id=agent_id)
    writer = uuid4()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, writer
    )
    binder, links = _binder(surface, earlier=earlier, owner=owner)

    await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(writer),
        route=_route(surface),
    )

    links.rebind_thread_address.assert_not_awaited()
    agent_conversations.open_surface_conversation.assert_awaited_once()


async def test_a_conversation_with_another_agent_is_not_adopted():
    pod_id, user_id = uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=uuid4(), number="pn-new")
    earlier = _earlier_link(surface, number="pn-old")
    owner = _owned_by(earlier, user_id=user_id, pod_id=pod_id, agent_id=uuid4())
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, links = _binder(surface, earlier=earlier, owner=owner)

    await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(surface),
    )

    links.rebind_thread_address.assert_not_awaited()
    agent_conversations.open_surface_conversation.assert_awaited_once()


async def test_an_adopted_conversation_still_goes_cold():
    """Adoption changes where the thread is looked for, not when it ends."""
    surface_settings.surface_dm_conversation_reset_after_hours = 24
    pod_id, agent_id, user_id = uuid4(), uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    earlier = _earlier_link(surface, number="pn-old", hours_ago=72)
    owner = _owned_by(earlier, user_id=user_id, pod_id=pod_id, agent_id=agent_id)
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, links = _binder(surface, earlier=earlier, owner=owner)
    links.update_conversation.side_effect = lambda **kwargs: earlier.model_copy(
        update={"conversation_id": kwargs["conversation_id"]}
    )

    _, created_title = await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(surface),
    )

    assert created_title is not None
    agent_conversations.open_surface_conversation.assert_awaited_once()


async def test_a_channel_thread_is_never_looked_up_by_person():
    """A channel thread is its own topic; there is no "same person" to continue."""
    pod_id, agent_id, user_id = uuid4(), uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, links = _binder(surface, earlier=None)

    await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new", is_dm=False),
        resolved_user=_person(user_id),
        route=_route(surface, kind="CHANNEL"),
    )

    links.find_latest_dm_link_for_person.assert_not_awaited()


async def test_a_first_ever_message_opens_a_conversation():
    pod_id, agent_id, user_id = uuid4(), uuid4(), uuid4()
    surface = _surface(pod_id=pod_id, agent_id=agent_id, number="pn-new")
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, links = _binder(surface, earlier=None)

    _, created_title = await binder.bind_conversation(
        surface=surface,
        parsed=_dm_on("pn-new"),
        resolved_user=_person(user_id),
        route=_route(surface),
    )

    assert created_title is not None
    links.rebind_thread_address.assert_not_awaited()
