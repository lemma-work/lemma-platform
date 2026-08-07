"""Choosing where to reach a person, and what that choice is allowed to assume.

Split out of :mod:`notification_service` because it is the part with real rules
in it and no side effects — which makes it the part worth testing directly.

The ordering below is the whole policy, and each step earns its place:

1. **A surface they chose.** ``UserPreferences.default_surfaces`` is already
   authoritative for *inbound* routing (``ingress_service._select_surface``).
   Using the same precedence for egress means a person is reached where they
   already talk to us, rather than somewhere we merely observed them once.
2. **A surface we have seen them on**, freshest inbound first. "Freshest" means
   where they last spoke to *us* — not where we last spoke at them, which is why
   it reads ``last_inbound_at`` and not ``updated_at``.
3. **Email**, which is the only family of platforms that can address someone who
   has never written to us first.
4. **Nothing** — and that is a legitimate outcome, not an exception. The in-app
   inbox still has the notification. What must never happen is a silent nothing,
   so the reason travels with the result and reaches the API.

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


def sort_key_for_link(link: AgentSurfaceConversationLink) -> datetime:
    activity = link.inbound_activity_at
    return activity if activity.tzinfo else activity.replace(tzinfo=timezone.utc)


def rank_candidates(
    candidates: list[DeliveryChannel],
    *,
    preferred_surface_ids: set[UUID],
) -> list[DeliveryChannel]:
    """Order resolved channels best-first.

    Chat before email: email is the fallback that always works, not the one
    people are watching. Within each, a surface the person explicitly chose
    beats one we merely observed, and among the observed, the freshest inbound
    wins.
    """

    def key(channel: DeliveryChannel) -> tuple[int, int, float]:
        capabilities = get_platform_capabilities(channel.platform.value)
        is_email = bool(capabilities and capabilities.is_email)
        chosen = channel.surface.id in preferred_surface_ids
        freshness = (
            sort_key_for_link(channel.link).timestamp() if channel.link else 0.0
        )
        # Tuple is compared ascending, so negate what should come first.
        return (int(is_email), 0 if chosen else 1, -freshness)

    return sorted(candidates, key=key)
