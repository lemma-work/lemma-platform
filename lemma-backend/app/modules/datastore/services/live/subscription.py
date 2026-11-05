"""A ``records`` subscription, and how its changes are framed for its socket.

The socket and its queue are core's (``app/core/live/connection.py``). What is
datastore's is what a subscription watches -- tables, ops, ``when``, the RLS
owner rule -- and the two framings its changes go out in: the live socket's
``changes`` frame, and the one-change-per-frame shape of the previous
``/datastore/changes`` socket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from app.core.live.connection import LiveConnection
from app.core.live.protocol import LiveErrorCode, ResyncReason
from app.core.live.connection import chunk
from app.modules.datastore.domain.live import (
    ChangePosition,
    EncodedChange,
    LiveConditions,
    LiveOp,
    changes_frame,
    legacy_ready_frame,
)


@dataclass(eq=False, slots=True)
class LiveSubscription:
    id: str
    connection: LiveConnection
    #: Table id to name, for every table this subscription covers.
    tables: dict[UUID, str]
    #: Subscribed without a table: follows the caller's readable set.
    all_tables: bool
    ops: frozenset[LiveOp] | None
    #: The log position this subscription has been brought up to; the hub
    #: delivers only changes after it.
    position: ChangePosition
    #: ``position`` as the client's cursor: what a resume from here sends.
    cursor: str = ""
    active: bool = True
    #: ``when`` over the current row; applied once the row is fetched.
    when: LiveConditions | None = None
    #: Framed for the previous ``/datastore/changes`` socket.
    legacy: bool = False
    #: Lets the hub stop feeding it; set by the hub that indexed it.
    on_release: Callable[["LiveSubscription"], None] | None = field(
        default=None, repr=False
    )

    def advance(self, position: ChangePosition, cursor: str) -> None:
        self.position = position
        self.cursor = cursor

    @property
    def user_id(self) -> UUID:
        return self.connection.user_id

    def wants(self, op: LiveOp, owner_user_id: UUID | None) -> bool:
        if self.ops is not None and op not in self.ops:
            return False
        # RLS: a row with an owner reaches only that owner; a shared row
        # (no owner) reaches every reader of the table.
        return owner_user_id is None or owner_user_id == self.connection.user_id

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.on_release is not None:
            self.on_release(self)


def offer_changes(
    subscription: LiveSubscription,
    cursor: str,
    changes: list[EncodedChange],
    *,
    previous_cursor: str,
) -> None:
    """Queue one batch on the subscription's socket.

    Only the batch's last frame carries the batch's cursor; the frames before
    it carry ``previous_cursor``. A client that drops mid-batch resumes from
    before the batch and receives it again, rather than from after it with the
    rest missing.
    """
    if not subscription.active or not changes:
        return
    if subscription.legacy:
        texts = [
            change.legacy_frame(
                pod_id=subscription.connection.pod_id,
                cursor=cursor if index == len(changes) - 1 else previous_cursor,
            )
            for index, change in enumerate(changes)
        ]
    else:
        runs = chunk([change.live_json() for change in changes])
        texts = [
            changes_frame(
                subscription.id,
                cursor if index == len(runs) - 1 else previous_cursor,
                run,
            )
            for index, run in enumerate(runs)
        ]
    subscription.connection.offer_batch(subscription.id, cursor, texts)


class LegacyFrames:
    """How the previous ``/datastore/changes`` socket words control frames.

    Its SDK learns its resume cursor from ``ready``, has no resync (it treats
    an unknown frame as a change, so a fresh ``ready`` moves its cursor past
    the gap instead), and no error or unsubscribed frame: it is refused before
    it subscribes.
    """

    def subscribed(self, subscription_id: str, cursor: str | None) -> str | None:
        return legacy_ready_frame(cursor)

    def resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> str | None:
        return legacy_ready_frame(cursor)

    def unsubscribed(self, subscription_id: str, reason: str) -> str | None:
        return None

    def error(
        self, request_id: str | None, code: LiveErrorCode, message: str
    ) -> str | None:
        return None


__all__ = ["LegacyFrames", "LiveSubscription", "offer_changes"]
