"""``changes.retention``: keep a pod's log to the catch-up window, and never less.

A change is deleted only when it is older than the window **and** every durable
subscriber of its pod has read it. A pod with no subscribers keeps the window's
slim rows for live catch-up; a pod with a stuck subscriber grows its own log --
visible as that pod's lag -- and costs no other pod anything, and only up to
the lag ceiling.

The newest deleted position becomes the pod's floor: a live reader whose
cursor is below it may have missed changes and is told to resync.

The same pass looks after the subscribers, before it deletes anything:

* a row its module no longer recognises is dropped (a subscription that
  lapsed without a sync); a row whose prefix no process here knows is kept,
  for a newer process that may still owe it, until its cursor has not moved
  for the lag ceiling;
* a subscriber with nothing readable on its tables is moved to the horizon.
  Nothing below the horizon is still to come, and it watches none of it, so
  a trigger on a quiet table no longer holds back a busy table's changes;
* a subscriber whose oldest unread change is older than the lag ceiling
  (``DATASTORE_CHANGE_MAX_LAG_SECONDS``) is lapsed: its module records what
  it skipped, its cursor moves to the horizon, and
  ``lemma.changes.subscriber_lapsed`` counts it;
* any other subscriber with readable changes gets a delivery -- the backstop
  for a wake lost between a write and a subscriber's creation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from opentelemetry import metrics
from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.changes.cursor import (
    ChangePosition,
    decode_cursor,
    encode_cursor,
)
from app.modules.datastore.changes.reader import count_between, read_after
from app.modules.datastore.changes.schema import FLOORS_TABLE, changes_table
from app.modules.datastore.changes.subscribers import (
    LapsedSpan,
    SubscriberRow,
    SubscriberTable,
    durable_subscriber,
)
from app.modules.datastore.changes.work import request_delivery
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    VERSIONS_TABLE,
    pod_id_for_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
subscriber_lapsed = meter.create_counter(
    "lemma.changes.subscriber_lapsed",
    description=(
        "Durable subscribers moved past changes they never read, for falling "
        "further behind than DATASTORE_CHANGE_MAX_LAG_SECONDS."
    ),
)

RETENTION = "changes.retention"
RETENTION_EVERY = timedelta(hours=1)
#: Pod schemas per item; the next page is its own item.
RETENTION_PAGE = 200
#: Rows per delete statement, so one pod's backlog is never one long statement.
DELETE_CHUNK = 50_000


class RetentionPage(BaseModel):
    after: str = ""


async def run_retention(ctx: JobContext, payload: RetentionPage) -> None:
    schemas = await _schemas_with_log(payload.after)
    deleted = 0
    for schema in schemas:
        ctx.raise_if_stopping()
        deleted += await retain_pod(ctx, UUID(pod_id_for_schema(schema)))
    async with ctx.complete_work() as uow:
        if len(schemas) == RETENTION_PAGE:
            await ctx.writer(uow).add(
                RETENTION,
                SYSTEM_NAMESPACE,
                RetentionPage(after=schemas[-1]),
                dedup_key=f"changes.retention:{schemas[-1]}",
            )
    if deleted:
        logger.info("datastore.changes.retention.observed", deleted=deleted)


async def retain_pod(ctx: JobContext, pod_id: UUID) -> int:
    """One pod's pass: tend its subscribers, then delete what none needs."""
    await _tend(ctx, pod_id, await _live_subscribers(ctx, pod_id))
    # Read again rather than trusting what tending meant to do: a sync may
    # have moved a row since, and a row added meanwhile holds its place.
    return await retain(pod_id, await SubscriberTable(pod_id).all())


async def retain(pod_id: UUID, subscribers: list[SubscriberRow]) -> int:
    """Delete what this pod's log no longer needs; how many rows went."""
    engine = get_datastore_engine()
    table = changes_table(schema_name_for_pod(pod_id))
    window = timedelta(seconds=datastore_settings.datastore_change_retention_seconds)
    where = ["at < now() - make_interval(secs => :window)"]
    params: dict[str, object] = {
        "window": window.total_seconds(),
        "limit": DELETE_CHUNK,
        "pod_id": str(pod_id),
    }
    if subscribers:
        read = min(subscriber.cursor for subscriber in subscribers)
        where.append("(xid, seq) <= (CAST(CAST(:xid AS text) AS xid8), :seq)")
        params.update(xid=str(read.xid), seq=read.seq)
    total = 0
    while True:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        f"WITH gone AS (DELETE FROM {table} WHERE (xid, seq) IN ("
                        f"SELECT xid, seq FROM {table} WHERE {' AND '.join(where)} "
                        "ORDER BY xid, seq LIMIT :limit) RETURNING xid, seq), "
                        "top AS (SELECT xid, seq FROM gone ORDER BY xid DESC, seq DESC "
                        "LIMIT 1), "
                        f"floor AS (INSERT INTO {FLOORS_TABLE} AS f "
                        "(pod_id, floor_xid, floor_seq) "
                        "SELECT CAST(:pod_id AS uuid), xid, seq FROM top "
                        "ON CONFLICT (pod_id) DO UPDATE SET floor_xid = EXCLUDED.floor_xid, "
                        "floor_seq = EXCLUDED.floor_seq, at = now() "
                        "WHERE (f.floor_xid, f.floor_seq) < "
                        "(EXCLUDED.floor_xid, EXCLUDED.floor_seq)) "
                        "SELECT count(*) FROM gone"
                    ),
                    params,
                )
            ).scalar_one()
        total += int(row)
        if int(row) < DELETE_CHUNK:
            return total


