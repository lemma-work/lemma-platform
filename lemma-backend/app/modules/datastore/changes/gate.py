"""When record writes switch from the datastore outbox to the change log.

A write appends to its pod's ``reserved_changes``, which exists only in schemas
at pod-schema version 3, and a writer migrates its own schema there on first
use. That is cheap for a schema that holds no search chunks -- a new pod's, or
one nothing has indexed -- and not for one whose chunks the version-2 step
rewrites, which no record write should wait on. So the switch waits until no
schema the runner has touched is below version 3 and no untouched schema holds
chunks: the background backfill's work. Until then writes keep staging outbox
events, and the converter (``converter``) moves those into the log -- so a
pod's subscribers see every change either way.

The switch is per process and one-way: once nothing blocks it, it
stays on, so a process never flips back to the outbox because a schema was
created a moment ago and has not been counted yet.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    VERSIONS_TABLE,
)
from app.modules.datastore.infrastructure.query_role import POD_SCHEMA_RE
from app.modules.datastore.infrastructure.session import get_datastore_engine

#: How often a process that has not switched asks again.
RECHECK_SECONDS = 30.0

_switched = False
_next_check = 0.0


async def change_log_active() -> bool:
    mode = datastore_settings.datastore_change_log_mode
    if mode == "off":
        return False
    if mode == "on":
        return True
    global _switched, _next_check
    if _switched:
        return True
    now = time.monotonic()
    if now < _next_check:
        return False
    _next_check = now + RECHECK_SECONDS
    if await schemas_blocking_switch(get_datastore_engine()) == 0:
        _switched = True
    return _switched


async def schemas_blocking_switch(engine: AsyncEngine) -> int:
    """Pod schemas a record write could not cheaply bring to the change log."""
    async with engine.connect() as conn:
        if not await conn.scalar(
            text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL")
        ):
            versions = "SELECT NULL::text AS schema_name, 0 AS version WHERE false"
        else:
            versions = f"SELECT schema_name, version FROM {VERSIONS_TABLE}"
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_namespace n "
                f"LEFT JOIN ({versions}) v ON v.schema_name = n.nspname "
                "WHERE n.nspname ~ :pattern AND ("
                "(v.version IS NOT NULL AND v.version < :version) OR "
                "(v.version IS NULL AND to_regclass("
                "quote_ident(n.nspname) || '.reserved_chunks') IS NOT NULL))"
            ),
            {"pattern": f"^{POD_SCHEMA_RE.pattern}$", "version": CHANGE_LOG_VERSION},
        )
    return int(count or 0)


def reset_change_log_gate() -> None:
    """Forget the switch: for tests that change the mode or the database."""
    global _switched, _next_check
    _switched = False
    _next_check = 0.0


__all__ = [
    "RECHECK_SECONDS",
    "change_log_active",
    "reset_change_log_gate",
    "schemas_blocking_switch",
]
