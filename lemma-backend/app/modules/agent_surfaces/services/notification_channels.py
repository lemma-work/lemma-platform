"""Working out how this agent can reach this person, and minting one if it cannot.

Split from ``notification_service`` because the two answer different questions.
That service owns what a notification *means* — the row, the lifecycle, whether
it was answered. This owns the routing: which of the agent's surfaces can carry
a message to one recipient, in what order, and why the answer is sometimes
nothing.

The rule is agent-scoped: candidates are the surfaces the *sending* agent speaks
through, so a recipient hears from the bot they already know that agent as. See
``notification_delivery.surfaces_for_agent`` for why that beats the recipient's
own preferred surface.

When an agent has no surface at all, one is minted here rather than the caller
being told to go and connect something. That used to happen only when the *pod*
had none, which left a hole big enough to break the pod assistant permanently:
the first agent to be given its own mailbox made the pod non-empty, and the
assistant — which looks for a surface with no agent of its own — was then never
given one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.services.email_surface_provisioning import (
    email_is_configured,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
    rank_candidates,
    reply_window_open,
    surfaces_for_agent,
)

logger = get_logger(__name__)

SurfaceProvisioner = Callable[
    [UUID, UUID | None, str | None],
    Awaitable[tuple[AgentSurfaceEntity | None, str | None]],
]


def can_cold_open(surface: AgentSurfaceEntity) -> bool:
    """Whether this surface can start a conversation from nothing.

    True for email, false for every chat platform: a bot with no prior thread
    has nowhere to put a message. That asymmetry is why email is the fallback.
    """
    capabilities = get_platform_capabilities(surface.surface_type.value)
    return bool(capabilities is not None and capabilities.can_cold_open)


class NotificationChannelResolver:
    def __init__(
        self,
        *,
        surface_repository,
        external_user_repository,
        conversation_link_repository,
        pod_membership_port,
        surface_provisioner: SurfaceProvisioner | None = None,
    ):
        self.surfaces = surface_repository
        self.external_users = external_user_repository
        self.links = conversation_link_repository
        self.membership = pod_membership_port
        # Injected rather than built here: creating a surface is the surface
        # service's job, and it already owns address allocation, credential
        # checks and receiver registration.
        self.surface_provisioner = surface_provisioner

    async def resolve(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        actor_agent_id: UUID | None = None,
        origin_surface_id: UUID | None = None,
        agent_name: str | None = None,
    ) -> tuple[list[DeliveryChannel], str]:
        """Every way this agent can reach this person, best first.

        Returns the reason alongside, so an empty list is never a silent no.
        """
        # ``list_by_pod`` is paginated and returns ``(items, next_cursor)``. A pod
        # with more surfaces than one page is not a real shape — they are bots a
        # human connected by hand — so the first page is the whole set.
        all_surfaces, _ = await self.surfaces.list_by_pod(pod_id)
        active = [surface for surface in all_surfaces if surface.is_active]
        surfaces = surfaces_for_agent(active, actor_agent_id=actor_agent_id)

        if not surfaces:
            provisioned, reason = await self.provision_mailbox(
                pod_id, actor_agent_id, agent_name
            )
            if provisioned is None:
                return [], reason or (
                    UndeliverableReason.NO_ACTIVE_SURFACE
                    if not active
                    else UndeliverableReason.NO_SURFACE_FOR_AGENT
                )
            surfaces = [provisioned]

        candidates, saw_chat_surface, window_closed = await self._channels_from(
            surfaces, recipient_user_id
        )

        if not candidates and saw_chat_surface:
            # Chat surfaces the recipient has never written to, and no mailbox to
            # fall back on. Email is meant to be the channel that always works;
            # it cannot be if this agent's only surfaces are bots that cannot
            # open a conversation.
            candidates = await self._email_fallback(
                surfaces,
                pod_id=pod_id,
                actor_agent_id=actor_agent_id,
                agent_name=agent_name,
                recipient_user_id=recipient_user_id,
            )

        if candidates:
            return rank_candidates(candidates, origin_surface_id=origin_surface_id), ""

        # Reasons are ordered by how actionable they are. "Their window closed"
        # tells someone to wait; "they never messaged the bot" tells them what to
        # go and do, and is the one that will actually be hit in practice.
        if window_closed:
            return [], UndeliverableReason.REPLY_WINDOW_CLOSED
        if saw_chat_surface:
            return [], UndeliverableReason.NEVER_INTERACTED
        return [], UndeliverableReason.NO_EMAIL_ADDRESS

    async def provision_mailbox(
        self,
        pod_id: UUID,
        agent_id: UUID | None,
        agent_name: str | None,
    ) -> tuple[AgentSurfaceEntity | None, str]:
        """``(surface, reason)`` — the reason explains a None.

        Asking someone to connect a surface before their first notification can
        send is a poor trade when we already run a system mailbox. Provisioned
        on first need rather than at pod creation: most pods never message
        anyone, and an address handed out is an address to keep working.

        A failure here is never fatal. The notification row exists and the inbox
        has it, so this degrades to an explanation rather than an exception —
        but the explanation says *which* thing went wrong, because "the pod has
        no active surface" on a deployment whose real problem was an unset mail
        domain sent us looking in the wrong place.
        """
        if not email_is_configured() or self.surface_provisioner is None:
            return None, UndeliverableReason.EMAIL_NOT_CONFIGURED
        try:
            surface, cause = await self.surface_provisioner(
                pod_id, agent_id, agent_name
            )
        except (AgentSurfaceError, OSError) as exc:
            # ``failure_type``/``failure_code``, not ``error``: the log pipeline
            # strips any field named ``error``, so that is where the cause of a
            # production failure goes to die.
            logger.warning(
                "agent_surfaces.notification_channels.provision_failed.degraded",
                pod_id=str(pod_id),
                failure_type=type(exc).__name__,
            )
            surface, cause = None, type(exc).__name__
        if surface is None:
            return None, (
                f"{UndeliverableReason.MAILBOX_PROVISION_FAILED} ({cause})"
                if cause
                else UndeliverableReason.MAILBOX_PROVISION_FAILED
            )
        return surface, ""

    async def _channels_from(
        self,
        surfaces: list[AgentSurfaceEntity],
        recipient_user_id: UUID,
    ) -> tuple[list[DeliveryChannel], bool, bool]:
        """``(candidates, saw_chat_surface, window_closed)`` for these surfaces."""
        candidates: list[DeliveryChannel] = []
        saw_chat_surface = False
        window_closed = False

        for surface in surfaces:
            if can_cold_open(surface):
                # No prior thread needed: the address *is* the channel.
                email_channel = await self._email_channel(surface, recipient_user_id)
                if email_channel is not None:
                    candidates.append(email_channel)
                continue

            saw_chat_surface = True
            channel, closed = await self._chat_channel(surface, recipient_user_id)
            window_closed = window_closed or closed
            if channel is not None:
                candidates.append(channel)

        return candidates, saw_chat_surface, window_closed

    async def _email_fallback(
        self,
        surfaces: list[AgentSurfaceEntity],
        *,
        pod_id: UUID,
        actor_agent_id: UUID | None,
        agent_name: str | None,
        recipient_user_id: UUID,
    ) -> list[DeliveryChannel]:
        """A mailbox for an agent whose only surfaces are chat bots it cannot use.

        Nothing to do when it already has one — an existing mailbox that yielded
        no channel means the *recipient* has no address on file, which minting a
        second mailbox would not fix.
        """
        if any(can_cold_open(surface) for surface in surfaces):
            return []
        provisioned, _ = await self.provision_mailbox(
            pod_id, actor_agent_id, agent_name
        )
        if provisioned is None:
            return []
        channel = await self._email_channel(provisioned, recipient_user_id)
        return [channel] if channel is not None else []

    async def _chat_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> tuple[DeliveryChannel | None, bool]:
        """``(channel, window_closed)`` for a chat surface.

        A bot cannot start a conversation, so this only yields a channel when
        the person has written to that surface before. ``window_closed`` is
        reported separately because "they went quiet too long ago" is a
        different thing to tell somebody than "they have never messaged us".
        """
        external = await self.external_users.get_by_resolved_user(
            platform=surface.surface_type.value,
            resolved_user_id=recipient_user_id,
        )
        if external is None or not external.external_user_id:
            return None, False
        link = await self.links.get_latest_by_surface_and_external_user(
            surface_id=surface.id,
            external_user_id=external.external_user_id,
        )
        if link is None:
            return None, False
        if not reply_window_open(
            platform=surface.surface_type,
            last_inbound_at=link.inbound_activity_at,
        ):
            return None, True
        return (
            DeliveryChannel(
                surface=surface,
                external_user_id=external.external_user_id,
                link=link,
            ),
            False,
        )

    async def _email_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> DeliveryChannel | None:
        address = await self.membership.get_user_email(recipient_user_id)
        if not address:
            return None
        return DeliveryChannel(surface=surface, email_address=address)


__all__ = ["NotificationChannelResolver", "SurfaceProvisioner", "can_cold_open"]
