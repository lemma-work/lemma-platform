"""A day of Slack webhooks, synthesised from the event types prod receives.

Prod took 71.5k Slack webhooks in a week against 2,277 agent runs from every
source: most of a workspace's Events API traffic is about messages nobody asked
the bot to read. This builds a deterministic mix with that shape, labelled with
what request-time classification must do with each delivery:

=====================================  ======  =================
kind                                   share   expected
=====================================  ======  =================
chatter in a channel no group adopted  55 %    drop
the bot's own messages echoed back     12 %    drop
edits and deletions                     8 %    drop
reactions                               6 %    drop
other events (user_change, files ...)   4 %    drop
app home opened                         3 %    platform_update
a retry of an actionable delivery       2 %    duplicate
a DM, a mention, a thread reply        10 %    message (stored)
=====================================  ======  =================
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

TEAM = "T0123456"
BOT = "U0AGSSTQZLH"
SHARES: tuple[tuple[str, float], ...] = (
    ("chatter", 0.55),
    ("echo", 0.12),
    ("edit", 0.08),
    ("reaction", 0.06),
    ("other", 0.04),
    ("home", 0.03),
    ("retry", 0.02),
    ("actionable", 0.10),
)
EXPECTED = {
    "chatter": "drop",
    "echo": "drop",
    "edit": "drop",
    "reaction": "drop",
    "other": "drop",
    "home": "platform_update",
    "retry": "duplicate",
    "actionable": "message",
}


@dataclass(frozen=True, slots=True)
class Delivery:
    kind: str
    payload: dict[str, Any]


def _envelope(event: dict[str, Any], event_id: str) -> dict[str, Any]:
    return {
        "token": "verification",
        "team_id": TEAM,
        "api_app_id": "A0123456",
        "type": "event_callback",
        "event_id": event_id,
        "event_time": 1700000000,
        "event": event,
    }


def slack_webhook_mix(count: int, *, seed: int = 7) -> list[Delivery]:
    rng = random.Random(seed)
    kinds = [kind for kind, _ in SHARES]
    weights = [share for _, share in SHARES]
    deliveries: list[Delivery] = []
    actionable: list[Delivery] = []
    for index in range(count):
        kind = rng.choices(kinds, weights)[0]
        ts = f"1700000000.{index:06d}"
        event_id = f"Ev{seed:02d}{index:08d}"
        if kind == "retry" and actionable:
            deliveries.append(Delivery("retry", rng.choice(actionable).payload))
            continue
        if kind == "retry":
            kind = "actionable"
        user = f"U{rng.randrange(10_000):05d}"
        channel = f"C{rng.randrange(400):05d}"
        if kind == "chatter":
            event = {
                "type": "message",
                "channel": channel,
                "channel_type": "channel",
                "user": user,
                "text": "standup in five",
                "ts": ts,
            }
        elif kind == "echo":
            event = {
                "type": "message",
                "channel": channel,
                "channel_type": "channel",
                "bot_id": "B0BOT",
                "user": BOT,
                "text": "Done.",
                "ts": ts,
            }
        elif kind == "edit":
            event = {
                "type": "message",
                "channel": channel,
                "subtype": rng.choice(["message_changed", "message_deleted"]),
                "ts": ts,
            }
        elif kind == "reaction":
            event = {
                "type": rng.choice(["reaction_added", "reaction_removed"]),
                "user": user,
                "reaction": "eyes",
                "item": {"type": "message", "channel": channel, "ts": ts},
            }
        elif kind == "other":
            event = {
                "type": rng.choice(["user_change", "file_shared", "channel_created"]),
                "user": user,
            }
        elif kind == "home":
            event = {
                "type": "app_home_opened",
                "user": user,
                "channel": f"D{user[1:]}",
                "tab": "home",
            }
        else:
            shape = rng.choice(["dm", "mention", "thread"])
            if shape == "dm":
                event = {
                    "type": "message",
                    "channel": f"D{user[1:]}",
                    "channel_type": "im",
                    "user": user,
                    "text": "hello",
                    "ts": ts,
                }
            elif shape == "mention":
                event = {
                    "type": "app_mention",
                    "channel": channel,
                    "user": user,
                    "text": f"<@{BOT}> summarise this",
                    "ts": ts,
                }
            else:
                event = {
                    "type": "message",
                    "channel": channel,
                    "channel_type": "channel",
                    "user": user,
                    "text": "and the follow-up?",
                    "ts": ts,
                    "thread_ts": "1699999999.000001",
                }
        delivery = Delivery(kind, _envelope(event, event_id))
        deliveries.append(delivery)
        if kind == "actionable":
            actionable.append(delivery)
    return deliveries


__all__ = ["EXPECTED", "SHARES", "Delivery", "slack_webhook_mix"]
