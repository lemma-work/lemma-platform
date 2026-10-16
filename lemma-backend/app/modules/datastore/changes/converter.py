"""The cutover: record events still on the old path become change-log rows.

At deploy, record changes can be in two places the change log never saw: rows
in the datastore outbox (written by a process still on the outbox path, until
every pod schema carries the log), and entries in ``datastore.events`` that the
two legacy consumer groups (DATASTORE triggers, MCP deliveries) had not
finished. ``changes.drain`` appends each to its pod's ``reserved_changes``
with the snapshot it carried, then deletes the outbox row or acknowledges the
entry. It is idempotent: a converted row keeps its event's id under a unique
index, so an event reached twice -- through the outbox and through two groups'
pending lists -- is appended once, and DATASTORE triggers key its fire on that
same id, which the legacy consumer used too.

It waits until no ``changes.sync_subscribers`` item is outstanding, so the
subscribers seeded by the migration exist before the changes they are owed are
appended. Only changes that occurred within ``LIVE_WINDOW`` are appended: an
older one was not in flight when the release went out, and firing triggers on
it now would be a replay. Those are still deleted or acknowledged, and counted
in the log. It keeps running while any process may still write to the outbox,
and exits (destroying the drained groups) once the outbox and both groups have
stayed empty for ``QUIET_FOR`` with the change log switched on.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from faststream.redis.parser.binary import BinaryMessageFormatV1
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    Defer,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.changes.gate import RECHECK_SECONDS, change_log_active
from app.modules.datastore.changes.schema import (
    PENDING_CHANNEL,
    PENDING_TABLE,
    changes_table,
)
from app.modules.datastore.changes.work import SYNC_SUBSCRIBERS
from app.modules.datastore.domain.events import DATASTORE_EVENTS_STREAM
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    migrate_pod_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository
from app.modules.datastore.infrastructure.session import get_datastore_engine

logger = get_logger(__name__)

DRAIN = "changes.drain"
#: The legacy groups whose unfinished entries are owed to subscribers.
LEGACY_GROUPS = ("schedule-datastore-events", "mcp-access-event-deliveries")
CONSUMER = "change-log-converter"
BATCH = 500
#: Between passes while there is nothing to do but wait.
IDLE_DELAY = timedelta(seconds=2)
#: How long everything must stay empty, with the log on, before the drain
#: ends: longer than a process takes to notice every schema is ready.
QUIET_FOR = timedelta(seconds=4 * RECHECK_SECONDS)

#: How recently a change must have occurred to be carried over.
LIVE_WINDOW = timedelta(minutes=30)

_OPS = {"INSERT": "I", "UPDATE": "U", "DELETE": "D"}

type Event = Mapping[str, object]


class DrainState(BaseModel):
    #: When the outbox and the groups were first seen empty with the log on.
    quiet_since: datetime | None = None


async def run_drain(ctx: JobContext, payload: DrainState) -> None:
    async with ctx.checkpoint() as uow:
        if await _syncs_outstanding(uow):
            raise Defer(IDLE_DELAY)
    # Entries are FastStream's binary frames, so the client must not decode.
    redis = get_redis(url=settings.redis_url, decode_responses=False)
    converted = 0
    while True:
        ctx.raise_if_stopping()
        async with ctx.checkpoint() as uow:
            from_outbox = await convert_outbox(uow)
            from_stream = await convert_stream(uow, redis)
        converted += from_outbox + from_stream
        if from_outbox < BATCH and from_stream < BATCH:
            break
    if converted:
        logger.info("datastore.changes.drain.converted", converted=converted)
    now = datetime.now(timezone.utc)
    if converted or not await change_log_active():
        state = DrainState()
    else:
        state = DrainState(quiet_since=payload.quiet_since or now)
    if state.quiet_since is not None and now - state.quiet_since >= QUIET_FOR:
        await _destroy_groups(redis)
        logger.info("datastore.changes.drain.finished")
        return
    async with ctx.complete_work() as uow:
        await ctx.writer(uow).add(
            DRAIN, SYSTEM_NAMESPACE, state, dedup_key=DRAIN, delay=IDLE_DELAY
        )


async def convert_outbox(uow: SqlAlchemyUnitOfWork) -> int:
    """One batch of unpublished record events from the datastore outbox."""
    engine = get_datastore_engine()
    async with engine.begin() as conn:
        if not await conn.scalar(
            text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
        ):
            return 0
        rows = (
            await conn.execute(
                text(
                    "SELECT id, payload FROM domain_event_outbox "
                    "WHERE event_type LIKE 'datastore.record.%' "
                    "AND published_at IS NULL AND dead_lettered_at IS NULL "
                    "ORDER BY occurred_at, id LIMIT :limit FOR UPDATE SKIP LOCKED"
                ),
                {"limit": BATCH},
            )
        ).all()
        if not rows:
            return 0
        events = [_as_event(row[1]) for row in rows]
        await append_events(
            conn,
            uow,
            _recent([event for event in events if event is not None], "outbox"),
        )
        await conn.execute(
            text("DELETE FROM domain_event_outbox WHERE id = ANY(:ids)"),
            {"ids": [row[0] for row in rows]},
        )
    return len(rows)


async def convert_stream(uow: SqlAlchemyUnitOfWork, redis: Redis) -> int:
    """One batch of each legacy group's unfinished entries; how many entries."""
    handled = 0
    for group in LEGACY_GROUPS:
        entries = await _read_group(redis, group)
        if not entries:
            continue
        events = [
            event
            for _, fields in entries
            if (event := _stream_event(fields)) is not None
        ]
        async with get_datastore_engine().begin() as conn:
            await append_events(conn, uow, _recent(events, group))
        await redis.xack(
            DATASTORE_EVENTS_STREAM, group, *[entry_id for entry_id, _ in entries]
        )
        handled += len(entries)
    return handled


