"""One-shot release step: move every stored `package` row onto `http`.

**Run this once, immediately after deploying the release that removes the
`package` connector kind, and before the catalog import.**

`ConnectorKind` no longer has a `package` member, and `AuthConfigEntity.kind` is
a strict enum, so a row still holding the string raises
``'package' is not a valid ConnectorKind`` on *every* read of that install.
Until this runs, any connector installed before the upgrade is unreadable --
which for a deployment with agent surfaces means Teams, WhatsApp, Telegram and
Resend bots stop resolving their credentials.

There is deliberately no Alembic migration. The catalog has never been managed
through migrations, and this is catalog data; the trade is that the fix is a
manual step rather than an ordered one, so it has to be run rather than
remembered.

What it does, and why each table is treated differently:

* ``auth_configs`` is **retagged, never deleted**. The OAuth app, client id,
  client secret and token are all unchanged -- only the execution route moved --
  and ``connector_accounts`` has a foreign key onto it, so deleting a row would
  disconnect a person who has nothing wrong with their install.
* ``connector_triggers`` is **retagged, never deleted**, and this one is the
  sharp edge: ``schedules.connector_trigger_id`` is a foreign key with
  ``ON DELETE SET NULL``, so deleting a trigger silently nulls every webhook
  schedule bound to it and the only symptom is a schedule that stopped firing.
  The exception is a package trigger whose ``http`` twin (same connector and
  event type) already exists: the unique index forbids the retag, so its
  schedules are repointed to the twin and then the duplicate is deleted.
* ``connector_operations`` is **deleted**. Nothing references an operation row,
  and the catalog import rebuilds the whole set from
  ``lemma_apps_config.json`` -- so the surviving names come back tagged `http`
  and the ones no longer curated correctly do not.

Idempotent: every statement is filtered on ``kind = 'package'``, so a second run
reports zero and changes nothing.

Usage::

    uv run python scripts/retag_package_installs.py --dry-run
    uv run python scripts/retag_package_installs.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.infrastructure.db.session import async_session_maker  # noqa: E402

_OLD_KIND = "package"
_NEW_KIND = "http"

# Ordered so the read-only count of what will change reads the same way in a
# dry run as the writes do in a real one.
_RETAG_TABLES = ("auth_configs", "connector_triggers")
_DELETE_TABLES = ("connector_operations",)


# A package trigger whose http twin (same connector, same event) already exists
# cannot be retagged: the unique index on (connector_id, kind, event_type) would
# reject the UPDATE and roll back the whole run. Those duplicates are folded into
# the twin instead -- schedules repointed first, because the foreign key is
# ON DELETE SET NULL and deleting a bound trigger would silently unbind them.
_TWIN_JOIN = """
    connector_triggers p
    JOIN connector_triggers h
      ON h.connector_id = p.connector_id
     AND h.event_type = p.event_type
     AND h.kind = :new
    WHERE p.kind = :old
"""


async def _execute(session, sql: str) -> int:
    result = await session.execute(text(sql), {"new": _NEW_KIND, "old": _OLD_KIND})
    return int(result.rowcount or 0)


async def retag_in_session(session) -> dict[str, int]:
    """Every write, in order, inside the caller's transaction; row counts back.

    The caller commits or rolls back. A dry run is these same statements rolled
    back, so its counts are exactly what a real run would do -- including the
    retag count net of the duplicates folded away before it.
    """
    touched: dict[str, int] = {}
    touched["repointed"] = await _execute(
        session,
        "UPDATE schedules s SET connector_trigger_id = twin.h_id "
        f"FROM (SELECT p.id AS p_id, h.id AS h_id FROM {_TWIN_JOIN}) twin "
        "WHERE s.connector_trigger_id = twin.p_id",
    )
    touched["deduplicated"] = await _execute(
        session,
        f"DELETE FROM connector_triggers WHERE id IN (SELECT p.id FROM {_TWIN_JOIN})",
    )
    for table in _RETAG_TABLES:
        touched[table] = await _execute(
            session,
            # S608: table names are a fixed literal allow-list
            f"UPDATE {table} SET kind = :new WHERE kind = :old",  # noqa: S608
        )
    for table in _DELETE_TABLES:
        touched[table] = await _execute(
            session,
            f"DELETE FROM {table} WHERE kind = :old",  # noqa: S608
        )
    return touched


async def retag(*, dry_run: bool) -> dict[str, int]:
    """Returns the row count touched per step."""
    async with async_session_maker() as session:
        # An exception leaves the transaction uncommitted, and closing the
        # session rolls it back: nothing is written unless every step succeeded.
        touched = await retag_in_session(session)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    return touched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    args = parser.parse_args()

    touched = asyncio.run(retag(dry_run=args.dry_run))
    verb = "would change" if args.dry_run else "changed"
    print(
        f"connector_triggers: {verb} {touched.get('deduplicated', 0)} package "
        "duplicate(s) of an existing http trigger (delete)"
    )
    print(f"schedules: {verb} {touched.get('repointed', 0)} row(s) (repoint to twin)")
    for table in (*_RETAG_TABLES, *_DELETE_TABLES):
        action = "retag" if table in _RETAG_TABLES else "delete"
        print(f"{table}: {verb} {touched.get(table, 0)} row(s) ({action})")
    if not any(touched.values()):
        print(f"Nothing left on kind='{_OLD_KIND}'.")
    elif args.dry_run:
        print("\nRe-run without --dry-run to apply, then run the catalog import.")
    else:
        print("\nNow run: uv run python scripts/import_connector_catalog.py")


if __name__ == "__main__":
    main()
