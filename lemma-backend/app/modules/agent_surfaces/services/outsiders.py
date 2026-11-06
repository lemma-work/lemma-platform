"""Letting somebody outside the pod ask its bot something, in a group that allows it.

Routing has always answered one question about a group message -- which pod is
this sender in -- and a sender in none of the candidates was dropped without a
word. That stays the answer everywhere except one place: a group the pod knows,
with a member answering for it (see ``domain/groups``). There, a stranger who
addresses the bot gets an answer, and this module is how:

* **Who counts.** Anybody not in the pod that owns the surface: a person with no
  Lemma account, a Lemma user in another pod, a colleague from the same
  organisation who was never added. They are all the same to the pod -- nobody
  it may grant anything to.
* **Who they are.** A contact of the pod, by the handle the platform vouched for
  (``sender_handles.group_handle``; on Slack, also anybody in a workspace the
  pod's organization holds, ``slack_workspace_people``) -- opened the first
  time they put the bot to work in one of its groups.
* **Where it runs.** In a conversation of their own in that group, looked after
  by the member who answers for it and naming the contact, so every run in it
  authorizes as that contact and reads only what is Public and what is theirs
  (``agent.domain.outsiders``). The member's own turns in the group are
  untouched: they still run as the member, in the member's own conversation.
* **How often.** ``ContactTurnLimiter``, per person -- the same window as
  when they write privately -- and per group a day.

Nothing here answers a stranger who is *refused*. Most platforms cannot reply
privately inside a group, and a public notice about somebody's standing is worse
than silence -- the refusal paths in ``fallback_reply_service`` already decide
that, and a group that does not welcome outsiders goes through them unchanged.
"""

from __future__ import annotations

from functools import partial

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.channels.contracts import Source
from app.modules.channels.contracts.places import place_heard_on, places_hearing
from app.modules.agent_surfaces.services.contact_turns import contact_turn
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.services.outsider_limits import (
    ContactTurnLimiter,
    person_in,
)
from app.modules.agent_surfaces.services.sender_handles import (
    SenderHandle,
    group_handle,
)
from app.modules.agent_surfaces.services.slack_workspace_people import (
    WorkspacePeople,
)
from app.modules.agent_surfaces.services.workspace_channels import (
    answers_slack_outsider,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    open_contact,
)

logger = get_logger(__name__)


def named_after(
    parsed: ParsedInboundSurfaceEvent, group: Source
) -> ParsedInboundSurfaceEvent:
    """The event, carrying the group's recorded name where it came without one.

    A WhatsApp message names no group, only its id; the pod's record of the
    group has the name somebody gave it, and that is what the owner should
    read the conversation under.
    """
    if parsed.metadata.get("chat_title") or not group.title:
        return parsed
    return parsed.model_copy(
        update={"metadata": {**parsed.metadata, "chat_title": group.title}}
    )