async def append_events(
    conn: AsyncConnection, uow: SqlAlchemyUnitOfWork, events: Sequence[Event]
) -> int:
    """Append record events to their pods' logs; skip ones already appended."""
    by_pod: dict[UUID, list[Event]] = defaultdict(list)
    for event in events:
        if str(event.get("event_type", "")).startswith("datastore.record."):
            by_pod[UUID(str(event["pod_id"]))].append(event)
    appended = 0
    tables = DatastoreTableRepository(uow)
    for pod_id, pod_events in by_pod.items():
        names = sorted({str(event["table_name"]) for event in pod_events})
        ids = {
            name: table.id
            for name, table in (
                await tables.get_many_by_datastore_and_names(pod_id, names)
            ).items()
        }
        schema = schema_name_for_pod(pod_id)
        version = await migrate_pod_schema(
            get_datastore_engine(), schema, target=CHANGE_LOG_VERSION, create=False
        )
        records = [_record(event, ids) for event in pod_events]
        kept = [record for record in records if record is not None]
        if version < CHANGE_LOG_VERSION or not kept:
            # The pod's schema or table is gone: there is nothing left to
            # trigger on, as the legacy consumer would have found too. (A
            # schema that exists is migrated up just above, so "below the
            # change-log version" here means it does not exist.)
            logger.info(
                "datastore.changes.drain.dropped",
                pod_id=str(pod_id),
                dropped=len(pod_events),
            )
            continue
        result = await conn.execute(
            text(
                f"INSERT INTO {changes_table(schema)} (table_id, op, record_id, intent, "
                "owner_user_id, outside_actor, event_id, new_row, old_values, at) "
                "SELECT table_id, op, record_id, 'normal', owner_user_id, outside_actor, "
                "event_id, new_row, old_values, at "
                "FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS r("
                "table_id uuid, op char(1), record_id text, owner_user_id uuid, "
                "outside_actor text, event_id uuid, new_row jsonb, old_values jsonb, "
                "at timestamptz) ORDER BY at "
                "ON CONFLICT (event_id) WHERE event_id IS NOT NULL DO NOTHING"
            ),
            {"records": _encode_records(kept)},
        )
        appended += int(result.rowcount or 0)
        await conn.execute(
            text(f"INSERT INTO {PENDING_TABLE} (pod_id) VALUES (CAST(:pod AS uuid))"),
            {"pod": str(pod_id)},
        )
    if appended:
        await conn.execute(text("SELECT pg_notify(:c, '')"), {"c": PENDING_CHANNEL})
    return appended


