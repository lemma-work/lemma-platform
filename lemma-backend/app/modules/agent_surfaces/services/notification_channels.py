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

That ordering is a default, not a policy. An agent that names a ``channel`` gets
that channel or a refusal — never a different one — because an agent picks a
channel for reasons the router cannot see ("she is travelling, use WhatsApp"),
and silently overriding it would leave the agent certain of something untrue.

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
    EMAIL_CHANNEL,
    DeliveryChannel,
    UndeliverableReason,
    channel_for_platform,
    channel_refused,
    channels_of,
    rank_candidates,
    reply_window_open,
    surfaces_for_agent,
    surfaces_on_channel,
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
        channel: str | None = None,
    ) -> tuple[list[DeliveryChannel], str]:
        """Every way this agent can reach this person, best first.

        Returns the reason alongside, so an empty list is never a silent no.

        ``channel`` narrows the whole thing to one channel the agent chose. It
        is a filter, not a preference: nothing outside it is tried.
        """
        # ``list_by_pod`` is paginated and returns ``(items, next_cursor)``. A pod
        # with more surfaces than one page is not a real shape — they are bots a
        # human connected by hand — so the first page is the whole set.
        all_surfaces, _ = await self.surfaces.list_by_pod(pod_id)
        active = [surface for surface in all_surfaces if surface.is_active]
        surfaces = surfaces_for_agent(active, actor_agent_id=actor_agent_id)

        # Before provisioning, not after: a mailbox is what "reach them somehow"
        # falls back to, and minting one to answer "reach them on Telegram" hands
        # out an address nobody asked for and that then has to keep working.
        if channel:
            return await self._resolve_on_channel(
                surfaces,
                channel=channel,
                pod_id=pod_id,
                recipient_user_id=recipient_user_id,
                actor_agent_id=actor_agent_id,
                agent_name=agent_name,
                origin_surface_id=origin_surface_id,
            )

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

    async def _resolve_on_channel(
        self,
        surfaces: list[AgentSurfaceEntity],
        *,
        channel: str,
        pod_id: UUID,
        recipient_user_id: UUID,
        actor_agent_id: UUID | None,
        agent_name: str | None,
        origin_surface_id: UUID | None,
    ) -> tuple[list[DeliveryChannel], str]:
        """This channel or nothing, with the refusal saying what would work.

        No email fallback and no rerouting: both exist to rescue a router that
        was guessing, and this one was told. The ranking still runs, because a
        channel can hold several surfaces and the freshest thread is still the
        best of them.
        """
        wanted = surfaces_on_channel(surfaces, channel=channel)
        cause = ""
        if not wanted and channel == EMAIL_CHANNEL:
            # Asking for email is the one request worth minting a surface for:
            # a mailbox is the only channel we can create on demand, and this is
            # the same first-need provisioning the default path already does. A
            # failure keeps its own reason — "the mail domain is unset" and "this
            # agent has no mailbox" send you to different places.
            provisioned, cause = await self.provision_mailbox(
                pod_id, actor_agent_id, agent_name
            )
            wanted = [provisioned] if provisioned is not None else []

        candidates, _, window_closed = await self._channels_from(
            wanted, recipient_user_id
        )
        if candidates:
            return rank_candidates(candidates, origin_surface_id=origin_surface_id), ""

        return [], channel_refused(
            channel,
            cause=cause
            or self._refusal_cause(channel, wanted=wanted, window_closed=window_closed),
            alternatives=await self._reachable_elsewhere(
                surfaces, channel=channel, recipient_user_id=recipient_user_id
            ),
        )

    @staticmethod
    def _refusal_cause(
        channel: str, *, wanted: list[AgentSurfaceEntity], window_closed: bool
    ) -> str:
        """Which of the four ways a named channel fails this one was."""
        if not wanted:
            return UndeliverableReason.no_surface_on(channel)
        if window_closed:
            return UndeliverableReason.window_closed_on(channel)
        if channel == EMAIL_CHANNEL:
            return UndeliverableReason.no_address_on(channel)
        return UndeliverableReason.never_interacted_on(channel)

    async def _reachable_elsewhere(
        self,
        surfaces: list[AgentSurfaceEntity],
        *,
        channel: str,
        recipient_user_id: UUID,
    ) -> list[str]:
        """The channels that would have worked, for the refusal to name.

        Costs a second resolution pass, which is why it only runs once the
        requested channel has already failed. A refusal that cannot say what to
        do instead just moves the guessing into the model.
        """
        others = [
            surface
            for surface in surfaces
            if channel_for_platform(surface.surface_type) != channel
        ]
        candidates, _, _ = await self._channels_from(others, recipient_user_id)
        alternatives = channels_of(candidates)
        # A mailbox this agent does not have yet still counts, because dropping
        # the channel argument would mint one and the message would go. Saying
        # "no other channel can reach them" there would talk an agent out of the
        # one call that works.
        if (
            EMAIL_CHANNEL not in alternatives
            and channel != EMAIL_CHANNEL
            and email_is_configured()
            and self.surface_provisioner is not None
            and await self.membership.get_user_email(recipient_user_id)
        ):
            alternatives = sorted([*alternatives, EMAIL_CHANNEL])
        return alternatives

    async def provision_mailbox(
        self,
        pod_id: UUID,
        agent_id: UUID | None,
        agent_name: str | None,
    ) -> tuple[AgentSurfaceEntity | None, str]:
        """``(surface, reason)`` — the reason explains a None.

        Asking someone to connect a surface before their first notification can
        send is a poor trade when we already run a system mailbox.

        The catch-up path, not the normal one. Pods and agents are given a
        mailbox as they are created, so what reaches here is a pod that predates
        that, or one whose surface has since been deleted. Provisioning on first
        need used to be the whole design — the argument being that most pods
        never message anyone — and that turned out to have the cost backwards:
        inbound routes on the address, so a pod that had not yet sent anything
        matched no surface and mail to the obvious guess started nothing.

        A failure here is never fatal. The notification row exists and the inbox
        has it, so this degrades to an explanation rather than an exception —
        but the explanation says *which* thing went wrong, because "the pod has
        no active surface" on a deployment whose real problem was an unset mail
        domain sent us looking in the wrong place.
        """
        if not email_is_configured() or self.surface_provisioner is None:
            return None, UndeliverableReason.EMAIL_NOT_CONFIGURED
        # No agent id means the pod assistant, and its mailbox is the pod's own:
        # `acme@`, not `pod-default.acme@`. The name travelling with a
        # notification is the assistant's internal one ("pod_default"), which is
        # not something to ask a person to type — and passing it produced
        # exactly that address on dev.
        try:
            surface, cause = await self.surface_provisioner(
                pod_id, agent_id, agent_name if agent_id is not None else None
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

        Every identity the person holds on the platform is tried, not just the
        most recently seen one. Slack ids are per workspace and Teams ids per
        tenant, so a pod with two Slack surfaces gives one person two identities
        — and taking whichever was seen last made the other surface permanently
        unreachable while reporting it as "they have never messaged us". The
        surface's own tenant narrows the list first; that check is permissive
        where the tenant was never recorded, so surfaces predating it keep
        working.
        """
        identities = await self.external_users.list_by_resolved_users(
            platform=surface.surface_type.value,
            resolved_user_ids=[recipient_user_id],
        )
        window_closed = False
        for external in identities:
            if not external.external_user_id:
                continue
            if not surface.matches_tenant(external.tenant_id):
                continue
            link = await self.links.get_latest_by_surface_and_external_user(
                surface_id=surface.id,
                external_user_id=external.external_user_id,
            )
            if link is None:
                continue
            if not reply_window_open(
                platform=surface.surface_type,
                last_inbound_at=link.inbound_activity_at,
            ):
                window_closed = True
                continue
            return (
                DeliveryChannel(
                    surface=surface,
                    external_user_id=external.external_user_id,
                    link=link,
                ),
                False,
            )
        return None, window_closed

    async def _email_channel(
        self, surface: AgentSurfaceEntity, recipient_user_id: UUID
    ) -> DeliveryChannel | None:
        address = await self.membership.get_user_email(recipient_user_id)
        if not address:
            return None
        return DeliveryChannel(surface=surface, email_address=address)

    async def reachable_channels(
        self,
        *,
        pod_id: UUID,
        recipients: dict[UUID, str | None],
        actor_agent_id: UUID | None = None,
    ) -> dict[UUID, list[str]]:
        """Which channels this agent could reach each of these people on now.

        The question an agent has to answer before it can sensibly *choose* a
        channel. Without it, picking one is a guess that costs a refused send to
        discover, and the guess would be wrong in exactly the case the argument
        exists for.

        Deliberately does not provision anything: a lookup that mints a mailbox
        as a side effect hands out an address nobody asked for. Where email is
        configured but the agent has no mailbox yet, email is still reported —
        the send path would create one, so reporting otherwise would be a
        forecast we know to be wrong. Emails come from the caller, which is
        already holding the member list, rather than a lookup per person.
        """
        all_surfaces, _ = await self.surfaces.list_by_pod(pod_id)
        active = [surface for surface in all_surfaces if surface.is_active]
        surfaces = surfaces_for_agent(active, actor_agent_id=actor_agent_id)

        reachable: dict[UUID, set[str]] = {user_id: set() for user_id in recipients}
        can_mail = any(can_cold_open(surface) for surface in surfaces) or (
            email_is_configured() and self.surface_provisioner is not None
        )
        if can_mail:
            for user_id, email in recipients.items():
                if email:
                    reachable[user_id].add(EMAIL_CHANNEL)

        for surface in surfaces:
            if not can_cold_open(surface):
                await self._add_chat_reach(surface, recipients, reachable)

        return {user_id: sorted(names) for user_id, names in reachable.items()}

    async def _add_chat_reach(
        self,
        surface: AgentSurfaceEntity,
        recipients: dict[UUID, str | None],
        reachable: dict[UUID, set[str]],
    ) -> None:
        """Add one chat surface's reach for everyone at once.

        Two queries per surface rather than two per surface *per person*: this
        runs on a tool the model calls before every message, and a pod of thirty
        would otherwise spend a hundred round trips answering a lookup.
        """
        identities = await self.external_users.list_by_resolved_users(
            platform=surface.surface_type.value,
            resolved_user_ids=list(recipients),
        )
        owners = {
            external.external_user_id: external.resolved_user_id
            for external in identities
            if external.external_user_id
            and external.resolved_user_id in reachable
            and surface.matches_tenant(external.tenant_id)
        }
        if not owners:
            return

        links = await self.links.list_latest_by_surface_and_external_users(
            surface_id=surface.id, external_user_ids=list(owners)
        )
        channel = channel_for_platform(surface.surface_type)
        for external_user_id, link in links.items():
            if reply_window_open(
                platform=surface.surface_type,
                last_inbound_at=link.inbound_activity_at,
            ):
                reachable[owners[external_user_id]].add(channel)


__all__ = ["NotificationChannelResolver", "SurfaceProvisioner", "can_cold_open"]
