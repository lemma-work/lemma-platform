"""Where the onboarding conversation meets the inbound path.

`surface_onboarding` knows what to say next and nothing about surfaces;
`chat_signup` knows how to make an account and nothing about chat. This is the
seam: it decides whether this sender is one we onboard at all, drives the
exchange, and calls identity once the address is proven.

Separate from `surface_inbound` because that file already carries the whole
routing decision, and separate from `surface_onboarding` so the state machine
stays testable without a database.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.infrastructure.adapters.redis_onboarding_store import (
    get_surface_onboarding_store,
)
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    private_reply_metadata,
)
from app.modules.agent_surfaces.services.surface_onboarding import (
    advance_onboarding,
    surface_label,
)
from app.modules.pod.contracts.agent_access import pod_organization_id

logger = get_logger(__name__)

# Where a sender arrives holding nothing but a phone or a handle, so the address
# has to be asked for and proved. Slack and Teams are absent on purpose: the
# workspace already vouched for an email, and asking somebody inside Slack to go
# and read a code would be the most obviously broken moment in the product.
_ASKS_FOR_AN_EMAIL = frozenset(
    {SurfacePlatform.WHATSAPP.value, SurfacePlatform.TELEGRAM.value}
)

# Where the address arrives already vouched for by the workspace an organization
# installed Lemma into, so there is nothing to ask and nothing to send.
_WORKSPACE_VOUCHES = frozenset(
    {SurfacePlatform.SLACK.value, SurfacePlatform.TEAMS.value}
)


def _vouched_email_sender(parsed: ParsedInboundSurfaceEvent) -> str | None:
    """The address of a mail sender the receiving service vouched for.

    No code: SPF and DKIM already answered the question a code would ask, and
    sending one would be asking somebody to prove what they just proved.
    """
    if not parsed.platform.is_email:
        return None
    if parsed.sender_authentication != EmailAuthenticationVerdict.PASS:
        return None
    return str(parsed.sender_email or "").strip().lower() or None


async def onboarding_reply(
    uow,
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
    onboard_sender,
    send_code_email,
) -> SurfaceReplyContext | None:
    """Take this sender one step further in, or None if that is not what this is.

    ``onboard_sender`` and ``send_code_email`` are passed rather than imported so
    the two crossings out of this module -- making an account and sending mail --
    are visible at the call site instead of buried here.
    """
    platform = str(parsed.platform).upper()
    audience = private_reply_metadata(parsed)
    if audience is None:
        return None

    proven = _vouched_email_sender(parsed)
    turn = None
    if proven is None and platform in _WORKSPACE_VOUCHES:
        # The install is the proof. Asking somebody standing inside their own
        # company's Slack to go and read a code would be the most obviously
        # broken moment in the product.
        proven = str(parsed.sender_email or "").strip().lower() or None
        if proven is None:
            # Teams can decline to give an email at all, and its adapter falls
            # back to a `userPrincipalName`, which is a sign-in name rather than
            # a mailbox. Without one there is nothing to onboard against.
            return None
    if proven is None:
        if platform not in _ASKS_FOR_AN_EMAIL:
            return None
        turn = await advance_onboarding(
            store=get_surface_onboarding_store(),
            event=parsed,
            send_code_email=send_code_email,
        )
        if turn is None:
            return None
        proven = turn.proven_email

    if proven is None:
        return _reply(surface, parsed, agent_display_name, turn.message, audience)

    # The organization whose surface they messaged. Somebody standing inside a
    # company's own Slack is not a candidate for a private organization of one,
    # so that organization is offered before the domain match -- and its own
    # join policy still decides, because a reachable surface is not an open
    # organization.
    arrived_through = await pod_organization_id(uow, surface.pod_id)

    onboarding = await onboard_sender(
        uow,
        email=proven,
        arrived_through_organization_id=arrived_through,
        full_name=parsed.sender_display_name,
        mobile_number=(
            parsed.sender_phone if platform == SurfacePlatform.WHATSAPP.value else None
        ),
        telegram_username=(
            parsed.metadata.get("sender_username")
            if platform == SurfacePlatform.TELEGRAM.value
            else None
        ),
    )
    logger.info(
        "agent_surfaces.onboarding.completed",
        platform=platform,
        account_created=onboarding.account_created,
    )
    return _reply(
        surface,
        parsed,
        agent_display_name,
        _welcome(onboarding, surface, agent_display_name, surface_label(platform)),
        audience,
    )


def _welcome(
    onboarding, surface: AgentSurfaceEntity, agent_display_name: str, label: str
) -> str:
    """What to say once somebody is actually in.

    Names what happened rather than only greeting: a person who has just been
    given an organization should be told they have one, and a person who was
    linked to an account they already had should not be told they were given
    anything.
    """
    if onboarding.workspace.entry == "surface_join":
        # In the organization, deliberately not in the pod. Being present in a
        # Slack channel is not access to the pod behind it -- that is a product
        # rule, not an oversight -- so the last step is theirs to ask for.
        base = settings.frontend_url.rstrip("/")
        return (
            f"You're in — I've added you to your team's Lemma organization. "
            f"{agent_display_name} works out of a workspace you're not in yet; "
            f"ask for access here and someone can let you in: "
            f"{base}/pod/{surface.pod_id}"
        )
    if not onboarding.account_created:
        return (
            f"Got it — that's your Lemma account. This {label} account reaches "
            f"{agent_display_name} as you from now on."
        )
    if onboarding.workspace.entry == "domain_join":
        return (
            "You're in — your team already had a Lemma workspace, so I've put "
            "you in it. Say what you need."
        )
    return (
        "You're set up: a Lemma workspace of your own, with an agent in it. "
        "Say what you need."
    )


def _reply(
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    agent_display_name: str,
    message: str,
    audience: dict[str, str],
) -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform(surface.surface_type),
        surface_id=surface.id,
        surface_account_id=surface.account_id,
        surface_config=surface.config,
        agent_display_name=agent_display_name,
        reply_kind="identity_link",
        reply_message=message,
        reply_metadata=dict(audience),
        event=parsed,
    )
