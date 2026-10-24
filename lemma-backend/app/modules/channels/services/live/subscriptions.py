"""The ``channel`` and ``inbox`` subscriptions a pod's hub feeds."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.core.live.connection import LiveConnection, LiveSubscription
from app.core.live.protocol import ResyncReason
from app.modules.channels.api.access import Caller


class InboxSocket(Protocol):
    """Where an ``inbox`` subscription's frames go: the pod socket it was
    made on, or the ``org`` topic relaying the pod's counts
    (``org_relay.py``)."""

    user_id: UUID
    closed: bool
    subscriptions: dict[str, LiveSubscription]

    def offer_subscribed(self, subscription_id: str, cursor: str | None) -> None: ...

    def offer_events(
        self,
        subscription_id: str,
        cursor: str | None,
        events: list[str],
        *,
        previous_cursor: str | None = None,
    ) -> None: ...

    def offer_resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> None: ...

    def offer_unsubscribed(self, subscription_id: str, reason: str) -> None: ...


@dataclass(eq=False, slots=True)
class ChannelSub:
    """One channel's timeline, for one reader."""

    id: str
    connection: LiveConnection
    channel_id: UUID
    #: Who they are to the channel rules; rebuilt when the pod's grants move.
    caller: Caller
    #: Where the subscriber is up to (``events.cursor_of``).
    cursor: str
    active: bool = True
    on_release: Callable[["ChannelSub"], None] | None = field(default=None, repr=False)

    @property
    def user_id(self) -> UUID:
        return self.connection.user_id

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.on_release is not None:
            self.on_release(self)


@dataclass(eq=False, slots=True)
class InboxSub:
    """One reader's counts across the pod's channels."""

    id: str
    connection: InboxSocket
    caller: Caller
    active: bool = True
    on_release: Callable[["InboxSub"], None] | None = field(default=None, repr=False)

    @property
    def user_id(self) -> UUID:
        return self.connection.user_id

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.on_release is not None:
            self.on_release(self)


__all__ = ["ChannelSub", "InboxSocket", "InboxSub"]
