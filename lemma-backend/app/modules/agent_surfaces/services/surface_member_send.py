"""Reaching one named pod member on one named surface.

Split from :mod:`surface_egress`, which is conversation-driven: something was
said in a thread and has to go back to it. This is the other direction --
``surface.send``, called by a function, a workflow or an agent that has a person
and a surface and no thread in hand -- and it is mostly the refusals, because
six different things can stand between the two.

Those refusals used to be one ``False``, which the endpoint turned into one 404
reading "Member has no reachable conversation on this surface." That is true of
two of them. It is wrong for a surface that is switched off, for a user who is
not in the pod at all, and for a wiring fault in this process -- and each of
those is a different thing for the caller to do next. The notification path
already had the vocabulary (:class:`UndeliverableReason`); this borrows it so
the two ways of reaching somebody explain themselves the same way.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)

logger = get_logger(__name__)


class SurfaceMemberSendMixin:
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
        # Members of this surface's pod only, and FAIL CLOSED: this was once
        # `if self.pod_membership_port is not None`, which skipped the check
        # entirely when mis-wired — turning a wiring bug into "any user id can be
        # messaged". Not running the check is not the same as passing it.
        if self.pod_membership_port is None:
            logger.error(
                "agent_surfaces.ingress_service.send_to_member_no_membership_port.failed",
                surface_id=str(surface.id),
            )
            return UndeliverableReason.SEND_NOT_AVAILABLE
        if surface.pod_id not in set(
            await self.pod_membership_port.get_user_pod_ids(user_id)
        ):
            return UndeliverableReason.NOT_A_POD_MEMBER
        external_user_repository = getattr(self, "external_user_repository", None)
        if external_user_repository is None:
            logger.error(
                "agent_surfaces.ingress_service.send_to_member_no_membership_port.failed",
                surface_id=str(surface.id),
            )
            return UndeliverableReason.SEND_NOT_AVAILABLE
        # Every identity they hold on this platform, not just the most recently
        # seen one: Slack ids are per workspace and Teams ids per tenant, so
        # taking one made a pod's second workspace unreachable. The surface's own
        # tenant narrows the list, permissively where none was ever recorded.
        identities = await external_user_repository.list_by_resolved_users(
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
                sent = await self.send_agent_message_for_conversation(
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
