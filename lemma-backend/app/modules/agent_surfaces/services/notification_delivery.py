"""Choosing where to reach a person, and what that choice is allowed to assume.

Split out of :mod:`notification_service` because it is the part with real rules
in it and no side effects — which makes it the part worth testing directly.

The choice belongs to the **sending agent**, not the recipient. That is the
identity argument: a pod connects its own Telegram bot or Slack app, and a
message from "the agent you already talk to there" carries the trust that
bot has earned. Reaching someone through an unrelated pod surface because they
once set it as a personal default borrows trust the sender never had, and asks
the recipient to place a message from a bot they have no relationship with.

So the candidate set is the surfaces this agent serves, and the ordering is:

1. **The surface the run is on.** If the agent is answering on Telegram right
   now, that is where it reaches out from — same bot, same conversation, no
   explaining required.
2. **Its other chat surfaces**, freshest inbound first. "Freshest" means where
   they last spoke to *us* — not where we last spoke at them, which is why it
   reads ``last_inbound_at`` and not ``updated_at``.
3. **Its mailbox.** Email is the only family that can address someone who has
   never written to us first, so it is the fallback and the default for agents
   with no chat surface at all.
4. **Nothing** — a legitimate outcome, not an exception. The in-app inbox still
   has the notification. What must never happen is a silent nothing, so the
   reason travels with the result and reaches the API.

An agent that has a reason to prefer one channel says so, and then the ranking
above is not consulted at all: the request is honoured or it fails, never
quietly rerouted. See :func:`surfaces_on_channel` and
``NotificationChannelResolver._resolve_on_channel``.

The recipient's own ``UserPreferences.default_surfaces`` deliberately plays no
part here. It remains authoritative for *inbound* routing
(``ingress_service._select_surface``), where the question is genuinely "which of
our surfaces did this person mean to talk to".

First success wins. Fan-out across three apps is how a genuinely useful feature
comes to read as spam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)


# The name an agent uses for "reach them by mail", whichever provider carries it.
EMAIL_CHANNEL = "email"


def channel_for_platform(platform: SurfacePlatform) -> str:
    """The channel name an agent uses for a platform.

    Every mail platform collapses to ``email``. Which provider carries it is a
    deployment detail — an agent asked to choose between "gmail" and "resend" is
    being asked a question it has no way to answer, and whose answer it cannot
    act on. Chat platforms keep their own name, because that name is the thing
    the recipient is actually looking at.
    """
    capabilities = get_platform_capabilities(platform.value)
    if capabilities is not None and capabilities.is_email:
        return EMAIL_CHANNEL
    return platform.value.lower()


def channel_label(channel: str) -> str:
    """How to name a channel inside a sentence — "WhatsApp", not "whatsapp"."""
    if channel == EMAIL_CHANNEL:
        return EMAIL_CHANNEL
    capabilities = get_platform_capabilities(channel)
    return capabilities.display_name if capabilities is not None else channel


def surfaces_on_channel(
    surfaces: list[AgentSurfaceEntity], *, channel: str
) -> list[AgentSurfaceEntity]:
    """The subset of an agent's surfaces that sit on one channel.

    More than one is normal and is the reason this returns a list: an agent can
    hold two Slack workspaces or two bots on the same platform, and "send it on
    Slack" means any of them that can actually reach the person.
    """
    return [
        surface
        for surface in surfaces
        if channel_for_platform(surface.surface_type) == channel
    ]


def channels_of(candidates: list["DeliveryChannel"]) -> list[str]:
    """The distinct channels a resolved candidate set covers."""
    return sorted({channel_for_platform(channel.platform) for channel in candidates})


def channel_refused(channel: str, *, cause: str, alternatives: list[str]) -> str:
    """Why a requested channel could not carry this, and what could instead.

    Always ends by saying nothing was sent elsewhere. An agent that named a
    channel had a reason for naming it, and quietly using another one leaves it
    believing something happened that did not — which would make the argument
    advisory in all but name. Naming the alternatives is what makes the refusal
    actionable: send again on one of them, or tell the person why you did not.
    """
    if alternatives:
        reachable = ", ".join(channel_label(name) for name in alternatives)
        return f"{cause} Nothing was sent elsewhere; {reachable} would reach them."
    return (
        f"{cause} Nothing was sent elsewhere, and no other channel can reach "
        "them either."
    )


class UndeliverableReason:
    """Strings the API hands the UI so it can say something actionable.

    Deliberately not an enum on the wire: these are explanatory, they will grow,
    and a client that meets an unknown one should show it rather than crash.
    """

    NO_ACTIVE_SURFACE = "The pod has no active surface to reach anyone on."
    NEVER_INTERACTED = (
        "This person has not messaged any of the pod's chat surfaces, and chat "
        "bots cannot start a conversation. Ask them to message the bot once, or "
        "connect an email surface."
    )
    REPLY_WINDOW_CLOSED = (
        "The only channel we can reach this person on has closed its free-form "
        "reply window."
    )
    NO_EMAIL_ADDRESS = "No email address is on file for this person."
    NO_SURFACE_FOR_AGENT = (
        "This agent has no surface it can reach people on. Connect a chat "
        "surface for it, or configure email so it gets a mailbox."
    )
    # These two used to be indistinguishable from NO_ACTIVE_SURFACE, with the
    # real cause in a `.degraded` log nobody reads. A deployment whose mail
    # domain was unset reported "the pod has no active surface", which sent us
    # looking at pods and surfaces for a day. The reason a person is told should
    # be the reason it happened.
    EMAIL_NOT_CONFIGURED = (
        "Email is not set up for this deployment, so this agent has no mailbox "
        "to reach people from. Set RESEND_API_KEY and RESEND_INBOUND_DOMAIN, or "
        "connect a chat surface."
    )
    # "This agent" was read as the *recipient*, and sent someone looking for a
    # person who had been resolved as an agent. The agent here is the sender —
    # say whose mailbox failed, since that is what has to be fixed.
    MAILBOX_PROVISION_FAILED = (
        "The sending agent has no surface, and creating a mailbox for it failed."
    )
    COLD_OPEN_UNSUPPORTED = (
        "The pod's only mailbox surface cannot start a new email thread. Ask "
        "them to email the pod address once, or connect a chat surface."
    )
    # The four below are for `surface.send`, which names its surface and its
    # recipient rather than searching for a route -- so it can fail in ways the
    # resolver above never reaches. All four used to be the same 404, which said
    # "no reachable conversation" about a switched-off surface and about someone
    # who is not in the pod at all.
    SURFACE_NOT_ACTIVE = (
        "This surface is not active, so nothing can be sent from it. Reconnect "
        "it, or send from another of the pod's surfaces."
    )
    NOT_A_POD_MEMBER = (
        "This person is not a member of the pod this surface belongs to. Add "
        "them to the pod, or send to somebody who is already in it."
    )
    SEND_FAILED = (
        "The surface could not deliver the message. It is worth trying again; "
        "if it keeps failing, check the surface's connection."
    )
    #: A wiring fault in this process rather than anything the caller did, so it
    #: says what the caller can act on and the detail goes to the log.
    SEND_NOT_AVAILABLE = (
        "Sending on this surface is unavailable right now. Try again shortly."
    )

    @staticmethod
    def wrong_tenant_on(channel: str) -> str:
        return (
            f"This person's {channel_label(channel)} account is in a different "
            "workspace from the one this surface is connected to."
        )

    # The four below take the channel the agent asked for. They are methods
    # rather than constants because a refusal that does not name the channel
    # reads as though routing failed, when what happened is that a specific
    # request could not be met — a different thing to tell an agent, and a
    # different thing for it to do next.
    @staticmethod
    def no_surface_on(channel: str) -> str:
        return f"This agent has no {channel_label(channel)} surface to send from."

    @staticmethod
    def never_interacted_on(channel: str) -> str:
        return (
            f"They have not messaged this agent on {channel_label(channel)}, and "
            "chat bots cannot start a conversation."
        )

    @staticmethod
    def window_closed_on(channel: str) -> str:
        return f"Their {channel_label(channel)} reply window has closed."

    @staticmethod
    def no_address_on(channel: str) -> str:
        return f"No {channel_label(channel)} address is on file for this person."


@dataclass(frozen=True)
class DeliveryChannel:
    """A resolved way to reach one person: which surface, and on which thread.

    ``link`` is None for a cold-open email — there is no prior thread by
    definition, which is exactly what makes email the last resort that still
    works.
    """

    surface: AgentSurfaceEntity
    external_user_id: str | None = None
    link: AgentSurfaceConversationLink | None = None
    email_address: str | None = None

    @property
    def platform(self) -> SurfacePlatform:
        return self.surface.surface_type

    @property
    def is_cold_open(self) -> bool:
        return self.link is None


def reply_window_open(
    *,
    platform: SurfacePlatform,
    last_inbound_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Whether a free-form message is still allowed on this channel.

    Only WhatsApp has a window today. Getting this wrong in the permissive
    direction does not fail loudly — Meta accepts the call and drops the message
    — so the check happens here rather than being left to the adapter.

    A channel with no recorded inbound is treated as *closed* when the platform
    has a window: the window is measured from their last message, and we have no
    evidence there was one.
    """
    capabilities = get_platform_capabilities(platform.value)
    if capabilities is None or capabilities.reply_window_hours is None:
        return True
    if last_inbound_at is None:
        return False
    if last_inbound_at.tzinfo is None:
        last_inbound_at = last_inbound_at.replace(tzinfo=timezone.utc)
    elapsed = (now or datetime.now(timezone.utc)) - last_inbound_at
    return elapsed < timedelta(hours=capabilities.reply_window_hours)