class ContactOpener(Protocol):
    """Finds or opens the contact a vouched-for handle names (``open_contact``)."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        /,
        *,
        pod_id: UUID,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> ContactRef: ...


class OutsiderDoor:
    """Decides whether a group sender from outside the pod is answered, and binds them."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        membership: SurfacePodMembershipPort,
        limiter: ContactTurnLimiter | None = None,
        contacts: ContactOpener = open_contact,
        people: WorkspacePeople | None = None,
    ) -> None:
        self.uow = uow
        self.open_contact = contacts
        self.people = people or WorkspacePeople(uow)
        #: The group a surface hears on a chat, if the pod knows one.
        self.group_at = partial(place_heard_on, uow)
        self.membership = membership
        self.limiter = limiter or ContactTurnLimiter()

    async def group_welcoming(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> Source | None:
        """The group that answers this sender as an outsider, or None.

        None for a private chat, for a group the pod does not know or has not
        opened to outsiders, and for a member of the pod -- a member is never an
        outsider, and routes as themselves.
        """
        if parsed.is_dm or not parsed.external_channel_id:
            return None
        # Membership first: members are most of any group's traffic, and none
        # of them should pay for a lookup that can only answer "not you". Asked
        # the way routing asks it (`SurfaceRouter.matches_user`), so a sender
        # cannot be a member to one and an outsider to the other.
        if surface.pod_id in await self._pods_of(sender):
            return None
        group = await self.group_at(
            surface_id=surface.id, external_channel_id=parsed.external_channel_id
        )
        return await self._welcoming(surface, parsed, sender, group)

    async def surface_for(
        self,
        candidates: Sequence[AgentSurfaceEntity],
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> AgentSurfaceEntity | None:
        """The first candidate whose group would answer this sender as an outsider.

        One read of the sender's pods and one of the chat's groups, whatever the
        number of candidates: on a shared bot every tenant is one.
        """
        if parsed.is_dm or not parsed.external_channel_id or not candidates:
            return None
        pods = await self._pods_of(sender)
        outside = [surface for surface in candidates if surface.pod_id not in pods]
        groups = {
            group.surface_id: group
            for group in await places_hearing(
                self.uow,
                external_channel_id=parsed.external_channel_id,
                surface_ids=[surface.id for surface in outside],
            )
        }
        for surface in outside:
            if await self._welcoming(surface, parsed, sender, groups.get(surface.id)):
                return surface
        return None

    async def _pods_of(self, sender: ResolvedSurfaceUser) -> set[UUID]:
        if sender.internal_user_id is None:
            return set()
        return set(await self.membership.get_user_pod_ids(sender.internal_user_id))

    async def _welcoming(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        group: Source | None,
    ) -> Source | None:
        """The group, if it answers people outside the pod at all."""
        if group is None or not group.welcomes_outsiders:
            return None
        # The bot's own switch, over every group it is in.
        if not surface.config.groups.answers_outsiders:
            return None
        if parsed.platform is SurfacePlatform.SLACK and not (
            answers_slack_outsider(group.shared_externally, parsed)
            or await self._outsider_handle_of(surface, parsed, sender) is not None
        ):
            return None
        # A member who has left the pod answers for nobody: every question
        # passed on would reach somebody who can no longer act on it.
        owner = group.owner_user_id
        if owner is None or surface.pod_id not in set(
            await self.membership.get_user_pod_ids(owner)
        ):
            return None
        return group

    async def _outsider_handle_of(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> SenderHandle | None:
        """The handle a group sender from outside the pod is a contact by.

        Somebody in a Slack workspace the pod's organization holds -- a guest
        too: the group let them in -- is one of its people, asked only now,
        after resolution, so a member it has not recognised yet is never
        opened as a contact (``GroupLog`` asks only ``group_handle``).
        """
        handle = group_handle(parsed)
        if handle is not None:
            return handle
        return await self.people.colleague_handle(
            surface, parsed, sender, private=False
        )

    async def prepare(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        group: Source,
        route: ResolvedSurfaceRoute,
        binder: ConversationBinder,
    ) -> SurfaceChatContext | None:
        """The run for a stranger's question, or None when it should not run."""
        owner = group.owner_user_id
        handle = await self._outsider_handle_of(surface, parsed, sender)
        if owner is None or not sender.external_user_id or handle is None:
            return None
        if not await self.limiter.allow(
            person=person_in(surface.pod_id, f"{handle.kind.value}:{handle.value}"),
            place=group.id,
            place_per_day=surface_settings.surface_outsider_turns_per_group_per_day,
        ):
            return None
        contact = await self.open_contact(
            self.uow,
            pod_id=surface.pod_id,
            kind=handle.kind,
            value=handle.value,
            strength=IdentityStrength.CHANNEL,
            display_name=parsed.sender_display_name or sender.display_name,
        )
        context = await contact_turn(
            surface=surface,
            parsed=named_after(parsed, group),
            sender=sender,
            route=route,
            binder=binder,
            contact=contact,
            keeper=owner,
        )
        logger.info(
            "agent_surfaces.outsiders.contact_turn.observed",
            surface_id=str(surface.id),
            contact_id=str(contact.id),
        )
        return context
