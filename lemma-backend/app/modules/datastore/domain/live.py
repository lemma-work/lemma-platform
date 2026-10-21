"""The ``records`` topic of the pod's live socket: what it is asked, and sent.

A ``records`` subscription watches a pod's tables (the socket and its frames
are core's, ``app/core/live``)::

    -> {"op":"subscribe","id":"s1","topic":"records","table":"tickets",
        "ops":["insert","update"],"when":{"status":"open"},"since":"<cursor>"}
    <- {"type":"subscribed","id":"s1","cursor":"…"}
    <- {"type":"changes","id":"s1","cursor":"…","changes":[{"op":"update",…}]}

``when`` takes the trigger grammar (``app/core/domain/record_conditions.py``)
restricted to its value operators -- equals, not_equals, in, not_in -- because a
live subscription is sent the row as it is now, with no prior image to ask
``changed`` / ``written`` / ``from`` / ``to`` of. It needs a ``table``. An insert
or update is sent when the current row matches; a delete is always sent, since
the row it would be tested against is gone and a client holding the record
needs to drop it. A row updated out of the filter is not sent.

Everything here is pure: parsing, the frame shapes, and the change values the
hub moves around. The hub lives in ``services/live``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from app.core.domain.record_conditions import (
    ColumnCondition,
    evaluate_match_conditions,
    parse_match_conditions,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.core.live.protocol import (
    LiveRequestInvalid,
    SubscribeRequest as CoreSubscribeRequest,
    dumps as _dumps,
    parse_params,
)

#: The value of a row as the client sees it: column name to JSON value.
RowPayload = dict[str, JsonValue]

LiveOp = Literal["insert", "update", "delete"]

#: A change's place in the pod's log: ``(xid, seq)``. Totally ordered per pod.
ChangePosition = tuple[int, int]


class UnsubscribedReason(StrEnum):
    ACCESS_REVOKED = "access_revoked"
    TABLE_DROPPED = "table_dropped"


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


class RecordsParams(BaseModel):
    """A ``records`` subscription's own fields, past what every topic has."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    #: One table by name; omitted means every table the caller can read now.
    table: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    ops: tuple[LiveOp, ...] | None = None
    #: Conditions over the current row; see the module docstring.
    when: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    """One ``records`` subscription, as the hub takes it."""

    id: str
    table: str | None = None
    ops: tuple[LiveOp, ...] | None = None
    since: str | None = None
    when: dict[str, JsonValue] | None = None

    def conditions(self) -> LiveConditions | None:
        """The parsed ``when``, or ``None``; raises ``ValueError`` saying why not."""
        if self.when is None:
            return None
        if self.table is None:
            raise ValueError("`when` needs a `table`.")
        parsed = parse_match_conditions(self.when)
        moving = sorted(c for c, cond in parsed.items() if cond.asks_about_movement)
        if moving:
            raise ValueError(
                "Live subscriptions see the current row only; `changed`, "
                "`written`, `from` and `to` are not available "
                f"(column {', '.join(moving)})."
            )
        return LiveConditions(parsed)


@dataclass(frozen=True, slots=True)
class LiveConditions:
    """A live subscription's ``when``: value operators over the current row."""

    columns: dict[str, ColumnCondition]

    def admit(self, op: LiveOp, row: RowPayload | None) -> bool:
        if op == "delete":
            return True
        return evaluate_match_conditions(
            self.columns, operation=op.upper(), payload=row
        )


def records_request(request: CoreSubscribeRequest) -> SubscribeRequest:
    """A socket's subscribe frame as a ``records`` subscription, or
    ``LiveRequestInvalid`` saying what is wrong with it."""
    params = parse_params(RecordsParams, request.params, request_id=request.id)
    parsed = SubscribeRequest(
        id=request.id,
        table=params.table,
        ops=params.ops,
        since=request.since,
        when=params.when,
    )
    try:
        parsed.conditions()
    except ValueError as exc:
        raise LiveRequestInvalid(f"when: {exc}", request_id=request.id) from exc
    return parsed


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveChange:
    """One row of the pod's change log, as much of it as live delivery needs."""

    position: ChangePosition
    table_id: UUID
    op: LiveOp
    record_id: str
    owner_user_id: UUID | None
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LivePage:
    """A read of the log after a cursor."""

    changes: tuple[LiveChange, ...]
    #: Where the next read starts. Unchanged when the page is empty.
    cursor: str
    #: The read stopped at its limit; more changes are already readable.
    more: bool


@dataclass(frozen=True, slots=True)
class LiveTable:
    id: UUID
    name: str


class LiveCursorExpired(Exception):
    """A cursor the log can no longer answer from: too old, or not a cursor."""


@dataclass(slots=True)
class MergedChange:
    """A record's net change within one batch: one entry per record."""

    op: LiveOp
    change: LiveChange


def merge_change(existing: MergedChange | None, change: LiveChange) -> MergedChange:
    """Fold a later change for the same record into what the batch holds.

    The client sees each record once per batch at its latest state: an insert
    followed by updates is still an insert, anything followed by a delete is a
    delete, and a delete followed by an insert is the insert.
    """
    if existing is None:
        return MergedChange(change.op, change)
    if change.op == "update" and existing.op == "insert":
        return MergedChange("insert", change)
    return MergedChange(change.op, change)


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EncodedChange:
    """A change encoded once per batch and shared by every socket sending it.

    Fan-out builds each socket's frame by joining these strings, so a row that
    a thousand subscriptions receive is serialised once, not a thousand times.
    """

    op: LiveOp
    table_name: str
    record_id: str
    row: RowPayload | None
    at: datetime | None
    _live: str | None = field(default=None, repr=False)

    def live_json(self) -> str:
        if self._live is None:
            self._live = _dumps(
                {
                    "op": self.op,
                    "table": self.table_name,
                    "record_id": self.record_id,
                    "row": self.row,
                }
            )
        return self._live

    def legacy_frame(self, *, pod_id: UUID, cursor: str) -> str:
        """The one-change-per-frame shape of ``/datastore/changes``.

        Kept byte-compatible for bundles built against the previous SDK; the
        operation stays upper-case there because that is what they shipped
        reading.
        """
        return _dumps(
            {
                "type": f"datastore.record.{self.op}",
                "pod_id": str(pod_id),
                "table_name": self.table_name,
                "record_id": self.record_id,
                "operation": self.op.upper(),
                "payload": self.row or {},
                "payload_truncated": False,
                "occurred_at": self.at.isoformat() if self.at else None,
                "cursor": cursor,
                "stream_id": cursor,
            }
        )


def changes_frame(subscription_id: str, cursor: str, changes: list[str]) -> str:
    return (
        '{"type":"changes","id":'
        + _dumps(subscription_id)
        + ',"cursor":'
        + _dumps(cursor)
        + ',"stream_id":'
        + _dumps(cursor)
        + ',"changes":['
        + ",".join(changes)
        + "]}"
    )


def legacy_ready_frame(cursor: str | None) -> str:
    return _dumps({"type": "ready", "since": cursor, "cursor": cursor})
