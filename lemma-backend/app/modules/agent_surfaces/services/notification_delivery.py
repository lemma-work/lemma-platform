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

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)

logger = get_logger(__name__)


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
    COLD_OPEN_UNSUPPORTED = (
        "The pod's only mailbox surface cannot start a new email thread. Ask "
        "them to email the pod address once, or connect a chat surface."
    )


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
    surfaces: list[AgentSurfaceEntity], *, actor_agent_id: UUID | None
) -> list[AgentSurfaceEntity]:
    """The surfaces this agent speaks through.

    Matched on ``surface.agent_id`` only. Channel routes and the Slack per-user
    DM choice deliberately play no part: both are keyed on things a notification
    does not have — a channel id, and the recipient's own external id — and
    honouring them would make "who can this agent reach" depend on where someone
    last happened to speak to it.

    ``actor_agent_id is None`` means the pod assistant, whose surfaces are the
    ones with no agent of their own. That is a *deliberate* absence rather than
    "unset", which is also why it must not be read as "every surface": routing
    the pod assistant through a named agent's bot would put the wrong name on
    the message.

    A caller with no agent at all — a workflow form assignment, or the
    notifications API — passes ``None`` and gets the same set, which is the pod's
    own surfaces. That is the right answer for them too: nobody's agent identity
    is being borrowed.
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
        freshness = (
            sort_key_for_link(channel.link).timestamp() if channel.link else 0.0
        )
        # Tuple is compared ascending, so negate what should come first.
        return (int(is_email), 0 if here else 1, -freshness)

    return sorted(candidates, key=key)
