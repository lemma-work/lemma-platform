"""Turning a page of the change log into frames: routing, merging, encoding.

Pure apart from the row fetch: given the subscriptions on a table, decide who
gets which change, fold each record to its latest state, fetch the current rows
once per table, and encode each change once for every socket that sends it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from opentelemetry import metrics

from app.modules.datastore.domain.live import (
    EncodedChange,
    LiveChange,
    MergedChange,
    RowPayload,
    merge_change,
)
from app.modules.datastore.domain.ports import LiveChangeFeedPort
from app.modules.datastore.services.live.subscription import LiveSubscription

meter = metrics.get_meter(__name__)
hub_row_fetches = meter.create_counter("lemma.live.hub_row_fetches")

#: Record ids per current-row fetch.
_ROW_FETCH_CHUNK = 1000

Pending = dict[LiveSubscription, dict[str, MergedChange]]


@dataclass(slots=True)
class HubStats:
    reads: int = 0
    row_fetches: int = 0
    wakes: int = 0
    batches: int = 0


def route_change(
    subscriptions: Iterable[LiveSubscription],
    change: LiveChange,
    pending: Pending,
    too_many: set[LiveSubscription],
    limit: int,
) -> None:
    """Add ``change`` to every subscription on its table that wants it.

    A subscription past ``limit`` records in this batch moves to ``too_many``
    and stops collecting: it gets one resync instead of the rows.
    """
    for subscription in subscriptions:
        if subscription in too_many or change.position <= subscription.position:
            continue
        if not subscription.wants(change.op, change.owner_user_id):
            continue
        merged = pending.setdefault(subscription, {})
        merged[change.record_id] = merge_change(merged.get(change.record_id), change)
        if len(merged) > limit:
            too_many.add(subscription)
            pending.pop(subscription, None)


async def encode_batch(
    feed: LiveChangeFeedPort, pod_id: UUID, pending: Pending, stats: HubStats
) -> dict[LiveSubscription, list[EncodedChange]]:
    """Fetch each table's current rows once and encode each change once."""
    wanted: dict[UUID, set[str]] = {}
    names: dict[UUID, str] = {}
    for subscription, merged in pending.items():
        for record_id, entry in merged.items():
            table_id = entry.change.table_id
            names[table_id] = subscription.tables.get(table_id, names.get(table_id, ""))
            if entry.op != "delete":
                wanted.setdefault(table_id, set()).add(record_id)
    rows: dict[UUID, dict[str, RowPayload]] = {}
    for table_id, record_ids in wanted.items():
        fetched: dict[str, RowPayload] = {}
        ordered = sorted(record_ids)
        for start in range(0, len(ordered), _ROW_FETCH_CHUNK):
            stats.row_fetches += 1
            hub_row_fetches.add(1)
            fetched.update(
                await feed.rows(
                    pod_id, table_id, ordered[start : start + _ROW_FETCH_CHUNK]
                )
            )
        rows[table_id] = fetched
    cache: dict[tuple[UUID, str], EncodedChange | None] = {}
    out: dict[LiveSubscription, list[EncodedChange]] = {}
    for subscription, merged in pending.items():
        encoded: list[EncodedChange] = []
        for record_id, entry in merged.items():
            key = (entry.change.table_id, record_id)
            if key not in cache:
                cache[key] = _encode_change(entry, names, rows)
            item = cache[key]
            if item is None:
                continue
            # After visibility (``wants`` in routing), on the row just fetched.
            if subscription.when is None or subscription.when.admit(item.op, item.row):
                encoded.append(item)
        if encoded:
            out[subscription] = encoded
    return out


def _encode_change(
    entry: MergedChange,
    names: dict[UUID, str],
    rows: dict[UUID, dict[str, RowPayload]],
) -> EncodedChange | None:
    change = entry.change
    row = None
    if entry.op != "delete":
        row = rows.get(change.table_id, {}).get(change.record_id)
        if row is None:
            # Gone since the change was logged; its delete is in a later batch.
            return None
    return EncodedChange(
        op=entry.op,
        table_name=names.get(change.table_id, ""),
        record_id=change.record_id,
        row=row,
        at=change.at,
    )
