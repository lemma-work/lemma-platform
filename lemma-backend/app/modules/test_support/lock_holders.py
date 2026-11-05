"""Who holds what a test's database reset is waiting on.

Each test starts by emptying the tables (``DatabaseManager.truncate_all``),
under a ``lock_timeout``. When something earlier in the same worker left a
transaction open -- a leaked session, a background task stuck mid-write -- every
later test fails its setup on that lock until the run ends, and the error names
only the reset's own ``DELETE``. This names the sessions holding a transaction
open in that database: how long, which locks, and the last statement each ran,
which is usually enough to find the code that left it open. ``idle in
transaction`` is a session somebody forgot to end; ``active`` with a wait event
is one stuck on a call while it holds what it wrote.
"""

from __future__ import annotations

import warnings

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_HOLDERS = """
SELECT a.pid,
       a.application_name,
       a.state,
       date_trunc('second', now() - a.xact_start)::text AS open_for,
       date_trunc('second', now() - a.state_change)::text AS in_state_for,
       coalesce(a.wait_event_type || ':' || a.wait_event, '') AS waiting_on,
       string_agg(DISTINCT coalesce(c.relname, l.locktype) || ':' || l.mode, ', ')
           AS locks,
       left(a.query, 400) AS query
FROM pg_stat_activity AS a
LEFT JOIN pg_locks AS l ON l.pid = a.pid AND l.granted
LEFT JOIN pg_class AS c ON c.oid = l.relation
WHERE a.datname = current_database()
  AND a.pid <> pg_backend_pid()
  AND a.xact_start IS NOT NULL
GROUP BY a.pid, a.application_name, a.state, a.xact_start, a.state_change,
         a.wait_event_type, a.wait_event, a.query
ORDER BY a.xact_start
"""


async def describe_lock_holders(database_url: str) -> str:
    """Every other session with a transaction open in this database, oldest
    first, one per line; or a line saying there is none."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            # pg_stat_activity is a snapshot per transaction: read it fresh.
            await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
            rows = (await conn.execute(text(_HOLDERS))).mappings().all()
    finally:
        await engine.dispose()
    if not rows:
        return "lock holders: no other session has a transaction open"
    return "lock holders, oldest first:\n" + "\n".join(
        f"  pid={row['pid']} app={row['application_name']!r} "
        f"state={row['state']} (for {row['in_state_for']}) "
        f"open_for={row['open_for']} waiting_on={row['waiting_on'] or '-'} "
        f"locks=[{row['locks'] or ''}] last_query={row['query']!r}"
        for row in rows
    )


class DatabaseResetWaited(UserWarning):
    """A test's database reset waited on a lock another session held."""


def warn_lock_holders(described: str) -> None:
    """Say who the reset waited on, in the run's warnings summary: shown for a
    test that later passed (and only ran slow) as much as for one that failed."""
    warnings.warn(
        f"database reset hit a lock timeout; {described}",
        DatabaseResetWaited,
        stacklevel=2,
    )
