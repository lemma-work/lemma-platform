"""The run that answers a person outside the pod, whichever door let them in.

A group the pod opened to outsiders (``OutsiderDoor``) and a private chat or
an email to a bot that answers contacts (``ContactDoor``) decide differently
whether somebody is answered: who looks after them, whether they opted out,
whether the mail was forged. Once either has decided, the turn is the same:
the person's own conversation at the place, kept by the member who looks
after them and naming the contact, so every run in it authorizes as the
contact and reads only what is Public and what is theirs
(``agent.domain.outsiders``); and the run is told who asked, by their own
name and number, so the member reading the thread sees who asked each
question.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.contracts.channels import Audience
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.chat_context_builder import build_chat_context
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import ContactRef


async def contact_turn(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    sender: ResolvedSurfaceUser,
    route: ResolvedSurfaceRoute,
    binder: ConversationBinder,
    contact: ContactRef,
    keeper: UUID,
) -> SurfaceChatContext:
    """The contact's run at the place ``parsed`` was said, kept by ``keeper``."""
    bound = await binder.bind_conversation(
        surface=surface,
        parsed=parsed,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=keeper,
            external_user_id=sender.external_user_id,
            display_name=contact.display_name or sender.display_name,
        ),
        route=route,
        contact_id=contact.id,
    )
    return build_chat_context(
        surface=surface,
        parsed=parsed,
        resolved_user=sender,
        user_id=keeper,
        route=route,
        conversation_id=bound.conversation_id,
        source_id=bound.source_id,
        created_conversation_title=bound.created_title,
        audience=Audience.contact(contact.id),
    )
