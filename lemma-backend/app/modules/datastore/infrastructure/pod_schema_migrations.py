"""Versioned DDL for the search tables inside each pod schema.

Pod schemas live in the datastore database, which Alembic does not manage, and
there is one per pod. Their search tables used to be set up by
``ensure_schema`` -- ten idempotent DDL statements behind a database-wide
advisory lock -- on the first search, index or removal in each process. Every
process paid it once per pod, searches queued behind chunk writes while holding
the lock every other pod's setup needed, and a pod that was never searched in
a given process never had its shape changed at all.

So the shape is versioned. Each pod schema has one row in
``public.lemma_pod_schema_versions`` saying which version of the DDL below it
carries, and :func:`migrate_pod_schema` brings one schema up to a version under
that schema's own advisory lock: idempotent, safe for two runners at once, and
never touching another pod. Writers (indexing) migrate inline on first use;
everything else is migrated by ``pod_schema.migrate`` work items that a
background backfill enqueues for every existing schema. Readers never run DDL:
they ask :func:`pod_schema_version` and read whichever shape is there.

A new version is a new entry in ``_STEPS``. Later work gates on
:func:`all_pod_schemas_at_least` before relying on it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.datastore.changes.schema import (
    create_change_log,
    create_global_tables,
)
from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure.pod_schema_catalog import (
    bootstrap_pod_schema,
)
from app.modules.datastore.infrastructure.query_role import (
    POD_SCHEMA_RE,
    withdraw_shared_role,
)

logger = get_logger(__name__)

#: The version every pod schema is migrated to by this release.
POD_SCHEMA_LATEST = 4

#: Version 1: ``reserved_chunks`` with its path, text and vector indexes.
CHUNKS_VERSION = 1
#: Version 2: ``search_files`` (one row per indexed file), a stored ``tsv``
#: column, and a unique ``(file_id, chunk_index)``.
SEARCH_FILES_VERSION = 2
#: Version 3: the record change log (``reserved_changes``) and its durable
#: subscribers (``reserved_change_subscribers``); see ``datastore.changes``.
CHANGE_LOG_VERSION = 3
#: Version 4: the query role every pod shared loses its grants on this schema;
#: queries run as the pod's own role (``query_role``).
POD_QUERY_ROLE_VERSION = 4

VERSIONS_TABLE = "public.lemma_pod_schema_versions"

#: Serialises the database-wide catalog writes (``CREATE EXTENSION``, the
#: versions table). The same constant the e2e harness takes before it creates
#: the extension, so the two cannot race on ``pg_extension``.
_GLOBAL_DDL_LOCK_KEY = 0x6C656D6D61

type _Step = Callable[[AsyncConnection, str], Awaitable[None]]

_globals_ready = False


def schema_name_for_pod(pod_id: object) -> str:
    return f"pod_{str(pod_id).replace('-', '_')}"


def pod_id_for_schema(schema_name: str) -> str:
    """The pod id a pod schema belongs to, as a canonical uuid string."""
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    raw = schema_name.removeprefix("pod_").replace("_", "")
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def forget_all() -> None:
    """Forget every schema and the global setup: for tests that recreate the database."""
    global _globals_ready
    pod_schema_memo.forget_all()
    _globals_ready = False


async def _ensure_globals(engine: AsyncEngine) -> None:
    """The extension and the versions table: once per process, under one lock."""
    global _globals_ready
    if _globals_ready:
        return
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _GLOBAL_DDL_LOCK_KEY}
        )
        # Azure Database for PostgreSQL checks CREATE EXTENSION privileges even
        # when IF NOT EXISTS would make it a no-op, and the runtime role is not
        # an extension admin there, so look before asking.
        if not await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ):
            await conn.execute(text("CREATE EXTENSION vector"))
        await conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {VERSIONS_TABLE} ("
                "schema_name text PRIMARY KEY, "
                "version integer NOT NULL, "
                "migrated_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        await create_global_tables(conn)
    _globals_ready = True


async def _versions_table_exists(conn: AsyncConnection) -> bool:
    return bool(
        await conn.scalar(text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL"))
    )


async def _schema_exists(conn: AsyncConnection, schema_name: str) -> bool:
    return bool(
        await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :name)"),
            {"name": schema_name},
        )
    )


async def _read_version(conn: AsyncConnection, schema_name: str) -> int:
    if not await _versions_table_exists(conn):
        return 0
    version = await conn.scalar(
        text(f"SELECT version FROM {VERSIONS_TABLE} WHERE schema_name = :name"),
        {"name": schema_name},
    )
    return int(version or 0)


async def pod_schema_version(engine: AsyncEngine, schema_name: str) -> int:
    """Which version of the search DDL ``schema_name`` carries; 0 for none.

    A read, never DDL: search calls it and reads whichever shape is there.
    """
    if pod_schema_memo.known_version(schema_name) >= POD_SCHEMA_LATEST:
        return POD_SCHEMA_LATEST
    async with engine.connect() as conn:
        version = await _read_version(conn, schema_name)
    if version >= POD_SCHEMA_LATEST:
        pod_schema_memo.remember(schema_name, version)
    return version


async def migrate_pod_schema(
    engine: AsyncEngine,
    schema_name: str,
    *,
    target: int = POD_SCHEMA_LATEST,
    create: bool = True,
) -> int:
    """Bring one pod schema up to ``target``; returns the version it is at.

    Each step commits on its own with the schema's version row, under the
    schema's advisory lock, so a runner that dies mid-way leaves the schema at
    the last whole step and the next runner resumes there. Two runners on one
    schema serialise on the lock and the second finds nothing to do.

    ``create=False`` leaves a schema that does not exist alone and returns 0:
    the background backfill must not resurrect the schema of a pod deleted
    between listing it and migrating it.
    """
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    if target > POD_SCHEMA_LATEST:
        raise ValueError(f"no pod schema version {target}")
    known = pod_schema_memo.known_version(schema_name)
    if known >= target:
        return known
    await _ensure_globals(engine)
    version = 0
    for step_version in range(1, target + 1):
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:schema_name))"),
                {"schema_name": schema_name},
            )
            version = await _read_version(conn, schema_name)
            if version >= step_version:
                continue
            if not await _schema_exists(conn, schema_name):
                if not create:
                    return 0
                await bootstrap_pod_schema(conn, schema_name, grant=False)
            await _STEPS[step_version](conn, schema_name)
            await conn.execute(
                text(
                    f"INSERT INTO {VERSIONS_TABLE} (schema_name, version) "
                    "VALUES (:name, :version) "
                    "ON CONFLICT (schema_name) DO UPDATE "
                    "SET version = EXCLUDED.version, migrated_at = now()"
                ),
                {"name": schema_name, "version": step_version},
            )
            version = step_version
    if version >= POD_SCHEMA_LATEST:
        pod_schema_memo.remember(schema_name, version)
    return version


async def ensure_change_log(engine: AsyncEngine, schema_name: str) -> None:
    """A record write's readiness check: ``schema_name`` carries the change log.

    After the first write in a process, a memo hit. Otherwise one read without
    a lock; only a schema neither migrated to version 3 nor already holding the
    change log runs DDL here, and then only the change log's two small tables
    -- never the version-2 step, whose rewrite of the search chunks grows with
    the pod and is the background backfill's (``pod_schema.migrate``) work. A
    schema with no search chunks yet is cheap all the way, and is migrated
    properly.
    """
    if pod_schema_memo.change_log_ready(schema_name):
        return
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    async with engine.connect() as conn:
        version = await _read_version(conn, schema_name)
        has_log, has_chunks = (
            await conn.execute(
                text(
                    "SELECT to_regclass(:changes) IS NOT NULL, "
                    "to_regclass(:chunks) IS NOT NULL"
                ),
                {
                    "changes": f'"{schema_name}".reserved_changes',
                    "chunks": f'"{schema_name}".reserved_chunks',
                },
            )
        ).one()
    if version >= POD_SCHEMA_LATEST:
        pod_schema_memo.remember(schema_name, version)
        return
    if version >= CHANGE_LOG_VERSION or has_log:
        pod_schema_memo.remember_change_log(schema_name)
        return
    if not has_chunks:
        # All the way, not only to the change log: every step after the chunk
        # rewrite is catalog-sized, so stopping short would only leave this
        # schema for the backfill to come back to.
        await migrate_pod_schema(engine, schema_name)
        return
    logger.info(
        "datastore.pod_schema_migrations.change_log_ahead_of_backfill.observed",
        schema_name=schema_name,
        version=version,
    )
    await _ensure_globals(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:schema_name))"),
            {"schema_name": schema_name},
        )
        await create_change_log(conn, schema_name)
    pod_schema_memo.remember_change_log(schema_name)


async def schemas_below(engine: AsyncEngine, version: int) -> int:
    """How many pod schemas are not yet at ``version``."""
    async with engine.connect() as conn:
        if not await _versions_table_exists(conn):
            versions_join = "SELECT NULL::text AS schema_name, 0 AS version WHERE false"
        else:
            versions_join = f"SELECT schema_name, version FROM {VERSIONS_TABLE}"
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_namespace n "
                f"LEFT JOIN ({versions_join}) v ON v.schema_name = n.nspname "
                "WHERE n.nspname ~ :pattern AND COALESCE(v.version, 0) < :version"
            ),
            {"pattern": f"^{POD_SCHEMA_RE.pattern}$", "version": version},
        )
    return int(count or 0)


async def all_pod_schemas_at_least(engine: AsyncEngine, version: int) -> bool:
    """The readiness gate: every pod schema carries ``version`` or later.

    A change that needs the new shape everywhere -- a write path that stops
    maintaining the old one -- switches only once this is true.
    """
    return await schemas_below(engine, version) == 0


async def list_schemas_below(
    engine: AsyncEngine, version: int, *, after: str, limit: int
) -> tuple[list[str], str | None]:
    """One keyset page of pod schemas below ``version``, and the next cursor.

    The cursor walks every pod schema in name order, not just the ones
    returned, so a page full of migrated schemas still moves forward.
    """
    async with engine.connect() as conn:
        names = list(
            (
                await conn.execute(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname ~ :pattern AND nspname > :after "
                        "ORDER BY nspname LIMIT :limit"
                    ),
                    {
                        "pattern": f"^{POD_SCHEMA_RE.pattern}$",
                        "after": after,
                        "limit": limit,
                    },
                )
            ).scalars()
        )
        if not names:
            return [], None
        migrated: dict[str, int] = {}
        if await _versions_table_exists(conn):
            rows = await conn.execute(
                text(
                    f"SELECT schema_name, version FROM {VERSIONS_TABLE} "
                    "WHERE schema_name = ANY(:names)"
                ),
                {"names": names},
            )
            migrated = {str(name): int(v) for name, v in rows.all()}
    below = [name for name in names if migrated.get(name, 0) < version]
    cursor = names[-1] if len(names) == limit else None
    return below, cursor


# --- The steps ---------------------------------------------------------------


async def _v1_chunks(conn: AsyncConnection, schema: str) -> None:
    """The chunk table as it has existed since search shipped.

    Every statement is ``IF NOT EXISTS``, because a schema reaching version 1
    for the first time usually already has all of it, built by the old lazy
    setup; this only records that it does.
    """
    dim = settings.embedding_dimension
    q = f'"{schema}"'
    await conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {q}.reserved_chunks ("
            "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
            "chunk_index INTEGER NOT NULL, "
            "file_id UUID NOT NULL, "
            "content TEXT NOT NULL, "
            f"embedding vector({dim}) NOT NULL, "
            "chunk_metadata JSONB, "
            "created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW())"
        )
    )
    for statement in (
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_file_id "
            f"ON {q}.reserved_chunks (file_id)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_path "
            f"ON {q}.reserved_chunks ((chunk_metadata ->> 'path'))"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_parent_path "
            f"ON {q}.reserved_chunks ((chunk_metadata ->> 'parent_path'))"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_path_pattern "
            f"ON {q}.reserved_chunks ((chunk_metadata ->> 'path') text_pattern_ops)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_text_search "
            f"ON {q}.reserved_chunks USING GIN (("
            "setweight(to_tsvector('english', COALESCE(chunk_metadata ->> 'path', '')), 'B') || "
            "setweight(to_tsvector('english', content), 'A')))"
        ),
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_embedding_hnsw",
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_embedding_ivfflat",
    ):
        await conn.execute(text(statement))
    # Half precision: about half the index memory of a full-precision HNSW
    # index for a negligible recall loss. A savepoint so a pgvector too old for
    # halfvec costs the index, not the table -- search still works by scan.
    savepoint = await conn.begin_nested()
    try:
        await conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_embedding_halfvec "
                f"ON {q}.reserved_chunks "
                f"USING hnsw ((embedding::halfvec({dim})) halfvec_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        )
    except Exception as exc:  # noqa: BLE001 - logged; the table still serves
        await savepoint.rollback()
        # An install without pgvector's halfvec is a deployment choice and
        # would otherwise warn once per pod; anything else is a fault someone
        # has to see, at warning, because production drops debug.
        if not _extension_missing(exc):
            logger.warning(
                "datastore.pod_schema_migrations.vector_index_build.degraded",
                schema_name=schema,
                exc_info=True,
            )
    else:
        await savepoint.commit()


def _extension_missing(exc: Exception) -> bool:
    message = str(exc).lower()
    return "extension" in message and (
        "does not exist" in message or "not installed" in message
    )


async def _v2_search_files(conn: AsyncConnection, schema: str) -> None:
    """One row per indexed file; chunks reference it.

    Path lived in every chunk's JSON, so a rename rewrote every chunk of every
    file under the folder, and the text-search vector was recomputed per hit
    from an expression. Now the path is one row per file (``path_tsv`` ranks
    it), the chunk's own vector is stored, and ``(file_id, chunk_index)`` is
    unique so a re-index is a replacement rather than an accumulation.
    """
    q = f'"{schema}"'
    statements = (
        (
            f"CREATE TABLE IF NOT EXISTS {q}.search_files ("
            "file_id UUID PRIMARY KEY, "
            "path TEXT NOT NULL, "
            "parent_path TEXT NOT NULL DEFAULT '/', "
            "sha256 TEXT, "
            "chunk_count INTEGER NOT NULL DEFAULT 0, "
            "indexed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(), "
            "path_tsv tsvector GENERATED ALWAYS AS "
            "(setweight(to_tsvector('english', path), 'B')) STORED)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_search_files_path_pattern "
            f"ON {q}.search_files (path text_pattern_ops)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_search_files_parent_path "
            f"ON {q}.search_files (parent_path)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_search_files_path_tsv "
            f"ON {q}.search_files USING GIN (path_tsv)"
        ),
        # Duplicates could exist: nothing prevented two concurrent index runs
        # of one file from both inserting. Keep the newest of each pair.
        (
            f"DELETE FROM {q}.reserved_chunks a USING {q}.reserved_chunks b "
            "WHERE a.file_id = b.file_id AND a.chunk_index = b.chunk_index "
            "AND (a.created_at, a.id) < (b.created_at, b.id)"
        ),
        # Chunks written before this version carry their file's path; one
        # row per file is rebuilt from them.
        (
            f"INSERT INTO {q}.search_files "
            "(file_id, path, parent_path, chunk_count, indexed_at) "
            "SELECT file_id, "
            "COALESCE(max(chunk_metadata ->> 'path'), ''), "
            "COALESCE(max(chunk_metadata ->> 'parent_path'), '/'), "
            "count(*), COALESCE(max(created_at), now()) "
            f"FROM {q}.reserved_chunks GROUP BY file_id "
            "ON CONFLICT (file_id) DO NOTHING"
        ),
        (
            f"ALTER TABLE {q}.reserved_chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
            "GENERATED ALWAYS AS (setweight(to_tsvector('english', content), 'A')) STORED"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS ix_reserved_chunks_tsv "
            f"ON {q}.reserved_chunks USING GIN (tsv)"
        ),
        (
            f"CREATE UNIQUE INDEX IF NOT EXISTS ux_reserved_chunks_file_chunk "
            f"ON {q}.reserved_chunks (file_id, chunk_index)"
        ),
        # The JSON path indexes and the per-row expression index only served
        # the version-1 queries; keeping them would charge every chunk write.
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_text_search",
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_path",
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_parent_path",
        f"DROP INDEX IF EXISTS {q}.ix_reserved_chunks_path_pattern",
    )
    for statement in statements:
        await conn.execute(text(statement))
    has_fk = await conn.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = 'fk_reserved_chunks_search_file' "
            "AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = :s))"
        ),
        {"s": schema},
    )
    if not has_fk:
        await conn.execute(
            text(
                f"ALTER TABLE {q}.reserved_chunks "
                "ADD CONSTRAINT fk_reserved_chunks_search_file "
                f"FOREIGN KEY (file_id) REFERENCES {q}.search_files (file_id) "
                "ON DELETE CASCADE"
            )
        )


_STEPS: dict[int, _Step] = {
    CHUNKS_VERSION: _v1_chunks,
    SEARCH_FILES_VERSION: _v2_search_files,
    CHANGE_LOG_VERSION: create_change_log,
    POD_QUERY_ROLE_VERSION: withdraw_shared_role,
}
if sorted(_STEPS) != list(range(1, POD_SCHEMA_LATEST + 1)):
    raise RuntimeError("pod schema steps must run 1..POD_SCHEMA_LATEST with no gap")