async def _live_subscribers(ctx: JobContext, pod_id: UUID) -> list[SubscriberRow]:
    """The pod's subscriber rows, minus those their module says are gone."""
    table = SubscriberTable(pod_id)
    rows = await table.all()
    live: list[SubscriberRow] = []
    for row in rows:
        found = durable_subscriber(row.subscriber)
        if found is None:
            # A prefix this process does not know is kept: dropping it would
            # let retention delete changes a newer process still owes. Kept
            # only while something moves it, though: a row nobody has
            # advanced for the lag ceiling has no process left to owe.
            if await table.delete_if_idle(row.subscriber, _max_lag()):
                logger.warning(
                    "datastore.changes.retention.unknown_subscriber_dropped",
                    pod_id=str(pod_id),
                    subscriber=row.subscriber,
                )
                continue
            live.append(row)
            continue
        module, subscriber_id = found
        async with ctx.checkpoint() as uow:
            spec = await module.spec(uow, pod_id, subscriber_id)
        if spec is None:
            await table.delete(row.subscriber)
            continue
        live.append(row)
    return live


def _max_lag() -> timedelta:
    return timedelta(seconds=datastore_settings.datastore_change_max_lag_seconds)


async def _tend(
    ctx: JobContext, pod_id: UUID, subscribers: list[SubscriberRow]
) -> None:
    """Move each subscriber to the horizon, lapse it, or wake it."""
    table = SubscriberTable(pod_id)
    for subscriber in subscribers:
        ctx.raise_if_stopping()
        if durable_subscriber(subscriber.subscriber) is None:
            # Not this process's to move: its cursor moving is what keeps the
            # row from being dropped as abandoned.
            continue
        page = await read_after(
            pod_id,
            encode_cursor(subscriber.cursor),
            table_ids=[subscriber.table_id],
            ops=sorted(subscriber.ops),
            limit=1,
        )
        if not page.changes:
            # Nothing it watches is readable: everything below the horizon is
            # final and none of it is its, so the horizon is where it is.
            await table.advance(
                subscriber.subscriber, decode_cursor(page.cursor), watching=subscriber
            )
            continue
        oldest = page.changes[0]
        if datetime.now(timezone.utc) - oldest.at > _max_lag():
            horizon = decode_cursor(page.horizon)
            await _lapse(ctx, pod_id, subscriber, oldest.position, horizon)
            continue
        async with ctx.checkpoint() as uow:
            await request_delivery(ctx.writer(uow), pod_id, subscriber.subscriber)


async def _lapse(
    ctx: JobContext,
    pod_id: UUID,
    subscriber: SubscriberRow,
    first: ChangePosition,
    horizon: ChangePosition,
) -> None:
    """Move ``subscriber`` past what it never read, once its module has said so.

    The module's record commits before the cursor moves, as a delivery's does:
    a crash between the two repeats the record, which is keyed on where the
    skipped span starts, and never moves the cursor unrecorded.
    """
    found = durable_subscriber(subscriber.subscriber)
    count, last = await count_between(
        pod_id,
        subscriber.cursor,
        horizon,
        table_ids=[subscriber.table_id],
        ops=sorted(subscriber.ops),
    )
    if found is not None and last is not None:
        module, subscriber_id = found
        async with ctx.checkpoint() as uow:
            await module.lapsed(
                uow,
                pod_id,
                subscriber_id,
                LapsedSpan(first=first, last=last, count=count),
            )
    await SubscriberTable(pod_id).advance(
        subscriber.subscriber, horizon, watching=subscriber
    )
    prefix = subscriber.subscriber.partition(":")[0]
    subscriber_lapsed.add(1, {"subscriber_kind": prefix})
    logger.warning(
        "datastore.changes.retention.subscriber_lapsed",
        pod_id=str(pod_id),
        subscriber=subscriber.subscriber,
        skipped=count,
    )


async def _schemas_with_log(after: str) -> list[str]:
    async with get_datastore_engine().connect() as conn:
        if not await conn.scalar(
            text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL")
        ):
            return []
        rows = await conn.execute(
            text(
                f"SELECT schema_name FROM {VERSIONS_TABLE} "
                "WHERE version >= :version AND schema_name > :after "
                "ORDER BY schema_name LIMIT :limit"
            ),
            {"version": CHANGE_LOG_VERSION, "after": after, "limit": RETENTION_PAGE},
        )
        return [str(name) for name in rows.scalars()]


def register_retention_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=RETENTION,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=RetentionPage,
        lease=timedelta(minutes=5),
    )(run_retention)
    registry.periodic(RETENTION, RETENTION_EVERY, RETENTION)


register_retention_work(default_registry)

__all__ = [
    "RETENTION",
    "RetentionPage",
    "register_retention_work",
    "retain",
    "retain_pod",
]
