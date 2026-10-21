"""Which place a message was said in, and whose channel a new place belongs to.

The rules that decide a person has one conversation however a chat moves: a
private chat is keyed by the person, never by the address it arrived on; a
group, a shared channel and an email thread with others on it are keyed by
the chat, and are a channel of their own; an email between the pod and one
person is that person's, on the inbox.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.channel_places import (
    home_of,
    is_shared,
    place_of,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.channels.contracts import (
    ContactsHome,
    GroupHome,
    MembersHome,
    SourceKind,
)

PERSON = "15550100"


def _surface(platform: SurfacePlatform) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=platform.value.lower(),
        agent_id=uuid4(),
        surface_type=platform,
        config=SurfaceConfig(),
        is_active=True,
    )


def _route(kind: str) -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=uuid4(),
        agent_id=None,
        agent_name=None,
        agent_display_name="Assistant",
        conversation_kind=kind,
        route_key=kind.lower(),
    )


def _said(
    platform: SurfacePlatform,
    *,
    chat: str,
    thread: str | None = None,
    is_dm: bool = True,
    cc: list[str] | None = None,
    title: str | None = None,
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id=chat,
        external_thread_id=thread or chat,
        sender_external_user_id=PERSON,
        message_text="hello",
        is_dm=is_dm,
        reply_target={"cc": cc} if cc else {},
        metadata={"chat_title": title} if title else {},
    )


def _place(surface, parsed, kind: str):
    return place_of(
        surface=surface, parsed=parsed, route=_route(kind), external_user_id=PERSON
    )


def test_a_private_chat_is_keyed_by_the_person_not_the_number_it_came_to():
    whatsapp = _surface(SurfacePlatform.WHATSAPP)

    on_one_number = _place(whatsapp, _said(SurfacePlatform.WHATSAPP, chat="pn-1"), "DM")
    on_another = _place(whatsapp, _said(SurfacePlatform.WHATSAPP, chat="pn-2"), "DM")

    assert on_one_number == on_another
    assert on_one_number.kind is SourceKind.PERSON
    assert (on_one_number.external_channel_id, on_one_number.external_user_id) == (
        None,
        PERSON,
    )


def test_a_group_is_keyed_by_the_chat_and_named_for_it():
    telegram = _surface(SurfacePlatform.TELEGRAM)
    parsed = _said(
        SurfacePlatform.TELEGRAM, chat="-100", is_dm=False, title="Launch crew"
    )

    place = _place(telegram, parsed, "CHANNEL")

    assert place.kind is SourceKind.CHAT
    assert (place.external_channel_id, place.external_user_id) == ("-100", None)
    assert place.title == "Launch crew"


def test_an_email_is_the_correspondents_until_somebody_else_is_copied():
    mail = _surface(SurfacePlatform.RESEND)
    alone = _said(SurfacePlatform.RESEND, chat="inbox", thread="<root@mail>")
    copied = _said(
        SurfacePlatform.RESEND, chat="inbox", thread="<root@mail>", cc=["ana@x.test"]
    )

    assert not is_shared(_route("EMAIL"), alone)
    assert is_shared(_route("EMAIL"), copied)
    private, shared = _place(mail, alone, "EMAIL"), _place(mail, copied, "EMAIL")
    # One place per correspondent on the inbox, whatever thread they write in.
    assert private.kind is SourceKind.PERSON
    assert (private.external_channel_id, private.external_user_id) == (None, PERSON)
    assert shared.kind is SourceKind.CHAT
    assert (shared.external_channel_id, shared.external_user_id) == (
        "<root@mail>",
        None,
    )


def test_a_new_private_place_belongs_to_its_person_and_a_shared_one_to_itself():
    whatsapp = _surface(SurfacePlatform.WHATSAPP)
    member, contact = uuid4(), uuid4()
    private = _place(whatsapp, _said(SurfacePlatform.WHATSAPP, chat="pn-1"), "DM")
    group = _place(
        whatsapp, _said(SurfacePlatform.WHATSAPP, chat="g-1", is_dm=False), "CHANNEL"
    )

    assert home_of(private, user_id=member, contact_id=None) == MembersHome(member)
    # A contact's: kept by the member looking after them, who follows it.
    assert home_of(private, user_id=member, contact_id=contact) == ContactsHome(
        contact, keeper_user_id=member
    )
    assert isinstance(home_of(group, user_id=member, contact_id=contact), GroupHome)
