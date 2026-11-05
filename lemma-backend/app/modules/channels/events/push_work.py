"""Pushes to members, after a line is written.

* ``channels.pushes`` -- subscribed to ``ChannelMessagePosted``: who hears of
  the line, folded into each one's push window (``services/pushes.fan_out``).
  One at a time per channel, so a channel's lines are folded in the order
  they were written.
* ``channels.push_window`` -- a member's window of a channel ended: what it
  holds goes as one push.

Both FAST: main-database statements only. The push itself is a delivery,
sent by whoever owns the platforms (``ChannelDeliveryDue``).
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.channels.contracts.facts import ChannelMessagePosted
from app.modules.channels.services.pushes import (
    CHANNELS_PUSH_WINDOW,
    PushWindowDue,
    fan_out,
    send_window,
)

CHANNELS_PUSHES = "channels.pushes"


async def pushes(ctx: FastContext, payload: ChannelMessagePosted) -> None:
    await fan_out(
        ctx.uow,
        channel_id=payload.channel_id,
        message_id=payload.message_id,
        assignee_user_id=payload.assignee_user_id,
    )


async def push_window(ctx: FastContext, payload: PushWindowDue) -> None:
    await send_window(ctx.uow, payload)


def register_push_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_PUSHES,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ChannelMessagePosted,
        concurrency=1,
    )(pushes)
    registry.handler(
        kind=CHANNELS_PUSH_WINDOW,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PushWindowDue,
    )(push_window)


register_push_work(default_registry)
facts.subscribe(
    ChannelMessagePosted,
    kind=CHANNELS_PUSHES,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channel-pushes:{fact.message_id}",
    concurrency_key=lambda fact: f"channel-pushes:{fact.channel_id}",
)
