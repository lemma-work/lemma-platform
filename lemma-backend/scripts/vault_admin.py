"""Operate the secrets vault on a running deployment.

Run where the database and the root key are reachable, with the backend's own
configuration (the same ``SECRET_KEY_PROVIDER`` / ``GCP_KMS_KEY_NAME`` /
``SECRET_ENCRYPTION_KEY(SET)``). Every command prints JSON.

Usage::

    # Root key, KEKs, and how many secrets each KEK holds
    uv run python scripts/vault_admin.py status

    # Round-trip a throwaway key through the root (IAM / keyset check)
    uv run python scripts/vault_admin.py probe

    # Rotate the key-encryption key, then move every data key onto it
    uv run python scripts/vault_admin.py rotate-kek
    uv run python scripts/vault_admin.py rewrap

    # Once `status` shows the old KEK holds nothing, stop keeping it
    uv run python scripts/vault_admin.py retire-kek <kek-id>

    # Rotate the token-signing key (tokens signed before keep verifying)
    uv run python scripts/vault_admin.py rotate-sign-key

Rotating the *root* needs no command here: with Cloud KMS, rotate the key in
KMS (new wraps use the new primary version; old versions keep unwrapping); with
a keyset, add a new primary entry and restart -- each process rewraps its KEKs
under the new primary when it loads them. Other processes pick up a new KEK on
their next refresh (``VAULT_KEK_REFRESH_SECONDS``); until then they keep
writing under the old one, which stays readable.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.core.crypto.roots.factory import validate_root_key_config
from app.core.infrastructure.db.session import async_session_maker, close_engine
from app.modules.vault.config import vault_settings
from app.modules.vault.infrastructure.models import VaultKey
from app.modules.vault.services.keyring import ENCRYPT, SIGN
from app.modules.vault.services.rewrap import rewrap_all, secrets_per_key
from app.modules.vault.services.runtime import get_vault_keyring


async def _status() -> dict[str, object]:
    keyring = get_vault_keyring()
    await keyring.load()
    async with async_session_maker() as session:
        keys = (
            (await session.execute(select(VaultKey).order_by(VaultKey.created_at)))
            .scalars()
            .all()
        )
        counts = await secrets_per_key(session)
    return {
        "root_provider": keyring.root.name,
        "keys": [
            {
                "id": str(key.id),
                "purpose": key.purpose,
                "state": key.state,
                "root_key_ref": key.root_key_ref,
                "needs_root_rewrap": keyring.root.needs_rewrap(key.root_key_ref),
                "secrets": counts.get(key.id, 0),
                "created_at": key.created_at.isoformat(),
            }
            for key in keys
        ],
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    validate_root_key_config()
    keyring = get_vault_keyring()
    if args.command == "status":
        return await _status()
    if args.command == "probe":
        return {
            "root_provider": keyring.root.name,
            "wrapped_under": await keyring.root.probe(),
        }
    if args.command == "rotate-kek":
        return {"active_kek": str(await keyring.rotate(ENCRYPT))}
    if args.command == "rotate-sign-key":
        return {"active_sign_key": str(await keyring.rotate(SIGN))}
    if args.command == "rewrap":
        await keyring.load()
        moved = await rewrap_all(
            async_session_maker,
            keyring,
            batch_size=vault_settings.vault_rewrap_batch_size,
        )
        return {"rewrapped": moved}
    if args.command == "retire-kek":
        await keyring.load()
        async with async_session_maker() as session:
            held = (await secrets_per_key(session)).get(args.kek_id, 0)
        if held:
            raise SystemExit(
                f"KEK {args.kek_id} still wraps {held} secrets; run `rewrap` first"
            )
        await keyring.retire(args.kek_id)
        return {"retired": str(args.kek_id)}
    raise SystemExit(f"unknown command {args.command}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "probe", "rotate-kek", "rewrap", "rotate-sign-key"):
        commands.add_parser(name)
    retire = commands.add_parser("retire-kek")
    retire.add_argument("kek_id", type=UUID)
    args = parser.parse_args()

    async def run() -> dict[str, object]:
        try:
            return await _run(args)
        finally:
            await close_engine()

    print(json.dumps(asyncio.run(run()), indent=2))


if __name__ == "__main__":
    main()
