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

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)

logger = get_logger(__name__)


class MemberReach:
    """Send to a person on a surface, or say exactly why it could not."""

    def __init__(
        self,
        *,
        egress: SurfaceEgress,
        pod_membership_port: SurfacePodMembershipPort,
        external_user_repository: ExternalSurfaceUserRepository,
        conversation_link_repository: SurfaceConversationLinkRepository,
    ) -> None:
        self.egress = egress
        self.pod_membership_port = pod_membership_port
        self.external_user_repository = external_user_repository
        self.conversation_link_repository = conversation_link_repository

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
            link = await self.conversation_link_repository.get_latest_by_surface_and_external_user(
                surface_id=surface.id, external_user_id=ext.external_user_id
            )
            if link is not None:
                sent = await self.egress.send_agent_message_for_conversation(
                    conversation_id=link.conversation_id, message=message
                )
                return None if sent else UndeliverableReason.SEND_FAILED
        # Held apart because the repair differs: a tenant mismatch means they
        # are on the platform but in another workspace, so nothing they do in
        # this one will help until the surface is pointed at theirs.
        channel = surface.surface_type.value
        if wrong_tenant:
            return UndeliverableReason.wrong_tenant_on(channel)
        return UndeliverableReason.never_interacted_on(channel)
