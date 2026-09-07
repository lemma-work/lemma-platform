"""The conversation that turns a stranger on a chat surface into somebody we know.

Three or four messages, and every one of them is a reply to something the person
just sent -- which is what keeps the whole exchange inside WhatsApp's free-form
window without anyone having to reason about the window.

    them  "can you check this invoice?"
    us    "I don't know you yet. What's your work email?"
    them  "ada@acme.com"
    us    "Sent a code to ada@acme.com. Send it back here."
    them  "H7K2QM"
    us    "You're in."

Email is the exception: a sender the receiving mail service vouched for has
already proved the address, so there is nothing to ask and nothing to send. That
is not a shortcut bolted on, it is the same rule -- prove the address -- being
satisfied by SPF and DKIM instead of by a code.

What this deliberately does not do is answer the invoice question. Holding the
first message and running it once the person is in is the obvious next thing and
a genuinely separate one: it needs the message, its attachments and its thread
stored beside the pending signup, and the run has to be held until access
exists.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.infrastructure.adapters.redis_onboarding_store import (
    PendingOnboarding,
    SurfaceOnboardingStore,
    code_attempts_exhausted,
)

logger = get_logger(__name__)

# Deliberately not RFC 5322. This decides whether a chat message is somebody
# offering an address, not whether an address is deliverable -- the code proves
# that, and a stricter pattern here only rejects real people with odd addresses.
_EMAIL = re.compile(r"[^\s@]+@[^\s@.]+\.[^\s@]+")

# Digits and letters that cannot be misread aloud or in a phone font: no O/0,
# no I/1. Somebody is going to read this off one screen and type it on another.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 6

_PLATFORM_LABELS = {
    "WHATSAPP": "WhatsApp",
    "TELEGRAM": "Telegram",
    "SLACK": "Slack",
    "TEAMS": "Teams",
}


@dataclass(frozen=True, slots=True)
class OnboardingTurn:
    """What to say back, and whether the exchange finished."""

    message: str
    #: Set once the address is proven and the caller should onboard it.
    proven_email: str | None = None


def surface_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(str(platform).upper(), str(platform).title())


def mint_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def find_email(message_text: str | None) -> str | None:
    """The address somebody typed, or None if they typed something else."""
    match = _EMAIL.search(str(message_text or ""))
    return match.group(0).strip().strip(".,;:<>").lower() if match else None


def looks_like_code(message_text: str | None) -> str | None:
    """The code somebody sent back, ignoring whatever they wrapped it in.

    Per word, not per message: people reply "my code is H7K2QM" and mean the
    code, and filtering the whole string instead would splice the sentence into
    it. The last match wins, because a code is what somebody types at the end.
    """
    found = None
    for token in re.split(r"[^A-Za-z0-9]+", str(message_text or "").upper()):
        if len(token) == _CODE_LENGTH and all(
            character in _CODE_ALPHABET for character in token
        ):
            found = token
    return found


async def advance_onboarding(
    *,
    store: SurfaceOnboardingStore,
    event: ParsedInboundSurfaceEvent,
    send_code_email,
) -> OnboardingTurn | None:
    """Take the next step with this sender, or None if there is nothing to say.

    ``send_code_email`` is passed in rather than imported so this stays testable
    without a mail server, and so the one place that decides an address is worth
    mailing stays visible.
    """
    platform = str(event.platform).upper()
    sender = str(event.sender_external_user_id or "").strip()
    if not sender:
        return None

    pending = await store.get(platform=platform, sender_external_user_id=sender)

    if pending is None:
        await store.put(
            platform=platform,
            sender_external_user_id=sender,
            pending=PendingOnboarding(step="awaiting_email"),
        )
        return OnboardingTurn(
            message=(
                "I don't know you yet. What's your email address? "
                "I'll send a code to check it's yours."
            )
        )

    if pending.step == "awaiting_email":
        return await _take_email(
            store=store,
            platform=platform,
            sender=sender,
            event=event,
            send_code_email=send_code_email,
        )

    return await _take_code(
        store=store, platform=platform, sender=sender, event=event, pending=pending
    )


async def _take_email(
    *, store, platform: str, sender: str, event, send_code_email
) -> OnboardingTurn:
    email = find_email(event.message_text)
    if not email:
        return OnboardingTurn(
            message=(
                "That doesn't look like an email address. "
                "Send just the address and I'll check it's yours."
            )
        )

    code = mint_code()
    sent = await send_code_email(
        to_email=email, code=code, surface_label=surface_label(platform)
    )
    if not sent:
        # Never claim a code is on its way when it is not: somebody would sit
        # watching an inbox that is never going to show anything.
        logger.warning("agent_surfaces.onboarding.code_email_failed", platform=platform)
        return OnboardingTurn(
            message=(
                f"I couldn't send a code to {email}. "
                "Check the address and send it again."
            )
        )

    await store.put(
        platform=platform,
        sender_external_user_id=sender,
        pending=PendingOnboarding(
            step="awaiting_code", email=email, code_hash=store.hash_code(code)
        ),
    )
    return OnboardingTurn(
        message=(f"Sent a code to {email}. Send it back here and I'll know it's you.")
    )


async def _take_code(
    *, store, platform: str, sender: str, event, pending: PendingOnboarding
) -> OnboardingTurn:
    code = looks_like_code(event.message_text)
    if code and pending.code_hash and store.hash_code(code) == pending.code_hash:
        await store.clear(platform=platform, sender_external_user_id=sender)
        return OnboardingTurn(
            message="That's you. Setting you up now.", proven_email=pending.email
        )

    # A second address instead of a code: they mistyped the first one. Start
    # that half over rather than making them fail three codes first.
    resent = find_email(event.message_text)
    if resent and resent != pending.email:
        await store.put(
            platform=platform,
            sender_external_user_id=sender,
            pending=PendingOnboarding(step="awaiting_email"),
        )
        return OnboardingTurn(
            message=f"Using {resent} instead — send that address again to confirm."
        )

    # Nothing code-shaped at all: they said something else, which is not a wrong
    # guess. Burning a try for "please" would spend somebody's three on chatter.
    if code is None:
        return OnboardingTurn(
            message="Send me the code from that email and I'll know it's you."
        )

    attempts = pending.attempts + 1
    if code_attempts_exhausted(attempts):
        await store.clear(platform=platform, sender_external_user_id=sender)
        return OnboardingTurn(
            message=(
                "That code didn't match, and I've run out of tries. "
                "Say hello again to start over."
            )
        )

    await store.put(
        platform=platform,
        sender_external_user_id=sender,
        pending=PendingOnboarding(
            step="awaiting_code",
            email=pending.email,
            code_hash=pending.code_hash,
            attempts=attempts,
        ),
    )
    return OnboardingTurn(message="That code didn't match. Try again.")
