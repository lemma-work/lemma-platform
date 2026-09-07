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
from dataclasses import dataclass, replace

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.infrastructure.adapters.redis_onboarding_store import (
    PendingOnboarding,
    SurfaceOnboardingStore,
    code_attempts_exhausted,
    turns_exhausted,
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


@dataclass(frozen=True, slots=True)
class _Step:
    """What to say, and what the exchange should remember afterwards."""

    turn: OnboardingTurn | None
    #: ``None`` clears the record: the exchange is finished, one way or another.
    pending: PendingOnboarding | None


async def advance_onboarding(
    *,
    store: SurfaceOnboardingStore,
    event: ParsedInboundSurfaceEvent,
    send_code_email,
) -> OnboardingTurn | None:
    """Take the next step with this sender, or None if there is nothing to say.

    Every reply costs a turn, and the counting happens here rather than in the
    branches. Each branch says what it wants remembered and this writes it once
    -- so a branch that replies without persisting anything cannot quietly
    escape the budget, which is exactly how "that doesn't look like an email"
    would have become an unbounded reply generator.

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
        step = _Step(
            turn=OnboardingTurn(
                message=(
                    "I don't know you yet. What's your email address? "
                    "I'll send a code to check it's yours."
                )
            ),
            pending=PendingOnboarding(step="awaiting_email"),
        )
    elif turns_exhausted(pending.turns):
        # Silence rather than another line. Each reply is an outbound message on
        # a number every pod shares, and an exchange this long is somebody
        # typing at the number rather than answering it. The record is left to
        # expire on its own, so coming back later is a fresh start rather than
        # an instant refusal.
        logger.info("agent_surfaces.onboarding.turns_exhausted", platform=platform)
        return None
    elif pending.step == "awaiting_email":
        step = await _take_email(
            platform=platform,
            event=event,
            send_code_email=send_code_email,
            code_hash=store.hash_code,
        )
    else:
        step = await _take_code(event=event, pending=pending, code_hash=store.hash_code)

    if step.pending is None:
        await store.clear(platform=platform, sender_external_user_id=sender)
    else:
        await store.put(
            platform=platform,
            sender_external_user_id=sender,
            pending=replace(step.pending, turns=(pending.turns if pending else 0) + 1),
        )
    return step.turn


async def _take_email(*, platform: str, event, send_code_email, code_hash) -> _Step:
    email = find_email(event.message_text)
    if not email:
        return _Step(
            turn=OnboardingTurn(
                message=(
                    "That doesn't look like an email address. "
                    "Send just the address and I'll check it's yours."
                )
            ),
            pending=PendingOnboarding(step="awaiting_email"),
        )

    code = mint_code()
    sent = await send_code_email(
        to_email=email, code=code, surface_label=surface_label(platform)
    )
    if not sent:
        # Never claim a code is on its way when it is not: somebody would sit
        # watching an inbox that is never going to show anything.
        logger.warning("agent_surfaces.onboarding.code_email_failed", platform=platform)
        return _Step(
            turn=OnboardingTurn(
                message=(
                    f"I couldn't send a code to {email}. "
                    "Check the address and send it again."
                )
            ),
            pending=PendingOnboarding(step="awaiting_email"),
        )

    return _Step(
        turn=OnboardingTurn(
            message=f"Sent a code to {email}. Send it back here and I'll know it's you."
        ),
        pending=PendingOnboarding(
            step="awaiting_code", email=email, code_hash=code_hash(code)
        ),
    )


async def _take_code(*, event, pending: PendingOnboarding, code_hash) -> _Step:
    code = looks_like_code(event.message_text)
    if code and pending.code_hash and code_hash(code) == pending.code_hash:
        return _Step(
            turn=OnboardingTurn(
                message="That's you. Setting you up now.", proven_email=pending.email
            ),
            pending=None,
        )

    # A second address instead of a code: they mistyped the first one. Start
    # that half over rather than making them fail three codes first.
    resent = find_email(event.message_text)
    if resent and resent != pending.email:
        return _Step(
            turn=OnboardingTurn(
                message=f"Using {resent} instead — send that address again to confirm."
            ),
            pending=PendingOnboarding(step="awaiting_email"),
        )

    # Nothing code-shaped at all: they said something else, which is not a wrong
    # guess. Burning a try for "please" would spend somebody's three on chatter.
    if code is None:
        return _Step(
            turn=OnboardingTurn(
                message="Send me the code from that email and I'll know it's you."
            ),
            pending=pending,
        )

    attempts = pending.attempts + 1
    if code_attempts_exhausted(attempts):
        return _Step(
            turn=OnboardingTurn(
                message=(
                    "That code didn't match, and I've run out of tries. "
                    "Say hello again to start over."
                )
            ),
            pending=None,
        )

    return _Step(
        turn=OnboardingTurn(message="That code didn't match. Try again."),
        pending=replace(pending, attempts=attempts),
    )
