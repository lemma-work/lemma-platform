"""A push to one member: the send, and what it ends as.

A push delivery stands for every line of one channel the member heard since
their last push from it (``channels`` coalesces them). ``MemberPushes``
chooses where the member is reached and sends; this turns what happened into
the delivery's outcome. Sent is ``SENT``. Unless it names or asks them, a
push whose lines the member has read in the channel by then is ``SKIPPED``
(``IN_APP``); one to a member who had the app open in the last
``PRESENT_FOR_SECONDS`` is held until its newest line is that old
(``FOLDED``), and goes then if they still have not read it -- having the app
open is not reading this channel. Held by the hourly budget is ``SKIPPED``
(``FOLDED``): its lines go with the member's next push from the channel, no
sooner than ``HOLD_FOR`` -- the hour's budget is what holds them, so the
next push says how many came. A platform asked that took none of it ends as
its failure says (``delivery_outcome.of_send``) -- so a push that may be on
their screen is not tried again -- and nowhere to reach them is ``SKIPPED``
(``UNREACHABLE``): it waits in Lemma.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.live.presence import seen_within
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.delivery_outcome import Outcome, of_send
from app.modules.agent_surfaces.services.member_pushes import (
    HELD_BY_BUDGET,
    MemberPushes,
    Push,
)
from app.modules.agent_surfaces.services.notification_delivery import Delivered
from app.modules.agent_surfaces.services.run_answer_sends import delivery_key
from app.modules.channels.contracts import (
    Channel,
    ChannelMessage,
    Delivery,
    DeliveryReasonCode as Code,
    Principal,
    member_of,
    read_upto,
)

#: The pushes over one unit of work.
type PushesFor = Callable[[SqlAlchemyUnitOfWork], MemberPushes]

#: A member who had the app open this recently may read a line there: a push
#: that does not name or ask them waits until its newest line is this old.
PRESENT_FOR_SECONDS = 5 * 60

#: How long a push the hourly budget held waits before its lines may go again.
HOLD_FOR = timedelta(minutes=30)

READ_IN_APP = "They read it in the app before it was pushed."
HELD_WHILE_IN_APP = (
    "They had the app open: held for a few minutes, and pushed then if still unread."
)


async def push_outcome(
    uow_factory: UnitOfWorkFactory,
    pushes_for: PushesFor,
    *,
    delivery: Delivery,
    channel: Channel,
    line: ChannelMessage,
) -> Outcome:
    """Push the delivery's lines to its member; what became of it."""
    recipient = delivery.recipient_user_id
    if recipient is None:  # pragma: no cover - a CHECK holds it to one target
        return Outcome.skipped("This delivery names nobody.", code=Code.UNREACHABLE)
    if not delivery.urgent:
        async with uow_factory() as uow:
            read = await read_upto(uow, channel_id=channel.id, user_id=recipient)
        if read >= delivery.seq:
            return Outcome.skipped(READ_IN_APP, code=Code.IN_APP)
        wait = _fresh_for(line)
        if wait is not None and await seen_within(
            channel.pod_id, [recipient], seconds=PRESENT_FOR_SECONDS
        ):
            return Outcome.folded(HELD_WHILE_IN_APP, hold_for=wait)
    raised: BaseException | None = None
    delivered = Delivered(reason="The push did not go.")
    with track_sends() as sends:
        async with uow_factory() as uow:
            member = await member_of(
                uow,
                channel_id=channel.id,
                principal=Principal.member(recipient),
            )
            try:
                delivered = await pushes_for(uow).push(
                    Push(
                        channel=channel,
                        line=line,
                        recipient_user_id=recipient,
                        member=member,
                        key=delivery_key(delivery),
                        urgent=delivery.urgent,
                        lines=delivery.lines,
                        # Charged once per delivery, not per try of it.
                        first_try=delivery.attempts <= 1,
                    )
                )
            except platform_transport_errors() as exc:
                raised = exc
            await uow.commit()
    if delivered.sent:
        return Outcome.delivered(
            delivered.platform, external_message_id=delivered.external_message_id
        )
    if delivered.held:
        return Outcome.folded(HELD_BY_BUDGET, hold_for=HOLD_FOR)
    if sends.tried:
        # A platform was asked and took none of it: whether to try again
        # is what that platform's failure says (``of_send``).
        return of_send(None, sends, platform=sends.platform or "The platform")
    if raised is not None or delivered.retryable:
        return Outcome.failed(delivered.reason or "The push did not go.")
    # Nowhere to reach them is a fact about them, not an outage: the line
    # waits in Lemma, and trying again changes nothing.
    return Outcome.skipped(
        delivered.reason or "Nothing could reach them.", code=Code.UNREACHABLE
    )


def _fresh_for(line: ChannelMessage) -> timedelta | None:
    """How much longer the newest line of a push is too fresh to push to a
    member with the app open; ``None`` once it is not."""
    left = timedelta(seconds=PRESENT_FOR_SECONDS) - (
        datetime.now(timezone.utc) - line.created_at
    )
    return left if left > timedelta(0) else None


__all__ = ["HOLD_FOR", "PRESENT_FOR_SECONDS", "PushesFor", "push_outcome"]
