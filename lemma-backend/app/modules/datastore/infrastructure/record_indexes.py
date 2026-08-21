"""The index a pod table needs to be listable at all.

Dynamic tables were created with nothing but their primary key. That reads as
a performance problem and is worse than one: ``_reject_if_too_expensive`` runs
``EXPLAIN`` and refuses any query whose planned cost exceeds
``datastore_query_max_cost``, and with no index the planner costs a sequential
scan — so past a certain row count ``query.execute`` stops working rather than
merely slowing down.

The shape follows the default listing sort exactly. Record listing orders by
``created_at DESC, <pk> DESC`` (the pk breaks ties; ``created_at`` alone can
repeat or drop rows across pages), and an RLS table filters ``user_id`` first,
so ``user_id`` leads the index for those. This index would have been the wrong
shape before that sort was made deterministic — it is right *because* of it.
"""

from __future__ import annotations

from hashlib import blake2b

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.log.log import get_logger

logger = get_logger(__name__)

# PostgreSQL truncates identifiers at 63 bytes *silently*. Nothing in the
# datastore validates identifier length -- ``sanitize_identifier`` checks the
# character set only -- so two long table names sharing a prefix would produce
# one truncated index name between them, and the second table's creation would
# fail with a confusing "already exists". The digest of the *full* name keeps
# them apart no matter how long they are, or how much they share.
_MAX_IDENTIFIER_BYTES = 63
_DIGEST_CHARS = 8
_SUFFIX = "_listing"
_PREFIX = "ix_"
_STEM_BUDGET = _MAX_IDENTIFIER_BYTES - len(_PREFIX) - len(_SUFFIX) - _DIGEST_CHARS - 1

#: How long the lazy build may wait for the table lock, and how long it may
#: then hold it. Both are short on purpose: this runs inside a request, and
#: a build that cannot finish quickly should get out of the way rather than
#: stall the caller and every writer behind it.
_INDEX_LOCK_TIMEOUT_MS = 2_000
_INDEX_BUILD_TIMEOUT_MS = 15_000


def record_index_name(table_name: str) -> str:
    """A schema-unique, length-safe index name derived from *table_name*.

    Index names are schema-scoped and every pod owns its schema, so this needs
    to be unique only within one pod — no pod id is mixed in.
    """
    digest = blake2b(table_name.encode("utf-8"), digest_size=4).hexdigest()
    stem = table_name.encode("utf-8")[:_STEM_BUDGET].decode("utf-8", "ignore")
    return f"{_PREFIX}{stem}_{digest}{_SUFFIX}"


def record_index_sql(
    schema_name: str,
    table_name: str,
    *,
    primary_key_column: str,
    has_created_at: bool,
    enable_rls: bool,
) -> str | None:
    """``CREATE INDEX IF NOT EXISTS`` for *table_name*, or None if pointless.

    None when a table has no ``created_at`` and no RLS: the listing then orders
    by the primary key alone, which the primary key's own index already serves.
    """
    columns: list[str] = []
    if enable_rls:
        columns.append('"user_id"')
    if has_created_at:
        columns.append('"created_at" DESC')
    if not columns:
        return None
    columns.append(f'"{primary_key_column}" DESC')
    return (
        f'CREATE INDEX IF NOT EXISTS "{record_index_name(table_name)}" '
        f'ON "{schema_name}"."{table_name}" ({", ".join(columns)})'
    )


def drop_record_index_sql(schema_name: str, table_name: str) -> str:
    """Drop it by the same derived name, for a shape change.

    Toggling RLS changes which columns the index leads with, and the name does
    not encode the shape — deliberately, so the lazy ``IF NOT EXISTS`` path
    cannot leave a second index behind. A toggle therefore drops and recreates.
    """
    return f'DROP INDEX IF EXISTS "{schema_name}"."{record_index_name(table_name)}"'


async def primary_key_column_of(conn, schema_name: str, table_name: str) -> str:
    """The table's primary key column, read from the catalog.

    The RLS toggle is handed a table *name*, not the schema the API layer has,
    so the index it rebuilds has to ask the database what the key is. Falls
    back to ``id``, which is what ``create_table`` uses when none is declared.
    """
    result = await conn.execute(
        text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = to_regclass(:qualified) AND i.indisprimary"
        ),
        {"qualified": f'"{schema_name}"."{table_name}"'},
    )
    return result.scalar() or "id"


