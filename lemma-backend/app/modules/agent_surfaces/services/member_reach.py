"""Reaching one named pod member on one named surface.

The other direction from :class:`SurfaceEgress`, which is conversation-driven:
something was said in a thread and has to go back to it. This is
``surface.send`` -- called by a function, a workflow, or an agent that has a
person and a surface and no thread in hand -- and it is mostly the refusals,
because six different things can stand between the two.

Those refusals used to be one ``False``, which the endpoint turned into one 404
reading "Member has no reachable conversation on this surface." That is true of
two of them. It is wrong for a surface that is switched off, for a user who is
not in the pod at all, and for a wiring fault in this process -- and each of
those is a different thing for the caller to do next. The notification path
already had the vocabulary (:class:`UndeliverableReason`); this borrows it so
the two ways of reaching somebody explain themselves the same way.

Two of those six are gone, and their absence is the point of this being an
object. Both said "a collaborator this method needs was never wired" -- checked
at send time, once fail-closed after an incident in which a missing membership
port skipped the membership check entirely and made any user id messageable. A
constructor that requires both collaborators moves that failure from the first
send to the wiring, where a ``TypeError`` names it. Nothing is trusted that was
not; the check happens earlier, and the refusal it produced (a "try again
shortly" for something retrying could never fix) is deleted with it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.domain.adapter_port import reply_window_reopener
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.held_replies import (
    hold_reply,
    needs_asking,
)
from app.modules.agent_surfaces.services.reengage_template import (
    reengage_template_for,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
    reply_window_open,
)
from app.modules.channels.contracts import Source

logger = get_logger(__name__)


class MemberReach:
    """Send to a person on a surface, or say exactly why it could not."""

    def __init__(
        self,
        *,
        egress: SurfaceEgress,
        pod_membership_port: SurfacePodMembershipPort,
        external_user_repository: ExternalSurfaceUserRepository,
        seats: ChannelSeats,
    ) -> None:
        self.egress = egress
        self.pod_membership_port = pod_membership_port
        self.external_user_repository = external_user_repository
        self.seats = seats

    async def send_to_member(
        self,
        *,
        surface: AgentSurfaceEntity,
        user_id: UUID,
        message: str,
    ) -> str | None:
        """Proactively send a message to a pod member on a specific surface.

        Powers ``surface.send`` (notifications from functions/workflows, or an
        agent reaching a specific member). Reuses the member's existing thread on
        the surface — bots can't cold-DM, so the member must have interacted
        before.

        Returns ``None`` when the message went out, and otherwise the reason it
        did not, written to be read by whoever asked.
        """
        if not surface.is_active:
            return UndeliverableReason.SURFACE_NOT_ACTIVE
        # Members of this surface's pod only.
        if surface.pod_id not in set(
            await self.pod_membership_port.get_user_pod_ids(user_id)
        ):
            return UndeliverableReason.NOT_A_POD_MEMBER
        # Every identity they hold on this platform, not just the most recently
        # seen one: Slack ids are per workspace and Teams ids per tenant, so
        # taking one made a pod's second workspace unreachable. The surface's own
        # tenant narrows the list, permissively where none was ever recorded.
        identities = await self.external_user_repository.list_by_resolved_users(
            platform=surface.surface_type.value, resolved_user_ids=[user_id]
        )
        wrong_tenant = False
        for ext in identities:
            if not ext.external_user_id or not surface.matches_tenant(ext.tenant_id):
                wrong_tenant = wrong_tenant or bool(ext.external_user_id)
                continue
            place = await self._private_place(surface, ext.external_user_id)
            if place is not None:
                source, conversation_id = place
                if not reply_window_open(
                    platform=surface.surface_type,
                    last_inbound_at=source.last_inbound_at,
                ):
                    return await self._reach_after_window(
                        surface, conversation_id, message
                    )
                sent = await self.egress.send_agent_message_for_conversation(
                    conversation_id=conversation_id, message=message
                )
                return None if sent else UndeliverableReason.SEND_FAILED
        # Held apart because the repair differs: a tenant mismatch means they
        # are on the platform but in another workspace, so nothing they do in
        # this one will help until the surface is pointed at theirs.
        channel = surface.surface_type.value
        if wrong_tenant:
            return UndeliverableReason.wrong_tenant_on(channel)
        return UndeliverableReason.never_interacted_on(channel)

    async def _private_place(
        self, surface: AgentSurfaceEntity, external_user_id: str
    ) -> tuple[Source, UUID] | None:
        """The person's private chat on this surface, and the conversation
        they last wrote into there -- or None if they never wrote to it."""
        places = await self.seats.person_places(
            platform=surface.surface_type.value,
            external_user_id=external_user_id,
            surface_ids=[surface.id],
            limit=1,
        )
        if not places:
            return None
        conversation_id = await self.seats.conversation_at(
            places[0].id, sender_external_id=external_user_id
        )
        return (places[0], conversation_id) if conversation_id else None

    async def _reach_after_window(
        self, surface: AgentSurfaceEntity, conversation_id: UUID, message: str
    ) -> str | None:
        """A member whose reply window has closed: ask them to write, and hold
        the message for when they do.

        Without a template there is no asking. Meta accepts a free-form send
        then with a 200 and drops it later, so the honest answer is a refusal
        now.
        """
        template = reengage_template_for(surface)
        if template is None:
            return UndeliverableReason.REPLY_WINDOW_CLOSED
        target = await self.egress.delivery.resolve_egress_target(conversation_id)
        reopener = reply_window_reopener(target.adapter) if target else None
        if target is None or reopener is None:
            return UndeliverableReason.REPLY_WINDOW_CLOSED
        uow = self.egress.uow
        asking = await needs_asking(uow, conversation_id)
        if asking:
            try:
                async with connection_released(uow.session):
                    await reopener.reopen_reply_window(
                        credentials=target.credentials,
                        event=target.event,
                        template=template,
                    )
            except platform_transport_errors():
                logger.warning(
                    "agent_surfaces.member_reach.reengage_not_sent.degraded",
                    surface_id=str(surface.id),
                    conversation_id=str(conversation_id),
                    exc_info=True,
                )
                # Nothing is held: a message behind an ask that never went
                # would wait for a reply that is not coming.
                return UndeliverableReason.SEND_FAILED
        await hold_reply(uow, conversation_id, message, asked=asking)
        return None
