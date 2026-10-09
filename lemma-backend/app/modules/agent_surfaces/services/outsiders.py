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
* **Where it runs.** In one conversation per group, belonging to the member who
  answers for it, marked as answering outsiders so every run in it authorizes as
  nobody and reads only what is Public (``agent.domain.outsiders``). The member's
  own turns in the group are untouched: they still run as the member, in the
  member's own conversation.
* **How often.** ``OutsiderTurnLimiter``, per person and per group.

Nothing here answers a stranger who is *refused*. Most platforms cannot reply
privately inside a group, and a public notice about somebody's standing is worse
than silence -- the refusal paths in ``fallback_reply_service`` already decide
that, and a group that does not welcome outsiders goes through them unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER, SurfaceGroup
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.services.chat_context_builder import build_chat_context
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.outsider_limits import OutsiderTurnLimiter
from app.modules.agent_surfaces.services.slack_groups import answers_slack_outsider
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

#: How the shared outsiders thread is labelled where a person is expected.
_OUTSIDERS_LABEL = "People outside the pod"


def named_after(
    parsed: ParsedInboundSurfaceEvent, group: SurfaceGroup
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


class OutsiderDoor:
    """Decides whether a group sender from outside the pod is answered, and binds them."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        membership: SurfacePodMembershipPort,
        limiter: OutsiderTurnLimiter | None = None,
    ) -> None:
        self.groups = SurfaceGroupRepository(uow.session)
        self.membership = membership
        self.limiter = limiter or OutsiderTurnLimiter()

    async def group_welcoming(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> SurfaceGroup | None:
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
        group = await self.groups.get(
            surface_id=surface.id, external_channel_id=parsed.external_channel_id
        )
        return await self._welcoming(surface, parsed, group)

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
            for group in await self.groups.list_for_channel(
                external_channel_id=parsed.external_channel_id,
                surface_ids=[surface.id for surface in outside],
            )
        }
        for surface in outside:
            if await self._welcoming(surface, parsed, groups.get(surface.id)):
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
        group: SurfaceGroup | None,
    ) -> SurfaceGroup | None:
        """The group, if it answers people outside the pod at all."""
        if group is None or not group.welcomes_outsiders:
            return None
        # The bot's own switch, over every group it is in.
        if not surface.config.groups.answers_outsiders:
            return None
        if parsed.platform is SurfacePlatform.SLACK and not answers_slack_outsider(
            group.shared_externally, parsed
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

    async def prepare(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        group: SurfaceGroup,
        route: ResolvedSurfaceRoute,
        binder: ConversationBinder,
    ) -> SurfaceChatContext | None:
        """The run for a stranger's question, or None when it should not run."""
        owner = group.owner_user_id
        if owner is None or not sender.external_user_id:
            return None
        if not await self.limiter.allow(
            group_id=group.id, sender_external_id=sender.external_user_id
        ):
            return None
        parsed = named_after(parsed, group)
        link, created_title = await binder.bind_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=owner,
                external_user_id=OUTSIDERS_LINK_USER,
                display_name=_OUTSIDERS_LABEL,
            ),
            route=route,
            for_outsiders=True,
        )
        return build_chat_context(
            surface=surface,
            parsed=parsed,
            # The stranger's own name and number go on the message, so the
            # owner reading the thread sees who asked each question.
            resolved_user=sender,
            user_id=owner,
            route=route,
            conversation_id=link.conversation_id,
            created_conversation_title=created_title,
            audience=Audience.outsiders(),
        )