async def table_has_column(
    conn, schema_name: str, table_name: str, column_name: str
) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND column_name = :column)"
        ),
        {"schema": schema_name, "table": table_name, "column": column_name},
    )
    return bool(result.scalar())


async def ensure_record_index(
    engine,
    schema_name: str,
    table_name: str,
    *,
    primary_key_column: str,
    has_created_at: bool,
    enable_rls: bool,
    lock,
    memo: set[tuple[str, str]],
) -> None:
    """Create the listing index for a table that predates it.

    Every table created from now on gets the index at creation, but the ones
    already out there have none — and those are exactly the tables big enough
    for it to matter. A migration cannot reach them: no migration in this repo
    has ever iterated pod schemas, and one that did would still miss any pod
    dormant on the day it ran, the same reasoning ``pod_delivery`` records for
    its own backfill. So it happens on next access instead.

    ``CREATE INDEX IF NOT EXISTS`` under the schema's advisory lock, and
    best-effort: a read that works without the index must not start failing
    because the index could not be built. Memoised in *memo*, so this costs one
    statement per table per process rather than one per read.

    **Bounded, because this runs on a request.** A plain ``CREATE INDEX`` takes
    a SHARE lock for the whole build, which blocks writers, and the advisory
    lock above has no timeout of its own — so on the very tables this exists to
    help, the largest ones, the first caller after a deploy would wait out the
    build and hold writers behind it. The timeouts turn that into a fast
    failure and a warning: the read still works unindexed, and the *next*
    process to come along tries again. Deliberately not ``CONCURRENTLY``, which
    cannot run inside a transaction and so could not share this lock at all.
    """
    memo_key = (schema_name, table_name)
    if memo_key in memo:
        return
    index_sql = record_index_sql(
        schema_name,
        table_name,
        primary_key_column=primary_key_column,
        has_created_at=has_created_at,
        enable_rls=enable_rls,
    )
    if index_sql is None:
        memo.add(memo_key)
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"SET LOCAL lock_timeout = '{_INDEX_LOCK_TIMEOUT_MS}ms'")
            )
            await conn.execute(
                text(f"SET LOCAL statement_timeout = '{_INDEX_BUILD_TIMEOUT_MS}ms'")
            )
            await lock(conn, schema_name)
            await conn.execute(text(index_sql))
    except DBAPIError:
        # Marked ensured anyway. Retrying on every read would turn a
        # permissions or disk problem into an extra statement on the hottest
        # path, and the reads work without it.
        logger.warning(
            "datastore.record.index.degraded",
            schema_name=schema_name,
            table_name=table_name,
        )
    memo.add(memo_key)


async def ensure_listing_index_for(schema_manager, ctx) -> None:
    """Ensure the index for the table *ctx* describes.

    The ``has_created_at`` test is the same expression the default listing sort
    uses, deliberately literally: an index whose shape disagreed with the
    ORDER BY would be built, maintained on every write, and never used.
    """
    await schema_manager.ensure_record_index(
        ctx.schema_name,
        ctx.table_name,
        primary_key_column=ctx.primary_key_column,
        has_created_at=any(c.name == "created_at" for c in ctx.columns),
        enable_rls=ctx.enable_rls,
    )


async def rebuild_record_index(
    conn, schema_name: str, table_name: str, *, enable_rls: bool
) -> None:
    """Rebuild the listing index after an RLS toggle changed its shape.

    Toggling RLS changes what the listing filters on first, so the index that
    serves it leads with a different column. This is also the path that
    *materializes* ``user_id`` on an existing table, which makes it the one
    place a table can acquire the column the index needs to lead with —
    skipping it would leave exactly the tables that need an index without one.

    The name does not encode the shape (deliberately, so the lazy
    ``IF NOT EXISTS`` path cannot leave a second index behind), so a toggle has
    to drop and recreate rather than create alongside.
    """
    await conn.execute(text(drop_record_index_sql(schema_name, table_name)))
    index_sql = record_index_sql(
        schema_name,
        table_name,
        primary_key_column=await primary_key_column_of(conn, schema_name, table_name),
        has_created_at=await table_has_column(
            conn, schema_name, table_name, "created_at"
        ),
        enable_rls=enable_rls,
    )
    if index_sql:
        await conn.execute(text(index_sql))
