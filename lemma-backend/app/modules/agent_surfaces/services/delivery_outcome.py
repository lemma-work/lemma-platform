"""What became of one send of a channel delivery, before it is recorded.

Four ends, one per status a delivery row can settle in after a try:

* ``SENT`` -- with the platform's id for the message, when it named one;
* ``SKIPPED`` -- not sent, on purpose (a contact's rules, nowhere to send,
  nothing left to send once reasoning was stripped);
* ``FAILED`` -- did not go, and is worth trying again after a backoff: the
  platform's failure was transient and nothing of the message arrived, or
  nothing reached the platform at all;
* ``DEAD`` -- did not go, and trying again would repeat what already arrived
  (part of a long message, or a send that may have landed) or meet the same
  refusal.

Every end but ``SENT`` carries a ``DeliveryReasonCode`` -- what code reads --
beside the sentence a member does. A send is retried only when a retry cannot
be the duplicate the person sees. Saying something twice is worse than
saying, visibly, that it may not have arrived -- except on a platform that
dedupes a resend by its key (``RESENDS_SAFELY``), where an ambiguous send is
simply sent again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.platforms.delivery import SendOutcome
from app.modules.channels.contracts import DeliveryReasonCode as Code

#: Platforms that honour the delivery's idempotency key: a resend under it is
#: the first send's result, never a second message (Resend keeps a key for a
#: day, longer than a delivery is retried).
RESENDS_SAFELY = frozenset({SurfacePlatform.RESEND.value})


class Result(StrEnum):
    SENT = "SENT"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DEAD = "DEAD"


@dataclass(frozen=True, slots=True)
class Outcome:
    result: Result
    platform: str | None = None
    reason: str | None = None
    code: Code | None = None
    external_message_id: str | None = None
    #: How long the platform asked to be left alone, in seconds: a retry waits
    #: at least this long.
    retry_after: float | None = None
    #: What the platform last answered, when it said (an HTTP status).
    provider_status: str | None = None
    #: A push the hourly budget held (``FOLDED``): how long before its lines
    #: may go again, with the member's next push from the channel.
    hold_for: timedelta | None = None

    @property
    def sent(self) -> bool:
        return self.result is Result.SENT

    @classmethod
    def delivered(
        cls,
        platform: str | None,
        *,
        external_message_id: str | None = None,
        reason: str | None = None,
    ) -> Outcome:
        return cls(
            Result.SENT,
            platform=platform,
            external_message_id=external_message_id,
            reason=reason,
        )

    @classmethod
    def skipped(
        cls, reason: str, *, code: Code, platform: str | None = None
    ) -> Outcome:
        return cls(Result.SKIPPED, platform=platform, reason=reason, code=code)

    @classmethod
    def folded(cls, reason: str, *, hold_for: timedelta) -> Outcome:
        return cls(Result.SKIPPED, reason=reason, code=Code.FOLDED, hold_for=hold_for)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        code: Code = Code.TRANSIENT,
        platform: str | None = None,
        retry_after: float | None = None,
        provider_status: str | None = None,
    ) -> Outcome:
        return cls(
            Result.FAILED,
            platform=platform,
            reason=reason,
            code=code,
            retry_after=retry_after,
            provider_status=provider_status,
        )

    @classmethod
    def dead(
        cls,
        reason: str,
        *,
        code: Code,
        platform: str | None = None,
        provider_status: str | None = None,
    ) -> Outcome:
        return cls(
            Result.DEAD,
            platform=platform,
            reason=reason,
            code=code,
            provider_status=provider_status,
        )


def may_have_arrived(platform: str) -> str:
    """What a member reads on a send whose platform never answered."""
    return (
        f"{platform} did not answer after it was sent, so it may or may not "
        "have arrived. It was not sent again in case it did."
    )


def of_send(
    receipt: DeliveryReceipt | None,
    sends: SendOutcome,
    *,
    platform: str,
    raised: BaseException | None = None,
    nothing_sent: str = "There was nothing to send there.",
) -> Outcome:
    """A platform send's outcome: its receipt, and what its tries ran into.

    With no platform called at all, ``raised`` is what stopped it first -- our
    own database, say -- and a retry may well get further; without one,
    nothing was there to send to (no thread, or a message that was nothing
    once reasoning was stripped), which ``nothing_sent`` says, and a retry
    would find the same.
    """
    if receipt is not None:
        return Outcome.delivered(
            platform, external_message_id=receipt.external_message_id
        )
    status = sends.provider_status
    if sends.partial:
        return Outcome.dead(
            f"Only part of it reached {platform}; it was not sent again, so as "
            "not to repeat that part.",
            code=Code.PARTIAL,
            platform=platform,
            provider_status=status,
        )
    if sends.gave_up_transient:
        return Outcome.failed(
            f"{platform} could not take it just now. It will be tried again.",
            platform=platform,
            retry_after=sends.retry_after,
            provider_status=status,
        )
    if not sends.tried:
        if raised is not None:
            return Outcome.failed(
                f"It could not be got ready to send ({type(raised).__name__}). "
                "It will be tried again.",
                platform=platform,
            )
        return Outcome.skipped(
            nothing_sent, code=Code.NOTHING_TO_SEND, platform=platform
        )
    if sends.ambiguous:
        if platform in RESENDS_SAFELY:
            return Outcome.failed(
                f"{platform} did not answer. It will be sent again under the "
                "same key, which it does not deliver twice.",
                platform=platform,
            )
        return Outcome.dead(
            may_have_arrived(platform), code=Code.MAY_HAVE_ARRIVED, platform=platform
        )
    return Outcome.dead(
        f"{platform} refused it; sending it again would meet the same refusal.",
        code=Code.REFUSED,
        platform=platform,
        provider_status=status,
    )


#: The platform's answer that refuses the bot itself, not the message. Not
#: 403: Telegram says that for one person who blocked the bot.
_REFUSED_CREDENTIALS = frozenset({"401"})


def refused_credentials(outcome: Outcome) -> bool:
    """Whether the platform gave up on the bot rather than the message: its
    credentials were refused, so every send of the bot meets the same answer."""
    return (
        outcome.result is Result.DEAD
        and outcome.code is Code.REFUSED
        and outcome.provider_status in _REFUSED_CREDENTIALS
    )


def timed_out(sends: SendOutcome, *, timeout: float) -> Outcome:
    """A send cut off by ``SEND_TIMEOUT``: tried again only when the platform
    was not called yet, or dedupes a resend; otherwise it may be there."""
    platform = sends.platform
    if not sends.tried or (platform is not None and platform in RESENDS_SAFELY):
        return Outcome.failed(
            f"No answer from the platform within {timeout:g} s. "
            "It will be tried again.",
            code=Code.TIMED_OUT,
            platform=platform,
        )
    return Outcome.dead(
        may_have_arrived(platform or "The platform"),
        code=Code.MAY_HAVE_ARRIVED,
        platform=platform,
    )


def raised_before_recording(sends: SendOutcome, exc: BaseException) -> Outcome:
    """A send that raised something no send path expects (a bug, or our own
    database going away mid-send): retried when no platform was called yet,
    else given up as possibly there."""
    if not sends.tried:
        return Outcome.failed(
            f"It could not be got ready to send ({type(exc).__name__}). "
            "It will be tried again.",
            platform=sends.platform,
        )
    return Outcome.dead(
        may_have_arrived(sends.platform or "The platform"),
        code=Code.MAY_HAVE_ARRIVED,
        platform=sends.platform,
    )


__all__ = [
    "RESENDS_SAFELY",
    "Outcome",
    "Result",
    "may_have_arrived",
    "of_send",
    "raised_before_recording",
    "refused_credentials",
    "timed_out",
]
