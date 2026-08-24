"""Mint the Telegram session the person-driven scenarios sign in with. Run once.

    cd tests/scenarios && uv run python -m harness.telegram_login

Telegram sends a code to the phone, and only the person holding it can type
that code in — which is the whole reason this is a script somebody runs rather
than something provisioning does. It prints a session string to put in the
environment; after that the suite signs in with it unattended, forever, and
nobody is asked for a code again.

Why a person at all: a bot cannot receive a message nobody sent it, and cannot
send one *as* a human. Every scenario about somebody messaging an agent —
sending a document, replying in a thread, arriving as a stranger — needs a real
account on the other end. That is what this signs in.

The session is a credential: it is that account, without a password prompt.
Keep it out of the repository (`test_no_real_address_is_hardcoded`'s sibling
rule) and out of anywhere shared.
"""

from __future__ import annotations

import asyncio
import os
import sys

from harness.credentials import TELEGRAM_APP
from harness.stack import load_deployment_env

SESSION_SETTING = "TELEGRAM_SESSION"


async def _mint() -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    settings = {**load_deployment_env(), **os.environ}
    api_id = (settings.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (settings.get("TELEGRAM_API_HASH") or "").strip()
    if not api_id or not api_hash:
        print(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH have to be set first.\n"
            "Create them at https://my.telegram.org → API development tools, "
            "against the account that will play the person.",
            file=sys.stderr,
        )
        return 1

    print(
        "Signing in as a person on Telegram.\n"
        "Telegram will send a code to this number — type it in when asked.\n"
    )
    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = await client.get_me()
        session = client.session.save()
        handle = f"@{me.username}" if me.username else me.first_name
        print(
            f"\nSigned in as {handle} (id {me.id}).\n\n"
            f"Put this in the environment, and keep it secret — it is that\n"
            f"account without a password prompt:\n\n"
            f"    {SESSION_SETTING}={session}\n\n"
            f"Then `make scenarios-provision` and the person-driven Telegram\n"
            f"scenarios will run without asking anybody for anything again."
        )
    return 0


def main() -> int:
    if not TELEGRAM_APP.available:
        print(
            f"{TELEGRAM_APP.name} is not configured: "
            f"set {', '.join(TELEGRAM_APP.missing)}.\n{TELEGRAM_APP.how}",
            file=sys.stderr,
        )
        return 1
    try:
        return asyncio.run(_mint())
    except KeyboardInterrupt:
        print("\nnothing was signed in.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