def surfaces_for_agent(
    surfaces: list[AgentSurfaceEntity], *, actor_agent_id: UUID
) -> list[AgentSurfaceEntity]:
    """The surfaces this agent speaks through — its own, and only its own.

    A surface belongs to exactly one agent, so "which of these are mine" is the
    whole question. There used to be a fallback here: an agent with no surface
    of its own borrowed the pod's unowned ones. That went with the shared bot it
    was written for, and it was already half-broken — a borrowed surface sent
    under the borrower's name but handed the *reply* to whoever the surface
    actually belonged to, because the conversation was opened against the
    surface's agent rather than the sender's.

    An agent with no surface is not stuck: `NotificationChannelResolver.resolve`
    mints it a mailbox on the spot.

    ``actor_agent_id`` is always a real id now, the pod's own assistant
    included. Callers must not pass ``None`` for it — see `effective_agent_id`.
    """
    return [surface for surface in surfaces if surface.agent_id == actor_agent_id]


def sort_key_for_link(link: AgentSurfaceConversationLink) -> datetime:
    activity = link.inbound_activity_at
    return activity if activity.tzinfo else activity.replace(tzinfo=timezone.utc)


def rank_candidates(
    candidates: list[DeliveryChannel],
    *,
    origin_surface_id: UUID | None = None,
) -> list[DeliveryChannel]:
    """Order resolved channels best-first.

    Chat before email: email is the fallback that always works, not the one
    people are watching. Within chat, the surface the run is *on* wins — an
    agent answering on Telegram should reach out from that same bot rather than
    from another of its own surfaces — and among the rest, the freshest inbound.
    """

    def key(channel: DeliveryChannel) -> tuple[int, int, float]:
        capabilities = get_platform_capabilities(channel.platform.value)
        is_email = bool(capabilities and capabilities.is_email)
        here = origin_surface_id is not None and channel.surface.id == origin_surface_id
        freshness = sort_key_for_link(channel.link).timestamp() if channel.link else 0.0
        # Tuple is compared ascending, so negate what should come first.
        return (int(is_email), 0 if here else 1, -freshness)

    return sorted(candidates, key=key)
