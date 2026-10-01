"""Fill in ``accounts.external_ref`` for accounts connected before it existed.

The tenant each account speaks for -- a Slack ``team.id``, an Atlassian
``cloud_id``, a Teams ``tid``, a Composio ``connection_id`` -- has always been
in the credentials. Those are a vault secret per account, so this cannot be a
SQL migration: reading them needs the application's vault keys.

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
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.connectors.domain.install_binding import resolve_external_ref
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    account_scope,
)
from app.modules.vault.contracts import Revealed, VaultError, vault_for

_BATCH = 500


async def _run(apply_changes: bool) -> dict[str, object]:
    filled: Counter[str] = Counter()
    corrected: Counter[str] = Counter()
    absent: Counter[str] = Counter()
    unreadable: Counter[str] = Counter()
    scanned = 0

    async with async_session_maker() as session:
        # Keyset, not OFFSET. OFFSET makes the database walk and discard every
        # row already seen, so the scan is quadratic in the table -- and this
        # runs over `accounts`, which is exactly the table expected to be large
        # enough to need batching in the first place. Ordering by `id` is what
        # makes the cursor work, and the primary key index already provides it.
        after: UUID | None = None
        while True:
            query = select(Account).order_by(Account.id).limit(_BATCH)
            if after is not None:
                query = query.where(Account.id > after)
            rows = (await session.execute(query)).scalars().all()
            if not rows:
                break
            after = rows[-1].id
            secrets = await _reveal_page(session, rows)

            for account in rows:
                scanned += 1
                connector_id = account.connector_id
                secret = secrets.get(account.id)
                if secret is None:
                    # A credential we cannot read is a key-rotation or corruption
                    # problem of its own. Counting it and moving on beats aborting
                    # a backfill over one row.
                    if account.credentials_secret_id is not None:
                        unreadable[connector_id] += 1
                    else:
                        absent[connector_id] += 1
                    continue
                credentials = secret.json()

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


async def _reveal_page(session, rows) -> dict[UUID, Revealed]:
    """The page's credentials by account id, in one vault read where possible.

    Falls back to one read per row when the batch refuses, so a single
    unreadable secret is counted as that and does not sink the page.
    """
    vault = vault_for(session)
    owned = {
        account.credentials_secret_id: account
        for account in rows
        if account.credentials_secret_id is not None
    }
    expected = {
        secret_id: account_scope(account.organization_id, account.user_id)
        for secret_id, account in owned.items()
    }
    try:
        revealed = await vault.reveal_many(
            expected, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )
    except VaultError:
        revealed = {}
        for secret_id, scope in expected.items():
            try:
                revealed[secret_id] = await vault.reveal(
                    secret_id, expect=scope, purpose=ACCOUNT_CREDENTIALS_PURPOSE
                )
            except VaultError:
                continue
    return {owned[secret_id].id: secret for secret_id, secret in revealed.items()}


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
