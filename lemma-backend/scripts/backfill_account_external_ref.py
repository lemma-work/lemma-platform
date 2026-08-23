"""Fill in ``accounts.external_ref`` for accounts connected before it existed.

The tenant each account speaks for -- a Slack ``team.id``, an Atlassian
``cloud_id``, a Teams ``tid``, a Composio ``connection_id`` -- has always been
in the credentials. Those are encrypted at rest, so this cannot be a SQL
migration: reading them needs the application's cipher.

Dry-run is the default and prints a per-connector summary:

    uv run python scripts/backfill_account_external_ref.py

Apply once the summary looks right:

    uv run python scripts/backfill_account_external_ref.py --apply

Idempotent, and safe to re-run: it only ever writes a row whose stored value
differs from what the credentials say, so a second pass reports zero changes.
An account whose credentials carry no tenant is left null, which is the correct
answer for most connectors rather than a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from sqlalchemy import select

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.modules.connectors.domain.install_binding import resolve_external_ref
from app.modules.connectors.infrastructure.models.account import Account

_BATCH = 500


async def _run(apply_changes: bool) -> dict[str, object]:
    cipher = get_secret_cipher()
    filled: Counter[str] = Counter()
    corrected: Counter[str] = Counter()
    absent: Counter[str] = Counter()
    unreadable: Counter[str] = Counter()
    scanned = 0

    async with async_session_maker() as session:
        offset = 0
        while True:
            rows = (
                (
                    await session.execute(
                        select(Account)
                        .order_by(Account.id)
                        .offset(offset)
                        .limit(_BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            offset += len(rows)

            for account in rows:
                scanned += 1
                connector_id = account.connector_id
                try:
                    credentials = await cipher.decrypt_json_async(account.credentials)
                except Exception:
                    # A credential we cannot read is a key-rotation or corruption
                    # problem of its own. Counting it and moving on beats aborting
                    # a backfill over one row.
                    unreadable[connector_id] += 1
                    continue

                resolved = resolve_external_ref(connector_id, credentials)
                if resolved is None:
                    absent[connector_id] += 1
                    continue
                if account.external_ref == resolved:
                    continue
                if account.external_ref is None:
                    filled[connector_id] += 1
                else:
                    corrected[connector_id] += 1
                if apply_changes:
                    account.external_ref = resolved

            if apply_changes:
                await session.commit()

    return {
        "applied": apply_changes,
        "scanned": scanned,
        "filled": dict(filled),
        "corrected": dict(corrected),
        "no_tenant": dict(absent),
        "unreadable_credentials": dict(unreadable),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the resolved values. Without it, only reports what would change.",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.apply)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
