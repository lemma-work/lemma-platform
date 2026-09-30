"""How signup talks to the person, and where it is allowed to talk at all.

Pulled out of `ChatOnboardingCoordinator` because it is a separate question from
the state machine. The machine decides *what* is true next; this decides what
the person sees and which surface can carry it -- a private DM, a Slack
ephemeral only the sender reads, or nothing, because a public notice about
somebody's half-finished account is worse than silence.

Functions taking the adapters explicitly rather than an object of their own:
they need the registry and a unit of work and nothing else, so an object here
would hold two references and call back into neither. The same shape as
`surface_bulk_teardown` and for the same reason.
"""

from __future__ import annotations

from pydantic import JsonValue

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    private_reply_metadata,
)
from app.modules.agent_surfaces.services.onboarding_inputs import (
    native_prompt_metadata,
)
from app.modules.agent_surfaces.services.plain_reply import reply_text
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)

#: Said in the room, not in the room's earshot -- see `room_notice`.
CHECK_YOUR_DM_MESSAGE = (
    "I've sent you a direct message to finish setting up your Lemma account. "
    "Answer me there and I'll pick this up."
)


def telegram_keyboard(step: str | None) -> dict[str, JsonValue]:
    """The buttons that belong to the step this message is actually asking about.

    One hard-coded Resend/Change email/Cancel keyboard used to ride on every
    Telegram reply. It was right for exactly one step. Under "Which workspace
    should this chat use?" it offered to resend a code that had already been
    used; under "Setup cancelled." it offered to resend a code for a signup that
    no longer existed; and pressing either sent text the step had no branch for,
    so the buttons the product drew were the ones it could not answer.

    The no-keyboard cases clear rather than omit: Telegram leaves the last
    custom keyboard on screen until it is told otherwise, so saying nothing
    leaves `Resend` sitting under a terminal message.
    """
    # `JsonValue` rather than the concrete nesting: it is a recursive union
    # and `dict` is invariant, so `list[list[dict[str, JsonValue]]]` is not
    # assignable to it however obviously JSON-shaped the value is.
    rows: JsonValue
    if step == OnboardingStep.AWAITING_PHONE:
        rows = [[{"text": "Share my contact", "request_contact": True}]]
    elif step == OnboardingStep.AWAITING_EMAIL:
        # No code has been sent yet, so there is nothing to resend and no other
        # address to change to.
        rows = [[{"text": "Cancel"}]]
    elif step == OnboardingStep.AWAITING_CODE:
        rows = [[{"text": "Resend"}, {"text": "Change email"}], [{"text": "Cancel"}]]
    else:
        return {"remove_keyboard": True}
    keyboard: dict[str, JsonValue] = {
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    return keyboard


async def say_privately(
    adapters: SurfacePlatformAdapterRegistry,
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    destination: ParsedInboundSurfaceEvent,
    message: str,
    *,
    step: str | None = None,
) -> None:
    """Say one thing privately, with whatever the step gives them to press.

    ``step`` is the step this message is *asking about*, which is not always the
    step the row is on -- the email handler writes AWAITING_CODE and then asks
    for the code, and a rolled-back write means the row may be on neither by the
    time this returns. ``None`` means there is nothing to answer: a terminal
    message, or a question typed rather than pressed.
    """
    if not destination.is_dm:
        raise ValueError("Onboarding replies require a private destination")
    adapter = adapters.get(destination.platform)
    assert adapter is not None
    metadata = await native_prompt_metadata(
        uows, binding_key=transport.binding_key, platform=destination.platform
    )
    metadata["private_onboarding"] = True
    if destination.platform == SurfacePlatform.TELEGRAM:
        metadata["reply_markup"] = telegram_keyboard(step)
    await reply_text(
        adapter=adapter,
        credentials=transport.credentials,
        event=destination,
        message=message,
        metadata=metadata,
    )


#: What "done" sounds like. Both completion paths used to say nothing at all:
#: the only sign of success was the replayed request coming back answered, and
#: `replay_onboarding` has several raises that dead-letter after their retries
#: -- so a failure there left somebody who had just proved their email with no
#: acknowledgement and no answer, and nothing to tell that apart from being
#: ignored. The confirmation is cheap and it is sent before the replay, so the
#: flow is never silent even when the replay is.
READY_MESSAGE = "You're all set. Picking up your message now."


def ready_message(invited_pod_name: str | None = None) -> str:
    """The confirmation, plus the one thing it never said.

    An account made here has a single login method and it is passwordless.
    Everything on the web that a person would reach for first -- a password,
    "forgot password", Continue with Google -- is refused for exactly that
    reason, and the only door that opens is a code sent to this same address.
    Nobody was ever told that, so signing up on WhatsApp and then trying the
    website looked like an account that did not work.

    Composed rather than folded into `READY_MESSAGE` so the constant stays the
    literal confirmation sentence that the recovery test matches on, and so the
    URL is read when the message is sent rather than when the module is
    imported.
    """
    joined = f"You were invited to {invited_pod_name}, and you're in it now.\n\n"
    return (
        f"{joined if invited_pod_name else ''}{READY_MESSAGE}\n\n"
        f"To use Lemma on the web, go to {settings.frontend_url.rstrip('/')}/login "
        "and enter this same email address. We'll send you a sign-in code -- "
        "there's no password to remember."
    )


#: What somebody is told when signup could only go on to an email code this
#: installation cannot send (Lemma Desktop, unless its owner set up email).
#: Asking for an address that no code will ever reach is worse than saying so;
#: the way in that does work is a profile number the bot can match.
NO_EMAIL_SIGNUP_MESSAGE = (
    "I don't recognise this number. If you have a Lemma account here, add this "
    "number to your profile (Settings → Profile) and share your contact again. "
    "Otherwise ask the person who runs this Lemma to invite you."
)


def initial_prompt_for(step: str) -> str:
    """The first thing said in the private chat, once there is one."""
    if step == OnboardingStep.AWAITING_PHONE:
        return "Share your own contact using the button below."
    return "What's your email address? I'll send a code to verify it."


async def room_notice(
    adapters: SurfacePlatformAdapterRegistry, transport: OnboardingTransport
) -> bool:
    """Answer the room, without making the room read somebody's signup.

    Returns whether anything was said. Handled-with-no-context is the right
    *routing* answer for a channel message during a pending signup -- nothing
    may reach an agent while nobody has proved who sent it -- but it was also
    the whole answer, so mentioning the bot in a channel produced absolute
    silence for the full TTL. The prompt that would have explained it is waiting
    in a DM the person may never have noticed, and from inside the channel there
    is nothing to distinguish that from a bot that is simply broken.

    `private_reply_metadata` owns where this can be said at all: on Slack an
    ephemeral only the sender sees, and nowhere else. Where a platform cannot
    answer one person inside a room, silence still stands.
    """
    event = transport.event
    audience = private_reply_metadata(event)
    if audience is None:
        return False
    adapter = adapters.get(event.platform)
    assert adapter is not None
    await reply_text(
        adapter=adapter,
        credentials=transport.credentials,
        event=event,
        message=CHECK_YOUR_DM_MESSAGE,
        metadata={**audience, "private_onboarding": True},
    )
    return True
