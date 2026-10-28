"""Channels on the ``org`` topic: each covered pod's ``inbox``, relayed.

A reader of an organization is told what the pod's ``inbox`` topic would tell
them -- ``inbox.updated`` (one channel's counts and the pod's totals) and
``question.waiting`` (an ask newly waiting on them) -- each stamped with its
pod by the ``org`` topic. It is the same subscription the pod socket's
``inbox`` is, joined to the pod's channels hub the same way: the same counts, the same
rule for which channels a reader may see, the same re-check when their access
moves. Only where it goes differs: to the ``org`` subscription rather than a
socket on the pod.

What the hub would say to a socket and cannot say here -- refetch (``resync``),
or the subscription ended (``unsubscribed``: channel reading lost) -- reaches
the reader as ``pod.stale``: re-read the pod's summary.
"""

from __future__ import annotations

import itertools
import json

from pydantic import JsonValue

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.connection import LiveSubscription
from app.core.live.org_feeds import OrgReader, OrgWatch
from app.core.live.protocol import JsonObject, ResyncReason
from app.modules.channels.services.live.readers import caller_for

_ids = itertools.count(1)


class _ReaderSocket:
    """An ``InboxSocket`` that hands the hub's frames to one ``org`` reader."""

    def __init__(self, reader: OrgReader) -> None:
        self._reader = reader
        self.user_id = reader.user_id
        self.closed = False
        self.subscriptions: dict[str, LiveSubscription] = {}

    def offer_subscribed(self, subscription_id: str, cursor: str | None) -> None:
        """The reader was told the organization's ``subscribed`` already."""

    def offer_events(
        self,
        subscription_id: str,
        cursor: str | None,
        events: list[str],
        *,
        previous_cursor: str | None = None,
    ) -> None:
        decoded: list[JsonObject] = []
        for event in events:
            value: JsonValue = json.loads(event)
            if isinstance(value, dict):
                decoded.append(value)
        self._reader.notify(decoded)

    def offer_resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> None:
        self._reader.stale()

    def offer_unsubscribed(self, subscription_id: str, reason: str) -> None:
        sub = self.subscriptions.pop(subscription_id, None)
        if sub is not None:
            sub.release()
        self._reader.stale()


class InboxRelay:
    name = "channels.inbox"

    async def watch(self, reader: OrgReader) -> OrgWatch | None:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            caller = await caller_for(uow, pod_id=reader.pod_id, user_id=reader.user_id)
        if caller is None:
            # No channel reading here: the pod's other notices still come.
            return None
        from app.modules.channels.services.live.hubs import channels_hubs

        return (
            await channels_hubs()
            .hub(reader.pod_id)
            .join_inbox(_ReaderSocket(reader), f"org:{next(_ids)}", caller=caller)
        )


__all__ = ["InboxRelay"]