def _occurred_at(event: Event) -> datetime | None:
    value = event.get("occurred_at")
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _recent(events: Sequence[Event], source: str) -> list[Event]:
    """The events that occurred within ``LIVE_WINDOW``; the rest are dropped.

    An event with no readable time is kept: it cannot be shown to be old, and
    the record events this drain reads always carry one.
    """
    horizon = datetime.now(timezone.utc) - LIVE_WINDOW
    kept = [
        event
        for event in events
        if (moment := _occurred_at(event)) is None or moment >= horizon
    ]
    if skipped := len(events) - len(kept):
        # A warning, not info: these are record changes a trigger or an MCP
        # subscription would have acted on, discarded by design.
        logger.warning(
            "datastore.changes.drain.stale_skipped", skipped=skipped, source=source
        )
    return kept


def _encode_records(records: list[dict[str, object]]) -> str:
    """On the loop, deliberately: at most ``BATCH`` events whose payloads the
    outbox already truncated, encoded inside the transaction that holds their
    rows locked -- offloading would hold that connection across a thread hop."""
    return json.dumps(records, default=str)


def _record(event: Event, table_ids: Mapping[str, UUID]) -> dict[str, object] | None:
    table_id = table_ids.get(str(event.get("table_name")))
    op = _OPS.get(str(event.get("operation")))
    if table_id is None or op is None:
        return None
    payload = event.get("payload") if not event.get("payload_truncated") else None
    previous = event.get("previous") if not event.get("previous_truncated") else None
    return {
        "table_id": str(table_id),
        "op": op,
        "record_id": str(event.get("record_id")),
        "owner_user_id": event.get("owner_user_id"),
        "outside_actor": event.get("outside_actor"),
        "event_id": str(event.get("event_id")),
        "new_row": payload if op != "D" else None,
        "old_values": payload if op == "D" else (previous if op == "U" else None),
        "at": event.get("occurred_at"),
    }


def _as_event(value: object) -> Event | None:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


def _stream_event(fields: Mapping[bytes, bytes]) -> Event | None:
    data = fields.get(b"__data__")
    if data is None:
        return None
    body, _headers = BinaryMessageFormatV1.parse(data)
    try:
        return _as_event(json.loads(body))
    except ValueError:
        logger.warning("datastore.changes.drain.undecodable_entry")
        return None


async def _read_group(
    redis: Redis, group: str
) -> list[tuple[bytes, Mapping[bytes, bytes]]]:
    """This consumer's pending entries first (a crashed pass), then new ones,
    then entries other (departed) consumers of the group left pending."""
    try:
        for start in ("0", ">"):
            reply = await redis.xreadgroup(
                group, CONSUMER, {DATASTORE_EVENTS_STREAM: start}, count=BATCH
            )
            entries = [entry for _, batch in reply or [] for entry in batch]
            if entries:
                return entries
        claimed = await redis.xautoclaim(
            DATASTORE_EVENTS_STREAM, group, CONSUMER, min_idle_time=0, count=BATCH
        )
    except ResponseError as exc:
        if "NOGROUP" in str(exc):
            return []
        raise
    return list(claimed[1]) if claimed else []


async def _destroy_groups(redis: Redis) -> None:
    for group in LEGACY_GROUPS:
        try:
            await redis.xgroup_destroy(DATASTORE_EVENTS_STREAM, group)
        except ResponseError as exc:
            # No stream, or the group was already destroyed: nothing to do.
            logger.info(
                "datastore.changes.drain.group_absent", group=group, detail=str(exc)
            )


async def _syncs_outstanding(uow: SqlAlchemyUnitOfWork) -> bool:
    return bool(
        await uow.session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM work_items WHERE kind = :kind AND state <> 'dead')"
            ),
            {"kind": SYNC_SUBSCRIBERS},
        )
    )


def register_drain_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=DRAIN,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=DrainState,
        lease=timedelta(minutes=2),
    )(run_drain)


register_drain_work(default_registry)

__all__ = [
    "DRAIN",
    "LEGACY_GROUPS",
    "DrainState",
    "append_events",
    "register_drain_work",
]
