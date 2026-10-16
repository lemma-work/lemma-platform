"""The surfaces module's work kinds, by name, before their handlers exist.

Inbound ingest, outbound delivery and the receivers move onto the work
runtime in pieces that land in either order; each names the kinds and builds
the dedup keys through these constants, so a writer and the handler
registration cannot disagree on a string. Constants only: importing this
module registers nothing.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

#: Routes one stored inbound message into a conversation.
SURFACE_INGEST: Final = "surface.ingest"
#: Sends one ``surface_deliveries`` row to its platform.
SURFACE_REPLY: Final = "surface.reply"
#: Writes the delivery row for a finished run's answer (an ``AgentRunFinished`` subscriber).
SURFACE_REPLY_FOR_RUN: Final = "surface.reply_for_run"
#: A perpetual poller for one credential (Telegram, Slack socket, Resend).
SURFACE_RECEIVER: Final = "surface.receiver"
#: Provider-side follow-ups of a webhook that is not a message (statuses, lifecycle).
SURFACE_PLATFORM_UPDATE: Final = "surface.platform_update"
#: Clears identities bound to a phone number a user no longer has.
SURFACE_MOBILE_CHANGED: Final = "surfaces.mobile_changed"
#: Expires a notification nobody answered in time.
NOTIFICATION_EXPIRE: Final = "notification.expire"

#: Periodic names (``periodic(name, every, kind)``).
RECEIVERS_RECONCILE_PERIODIC: Final = "surfaces.receivers.reconcile"
ONBOARDING_CLEANUP_PERIODIC: Final = "surfaces.onboarding_cleanup"


def ingest_key(inbound_message_id: UUID) -> str:
    return f"ingest:{inbound_message_id}"


def run_reply_key(run_id: UUID) -> str:
    return f"run-reply:{run_id}"


def receiver_key(credential_key: str) -> str:
    return f"receiver:{credential_key}"


def notification_expire_key(notification_id: UUID) -> str:
    return f"notification-expire:{notification_id}"


__all__ = [
    "NOTIFICATION_EXPIRE",
    "ONBOARDING_CLEANUP_PERIODIC",
    "RECEIVERS_RECONCILE_PERIODIC",
    "SURFACE_INGEST",
    "SURFACE_MOBILE_CHANGED",
    "SURFACE_PLATFORM_UPDATE",
    "SURFACE_RECEIVER",
    "SURFACE_REPLY",
    "SURFACE_REPLY_FOR_RUN",
    "ingest_key",
    "notification_expire_key",
    "receiver_key",
    "run_reply_key",
]
