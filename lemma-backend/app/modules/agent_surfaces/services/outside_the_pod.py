"""The two ways a sender who is not in the pod can still be answered.

In a group the pod opened to outsiders, they are one of its people from outside
(``OutsiderDoor``). In a private chat with a bot that answers contacts, they are
a contact (``ContactDoor``). Anywhere else they are refused, and that refusal is
not decided here.
"""

from __future__ import annotations

from enum import Enum

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.services.contacts import ContactDoor
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.outsiders import OutsiderDoor
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter


class NotOutside(Enum):
    """Neither door applies: the sender routes, or is refused, as before."""

    NOT_OUTSIDE = "not_outside"


NOT_OUTSIDE = NotOutside.NOT_OUTSIDE


async def answer_outside_the_pod(
    *,
    uow: SqlAlchemyUnitOfWork,
    router: SurfaceRouter,
    binder: ConversationBinder,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    sender: ResolvedSurfaceUser,
) -> SurfaceChatContext | SurfaceReplyContext | None | NotOutside:
    """The run for a sender from outside the pod, ``None``, or ``NOT_OUTSIDE``.

    ``None`` is a door that applied and decided nothing should run -- a limit,
    parked mail, nobody looking after them -- and gets no reply. A reply
    context is the one refusal a bot for known contacts gives a stranger.
    """
    membership = router.pod_membership_port
    outsiders = OutsiderDoor(uow=uow, membership=membership)
    group = await outsiders.group_welcoming(
        surface=surface, parsed=parsed, sender=sender
    )
    contacts = ContactDoor(uow=uow, membership=membership)
    if group is None and not await contacts.applies(
        surface=surface, parsed=parsed, sender=sender
    ):
        return NOT_OUTSIDE
    route = await router.resolve_route(surface=surface, parsed=parsed)
    if route is None:
        return None
    if group is not None:
        return await outsiders.prepare(
            surface=surface,
            parsed=parsed,
            sender=sender,
            group=group,
            route=route,
            binder=binder,
        )
    return await contacts.prepare(
        surface=surface, parsed=parsed, sender=sender, route=route, binder=binder
    )
