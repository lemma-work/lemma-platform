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
    await adapter.send_message(
        credentials=transport.credentials,
        event=destination,
        message=message,
        metadata=metadata,
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
    await adapter.send_message(
        credentials=transport.credentials,
        event=event,
        message=CHECK_YOUR_DM_MESSAGE,
        metadata={**audience, "private_onboarding": True},
    )
    return True
