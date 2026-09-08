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


async def _count(session, table: str) -> int:
    result = await session.execute(
        text(f"SELECT count(*) FROM {table} WHERE kind = :kind"),  # noqa: S608
        {"kind": _OLD_KIND},
    )
    return int(result.scalar() or 0)


async def retag(*, dry_run: bool) -> dict[str, int]:
    """Returns the row count touched per table."""
    touched: dict[str, int] = {}
    async with async_session_maker() as session:
        try:
            for table in _RETAG_TABLES:
                touched[table] = await _count(session, table)
                if not dry_run and touched[table]:
                    await session.execute(
                        text(  # noqa: S608 - table names are a fixed literal allow-list
                            f"UPDATE {table} SET kind = :new WHERE kind = :old"
                        ),
                        {"new": _NEW_KIND, "old": _OLD_KIND},
                    )
            for table in _DELETE_TABLES:
                touched[table] = await _count(session, table)
                if not dry_run and touched[table]:
                    await session.execute(
                        text(  # noqa: S608 - table names are a fixed literal allow-list
                            f"DELETE FROM {table} WHERE kind = :old"
                        ),
                        {"old": _OLD_KIND},
                    )
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        except Exception:
            await session.rollback()
            raise
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
