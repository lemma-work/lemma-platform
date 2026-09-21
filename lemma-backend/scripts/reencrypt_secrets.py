"""Re-encrypt every secret at rest onto the current primary key.

Drives key rotation and key-compromise recovery. It walks each column in
``app.core.crypto.rotation.REGISTRY``, decrypts every value with whatever key or
KEK version produced it, and writes it back under the current primary.

Dry-run is the default and reports what each column would do:

    uv run python scripts/reencrypt_secrets.py

Apply once the report looks right:

    uv run python scripts/reencrypt_secrets.py --apply

Idempotent: a row already under the primary key is skipped, so a second pass
reports zero migrated. ``--force`` overrides that, which is what re-wraps KMS
DEKs after the KEK itself has been rotated to a new version -- the ciphertext is
already under the primary key id there, so nothing else would touch it.

**The old key must still be resolvable when this runs.** Under the static
provider that means the retired key stays in ``SECRET_ENCRYPTION_KEYSET`` until
after the walk; the cipher falls back to a ``MultiFernet`` over every configured
candidate to read a value whose key id it no longer recognises, and a keyset
edited down to one key first leaves those rows unreadable. Rotate the keyset,
run this, then drop the retired key.

The reported ``key_provider`` is there to be checked before ``--apply``: it says
which provider the primary key is actually coming from, so a run that meant to
land on KMS does not silently re-encrypt the estate under a static key.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.crypto.rotation import REGISTRY, EncryptedColumn, reencrypt_all
from app.core.infrastructure.db.session import async_session_maker, close_engine


def _select_columns(labels: list[str]) -> list[EncryptedColumn]:
    """Resolve ``--column`` labels, naming the valid ones when one is wrong."""
    if not labels:
        return list(REGISTRY)
    by_label = {col.label: col for col in REGISTRY}
    unknown = [label for label in labels if label not in by_label]
    if unknown:
        raise SystemExit(
            f"unknown column(s): {', '.join(sorted(unknown))}. "
            f"Registered: {', '.join(sorted(by_label))}"
        )
    return [by_label[label] for label in labels]


async def _run(
    *,
    apply_changes: bool,
    force: bool,
    batch_size: int,
    columns: list[EncryptedColumn],
) -> dict[str, object]:
    cipher = get_secret_cipher()
    try:
        async with async_session_maker() as session:
            report = await reencrypt_all(
                session,
                cipher,
                force=force,
                batch_size=batch_size,
                dry_run=not apply_changes,
                columns=columns,
            )
    finally:
        # A script that leaves the engine's pool open exits on the asyncio
        # cleanup rather than on its own result, which reads as a crash after a
        # successful rotation.
        await close_engine()

    return {
        "applied": apply_changes,
        "forced": force,
        "key_provider": settings.effective_secret_key_provider(),
        "columns": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the re-encrypted values. Without it, only reports what would change.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-encrypt rows already under the primary key, to re-wrap KMS DEKs "
        "under a freshly rotated KEK version.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows read per keyset page (default: 500).",
    )
    parser.add_argument(
        "--column",
        action="append",
        default=[],
        metavar="LABEL",
        help="Limit to one registered column, repeatable. Default: every column.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        _run(
            apply_changes=args.apply,
            force=args.force,
            batch_size=args.batch_size,
            columns=_select_columns(args.column),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
